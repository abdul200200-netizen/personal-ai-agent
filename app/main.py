import asyncio
import collections
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.database import db
from app.services.google_workspace import google_workspace
from app.services.opencode import opencode_service
from app.services.telegram import telegram_service

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("app")


class _RingBufferHandler(logging.Handler):
    """Keeps recent WARNING+ logs in memory so /api/diagnostics can expose them."""

    def __init__(self, capacity: int = 200):
        super().__init__(level=logging.WARNING)
        self.buffer = collections.deque(maxlen=capacity)
        # NOTE: do not replace self.lock. logging.Handler.handle() already holds
        # it (an RLock) while calling emit(), so swapping in a plain Lock here
        # would deadlock the process on the first warning log.

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # The inherited RLock is reentrant, so re-acquiring it is safe.
            with self.lock:
                self.buffer.append(self.format(record))
        except Exception:  # pragma: no cover - never break logging
            pass


_LOG_RING_HANDLER = _RingBufferHandler()
_LOG_RING_HANDLER.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
)
logging.getLogger().addHandler(_LOG_RING_HANDLER)


async def _startup_self_check() -> None:
    """Log immediately whether the agent can talk to OpenCode.

    Runs in the background so a slow or unreachable network never blocks startup.
    """
    for issue in opencode_service.configuration_issues():
        logger.warning("OpenCode configuration: %s", issue)

    report = await opencode_service.diagnostics()
    if report.get("ok"):
        logger.info(
            "OpenCode self-check passed (mode=%s, model=%s).",
            report.get("mode"),
            report.get("model"),
        )
        return

    logger.error(
        "OpenCode self-check FAILED. The bot will receive messages but cannot "
        "generate replies until this is fixed."
    )
    for check in report.get("checks", []):
        if not check.get("ok"):
            logger.error("  - %s: %s", check.get("name"), check.get("detail"))
    if report.get("suggested_fix"):
        logger.error("  How to fix: %s", report["suggested_fix"])


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    logger.info(
        "Starting Personal AI Agent | opencode mode=%s model=%s telegram_polling=%s",
        settings.opencode_mode,
        settings.opencode_model,
        settings.telegram_polling,
    )
    if settings.uses_anonymous_opencode_key:
        logger.warning(
            "OPENCODE_API_KEY is not a real key ('%s'). OpenCode Zen's anonymous "
            "free tier was closed to third-party apps on 2026-09-23, so chat "
            "requests will fail until you set a real key from "
            "https://opencode.ai/auth (or use OPENCODE_MODE=cli).",
            settings.opencode_api_key or "<empty>",
        )
    self_check_task = asyncio.create_task(_startup_self_check())
    await telegram_service.start_polling_if_enabled()
    try:
        yield
    finally:
        self_check_task.cancel()
        await telegram_service.stop_polling()


app = FastAPI(
    title="Personal AI Agent (OpenCode)",
    description="Deployable AI agent with Telegram integration, FastAPI, SQLite, Google Calendar/Sheets, powered exclusively by OpenCode.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_INDEX = Path(__file__).parent / "static" / "index.html"


# ---------------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    session_id: str = Field(default="web_default")
    message: str
    model: Optional[str] = None
    user_id: str = Field(default="default")


class TaskCreateRequest(BaseModel):
    title: str
    due_date: Optional[str] = None


class MemorySaveRequest(BaseModel):
    key: str
    value: str


class CalendarEventCreateRequest(BaseModel):
    summary: str
    start_time: str
    end_time: str
    description: str = ""
    location: str = ""


class SheetAppendRequest(BaseModel):
    values: List[Any]
    spreadsheet_id: Optional[str] = None
    range_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
async def root_dashboard():
    if STATIC_INDEX.exists():
        return FileResponse(STATIC_INDEX)
    return JSONResponse({"status": "ok", "provider": "opencode"})


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "provider": "opencode",
        "model": settings.opencode_model,
    }


@app.get("/api/status")
async def api_status():
    return {
        "status": "online",
        "opencode": {
            "provider": "opencode",
            "mode": settings.opencode_mode,
            "base_url": settings.opencode_base_url,
            "model": settings.opencode_model,
            "api_key_is_placeholder": settings.uses_anonymous_opencode_key,
            "config_issues": opencode_service.configuration_issues(),
        },
        "telegram": {
            "configured": telegram_service.is_configured,
            "polling": settings.telegram_polling,
        },
        "google_workspace": {
            "live_configured": google_workspace.is_live_configured,
            "calendar_id": settings.google_calendar_id,
            "spreadsheet_id_set": bool(settings.google_spreadsheet_id),
        },
        "database": {
            "sqlite_path": db.db_path,
        },
    }


