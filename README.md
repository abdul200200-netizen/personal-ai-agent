# personal-ai-agent

Deployable Personal AI Agent with **Telegram integration**, **FastAPI**, **SQLite**, **Google Calendar / Google Sheets**, and **OpenCode** as the sole AI provider.

---

## Features

- **OpenCode AI Engine Only (`app/services/opencode.py`)**
  - Exclusively uses **OpenCode** (no external multi-model LLM fallback routers).
  - Supports **OpenCode Zen / HTTP API mode** (`https://opencode.ai/zen/v1` or local `opencode serve`) and **OpenCode CLI mode** (`opencode run`).
  - Built-in function/tool calling for managing calendar events, spreadsheets, tasks, and persistent user memories.
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
  - Interactive browser UI at `/` and REST API endpoints for chat, status, tasks, memories, calendar, and sheets.

---

## Quick Start

### 1. Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` to set your OpenCode, Telegram, and Google credentials:

```env
OPENCODE_MODE=api
OPENCODE_BASE_URL=https://opencode.ai/zen/v1
OPENCODE_API_KEY=public
OPENCODE_MODEL=big-pickle
```

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
