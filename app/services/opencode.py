import asyncio
import json
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.database import db
from app.services.google_workspace import google_workspace


AGENT_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_calendar_events",
            "description": "List upcoming events from Google Calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of events to return (default 10).",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "Create a new event in Google Calendar.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Event title/summary."},
                    "start_time": {
                        "type": "string",
                        "description": "ISO 8601 start time (e.g. 2026-10-05T09:00:00Z).",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "ISO 8601 end time (e.g. 2026-10-05T10:00:00Z).",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional event description.",
                    },
                    "location": {
                        "type": "string",
                        "description": "Optional event location.",
                    },
                },
                "required": ["summary", "start_time", "end_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_sheet_rows",
            "description": "Read rows from the configured Google Sheet, or from the local SQLite row store when no sheet is configured.",
            "parameters": {
                "type": "object",
                "properties": {
                    "range_name": {
                        "type": "string",
                        "description": "A1 notation range, e.g. 'Sheet1!A:E'.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_sheet_row",
            "description": "Append a row to the configured Google Sheet, or to the local SQLite row store when no sheet is configured.",
            "parameters": {
                "type": "object",
                "properties": {
                    "values": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of cell values to append as a new row.",
                    },
                    "range_name": {
                        "type": "string",
                        "description": "Optional sheet range (default Sheet1!A:E).",
                    },
                },
                "required": ["values"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save a persistent personal memory or preference in SQLite.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Memory key/topic."},
                    "value": {"type": "string", "description": "Memory content/value."},
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_memories",
            "description": "List all saved personal memories.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_task",
            "description": "Add a personal task or to-do item.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task description."},
                    "due_date": {
                        "type": "string",
                        "description": "Optional due date string.",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List personal tasks (optionally filtered by status: pending or completed).",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["pending", "completed"],
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Mark a task as completed by its numeric ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "ID of the task."}
                },
                "required": ["task_id"],
            },
        },
    },
]