@app.get("/api/models")
async def list_opencode_models():
    return await opencode_service.list_models()


def _recent_errors(limit: int = 25) -> List[str]:
    """Return the most recent WARNING+ log lines captured by the log handler."""
    with _LOG_RING_HANDLER.lock:
        return list(_LOG_RING_HANDLER.buffer)[-limit:]


@app.get("/api/diagnostics")
async def diagnostics():
    """Live end-to-end check of OpenCode + Telegram.

    Use this first whenever the bot appears to have stopped: it reports the
    exact upstream error and how to fix it.
    """
    opencode_report, telegram_report = await asyncio.gather(
        opencode_service.diagnostics(),
        telegram_service.diagnostics(),
    )
    return {
        "ok": bool(opencode_report.get("ok") and telegram_report.get("ok")),
        "opencode": opencode_report,
        "telegram": telegram_report,
        "recent_errors": _recent_errors(),
    }


@app.post("/api/chat")
async def chat_endpoint(payload: ChatRequest):
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    return await opencode_service.chat(
        session_id=payload.session_id,
        user_message=payload.message.strip(),
        channel="web",
        user_id=payload.user_id,
        model_override=payload.model,
    )


@app.get("/api/conversations")
async def list_conversations():
    return {"conversations": db.list_conversations()}


@app.get("/api/conversations/{session_id}/messages")
async def get_conversation_messages(session_id: str, limit: int = 50):
    return {
        "session_id": session_id,
        "messages": db.get_messages(session_id, limit=limit),
    }


@app.delete("/api/conversations/{session_id}")
async def delete_conversation(session_id: str):
    db.clear_conversation(session_id)
    return {"status": "cleared", "session_id": session_id}


# Tasks
@app.get("/api/tasks")
async def get_tasks(status: Optional[str] = None):
    return {"tasks": db.list_tasks(status=status)}


@app.post("/api/tasks")
async def create_task(payload: TaskCreateRequest):
    task = db.add_task(title=payload.title, due_date=payload.due_date)
    return {"task": task}


@app.post("/api/tasks/{task_id}/complete")
async def complete_task(task_id: int):
    updated = db.update_task_status(task_id, "completed")
    if not updated:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task": updated}


# Memories
@app.get("/api/memories")
async def get_memories():
    return {"memories": db.list_memories()}


@app.post("/api/memories")
async def save_memory(payload: MemorySaveRequest):
    mem = db.save_memory(key=payload.key, value=payload.value)
    return {"memory": mem}


# Google Calendar
@app.get("/api/calendar/events")
async def list_calendar_events(max_results: int = 10):
    return google_workspace.list_calendar_events(max_results=max_results)


@app.post("/api/calendar/events")
async def create_calendar_event(payload: CalendarEventCreateRequest):
    return google_workspace.create_calendar_event(
        summary=payload.summary,
        start_time=payload.start_time,
        end_time=payload.end_time,
        description=payload.description,
        location=payload.location,
    )


# Google Sheets
@app.get("/api/sheets/rows")
async def read_sheet_rows(
    spreadsheet_id: Optional[str] = None, range_name: Optional[str] = None
):
    return google_workspace.read_sheet_rows(
        spreadsheet_id=spreadsheet_id, range_name=range_name
    )


@app.post("/api/sheets/rows")
async def append_sheet_row(payload: SheetAppendRequest):
    return google_workspace.append_sheet_row(
        values=payload.values,
        spreadsheet_id=payload.spreadsheet_id,
        range_name=payload.range_name,
    )


# Telegram Webhook
@app.post("/webhook/telegram")
async def telegram_webhook(
    update: Dict[str, Any],
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
):
    if settings.telegram_webhook_secret:
        if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
            raise HTTPException(status_code=403, detail="Invalid webhook secret token")
    try:
        return await telegram_service.handle_update(update)
    except Exception as exc:
        # Always answer 200 so Telegram does not retry the same update forever.
        logger.exception("Telegram webhook failed to process update")
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
