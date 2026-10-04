import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings
from app.database import db
from app.services.google_workspace import google_workspace

logger = logging.getLogger(__name__)

# OpenCode Zen closed its anonymous free tier to non-official clients on
# 2026-09-23. Requests from this app using the placeholder key "public" (or no
# key at all) are now rejected with 401/403/429 instead of answering.
ANONYMOUS_KEY_HINT = (
    "OpenCode Zen no longer allows the anonymous 'public' key from third-party "
    "apps (changed 2026-09-23). Create a real key at https://opencode.ai/auth "
    "and set OPENCODE_API_KEY, then restart the app.\n"
    "Free alternative: install the official OpenCode CLI and set "
    "OPENCODE_MODE=cli (the free tier still works from inside the official client)."
)


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
            "description": "Read rows from the configured Google Sheet.",
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
            "description": "Append a row of values to the configured Google Sheet.",
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


def _extract_error_message(body_text: str) -> str:
    """Pull a human-readable message out of an OpenAI/Anthropic-style error body.

    Nested ``message`` fields win over generic top-level ``type`` values, so a
    body like {"type":"error","error":{"type":"FreeTierError","message":"..."}}
    reports the real explanation instead of the word "error".
    """

    def find_message(node: Any, depth: int = 0) -> str:
        if depth > 4 or node is None:
            return ""
        if isinstance(node, str):
            return node.strip()
        if isinstance(node, list):
            for item in node:
                found = find_message(item, depth + 1)
                if found:
                    return found
            return ""
        if isinstance(node, dict):
            for key in ("message", "detail", "description"):
                value = node.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, (dict, list)):
                    found = find_message(value, depth + 1)
                    if found:
                        return found
            for key in ("error", "response", "data"):
                if key in node:
                    found = find_message(node[key], depth + 1)
                    if found:
                        return found
            for key in ("type", "code"):
                value = node.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    if not body_text:
        return ""
    try:
        payload = json.loads(body_text)
    except Exception:
        return body_text.strip()[:300]

    return (find_message(payload) or body_text.strip())[:300]


