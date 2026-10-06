# personal-ai-agent

Deployable Personal AI Agent with **Telegram integration**, **FastAPI**, **SQLite**, **Google Calendar / Google Sheets**, **Clinical Evidence Search**, and **OpenCode** as the sole AI provider.

---

## Features

- **OpenCode AI Engine Only (`app/services/opencode.py`)**
  - Exclusively uses **OpenCode** (no external multi-model LLM fallback routers).
  - Supports **OpenCode Zen / HTTP API mode** (`https://opencode.ai/zen/v1` or local `opencode serve`) and a text-only **OpenCode CLI fallback** (`opencode run`).
  - API mode provides tool calling for calendar, spreadsheets, tasks, safe persistent memories, and live clinical-evidence/drug searches; use `OPENCODE_MODE=api` for the full agent.
- **Clinical Evidence Search (`app/tools/clinical_evidence_search.py`)**
  - **Live search** of PubMed, Europe PMC, ClinicalTrials.gov, and openFDA
  - **Proper citations** with PMID/PMCID/NCT identifiers, dates, and links
  - **PHI protection** — automatic detection and blocking of patient-identifiable information
  - **Evidence labeling** — peer-reviewed, registry data, regulatory data clearly distinguished
  - **PICO framing** support for structured clinical questions
  - All sources work **without API keys** (optional keys raise rate limits)
- **Telegram Integration (`app/services/telegram.py`)**
  - Supports **Webhook** (`POST /webhook/telegram`) and background **Long Polling** (`TELEGRAM_POLLING=true`).
  - Private by default: only IDs in `TELEGRAM_ALLOWED_USER_IDS` are accepted; group chats are ignored. An empty allowlist denies everyone.
  - Built-in commands include `/start`, `/help`, `/status`, `/tasks`, `/calendar`, `/memory`, `/forget`, `/proposals`, `/approve`, `/reject`, `/think`, `/brief`, `/evidence`, `/drugs`, and `/clear`.
  - Daily, weekly, and monthly Telegram reflections are **opt-in**; timezone and daily times are configurable per user.
  - Long replies are split safely, webhook updates are deduplicated, and message content is not written to application logs.
- **Google Calendar & Google Sheets (`app/services/google_workspace.py`)**
  - List and create Google Calendar events (`list_calendar_events`, `create_calendar_event`).
  - Read and append Google Sheets rows (`read_sheet_rows`, `append_sheet_row`).
  - Automatic local SQLite fallback when Google Service Account credentials are not configured yet.
- **SQLite Persistence and Human-Reviewed Learning (`app/database.py`)**
  - Stores multi-session conversation history, personal tasks, preferences, and persistent user memories.
  - The agent can propose memory or skill improvements; users review them with `/proposals`, `/approve`, and `/reject`. Skill files are never edited by the bot.
- **Thinking Mode and Cadence (`app/services/scheduler.py`)**
  - `/think on|off` enables constructive Socratic challenge for strategic/design questions without exposing private chain-of-thought.
  - `/brief on` opts in to daily intent/ledger prompts, weekly calibration, and month-end review; `/brief off` pauses them.
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

# Telegram Bot (optional but recommended)
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_ALLOWED_USER_IDS=your_numeric_telegram_user_id
TELEGRAM_ALLOW_ALL_USERS=false
TELEGRAM_POLLING=true
# TELEGRAM_CHAT_ID is not needed for polling; the bot replies to the incoming private chat.
# In webhook mode, also set a strong TELEGRAM_WEBHOOK_SECRET.

# Timezone for optional proactive briefs (IANA timezone)
USER_TIMEZONE=Asia/Riyadh

# Clinical Evidence APIs (optional — all work without keys at lower rate limits)
NCBI_API_KEY=          # PubMed: 3 req/s → 10 req/s
OPENFDA_API_KEY=       # openFDA: 120/min → 240/min
UMLS_API_KEY=          # UMLS terminology (optional)
```

**Clinical Evidence APIs**: All clinical sources (PubMed, Europe PMC, ClinicalTrials.gov, openFDA) work without API keys. Optional keys only raise rate limits:
- **NCBI_API_KEY**: Get at https://www.ncbi.nlm.nih.gov/account/settings/
- **OPENFDA_API_KEY**: Get at https://open.fda.gov/apis/authorization/
- **UMLS_API_KEY**: Get at https://uts.nlm.nih.gov/uts/

### 🚀 Deploy to Railway (Recommended)

See the full deployment guide: [docs/DEPLOYMENT_TELEGRAM_RAILWAY.md](docs/DEPLOYMENT_TELEGRAM_RAILWAY.md)

Quick steps:
1. Create a Telegram Bot via @BotFather
2. Create Railway account and connect GitHub repo
3. Add environment variables (see guide)
4. Deploy - Railway auto-builds from Dockerfile
5. Bot is live! Test with `/start` in Telegram

For an agent with tool use (tasks, calendar, memory, and evidence search), keep `OPENCODE_MODE=api`. The Telegram polling setup does not require a separate chat ID: the bot answers the private chat that sent the message. Restrict access with your numeric Telegram **user ID**, not a bot token or group chat ID. Allowlisted IDs share the same tasks, memories, and calendar, so keep your own ID as the only entry unless shared access is intentional.

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
| `GET` / `POST` | `/api/memories` | List or save persistent agent memories (PHI/credentials rejected) |
| `DELETE` | `/api/memories/{key}` | Delete a saved memory |
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

The agent uses a **Hermes-inspired identity and skills layer** (not the separate Hermes runtime):

- **SOUL.md**: Loaded into the assistant's system prompt for identity, behavior rules, and PHI boundaries
- **USER.md**: User-editable profile and preferences, loaded as context
- **MEMORY.md**: Durable, non-sensitive workspace memory, loaded as context
- **`skills/*/SKILL.md`**: Relevant workflows are selected per request (personal assistant by default; clinical evidence when detected), rather than loading every skill every time
- **Thinking Mode**: Per-user, off by default; turn on with `/think on` for respectful assumption-testing and decision support, or use `GET /api/preferences/{user_id}` and `PUT /api/preferences/thinking-mode` from an API client
- **Learning proposals**: Memory changes require explicit approval. Skill proposals are review-only and require a maintainer code change; the bot never edits skill files.
- **SQLite memories**: Reviewable with `/memory` and deletable with `/forget <key>`
- **Temporal cadence**: `/brief on` opts in to 06:00 morning intent, 21:00 evening ledger, Thursday 20:00 calibration, and last-day-of-month 20:00 audit in the selected timezone (default `Asia/Riyadh`).

The documents are re-read for new requests, so they can be edited without changing Python code; production changes still require a redeploy. Keep secrets and patient-identifiable information out of these files: workspace context and saved memories are included in prompts sent to the configured OpenCode provider. Live clinical tools independently block queries flagged as patient-identifiable. Scheduled messages are opt-in and omit task/calendar items flagged by the PHI heuristic.
