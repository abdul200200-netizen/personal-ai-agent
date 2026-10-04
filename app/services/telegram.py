import asyncio
from typing import Any, Dict, Optional

import httpx

from app.config import settings
from app.database import db
from app.services.google_workspace import google_workspace
from app.services.opencode import opencode_service


class TelegramService:
    """Telegram Bot integration (Webhook + optional Polling) backed by OpenCode."""

    def __init__(self):
        self._polling_task: Optional[asyncio.Task] = None
        self._last_update_id: int = 0

    @property
    def is_configured(self) -> bool:
        return bool(settings.telegram_bot_token)

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"

    def is_user_allowed(self, user_id: int) -> bool:
        if not settings.telegram_allowed_user_ids:
            return True
        return user_id in settings.telegram_allowed_user_ids

    async def send_message(self, chat_id: int, text: str) -> Dict[str, Any]:
        if not self.is_configured:
            return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is not configured"}

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                self._api_url("sendMessage"),
                json={
                    "chat_id": chat_id,
                    "text": text[:4096],
                },
            )
            return resp.json()

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

        # Handle built-in slash commands
        if text.startswith("/start") or text.startswith("/help"):
            reply = (
                "Personal AI Agent (powered by OpenCode)\n\n"
                "Commands:\n"
                "/status - Show OpenCode & integrations status\n"
                "/tasks - List your personal tasks\n"
                "/calendar - List upcoming calendar events\n"
                "/clear - Reset conversation history\n\n"
                "Or send any message to chat with your OpenCode agent."
            )
        elif text.startswith("/clear"):
            db.clear_conversation(session_id)
            reply = "Conversation history cleared."
        elif text.startswith("/status"):
            reply = (
                f"Provider: OpenCode ({settings.opencode_mode})\n"
                f"Model: {settings.opencode_model}\n"
                f"Base URL: {settings.opencode_base_url}\n"
                f"Google Workspace Live: {google_workspace.is_live_configured}"
            )
        elif text.startswith("/tasks"):
            tasks = db.list_tasks()
            if not tasks:
                reply = "No tasks found."
            else:
                lines = [
                    f"{'✅' if t['status'] == 'completed' else '⬜'} #{t['id']} {t['title']}"
                    for t in tasks[:15]
                ]
                reply = "Your Tasks:\n" + "\n".join(lines)
        elif text.startswith("/calendar"):
            cal = google_workspace.list_calendar_events(max_results=5)
            events = cal.get("events", [])
            if not events:
                reply = f"No upcoming events ({cal.get('source')})."
            else:
                lines = [
                    f"• {e['summary']} ({e['start_time']})" for e in events
                ]
                reply = f"Upcoming Events ({cal.get('source')}):\n" + "\n".join(lines)
        else:
            agent_res = await opencode_service.chat(
                session_id=session_id,
                user_message=text,
                channel="telegram",
                user_id=str(user_id),
            )
            reply = agent_res.get("reply", "")

        if self.is_configured:
            await self.send_message(chat_id, reply)

        return {
            "status": "processed",
            "session_id": session_id,
            "chat_id": chat_id,
            "reply": reply,
        }

    async def start_polling_if_enabled(self) -> None:
        if not (self.is_configured and settings.telegram_polling):
            return
        if self._polling_task and not self._polling_task.done():
            return
        self._polling_task = asyncio.create_task(self._poll_loop())

    async def stop_polling(self) -> None:
        if self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self) -> None:
        while True:
            try:
                async with httpx.AsyncClient(timeout=35.0) as client:
                    resp = await client.get(
                        self._api_url("getUpdates"),
                        params={"offset": self._last_update_id + 1, "timeout": 25},
                    )
                    data = resp.json()
                    for upd in data.get("result", []):
                        self._last_update_id = max(
                            self._last_update_id, upd.get("update_id", 0)
                        )
                        await self.handle_update(upd)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(5)


telegram_service = TelegramService()