def execute_agent_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute one of the built-in personal AI agent tools."""
    try:
        if name == "list_calendar_events":
            return google_workspace.list_calendar_events(
                max_results=int(arguments.get("max_results", 10))
            )
        if name == "create_calendar_event":
            return google_workspace.create_calendar_event(
                summary=arguments["summary"],
                start_time=arguments["start_time"],
                end_time=arguments["end_time"],
                description=arguments.get("description", ""),
                location=arguments.get("location", ""),
            )
        if name == "read_sheet_rows":
            return google_workspace.read_sheet_rows(
                range_name=arguments.get("range_name")
            )
        if name == "append_sheet_row":
            return google_workspace.append_sheet_row(
                values=arguments.get("values", []),
                range_name=arguments.get("range_name"),
            )
        if name == "save_memory":
            return db.save_memory(
                key=arguments["key"],
                value=arguments["value"],
            )
        if name == "list_memories":
            return {"memories": db.list_memories()}
        if name == "add_task":
            return db.add_task(
                title=arguments["title"],
                due_date=arguments.get("due_date"),
            )
        if name == "list_tasks":
            return {"tasks": db.list_tasks(status=arguments.get("status"))}
        if name == "complete_task":
            updated = db.update_task_status(int(arguments["task_id"]), "completed")
            return {"task": updated}
        return {"error": f"Unknown tool: {name}"}
    except Exception as exc:
        return {"error": str(exc)}


class OpenCodeService:
    """
    AI Agent service powered exclusively by OpenCode.
    Supports:
      - OpenCode API mode (`https://opencode.ai/zen/v1` or local `opencode serve`)
      - OpenCode CLI mode (`opencode run`)
    """

    def _normalize_model_for_api(self, model: str) -> str:
        model = (model or settings.opencode_model or "big-pickle").strip()
        if model.startswith("opencode/"):
            return model.split("/", 1)[1]
        return model

    def _normalize_model_for_cli(self, model: str) -> str:
        model = (model or settings.opencode_model or "opencode/big-pickle").strip()
        if "/" not in model:
            return f"opencode/{model}"
        return model

    def _build_system_prompt(self) -> str:
        memories = db.list_memories()
        mem_lines = (
            "\n".join(f"- {m['key']}: {m['value']}" for m in memories[:15])
            if memories
            else "None stored yet."
        )
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return (
            "You are a helpful Personal AI Agent powered exclusively by OpenCode.\n"
            f"Current UTC time: {now_utc}\n"
            "You have tools for Google Calendar, spreadsheet-like rows, tasks, and saved memories. "
            "Google Sheets is optional: when no sheet is configured, read_sheet_rows and "
            "append_sheet_row use the local SQLite row store. Be clear that these local rows "
            "are not synced to Google Sheets.\n"
            "Saved user memories:\n"
            f"{mem_lines}\n"
            "Be concise, accurate, and proactive in using tools when the user asks about their "
            "schedule, spreadsheets, tasks, or preferences."
        )

    async def list_models(self) -> Dict[str, Any]:
        """Query available models from the OpenCode API."""
        url = f"{settings.opencode_base_url.rstrip('/')}/models"
        headers = {
            "Authorization": f"Bearer {settings.opencode_api_key or 'public'}",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                models = [m.get("id") for m in data.get("data", []) if m.get("id")]
                return {
                    "provider": "opencode",
                    "mode": settings.opencode_mode,
                    "base_url": settings.opencode_base_url,
                    "active_model": settings.opencode_model,
                    "available_models": models,
                }
        except Exception as exc:
            fallback_models = list(
                dict.fromkeys([settings.opencode_model, "big-pickle", "grok-code"])
            )
            return {
                "provider": "opencode",
                "mode": settings.opencode_mode,
                "base_url": settings.opencode_base_url,
                "active_model": settings.opencode_model,
                "available_models": fallback_models,
                "warning": str(exc),
            }

    async def chat(
        self,
        session_id: str,
        user_message: str,
        channel: str = "web",
        user_id: str = "default",
        model_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Process a user message with OpenCode, persisting conversation history in SQLite."""
        active_model = model_override or settings.opencode_model
        db.add_message(
            session_id=session_id,
            role="user",
            content=user_message,
            model=f"opencode/{self._normalize_model_for_api(active_model)}",
            channel=channel,
            user_id=user_id,
        )

        if settings.opencode_mode == "cli":
            result = await self._chat_via_cli(session_id, user_message, active_model)
        else:
            result = await self._chat_via_api(session_id, active_model)

        reply_text = result.get("reply", "")
        tool_calls_executed = result.get("tool_calls", [])
        model_used = result.get(
            "model", f"opencode/{self._normalize_model_for_api(active_model)}"
        )

        db.add_message(
            session_id=session_id,
            role="assistant",
            content=reply_text,
            model=model_used,
            tool_calls=tool_calls_executed,
            channel=channel,
            user_id=user_id,
        )

        return {
            "session_id": session_id,
            "provider": "opencode",
            "mode": settings.opencode_mode,
            "model": model_used,
            "reply": reply_text,
            "tool_calls": tool_calls_executed,
        }

    async def _chat_via_api(
        self, session_id: str, active_model: str
    ) -> Dict[str, Any]:
        api_model = self._normalize_model_for_api(active_model)
        history = db.get_messages(session_id, limit=20)

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._build_system_prompt()}
        ]
        for msg in history:
            if msg["role"] in ("user", "assistant"):
                messages.append({"role": msg["role"], "content": msg["content"]})

        url = f"{settings.opencode_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.opencode_api_key or 'public'}",
            "Content-Type": "application/json",
        }
        executed_tools: List[Dict[str, Any]] = []

        try:
            async with httpx.AsyncClient(timeout=settings.opencode_timeout) as client:
                for _step in range(3):
                    payload: Dict[str, Any] = {
                        "model": api_model,
                        "messages": messages,
                        "tools": AGENT_TOOLS,
                        "tool_choice": "auto",
                    }
                    resp = await client.post(url, headers=headers, json=payload)

                    # If the selected model does not support tools schema, retry without tools parameter
                    if resp.status_code >= 400 and _step == 0:
                        fallback_payload = {
                            "model": api_model,
                            "messages": messages,
                        }
                        resp = await client.post(
                            url, headers=headers, json=fallback_payload
                        )

                    resp.raise_for_status()
                    data = resp.json()
                    choice = (data.get("choices") or [{}])[0]
                    message_obj = choice.get("message") or {}
                    tool_calls = message_obj.get("tool_calls") or []

                    if not tool_calls:
                        content = message_obj.get("content") or ""
                        return {
                            "reply": content.strip() or "(Empty response from OpenCode)",
                            "model": f"opencode/{api_model}",
                            "tool_calls": executed_tools,
                        }

                    messages.append(message_obj)
                    for tc in tool_calls:
                        fn = tc.get("function") or {}
                        fn_name = fn.get("name", "")
                        raw_args = fn.get("arguments") or "{}"
                        try:
                            parsed_args = (
                                json.loads(raw_args)
                                if isinstance(raw_args, str)
                                else raw_args
                            )
                        except Exception:
                            parsed_args = {}

                        tool_output = execute_agent_tool(fn_name, parsed_args)
                        executed_tools.append(
                            {
                                "name": fn_name,
                                "arguments": parsed_args,
                                "result": tool_output,
                            }
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.get("id", "call_1"),
                                "name": fn_name,
                                "content": json.dumps(tool_output),
                            }
                        )

                # Final synthesis if loop exhausted
                final_resp = await client.post(
                    url,
                    headers=headers,
                    json={"model": api_model, "messages": messages},
                )
                final_resp.raise_for_status()
                final_data = final_resp.json()
                final_content = (
                    (final_data.get("choices") or [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                return {
                    "reply": final_content.strip() or "Completed tool actions.",
                    "model": f"opencode/{api_model}",
                    "tool_calls": executed_tools,
                }
        except Exception as exc:
            return {
                "reply": (
                    f"OpenCode API error ({url}, model={api_model}): {exc}\n\n"
                    "Please verify OPENCODE_API_KEY, OPENCODE_BASE_URL, and OPENCODE_MODEL."
                ),
                "model": f"opencode/{api_model}",
                "tool_calls": executed_tools,
            }

    async def _chat_via_cli(
        self, session_id: str, user_message: str, active_model: str
    ) -> Dict[str, Any]:
        cli_bin = settings.opencode_cli_path or "opencode"
        cli_model = self._normalize_model_for_cli(active_model)
        if not shutil.which(cli_bin):
            return {
                "reply": (
                    f"OpenCode CLI binary '{cli_bin}' was not found in PATH. "
                    "Install OpenCode CLI (`curl -fsSL https://opencode.ai/install | bash`) "
                    "or set OPENCODE_MODE=api."
                ),
                "model": cli_model,
                "tool_calls": [],
            }

        proc = await asyncio.create_subprocess_exec(
            cli_bin,
            "run",
            "-m",
            cli_model,
            user_message,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=settings.opencode_timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            return {
                "reply": "OpenCode CLI timed out.",
                "model": cli_model,
                "tool_calls": [],
            }

        out_text = stdout.decode("utf-8", errors="replace").strip()
        err_text = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0 and not out_text:
            return {
                "reply": f"OpenCode CLI error (exit {proc.returncode}): {err_text}",
                "model": cli_model,
                "tool_calls": [],
            }

        return {
            "reply": out_text or err_text or "(No output from OpenCode CLI)",
            "model": cli_model,
            "tool_calls": [],
        }


opencode_service = OpenCodeService()