def classify_api_failure(
    status_code: int, body_text: str, model: str
) -> Tuple[str, str, str]:
    """Turn an OpenCode HTTP failure into (kind, message, suggested_fix)."""
    upstream = _extract_error_message(body_text)
    detail = f" Upstream said: {upstream}" if upstream else ""
    # Check the raw body too, so detection survives unusual error envelopes.
    lowered = f"{upstream} {body_text}".lower()

    if status_code in (401, 403) and "freetier" in lowered:
        return (
            "free_tier_blocked",
            "OpenCode Zen rejected the request: its free tier can only be used from "
            f"inside the official OpenCode client.{detail}",
            ANONYMOUS_KEY_HINT,
        )
    if status_code in (401, 403):
        return (
            "unauthorized",
            f"OpenCode rejected the API key (HTTP {status_code}, model={model}).{detail}",
            "Check OPENCODE_API_KEY at https://opencode.ai/auth. Keys may expire or be "
            "revoked, and a placeholder such as 'public' will not work.",
        )
    if status_code == 402:
        return (
            "payment_required",
            f"OpenCode Zen reports insufficient credits (HTTP 402).{detail}",
            "Add credits to your OpenCode Zen account, or switch OPENCODE_MODEL to a "
            "free model id and restart the app.",
        )
    if status_code == 429:
        return (
            "rate_limited",
            f"OpenCode rate-limited this request (HTTP 429).{detail}",
            "Wait and retry. Anonymous/placeholder keys are limited far more "
            "aggressively than real keys, so set a real OPENCODE_API_KEY.",
        )
    if status_code == 404:
        return (
            "model_not_found",
            f"Model or endpoint not found (HTTP 404, model={model}).{detail}",
            "Run GET /api/models (or GET /api/diagnostics) to list valid model ids "
            "and update OPENCODE_MODEL. Free model ids rotate over time.",
        )
    if status_code >= 500:
        return (
            "upstream_error",
            f"OpenCode upstream error (HTTP {status_code}).{detail}",
            "Temporary provider outage - the agent will retry on the next message.",
        )
    return (
        "http_error",
        f"OpenCode API error (HTTP {status_code}, model={model}).{detail}",
        "Check OPENCODE_BASE_URL, OPENCODE_MODEL and OPENCODE_API_KEY.",
    )


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
            "You have access to tools for managing Google Calendar events, Google Sheets rows, "
            "personal tasks, and persistent SQLite memories.\n"
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

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    def configuration_issues(self) -> List[str]:
        """Static (no network) configuration problems, most important first."""
        issues: List[str] = []
        if settings.opencode_mode == "cli":
            cli_bin = settings.opencode_cli_path or "opencode"
            if not shutil.which(cli_bin):
                issues.append(
                    f"OPENCODE_MODE=cli but the '{cli_bin}' binary is not in PATH. "
                    "Install it (curl -fsSL https://opencode.ai/install | bash) or "
                    "set OPENCODE_MODE=api with a real OPENCODE_API_KEY."
                )
            return issues

        if settings.uses_anonymous_opencode_key:
            issues.append(ANONYMOUS_KEY_HINT)
        if not settings.opencode_base_url:
            issues.append("OPENCODE_BASE_URL is empty.")
        if not settings.opencode_model:
            issues.append("OPENCODE_MODEL is empty.")
        return issues

    async def diagnostics(self) -> Dict[str, Any]:
        """Live self-test: proves whether the agent can actually reach a model.

        The 2026-09-23 OpenCode Zen change means ``/models`` can keep working
        while ``/chat/completions`` is rejected, so both are checked.
        """
        api_model = self._normalize_model_for_api(settings.opencode_model)
        report: Dict[str, Any] = {
            "provider": "opencode",
            "mode": settings.opencode_mode,
            "base_url": settings.opencode_base_url,
            "model": settings.opencode_model,
            "ok": False,
            "issues": self.configuration_issues(),
            "checks": [],
        }

        if settings.opencode_mode == "cli":
            cli_bin = settings.opencode_cli_path or "opencode"
            path = shutil.which(cli_bin)
            report["checks"].append(
                {
                    "name": "cli_binary",
                    "ok": bool(path),
                    "detail": path or f"'{cli_bin}' not found in PATH",
                }
            )
            report["ok"] = bool(path)
            if not report["ok"]:
                report["suggested_fix"] = report["issues"][0] if report["issues"] else ""
            return report

        url = f"{settings.opencode_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {settings.opencode_api_key or 'public'}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                models_url = f"{settings.opencode_base_url.rstrip('/')}/models"
                try:
                    resp = await client.get(models_url, headers=headers)
                    report["checks"].append(
                        {
                            "name": "models_endpoint",
                            "ok": resp.status_code < 400,
                            "http_status": resp.status_code,
                            "detail": (
                                f"{len(resp.json().get('data', []))} models listed"
                                if resp.status_code < 400
                                else _extract_error_message(resp.text)
                            ),
                        }
                    )
                except Exception as exc:
                    report["checks"].append(
                        {
                            "name": "models_endpoint",
                            "ok": False,
                            "detail": f"{type(exc).__name__}: {exc}",
                        }
                    )

                try:
                    resp = await client.post(
                        url,
                        headers=headers,
                        json={
                            "model": api_model,
                            "messages": [
                                {"role": "user", "content": "Reply with the single word: ok"}
                            ],
                            "max_tokens": 8,
                        },
                    )
                    if resp.status_code < 400:
                        content = (
                            (resp.json().get("choices") or [{}])[0]
                            .get("message", {})
                            .get("content", "")
                        )
                        report["checks"].append(
                            {
                                "name": "chat_completion",
                                "ok": True,
                                "http_status": resp.status_code,
                                "detail": (content or "").strip()[:120] or "(empty reply)",
                            }
                        )
                        report["ok"] = True
                    else:
                        kind, message, fix = classify_api_failure(
                            resp.status_code, resp.text, api_model
                        )
                        report["checks"].append(
                            {
                                "name": "chat_completion",
                                "ok": False,
                                "http_status": resp.status_code,
                                "error": kind,
                                "detail": message,
                            }
                        )
                        report["suggested_fix"] = fix
                except Exception as exc:
                    report["checks"].append(
                        {
                            "name": "chat_completion",
                            "ok": False,
                            "error": "network_error",
                            "detail": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    report["suggested_fix"] = (
                        "The app could not reach the OpenCode endpoint. Check outbound "
                        "network access / DNS / proxy settings from the container or host."
                    )
        except Exception as exc:  # pragma: no cover - safety net
            logger.exception("OpenCode diagnostics failed")
            report["checks"].append(
                {"name": "client", "ok": False, "detail": f"{type(exc).__name__}: {exc}"}
            )

        if not report.get("suggested_fix") and report["issues"]:
            report["suggested_fix"] = report["issues"][0]
        return report

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

        response: Dict[str, Any] = {
            "session_id": session_id,
            "provider": "opencode",
            "mode": settings.opencode_mode,
            "model": model_used,
            "reply": reply_text,
            "tool_calls": tool_calls_executed,
        }
        if result.get("error"):
            # Machine-readable failure kind (free_tier_blocked, unauthorized, ...)
            response["error"] = result["error"]
        return response

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
        tools_disabled = False

        async def _fail(status_code: int, body_text: str) -> Dict[str, Any]:
            kind, message, fix = classify_api_failure(status_code, body_text, api_model)
            logger.error(
                "OpenCode request failed [%s] url=%s model=%s http=%s body=%s",
                kind,
                url,
                api_model,
                status_code,
                (body_text or "")[:500],
            )
            return {
                "reply": f"{message}\n\nHow to fix: {fix}",
                "model": f"opencode/{api_model}",
                "tool_calls": executed_tools,
                "error": kind,
            }

        try:
            async with httpx.AsyncClient(timeout=settings.opencode_timeout) as client:
                for _step in range(3):
                    payload: Dict[str, Any] = {
                        "model": api_model,
                        "messages": messages,
                    }
                    if not tools_disabled:
                        payload["tools"] = AGENT_TOOLS
                        payload["tool_choice"] = "auto"

                    resp = await client.post(url, headers=headers, json=payload)

                    if resp.status_code >= 400:
                        body_text = resp.text
                        # Some models reject the tools schema; retry once without it.
                        if (
                            not tools_disabled
                            and _step == 0
                            and resp.status_code in (400, 404, 422)
                            and any(
                                word in body_text.lower()
                                for word in ("tool", "function")
                            )
                        ):
                            logger.warning(
                                "Model %s rejected the tools schema (HTTP %s); "
                                "retrying without tools.",
                                api_model,
                                resp.status_code,
                            )
                            tools_disabled = True
                            continue
                        return await _fail(resp.status_code, body_text)

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

                # Final synthesis if the tool loop was exhausted
                final_resp = await client.post(
                    url,
                    headers=headers,
                    json={"model": api_model, "messages": messages},
                )
                if final_resp.status_code >= 400:
                    return await _fail(final_resp.status_code, final_resp.text)
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
            logger.exception("OpenCode API call raised url=%s model=%s", url, api_model)
            return {
                "reply": (
                    f"Could not reach OpenCode at {url} (model={api_model}): {exc}\n\n"
                    "How to fix: verify outbound network access from the container/host, "
                    "OPENCODE_BASE_URL, and that OPENCODE_MODE is correct (api or cli)."
                ),
                "model": f"opencode/{api_model}",
                "tool_calls": executed_tools,
                "error": "network_error",
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
