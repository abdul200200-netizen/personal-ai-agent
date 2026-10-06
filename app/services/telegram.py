import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings
from app.database import db
from app.services.google_workspace import google_workspace
from app.services.opencode import opencode_service

logger = logging.getLogger(__name__)

# Leave a little headroom below Telegram's 4096-character sendMessage limit.
TELEGRAM_MESSAGE_LIMIT = 3900


def split_telegram_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> List[str]:
    """Split long text without dropping content or breaking Unicode surrogate pairs."""
    remaining = str(text or "")
    if not remaining:
        return []

    chunks: List[str] = []
    while remaining:
        units = 0
        cut = 0
        last_break = 0
        for index, character in enumerate(remaining):
            character_units = len(character.encode("utf-16-le")) // 2
            if units + character_units > limit:
                break
            units += character_units
            cut = index + 1
            if character.isspace():
                last_break = cut

        if cut == 0:  # A single character is larger than the configured limit.
            cut = 1
        if cut < len(remaining) and last_break >= int(cut * 0.6):
            cut = last_break

        chunks.append(remaining[:cut])
        remaining = remaining[cut:]

    return chunks


class TelegramService:
    """Private Telegram interface for the OpenCode personal assistant."""

    def __init__(self):
        self._polling_task: Optional[asyncio.Task] = None
        self._last_update_id = 0

    @property
    def is_configured(self) -> bool:
        return bool(settings.telegram_bot_token)

    @property
    def is_polling(self) -> bool:
        return bool(self._polling_task and not self._polling_task.done())

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"

    def is_user_allowed(self, user_id: int) -> bool:
        if settings.telegram_allow_all_users:
            return True
        return bool(
            settings.telegram_allowed_user_ids
            and user_id in settings.telegram_allowed_user_ids
        )

    @staticmethod
    def _split_command(text: str) -> Tuple[Optional[str], str]:
        """Parse /command, /command@botname, and optional arguments."""
        parts = text.strip().split(maxsplit=1)
        if not parts or not parts[0].startswith("/"):
            return None, ""
        command = parts[0][1:].split("@", 1)[0].lower()
        if not re.fullmatch(r"[a-z0-9_]+", command):
            return None, ""
        return command, parts[1].strip() if len(parts) > 1 else ""

    async def _call_telegram_api(
        self, method: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        if not self.is_configured:
            return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is not configured"}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(self._api_url(method), json=payload)
                try:
                    result = response.json()
                except ValueError:
                    result = {}
                if response.status_code >= 400:
                    logger.warning(
                        "Telegram API %s returned HTTP %s", method, response.status_code
                    )
                    return {"ok": False, "error": f"Telegram API HTTP {response.status_code}"}
                if not isinstance(result, dict) or not result.get("ok"):
                    # Do not log Telegram's description or request URL; either could contain user data/token.
                    logger.warning("Telegram API %s returned ok=false", method)
                    return {"ok": False, "error": "Telegram API request failed"}
                return result
        except httpx.HTTPError as exc:
            logger.warning(
                "Telegram API %s request failed (%s)", method, type(exc).__name__
            )
            return {"ok": False, "error": "Telegram API request failed"}
        except Exception as exc:
            logger.warning(
                "Telegram API %s request failed (%s)", method, type(exc).__name__
            )
            return {"ok": False, "error": "Telegram API request failed"}

    async def send_message(self, chat_id: int, text: str) -> Dict[str, Any]:
        if not self.is_configured:
            logger.info("Telegram is not configured; response was not sent")
            return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is not configured"}

        chunks = split_telegram_message(text or "(No response)")
        results = []
        for chunk in chunks:
            result = await self._call_telegram_api(
                "sendMessage", {"chat_id": chat_id, "text": chunk}
            )
            if not result.get("ok"):
                return result
            results.append(result.get("result"))
        return {"ok": True, "result": results, "chunks_sent": len(results)}

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> Dict[str, Any]:
        return await self._call_telegram_api(
            "sendChatAction", {"chat_id": chat_id, "action": action}
        )

    @staticmethod
    def _help_text() -> str:
        return (
            "Your private Personal AI Agent (OpenCode)\n\n"
            "Send a message to chat. You can ask for current clinical evidence, manage tasks, "
            "or request help with your calendar.\n\n"
            "Commands:\n"
            "/start, /help — show this help\n"
            "/status — show service status\n"
            "/tasks — list tasks\n"
            "/calendar — list upcoming events\n"
            "/memory — review saved memories\n"
            "/forget <key> — delete a saved memory\n"
            "/evidence <question> — search current clinical evidence\n"
            "/drugs <name> — search general openFDA drug information\n"
            "/clear — clear this chat's conversation history\n\n"
            "For privacy, the bot only accepts approved Telegram user IDs in private chats."
        )

    def _status_text(self) -> str:
        if settings.telegram_allow_all_users:
            access = "Public access enabled"
        elif settings.telegram_allowed_user_ids:
            access = f"Private allowlist enabled ({len(settings.telegram_allowed_user_ids)} user(s))"
        else:
            access = "Private; no authorized user IDs configured"
        return (
            "Personal AI Agent status\n"
            f"Provider: OpenCode ({settings.opencode_mode})\n"
            f"Model: {settings.opencode_model}\n"
            f"Telegram: {'configured' if self.is_configured else 'not configured'}\n"
            f"Polling: {'running' if self.is_polling else 'not running'}\n"
            f"Access: {access}\n"
            f"Google Workspace live: {google_workspace.is_live_configured}"
        )

    async def _process_text(
        self, text: str, session_id: str, user_id: int, chat_id: int
    ) -> str:
        command, argument = self._split_command(text)
        if command in {"start", "help"}:
            return self._help_text()
        if command == "status":
            return self._status_text()
        if command == "clear":
            db.clear_conversation(session_id)
            return "Conversation history cleared. Saved memories and tasks were not changed."
        if command == "tasks":
            tasks = db.list_tasks()
            if not tasks:
                return "No tasks found."
            lines = [
                f"{'✅' if task['status'] == 'completed' else '⬜'} #{task['id']} {task['title']}"
                + (f" — due {task['due_date']}" if task.get("due_date") else "")
                for task in tasks[:20]
            ]
            return "Your tasks:\n" + "\n".join(lines)
        if command == "calendar":
            calendar = google_workspace.list_calendar_events(max_results=5)
            events = calendar.get("events", [])
            if not events:
                return f"No upcoming events ({calendar.get('source', 'calendar')})."
            lines = [
                f"• {event.get('summary', 'Untitled')} ({event.get('start_time', 'time unknown')})"
                for event in events
            ]
            return f"Upcoming events ({calendar.get('source', 'calendar')}):\n" + "\n".join(lines)
        if command == "memory":
            memories = db.list_memories()
            if not memories:
                return "No saved memories yet. You can ask me to remember a non-sensitive preference."
            lines = [f"• {item['key']}: {item['value']}" for item in memories[:20]]
            return "Saved memories:\n" + "\n".join(lines)
        if command == "forget":
            if not argument:
                return "Usage: /forget <memory key>. Use /memory to see saved keys."
            deleted = db.delete_memory(argument)
            return f"Memory '{argument}' deleted." if deleted else f"No memory found with key '{argument}'."
        if command == "evidence":
            if not argument:
                return "Usage: /evidence <general, de-identified clinical question>"
            text = f"Search current clinical evidence for: {argument}"
        elif command == "drugs":
            if not argument:
                return "Usage: /drugs <drug name>"
            text = f"Search openFDA drug labeling and safety information for: {argument}"
        elif command:
            return f"Unknown command /{command}. Send /help to see available commands."

        logger.info(
            "Processing Telegram message (user_id=%s, characters=%s)",
            user_id,
            len(text),
        )
        await self.send_chat_action(chat_id, "typing")
        response = await opencode_service.chat(
            session_id=session_id,
            user_message=text,
            channel="telegram",
            user_id=str(user_id),
        )
        reply = str(response.get("reply") or "I couldn't create a response. Please try again.")
        logger.info("Telegram assistant response generated (user_id=%s)", user_id)
        return reply

    async def handle_update(self, update: Dict[str, Any]) -> Dict[str, Any]:
        update_id = update.get("update_id")
        try:
            if isinstance(update_id, int) and not db.claim_telegram_update(update_id):
                return {"status": "duplicate", "update_id": update_id}

            message = update.get("message") or update.get("edited_message")
            if not message:
                return {"status": "ignored", "reason": "no_message"}

            chat = message.get("chat") or {}
            from_user = message.get("from") or {}
            chat_id = chat.get("id")
            raw_user_id = from_user.get("id")
            text = (message.get("text") or "").strip()

            if not isinstance(chat_id, int) or not isinstance(raw_user_id, int) or not text:
                return {"status": "ignored", "reason": "missing_private_text_message_fields"}

            # Do not handle group messages: personal conversation history and data must stay private.
            chat_type = chat.get("type")
            if chat_type not in (None, "private"):
                return {"status": "ignored", "reason": "private_chats_only"}

            if not self.is_user_allowed(raw_user_id):
                # Reject silently to avoid confirming bot access or encouraging unsolicited traffic.
                return {"status": "unauthorized"}

            session_id = f"tg_{raw_user_id}"
            reply = await self._process_text(text, session_id, raw_user_id, chat_id)

            if self.is_configured:
                sent = await self.send_message(chat_id, reply)
                if not sent.get("ok"):
                    logger.warning("Telegram reply was not delivered (user_id=%s)", raw_user_id)

            return {
                "status": "processed",
                "session_id": session_id,
                "chat_id": chat_id,
                "reply": reply,
            }
        except Exception:
            logger.exception("Error handling Telegram update (update_id=%s)", update_id)
            return {
                "status": "error",
                "reply": "Sorry, I couldn't process that message. Please try again.",
            }

    async def start_polling_if_enabled(self) -> None:
        if not (self.is_configured and settings.telegram_polling):
            logger.info("Telegram polling is disabled or not configured")
            return
        if not settings.telegram_allow_all_users and not settings.telegram_allowed_user_ids:
            logger.warning(
                "Telegram polling is enabled but no allowed user IDs are configured; "
                "all users will be rejected. Set TELEGRAM_ALLOWED_USER_IDS."
            )
        if self._polling_task and not self._polling_task.done():
            return
        logger.info("Starting Telegram long polling")
        self._polling_task = asyncio.create_task(self._poll_loop())

    async def stop_polling(self) -> None:
        if self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                logger.info("Telegram polling task stopped")

    async def _poll_loop(self) -> None:
        """Long-poll updates with bounded retry backoff; never log the token-bearing URL."""
        backoff_seconds = 1
        try:
            # getUpdates is incompatible with an active webhook; polling mode owns this bot.
            await self._call_telegram_api(
                "deleteWebhook", {"drop_pending_updates": False}
            )
            async with httpx.AsyncClient(timeout=httpx.Timeout(35.0, connect=10.0)) as client:
                while True:
                    try:
                        response = await client.get(
                            self._api_url("getUpdates"),
                            params={
                                "offset": self._last_update_id + 1,
                                "timeout": 25,
                                "allowed_updates": '["message","edited_message"]',
                            },
                        )
                        response.raise_for_status()
                        data = response.json()
                        if not data.get("ok"):
                            logger.warning("Telegram getUpdates returned ok=false")
                            await asyncio.sleep(backoff_seconds)
                            backoff_seconds = min(backoff_seconds * 2, 30)
                            continue

                        backoff_seconds = 1
                        for item in data.get("result", []):
                            result = await self.handle_update(item)
                            item_id = item.get("update_id")
                            if isinstance(item_id, int):
                                self._last_update_id = max(self._last_update_id, item_id)
                            if result.get("status") == "error":
                                logger.warning("A Telegram update could not be processed")
                    except asyncio.CancelledError:
                        raise
                    except httpx.HTTPStatusError as exc:
                        logger.warning(
                            "Telegram polling returned HTTP %s", exc.response.status_code
                        )
                        await asyncio.sleep(backoff_seconds)
                        backoff_seconds = min(backoff_seconds * 2, 30)
                    except (httpx.HTTPError, ValueError) as exc:
                        logger.warning(
                            "Telegram polling request failed (%s)", type(exc).__name__
                        )
                        await asyncio.sleep(backoff_seconds)
                        backoff_seconds = min(backoff_seconds * 2, 30)
                    except Exception as exc:
                        logger.warning(
                            "Unexpected Telegram polling error (%s)", type(exc).__name__
                        )
                        await asyncio.sleep(backoff_seconds)
                        backoff_seconds = min(backoff_seconds * 2, 30)
        except asyncio.CancelledError:
            logger.info("Telegram polling cancelled")
            raise


telegram_service = TelegramService()
