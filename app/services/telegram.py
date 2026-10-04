import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

from app.config import settings
from app.database import db
from app.services.google_workspace import google_workspace
from app.services.opencode import opencode_service

logger = logging.getLogger(__name__)

# How many consecutive getUpdates failures before we log a loud conflict warning
# and try to repair a broken webhook registration.
_CONFLICT_WARN_AFTER = 2


class TelegramService:
    """Telegram Bot integration (Webhook + optional Polling) backed by OpenCode."""

    def __init__(self):
        self._polling_task: Optional[asyncio.Task] = None
        self._last_update_id: int = 0
        self._consecutive_failures: int = 0
        self._last_error: Optional[str] = None

    @property
    def is_configured(self) -> bool:
        return bool(settings.telegram_bot_token)

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"

    def is_user_allowed(self, user_id: int) -> bool:
        if not settings.telegram_allowed_user_ids:
            return True
        return user_id in settings.telegram_allowed_user_ids

    # ------------------------------------------------------------------
    # Low level Telegram API helper
    # ------------------------------------------------------------------
    async def call_api(
        self,
        method: str,
        payload: Optional[Dict[str, Any]] = None,
        http_method: str = "POST",
        timeout: float = 35.0,
    ) -> Dict[str, Any]:
        """Call a Telegram Bot API method, never raising on transport errors."""
        if not self.is_configured:
            return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is not configured"}

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if http_method == "GET":
                    resp = await client.get(self._api_url(method), params=payload)
                else:
                    resp = await client.post(self._api_url(method), json=payload)
                data = resp.json()
        except Exception as exc:
            logger.error("Telegram %s call failed: %s: %s", method, type(exc).__name__, exc)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        if not data.get("ok", False):
            logger.error(
                "Telegram %s rejected: http=%s description=%s",
                method,
                resp.status_code,
                data.get("description") or data,
            )
        return data

    async def send_message(self, chat_id: int, text: str) -> Dict[str, Any]:
        body = text or "(empty reply)"
        if len(body) > 4096:
            body = body[:4090] + "\n...[truncated]"
        return await self.call_api("sendMessage", {"chat_id": chat_id, "text": body})

    # ------------------------------------------------------------------
    # Update handling
    # ------------------------------------------------------------------
    async def handle_update(self, update: Dict[str, Any]) -> Dict[str, Any]:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return {"status": "ignored", "reason": "no_message"}

        chat = message.get("chat") or {}
        from_user = message.get("from") or {}
        chat_id = chat.get("id")
        user_id = from_user.get("id", chat_id)
        text = (message.get("text") or "").strip()

        if not chat_id or not text:
            return {"status": "ignored", "reason": "empty_text_or_chat"}

        if user_id is not None and not self.is_user_allowed(int(user_id)):
            reply = "Unauthorized Telegram user ID."
            if self.is_configured:
                await self.send_message(chat_id, reply)
            return {"status": "unauthorized", "reply": reply}

        session_id = f"tg_{chat_id}"

        try:
            reply = await self._build_reply(session_id, chat_id, user_id, text)
        except Exception as exc:
            # Never let one bad update kill the polling loop silently.
            logger.exception("Failed to handle Telegram update %s", update.get("update_id"))
            self._last_error = f"{type(exc).__name__}: {exc}"
            reply = (
                "Sorry, I could not process that message. "
                f"Error: {type(exc).__name__}: {exc}"
            )

        if self.is_configured:
            await self.send_message(chat_id, reply)

        return {
            "status": "processed",
            "session_id": session_id,
            "chat_id": chat_id,
            "reply": reply,
        }

    async def _build_reply(
        self, session_id: str, chat_id: int, user_id: Any, text: str
    ) -> str:
        # Handle built-in slash commands
        if text.startswith("/start") or text.startswith("/help"):
            return (
                "Personal AI Agent (powered by OpenCode)\n\n"
                "Commands:\n"
                "/status - Show OpenCode & integrations status\n"
                "/tasks - List your personal tasks\n"
                "/calendar - List upcoming calendar events\n"
                "/clear - Reset conversation history\n"
                "/ai_status - Run a live OpenCode connectivity check\n\n"
                "Or send any message to chat with your OpenCode agent."
            )
        if text.startswith("/clear"):
            db.clear_conversation(session_id)
            return "Conversation history cleared."
        if text.startswith("/ai_status"):
            report = await opencode_service.diagnostics()
            lines = [
                f"OpenCode: {'OK' if report['ok'] else 'NOT WORKING'}",
                f"Mode: {report['mode']} | Model: {report['model']}",
            ]
            for check in report.get("checks", []):
                lines.append(f"- {check['name']}: {'ok' if check['ok'] else 'FAIL'} ({check.get('detail', '')})")
            if report.get("suggested_fix"):
                lines.append(f"\nFix: {report['suggested_fix']}")
            return "\n".join(lines)
        if text.startswith("/status"):
            return (
                f"Provider: OpenCode ({settings.opencode_mode})\n"
                f"Model: {settings.opencode_model}\n"
                f"Base URL: {settings.opencode_base_url}\n"
                f"Google Workspace Live: {google_workspace.is_live_configured}"
            )
        if text.startswith("/tasks"):
            tasks = db.list_tasks()
            if not tasks:
                return "No tasks found."
            lines = [
                f"{'✅' if t['status'] == 'completed' else '⬜'} #{t['id']} {t['title']}"
                for t in tasks[:15]
            ]
            return "Your Tasks:\n" + "\n".join(lines)
        if text.startswith("/calendar"):
            cal = google_workspace.list_calendar_events(max_results=5)
            events = cal.get("events", [])
            if not events:
                return f"No upcoming events ({cal.get('source')})."
            lines = [f"• {e['summary']} ({e['start_time']})" for e in events]
            return f"Upcoming Events ({cal.get('source')}):\n" + "\n".join(lines)

        agent_res = await opencode_service.chat(
            session_id=session_id,
            user_message=text,
            channel="telegram",
            user_id=str(user_id),
        )
        return agent_res.get("reply", "")

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------
    async def start_polling_if_enabled(self) -> None:
        if not (self.is_configured and settings.telegram_polling):
            return
        if self._polling_task and not self._polling_task.done():
            return

        # A previously registered webhook makes getUpdates fail with 409
        # Conflict forever, which looks exactly like a dead bot. Clear it first.
        cleared = await self.call_api(
            "deleteWebhook", {"drop_pending_updates": False}, http_method="POST"
        )
        if cleared.get("ok"):
            logger.info("Telegram polling: cleared any existing webhook registration.")
        else:
            logger.warning(
                "Telegram polling: could not clear webhook (%s). If the bot stays "
                "silent, call deleteWebhook manually or unset the webhook in BotFather.",
                cleared.get("description") or cleared.get("error"),
            )

        self._polling_task = asyncio.create_task(self._poll_loop())
        logger.info("Telegram long polling started.")

    async def stop_polling(self) -> None:
        if self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            logger.info("Telegram long polling stopped.")

    async def _poll_loop(self) -> None:
        backoff = 1.0
        while True:
            try:
                async with httpx.AsyncClient(timeout=35.0) as client:
                    resp = await client.get(
                        self._api_url("getUpdates"),
                        params={"offset": self._last_update_id + 1, "timeout": 25},
                    )
                    data = resp.json()

                    if resp.status_code == 409 or not data.get("ok", True):
                        self._consecutive_failures += 1
                        self._last_error = (
                            data.get("description") or f"HTTP {resp.status_code}"
                        )
                        logger.error(
                            "Telegram getUpdates failed (attempt %s): %s",
                            self._consecutive_failures,
                            self._last_error,
                        )
                        if "conflict" in (self._last_error or "").lower():
                            if self._consecutive_failures == _CONFLICT_WARN_AFTER:
                                logger.error(
                                    "Another process is polling this bot token, or a "
                                    "webhook is still registered. Make sure only ONE "
                                    "instance runs and that no webhook is set; "
                                    "attempting to clear the webhook now."
                                )
                            if self._consecutive_failures >= _CONFLICT_WARN_AFTER:
                                await self.call_api("deleteWebhook", {})
                        elif resp.status_code == 401:
                            logger.error(
                                "Telegram rejected the bot token (401). Check "
                                "TELEGRAM_BOT_TOKEN; polling cannot continue."
                            )
                            return
                    else:
                        if self._consecutive_failures:
                            logger.info("Telegram getUpdates recovered.")
                        self._consecutive_failures = 0
                        self._last_error = None
                        backoff = 1.0
                        for upd in data.get("result", []):
                            self._last_update_id = max(
                                self._last_update_id, upd.get("update_id", 0)
                            )
                            try:
                                await self.handle_update(upd)
                            except Exception:
                                logger.exception(
                                    "Unhandled error while processing update %s",
                                    upd.get("update_id"),
                                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._consecutive_failures += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.error("Telegram polling error: %s", self._last_error)

            await asyncio.sleep(backoff)
            if self._consecutive_failures:
                backoff = min(backoff * 2, 60.0)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    async def diagnostics(self) -> Dict[str, Any]:
        """Report whether Telegram can actually deliver updates to this bot."""
        report: Dict[str, Any] = {
            "configured": self.is_configured,
            "polling_enabled": settings.telegram_polling,
            "polling_running": bool(
                self._polling_task and not self._polling_task.done()
            ),
            "allowed_user_ids": settings.telegram_allowed_user_ids,
            "ok": False,
            "checks": [],
        }
        if not self.is_configured:
            report["suggested_fix"] = "Set TELEGRAM_BOT_TOKEN in .env."
            return report

        me, info = await asyncio.gather(
            self.call_api("getMe", http_method="GET", timeout=10.0),
            self.call_api("getWebhookInfo", http_method="GET", timeout=10.0),
        )
        report["checks"].append(
            {
                "name": "getMe",
                "ok": bool(me.get("ok")),
                "detail": (me.get("result") or {}).get("username")
                or me.get("description")
                or me.get("error"),
            }
        )

        webhook_url = ((info.get("result") or {}).get("url") or "").strip()
        report["checks"].append(
            {
                "name": "getWebhookInfo",
                "ok": info.get("ok", False),
                "webhook_registered": bool(webhook_url),
                "detail": webhook_url or "no webhook registered",
            }
        )

        if settings.telegram_polling and webhook_url:
            report["suggested_fix"] = (
                "TELEGRAM_POLLING=true but a webhook is still registered — Telegram "
                "refuses getUpdates in that case. Polling clears it automatically on "
                "startup; if it keeps failing, delete the webhook in BotFather or call "
                "https://api.telegram.org/bot<token>/deleteWebhook."
            )
        elif not settings.telegram_polling and not webhook_url:
            report["suggested_fix"] = (
                "TELEGRAM_POLLING=false and no webhook is registered, so Telegram has "
                "nowhere to deliver messages. Either set TELEGRAM_POLLING=true or "
                "register POST /webhook/telegram via setWebhook."
            )
        elif self._last_error:
            report["suggested_fix"] = f"Last polling error: {self._last_error}"

        telegram_ok = all(c.get("ok") for c in report["checks"])
        delivery_ok = bool(webhook_url) or report["polling_running"]
        report["ok"] = bool(telegram_ok and delivery_ok)
        return report


telegram_service = TelegramService()
