import asyncio

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services.identity import load_workspace_context
from app.services.opencode import opencode_service
from app.services.telegram import split_telegram_message, telegram_service
from app.tools.clinical_evidence_search import search_clinical_evidence

client = TestClient(app)


def test_workspace_identity_and_skills_are_loaded_into_context():
    context = load_workspace_context()
    prompt = opencode_service._build_system_prompt()

    assert "Agent identity and safety rules" in context
    assert "Personal assistant skill" in context
    assert "Clinical evidence skill" in context
    assert "Do NOT diagnose" in prompt
    assert "search_clinical_evidence" in prompt


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
