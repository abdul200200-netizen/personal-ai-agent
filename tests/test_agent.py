import os
import tempfile
import pytest
from fastapi.testclient import TestClient

# Point SQLite to a temporary file during tests
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["SQLITE_DB_PATH"] = _tmp_db.name

from app.main import app  # noqa: E402
from app.services.opencode import opencode_service  # noqa: E402


client = TestClient(app)


def test_health_and_status_only_opencode():
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"
    assert data["provider"] == "opencode"

    status_res = client.get("/api/status")
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["opencode"]["provider"] == "opencode"
    assert "gemini" not in status_data
    assert "bedrock" not in status_data
    assert "kimi" not in status_data


def test_tasks_and_memories():
    # Create task
    res = client.post("/api/tasks", json={"title": "Review OpenCode deployment"})
    assert res.status_code == 200
    task_id = res.json()["task"]["id"]

    # Complete task
    comp = client.post(f"/api/tasks/{task_id}/complete")
    assert comp.status_code == 200
    assert comp.json()["task"]["status"] == "completed"

    # Save & list memory
    mem = client.post(
        "/api/memories", json={"key": "preferred_provider", "value": "opencode"}
    )
    assert mem.status_code == 200
    mems = client.get("/api/memories").json()["memories"]
    assert any(
        m["key"] == "preferred_provider" and m["value"] == "opencode" for m in mems
    )


def test_calendar_and_sheets_endpoints():
    # Create & list calendar event
    ev = client.post(
        "/api/calendar/events",
        json={
            "summary": "OpenCode Sprint Planning",
            "start_time": "2026-10-05T09:00:00Z",
            "end_time": "2026-10-05T10:00:00Z",
        },
    )
    assert ev.status_code == 200
    events = client.get("/api/calendar/events").json()["events"]
    assert any(e["summary"] == "OpenCode Sprint Planning" for e in events)

    # Append & read sheet row
    sh = client.post(
        "/api/sheets/rows", json={"values": ["2026-10-04", "OpenCode API", "Active"]}
    )
    assert sh.status_code == 200
    rows = client.get("/api/sheets/rows").json()["rows"]
    assert ["2026-10-04", "OpenCode API", "Active"] in rows


def test_chat_and_telegram_webhook(monkeypatch):
    async def mock_chat_via_api(session_id: str, active_model: str):
        return {
            "reply": "Hello from OpenCode!",
            "model": f"opencode/{active_model}",
            "tool_calls": [],
        }

    monkeypatch.setattr(opencode_service, "_chat_via_api", mock_chat_via_api)

    chat_res = client.post(
        "/api/chat",
        json={"session_id": "test_sess", "message": "Hi OpenCode"},
    )
    assert chat_res.status_code == 200
    data = chat_res.json()
    assert data["provider"] == "opencode"
    assert data["reply"] == "Hello from OpenCode!"

    tg_res = client.post(
        "/webhook/telegram",
        json={
            "update_id": 1001,
            "message": {
                "message_id": 1,
                "from": {"id": 42},
                "chat": {"id": 42},
                "text": "/status",
            },
        },
    )
    assert tg_res.status_code == 200
    assert "Provider: OpenCode" in tg_res.json()["reply"]


def test_opencode_tool_calling_loop(monkeypatch):
    import httpx

    call_count = {"n": 0}

    class DummyResponse:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    async def mock_post(self, url, headers=None, json=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return DummyResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_task_1",
                                        "type": "function",
                                        "function": {
                                            "name": "add_task",
                                            "arguments": '{"title": "Deploy OpenCode agent"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            )
        return DummyResponse(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Added task: Deploy OpenCode agent.",
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    res = client.post(
        "/api/chat",
        json={"session_id": "tool_test", "message": "Add a task to deploy OpenCode agent"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["provider"] == "opencode"
    assert body["reply"] == "Added task: Deploy OpenCode agent."
    assert len(body["tool_calls"]) == 1
    assert body["tool_calls"][0]["name"] == "add_task"

