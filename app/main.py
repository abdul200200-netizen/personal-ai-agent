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
from app.tools.clinical_evidence_search import search_clinical_evidence, search_drugs


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    await telegram_service.start_polling_if_enabled()
    try:
        yield
    finally:
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


class ClinicalEvidenceSearchRequest(BaseModel):
    question: str
    population: Optional[str] = None
    intervention: Optional[str] = None
    comparator: Optional[str] = None
    outcomes: Optional[List[str]] = None
    jurisdiction: Optional[str] = None
    sources: Optional[List[str]] = None  # pubmed, europe_pmc, clinicaltrials, openfda
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    study_types: Optional[List[str]] = None
    max_results_per_source: int = 5


class DrugSearchRequest(BaseModel):
    drug_name: str
    include_events: bool = False
    max_results: int = 5


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


# Clinical Evidence Search
@app.post("/api/evidence/search")
async def search_evidence(payload: ClinicalEvidenceSearchRequest):
    """Search clinical evidence sources (PubMed, Europe PMC, ClinicalTrials.gov, openFDA)."""
    result = await search_clinical_evidence(
        question=payload.question,
        population=payload.population,
        intervention=payload.intervention,
        comparator=payload.comparator,
        outcomes=payload.outcomes,
        jurisdiction=payload.jurisdiction,
        sources=payload.sources,
        date_from=payload.date_from,
        date_to=payload.date_to,
        study_types=payload.study_types,
        max_results_per_source=payload.max_results_per_source,
    )
    return result


@app.post("/api/evidence/drugs")
async def search_drug_info(payload: DrugSearchRequest):
    """Search openFDA for drug labeling and (optionally) adverse events."""
    result = await search_drugs(
        drug_name=payload.drug_name,
        include_events=payload.include_events,
        max_results=payload.max_results,
    )
    return result


@app.get("/api/evidence/sources")
async def list_evidence_sources():
    """List available clinical evidence sources and their configuration status."""
    return {
        "sources": {
            "pubmed": {
                "name": "PubMed E-utilities",
                "type": "peer_reviewed",
                "configured": True,
                "api_key": bool(settings.ncbi_api_key),
                "rate_limit": "10 req/s with key, 3 req/s without",
                "description": "Peer-reviewed biomedical literature",
            },
            "europe_pmc": {
                "name": "Europe PMC",
                "type": "peer_reviewed",
                "configured": True,
                "api_key": False,
                "rate_limit": "5 req/s (conservative)",
                "description": "Complementary literature + open-access full text",
            },
            "clinicaltrials": {
                "name": "ClinicalTrials.gov API v2",
                "type": "registry_data",
                "configured": True,
                "api_key": False,
                "rate_limit": "5 req/s (conservative)",
                "description": "Trial status and results (registry data, NOT peer-reviewed)",
            },
            "openfda": {
                "name": "openFDA",
                "type": "regulatory_data",
                "configured": True,
                "api_key": bool(settings.openfda_api_key),
                "rate_limit": "240/min with key, 120/min without",
                "description": "Drug labeling, adverse events, recalls (regulatory data)",
            },
        },
        "defaults": {
            "lookback_years": settings.retrieval_default_window_years,
            "cache_ttl_seconds": settings.clinical_evidence_cache_ttl,
        },
    }


# Telegram Webhook
@app.post("/webhook/telegram")
async def telegram_webhook(
    update: Dict[str, Any],
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
):
    if settings.telegram_webhook_secret:
        if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
            raise HTTPException(status_code=403, detail="Invalid webhook secret token")
    return await telegram_service.handle_update(update)
