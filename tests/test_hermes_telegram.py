import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.config import settings
from app.database import db
from app.main import app
from app.services.identity import load_workspace_context, select_active_skills
from app.services.opencode import opencode_service
from app.services.scheduler import brief_scheduler
from app.services.telegram import split_telegram_message, telegram_service
from app.tools.clinical_evidence_search import search_clinical_evidence

client = TestClient(app)


def test_workspace_identity_and_skills_are_loaded_progressively():
    personal_context = load_workspace_context(select_active_skills("Add a task to my calendar"))
    clinical_context = load_workspace_context(select_active_skills("Search recent clinical evidence"))
    personal_prompt = opencode_service._build_system_prompt(
        user_id="skill-test", user_message="Add a task to my calendar"
    )
    clinical_prompt = opencode_service._build_system_prompt(
        user_id="skill-test", user_message="Search recent clinical evidence"
    )

    assert "Agent identity and safety rules" in personal_context
    assert "Personal assistant skill" in personal_context
    assert "Clinical evidence skill" not in personal_context
    assert "Clinical evidence skill" in clinical_context
    assert "Do NOT diagnose" in clinical_prompt
    assert "search_clinical_evidence" in clinical_prompt


def test_phi_chat_is_blocked_before_storage_or_provider_call(monkeypatch):
    called = False

    async def unexpected_provider_call(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("PHI must not be forwarded to OpenCode")

    monkeypatch.setattr(opencode_service, "_chat_via_api", unexpected_provider_call)
    response = client.post(
        "/api/chat",
        json={
            "session_id": "phi_guard_test",
            "message": "My patient John Smith has diabetes",
        },
    )

    assert response.status_code == 200
    assert response.json()["blocked"] is True
    assert "de-identified" in response.json()["reply"]
    assert called is False
    assert client.get("/api/conversations/phi_guard_test/messages").json()["messages"] == []


def test_structured_clinical_search_blocks_phi_in_any_field():
    result = asyncio.run(
        search_clinical_evidence(
            question="heart failure treatment evidence",
            population="My patient John Smith",
        )
    )
    assert result["error"]
    assert result["results"] == []


def test_memory_endpoints_enforce_policy_and_allow_deletion():
    blocked = client.post(
        "/api/memories",
        json={"key": "patient", "value": "John Smith, DOB 15/03/1985"},
    )
    assert blocked.status_code == 400

    saved = client.post(
        "/api/memories",
        json={"key": "response_style", "value": "Use concise bullet points"},
    )
    assert saved.status_code == 200
    assert saved.json()["memory"]["key"] == "response_style"

    deleted = client.delete("/api/memories/response_style")
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"
    assert client.delete("/api/memories/response_style").status_code == 404


def test_thinking_mode_is_user_configurable_without_exposing_chain_of_thought():
    user_id = "thinking-mode-test"
    db.set_preference(user_id, "thinking_mode", "off")
    normal_prompt = opencode_service._build_system_prompt(
        user_id=user_id, user_message="Review my strategy"
    )
    db.set_preference(user_id, "thinking_mode", "on")
    thinking_prompt = opencode_service._build_system_prompt(
        user_id=user_id, user_message="Review my strategy"
    )
    db.set_preference(user_id, "thinking_mode", "off")

    assert "Thinking Mode is OFF" in normal_prompt
    assert "Thinking Mode is ON" in thinking_prompt
    assert "Never reveal hidden chain-of-thought" in thinking_prompt


def test_memory_learning_proposals_require_approval_and_skill_changes_stay_manual():
    user_id = "987654398"
    memory_proposal = db.create_proposal(
        user_id=user_id,
        proposal_type="memory",
        target="response_style",
        proposed_value="Prefer concise, structured answers",
        rationale="This preference appeared consistently.",
        source_session_id="session-learning-test",
    )
    assert memory_proposal["status"] == "pending"
    assert not any(item["key"] == "response_style" for item in db.list_memories())

    approved = db.review_proposal(user_id, memory_proposal["id"], "approve")
    assert approved["status"] == "approved"
    assert any(item["key"] == "response_style" for item in db.list_memories())
    db.delete_memory("response_style")

    skill_proposal = db.create_proposal(
        user_id=user_id,
        proposal_type="skill",
        target="personal-assistant",
        proposed_value="Suggested wording improvement for task confirmations.",
        rationale="Make task completion confirmations more specific.",
    )
    skill_approval_reply = telegram_service._proposal_command(
        int(user_id), "approve", str(skill_proposal["id"])
    )
    assert "did not edit any files" in skill_approval_reply
    assert db.list_proposals(user_id, status="approved")[0]["proposal_type"] == "skill"


def test_brief_scheduler_includes_weekly_and_month_end_cadence():
    timezone_name = ZoneInfo("Asia/Riyadh")
    thursday = datetime(2026, 10, 8, 20, 0, tzinfo=timezone_name)
    thursday_jobs = brief_scheduler._jobs_due_for("cadence-test", thursday)
    assert any(job[0] == "weekly_calibration" for job in thursday_jobs)
    assert not any(job[0] == "monthly_audit" for job in thursday_jobs)

    month_end = datetime(2026, 10, 31, 20, 0, tzinfo=timezone_name)
    month_end_jobs = brief_scheduler._jobs_due_for("cadence-test", month_end)
    monthly_job = next(job for job in month_end_jobs if job[0] == "monthly_audit")
    assert monthly_job[1] == "2026-10"


def test_brief_scheduler_is_opt_in_timezone_aware_and_idempotent(monkeypatch):
    user_id = "987654399"
    sent = []

    async def fake_send_message(chat_id, text):
        sent.append((chat_id, text))
        return {"ok": True}

    monkeypatch.setattr(telegram_service, "send_message", fake_send_message)
    monkeypatch.setattr(settings, "telegram_allowed_user_ids", [int(user_id)])
    monkeypatch.setattr(settings, "telegram_allow_all_users", False)
    db.set_preference(user_id, "briefs_enabled", "true")
    db.set_preference(user_id, "timezone", "Asia/Riyadh")
    db.set_preference(user_id, "morning_brief_time", "06:00")
    db.add_task("Scheduler test: define today's top priority")

    now_utc = datetime(2026, 10, 8, 3, 2, tzinfo=timezone.utc)  # 06:02 in Riyadh
    first_run = asyncio.run(brief_scheduler.run_due_jobs_once(now_utc))
    duplicate_run = asyncio.run(brief_scheduler.run_due_jobs_once(now_utc))
    db.set_preference(user_id, "briefs_enabled", "false")

    assert first_run == 1
    assert duplicate_run == 0
    assert len(sent) == 1
    assert sent[0][0] == int(user_id)
    assert "Morning Intent" in sent[0][1]
    assert "top priority" in sent[0][1]


def test_telegram_thinking_and_brief_commands_persist_preferences(monkeypatch):
    user_id = 987654397
    monkeypatch.setattr(settings, "telegram_allowed_user_ids", [user_id])
    monkeypatch.setattr(settings, "telegram_allow_all_users", False)
    monkeypatch.setattr(settings, "telegram_bot_token", "")

    async def send_command(update_id, text):
        return await telegram_service.handle_update(
            {
                "update_id": update_id,
                "message": {
                    "from": {"id": user_id},
                    "chat": {"id": user_id, "type": "private"},
                    "text": text,
                },
            }
        )

    think_result = asyncio.run(send_command(987654390, "/think on"))
    brief_result = asyncio.run(send_command(987654391, "/brief on"))
    time_result = asyncio.run(send_command(987654392, "/brief morning 06:15"))
    timezone_result = asyncio.run(send_command(987654393, "/brief timezone Asia/Riyadh"))
    asyncio.run(send_command(987654394, "/brief off"))

    assert "Thinking Mode is ON" in think_result["reply"]
    assert "are ON" in brief_result["reply"]
    assert "06:15" in time_result["reply"]
    assert "Asia/Riyadh" in timezone_result["reply"]
    assert db.get_preference(str(user_id), "thinking_mode") == "on"
    assert db.get_preference(str(user_id), "briefs_enabled") == "false"


def test_telegram_private_allowlist_and_update_deduplication(monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_user_ids", [42])
    monkeypatch.setattr(settings, "telegram_allow_all_users", False)
    monkeypatch.setattr(settings, "telegram_bot_token", "")
    update = {
        "update_id": 987654321,
        "message": {
            "message_id": 1,
            "from": {"id": 42},
            "chat": {"id": 42, "type": "private"},
            "text": "/status",
        },
    }

    result = asyncio.run(telegram_service.handle_update(update))
    duplicate = asyncio.run(telegram_service.handle_update(update))

    assert result["status"] == "processed"
    assert "Provider: OpenCode" in result["reply"]
    assert duplicate["status"] == "duplicate"


def test_telegram_rejects_unknown_users_and_groups(monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_user_ids", [42])
    monkeypatch.setattr(settings, "telegram_allow_all_users", False)
    monkeypatch.setattr(settings, "telegram_bot_token", "")

    unauthorized = asyncio.run(
        telegram_service.handle_update(
            {
                "update_id": 987654322,
                "message": {
                    "from": {"id": 43},
                    "chat": {"id": 43, "type": "private"},
                    "text": "/start",
                },
            }
        )
    )
    group = asyncio.run(
        telegram_service.handle_update(
            {
                "update_id": 987654323,
                "message": {
                    "from": {"id": 42},
                    "chat": {"id": -100, "type": "supergroup"},
                    "text": "Hello",
                },
            }
        )
    )

    assert unauthorized["status"] == "unauthorized"
    assert group["status"] == "ignored"
    assert group["reason"] == "private_chats_only"


def test_webhook_requires_secret_and_is_disabled_while_polling(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "configured-test-token")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "")
    monkeypatch.setattr(settings, "telegram_polling", True)

    polling_response = client.post("/webhook/telegram", json={})
    assert polling_response.status_code == 409

    monkeypatch.setattr(settings, "telegram_polling", False)
    missing_secret_response = client.post("/webhook/telegram", json={})
    assert missing_secret_response.status_code == 503


def test_telegram_long_message_split_preserves_unicode_and_content():
    text = ("Hello 🙂 world\n" * 700) + "the end"
    chunks = split_telegram_message(text, limit=120)

    assert len(chunks) > 1
    assert "".join(chunks) == text
    assert all(len(chunk.encode("utf-16-le")) // 2 <= 120 for chunk in chunks)
