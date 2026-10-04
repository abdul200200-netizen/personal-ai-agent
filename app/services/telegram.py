import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

from app.config import settings
from app.database import db
from app.services.google_workspace import google_workspace
from app.services.opencode import opencode_service

logger = logging.getLogger(__name__)


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
            logger.warning("Telegram not configured - cannot send message")
            return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is not configured"}

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    self._api_url("sendMessage"),
                    json={
                        "chat_id": chat_id,
                        "text": text[:4096],
                    },
                )
                result = resp.json()
                if not result.get("ok"):
                    logger.error(f"Telegram API error: {result}")
                return result
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return {"ok": False, "error": str(e)}

    async def handle_update(self, update: Dict[str, Any]) -> Dict[str, Any]:
        try:
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
                logger.info(f"[Telegram] Processing user message: {user_id} -> {text[:50]}")
                agent_res = await opencode_service.chat(
                    session_id=session_id,
                    user_message=text,
                    channel="telegram",
                    user_id=str(user_id),
                )
                reply = agent_res.get("reply", "")
                logger.info(f"[Telegram] OpenCode replied: {reply[:50]}")

            if self.is_configured:
                send_result = await self.send_message(chat_id, reply)
                if not send_result.get("ok"):
                    logger.error(f"Failed to send reply to Telegram: {send_result}")

            return {
                "status": "processed",
                "session_id": session_id,
                "chat_id": chat_id,
                "reply": reply,
            }
        except Exception as e:
            logger.exception(f"Error handling Telegram update: {e}")
            return {"status": "error", "error": str(e)}

    async def start_polling_if_enabled(self) -> None:
        if not (self.is_configured and settings.telegram_polling):
            logger.info("Telegram polling is disabled or not configured")
            return
        if self._polling_task and not self._polling_task.done():
            logger.info("Telegram polling task already running")
            return
        logger.info("Starting Telegram polling...")
        self._polling_task = asyncio.create_task(self._poll_loop())

    async def stop_polling(self) -> None:
        if self._polling_task and not self._polling_task.done():
            logger.info("Stopping Telegram polling...")
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                logger.info("Telegram polling task cancelled")

    async def _poll_loop(self) -> None:
        """Long-polling loop for receiving Telegram updates."""
        consecutive_errors = 0
        max_consecutive_errors = 10
        
        while True:
            try:
                async with httpx.AsyncClient(timeout=35.0) as client:
                    logger.debug(f"Polling for updates (offset={self._last_update_id + 1})")
                    resp = await client.get(
                        self._api_url("getUpdates"),
                        params={"offset": self._last_update_id + 1, "timeout": 25},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    
                    if not data.get("ok"):
                        logger.error(f"Telegram getUpdates API error: {data}")
                        consecutive_errors += 1
                        if consecutive_errors >= max_consecutive_errors:
                            logger.error(f"Too many consecutive Telegram API errors ({consecutive_errors}), stopping polling")
                            break
                        await asyncio.sleep(5)
                        continue
                    
                    consecutive_errors = 0  # Reset error counter on success
                    updates = data.get("result", [])
                    
                    if updates:
                        logger.debug(f"Received {len(updates)} updates")
                    
                    for upd in updates:
                        self._last_update_id = max(
                            self._last_update_id, upd.get("update_id", 0)
                        )
                        await self.handle_update(upd)
                        
            except asyncio.CancelledError:
                logger.info("Telegram polling cancelled")
                break
            except httpx.HTTPError as e:
                logger.error(f"Telegram API HTTP error: {e}")
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(f"Too many HTTP errors ({consecutive_errors}), stopping polling")
                    break
                await asyncio.sleep(5)
            except Exception as e:
                logger.exception(f"Unexpected error in Telegram polling loop: {e}")
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(f"Too many errors ({consecutive_errors}), stopping polling")
                    break
                await asyncio.sleep(5)


telegram_service = TelegramService()
