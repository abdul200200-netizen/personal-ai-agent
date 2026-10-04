# personal-ai-agent

Deployable Personal AI Agent with **Telegram integration**, **FastAPI**, **SQLite**, **Google Calendar / Google Sheets**, and **OpenCode** as the sole AI provider.

---

## Features

- **OpenCode AI Engine Only (`app/services/opencode.py`)**
  - Exclusively uses **OpenCode** (no external multi-model LLM fallback routers).
  - Supports **OpenCode Zen / HTTP API mode** (`https://opencode.ai/zen/v1` or local `opencode serve`) and **OpenCode CLI mode** (`opencode run`).
  - API mode requires a real Zen key (`public` stopped working on 2026-09-23 — see [Troubleshooting](#troubleshooting-the-bot-stopped-replying)); upstream failures are classified (401/402/403/404/429/5xx/network) and returned as actionable messages instead of silence.
  - Built-in function/tool calling for managing calendar events, spreadsheets, tasks, and persistent user memories.
- **Telegram Integration (`app/services/telegram.py`)**
  - Supports both **Webhook** (`POST /webhook/telegram`) and background **Long Polling** (`TELEGRAM_POLLING=true`).
  - Built-in slash commands (`/start`, `/help`, `/status`, `/ai_status`, `/tasks`, `/calendar`, `/clear`) and natural-language chat via OpenCode.
  - Never goes silently dead: polling logs every failure, auto-clears a conflicting webhook (`409 Conflict`), backs off on errors, and one bad update can't kill the loop.
- **Self-diagnostics (`GET /api/diagnostics`)**
  - Live end-to-end check of the OpenCode endpoint and Telegram delivery with a suggested fix, surfaced in the dashboard via **Run diagnostics**.
  - Warnings/errors are ring-buffered in memory and returned in `recent_errors`.
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
OPENCODE_API_KEY=sk-your-real-key-here   # from https://opencode.ai/auth
OPENCODE_MODEL=big-pickle
```

> ⚠️ **`OPENCODE_API_KEY` must be a real key.** On **2026-09-23 OpenCode Zen closed
> anonymous free-tier access to third-party apps**, so the old placeholder value
> `public` no longer works — the bot then receives your messages but answers with an
> error (HTTP 401/403 `FreeTierError`), or gets rate-limited with HTTP 429. Create a
> key at <https://opencode.ai/auth>, or run the official CLI instead with
> `OPENCODE_MODE=cli` (see below). This is the single most common reason the bot
> "stops working".

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

## Troubleshooting: "the bot stopped replying"

Run the built-in self-check first — it reports the **exact** upstream error and how to fix it:

```bash
curl -s localhost:8000/api/diagnostics | python -m json.tool
```

Or from Telegram: send `/ai_status`. Or open the web dashboard (<http://localhost:8000>) and click **Run diagnostics**.

At startup the app also logs a self-check to the console, so `docker compose logs -f` shows the problem immediately instead of failing silently.

| Symptom in diagnostics / logs | Cause | Fix |
|---|---|---|
| `403 FreeTierError: OpenCode's free tier can only be used from within OpenCode` | `OPENCODE_API_KEY` is unset or still the placeholder `public` (Zen changed this on **2026-09-23**) | Set a real key from <https://opencode.ai/auth>, or switch to `OPENCODE_MODE=cli` |
| `401 unauthorized` / `429 rate_limited` | Expired, revoked or anonymous key | Set a real `OPENCODE_API_KEY`, then restart |
| `402 payment_required` | No credits left on the Zen account | Top up, or set `OPENCODE_MODEL` to a free model id |
| `404 model_not_found` | Model id was retired (free ids rotate) | Check `GET /api/models` and update `OPENCODE_MODEL` |
| `network_error` / `ConnectError` | Container has no outbound access to `opencode.ai` (DNS, firewall, proxy) | Fix egress; note that a sandbox blocking TLS looks identical to an outage |
| Telegram bot is silent, diagnostics show `getUpdates failed: Conflict` | A webhook is registered **or** a second instance is polling the same token | Only run one instance. Polling auto-clears the webhook on startup; otherwise call `https://api.telegram.org/bot<token>/deleteWebhook` |
| Telegram bot is silent, no webhook and polling is off | Messages have nowhere to be delivered | Set `TELEGRAM_POLLING=true`, or register `POST /webhook/telegram` via `setWebhook` |
| Telegram replies `Unauthorized Telegram user ID.` | Your user id isn't in the allow-list | Add it to `TELEGRAM_ALLOWED_USER_IDS` (empty = allow everyone) |

Warnings and errors are also kept in memory and returned in the `recent_errors` field of `/api/diagnostics`, so you can diagnose without shell access to the container.

### Using the official OpenCode CLI (free tier alternative)

The Zen free tier still works when requests come from the official OpenCode client, so `cli` mode is the key-free option:

```bash
curl -fsSL https://opencode.ai/install | bash   # install the CLI on the host/container
```

```env
OPENCODE_MODE=cli
OPENCODE_CLI_PATH=opencode
OPENCODE_MODEL=opencode/big-pickle   # cli mode expects the opencode/<model-id> form
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check & active OpenCode model |
| `GET` | `/api/diagnostics` | **Live self-check** of OpenCode + Telegram, with the exact error and suggested fix, plus recent warnings/errors |
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
