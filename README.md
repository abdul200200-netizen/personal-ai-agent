# personal-ai-agent

Deployable Personal AI Agent with **Telegram integration**, **FastAPI**, **SQLite**, **Google Calendar / Google Sheets**, **Clinical Evidence Search**, and **OpenCode** as the sole AI provider.

---

## Features

- **OpenCode AI Engine Only (`app/services/opencode.py`)**
  - Exclusively uses **OpenCode** (no external multi-model LLM fallback routers).
  - Supports **OpenCode Zen / HTTP API mode** (`https://opencode.ai/zen/v1` or local `opencode serve`) and **OpenCode CLI mode** (`opencode run`).
  - Built-in function/tool calling for managing calendar events, spreadsheets, tasks, and persistent user memories.
- **Clinical Evidence Search (`app/tools/clinical_evidence_search.py`)**
  - **Live search** of PubMed, Europe PMC, ClinicalTrials.gov, and openFDA
  - **Proper citations** with PMID/PMCID/NCT identifiers, dates, and links
  - **PHI protection** — automatic detection and blocking of patient-identifiable information
  - **Evidence labeling** — peer-reviewed, registry data, regulatory data clearly distinguished
  - **PICO framing** support for structured clinical questions
  - All sources work **without API keys** (optional keys raise rate limits)
- **Telegram Integration (`app/services/telegram.py`)**
  - Supports both **Webhook** (`POST /webhook/telegram`) and background **Long Polling** (`TELEGRAM_POLLING=true`).
  - Built-in slash commands (`/start`, `/help`, `/status`, `/tasks`, `/calendar`, `/clear`) and natural-language chat via OpenCode.
- **Google Calendar & Google Sheets (`app/services/google_workspace.py`)**
  - List and create Google Calendar events (`list_calendar_events`, `create_calendar_event`).
  - Read and append Google Sheets rows (`read_sheet_rows`, `append_sheet_row`).
  - Automatic local SQLite fallback when Google Service Account credentials are not configured yet.
- **SQLite Persistence (`app/database.py`)**
  - Stores multi-session conversation history, personal tasks, and persistent user memories.
- **FastAPI + Web Dashboard (`app/main.py`, `app/static/index.html`)**
  - Interactive browser UI at `/` and REST API endpoints for chat, status, tasks, memories, calendar, sheets, and clinical evidence.
- **Hermes-Style Identity (`workspace/`)**
  - SOUL.md: Agent identity, behavior rules, safety guardrails
  - USER.md: User profile template (preferences, professional context)
  - MEMORY.md: Durable non-sensitive preferences

---

## Quick Start

### 1. Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` to set your OpenCode, Telegram, Google, and clinical API credentials:

```env
# OpenCode (required)
OPENCODE_MODE=api
OPENCODE_BASE_URL=https://opencode.ai/zen/v1
OPENCODE_API_KEY=public
OPENCODE_MODEL=big-pickle

# Clinical Evidence APIs (optional — all work without keys at lower rate limits)
NCBI_API_KEY=          # PubMed: 3 req/s → 10 req/s
OPENFDA_API_KEY=       # openFDA: 120/min → 240/min
UMLS_API_KEY=          # UMLS terminology (optional)
```

**Clinical Evidence APIs**: All clinical sources (PubMed, Europe PMC, ClinicalTrials.gov, openFDA) work without API keys. Optional keys only raise rate limits:
- **NCBI_API_KEY**: Get at https://www.ncbi.nlm.nih.gov/account/settings/
- **OPENFDA_API_KEY**: Get at https://open.fda.gov/apis/authorization/
- **UMLS_API_KEY**: Get at https://uts.nlm.nih.gov/uts/

### 2. Install & Run Locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` to use the Web Dashboard or inspect `http://localhost:8000/docs` for the OpenAPI specification.

### 3. Run with Docker

```bash
docker compose up --build -d
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check & active OpenCode model |
| `GET` | `/api/status` | Full status of OpenCode, Telegram, Google Workspace, and SQLite |
| `GET` | `/api/models` | List available models from OpenCode |
| `POST` | `/api/chat` | Send a message to the OpenCode agent |
| `GET` | `/api/conversations` | List conversation sessions |
| `GET` | `/api/conversations/{session_id}/messages` | Get messages in a session |
| `DELETE` | `/api/conversations/{session_id}` | Clear a conversation session |
| `GET` / `POST` | `/api/tasks` | List or create personal tasks |
| `POST` | `/api/tasks/{task_id}/complete` | Complete a task |
| `GET` / `POST` | `/api/memories` | List or save persistent agent memories |
| `GET` / `POST` | `/api/calendar/events` | List or create Google Calendar events |
| `GET` / `POST` | `/api/sheets/rows` | Read or append Google Sheets rows |
| `POST` | `/webhook/telegram` | Telegram Bot webhook endpoint |
| `POST` | `/api/evidence/search` | **Search clinical evidence** (PubMed, Europe PMC, ClinicalTrials.gov, openFDA) |
| `POST` | `/api/evidence/drugs` | **Search drug information** (openFDA labeling + adverse events) |
| `GET` | `/api/evidence/sources` | List available clinical evidence sources and configuration |

---

## Clinical Evidence Search

The agent provides **live clinical evidence retrieval** from authoritative sources with proper citations and safety guardrails.

### Example: Search for Recent Evidence

```bash
curl -X POST http://localhost:8000/api/evidence/search \
  -H "Content-Type: application/json" \
  -d '{
    "question": "SGLT2 inhibitors for heart failure",
    "population": "adults with HFrEF",
    "intervention": "empagliflozin",
    "sources": ["pubmed", "europe_pmc"],
    "max_results_per_source": 5
  }'
```

**Response** includes:
- Search metadata (date, sources queried, limitations)
- Results with **full citations**: source | PMID/PMCID/NCT | publication date | retrieval date | URL
- Evidence type labels (peer-reviewed, registry data, regulatory data)
- PHI protection warnings (if patient identifiers detected)

### Example: Drug Information

```bash
curl -X POST http://localhost:8000/api/evidence/drugs \
  -H "Content-Type: application/json" \
  -d '{
    "drug_name": "metformin",
    "include_events": true,
    "max_results": 3
  }'
```

### Safety Guardrails

- **No diagnosis or prescribing**: The agent retrieves evidence but does NOT make clinical decisions
- **PHI protection**: Automatic detection and blocking of patient-identifiable information
- **Citation enforcement**: Every claim must cite source, identifier, date, and link
- **Evidence labeling**: Clear distinction between peer-reviewed literature, registry data, and regulatory data
- **Uncertainty disclosure**: Agent states limitations, conflicts, and gaps in evidence

See `clinical/evidence-policy.md` for full policy details.

---

## Hermes-Style Identity

The agent uses a **Hermes-inspired identity system** (see `workspace/`):

- **SOUL.md**: Defines agent identity, behavior rules, safety guardrails, and PHI boundaries
- **USER.md**: User-editable profile (preferences, professional context, clinical defaults)
- **MEMORY.md**: Durable non-sensitive preferences (reviewable and deletable)

This provides a **transparent, auditable personality layer** that separates personal context from clinical evidence retrieval.
