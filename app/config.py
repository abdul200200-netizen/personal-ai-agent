import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

load_dotenv()


def _parse_int_list(raw: str) -> List[int]:
    if not raw or not raw.strip():
        return []
    result = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
            result.append(int(part))
    return result


@dataclass
class Settings:
    # OpenCode (sole AI provider)
    opencode_mode: str = field(
        default_factory=lambda: os.getenv("OPENCODE_MODE", "api").strip().lower()
    )
    opencode_base_url: str = field(
        default_factory=lambda: os.getenv(
            "OPENCODE_BASE_URL", "https://opencode.ai/zen/v1"
        ).rstrip("/")
    )
    opencode_api_key: str = field(
        default_factory=lambda: os.getenv("OPENCODE_API_KEY", "public").strip()
    )
    opencode_model: str = field(
        default_factory=lambda: os.getenv("OPENCODE_MODEL", "big-pickle").strip()
    )
    opencode_cli_path: str = field(
        default_factory=lambda: os.getenv("OPENCODE_CLI_PATH", "opencode").strip()
    )
    opencode_timeout: float = field(
        default_factory=lambda: float(os.getenv("OPENCODE_TIMEOUT", "60"))
    )

    # Telegram integration
    telegram_bot_token: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    )
    telegram_webhook_secret: str = field(
        default_factory=lambda: os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    )
    telegram_allowed_user_ids: List[int] = field(
        default_factory=lambda: _parse_int_list(
            os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
        )
    )
    # A personal assistant should be private by default. Set this to true only for
    # an intentionally public bot; otherwise at least one allowed user ID is required.
    telegram_allow_all_users: bool = field(
        default_factory=lambda: os.getenv("TELEGRAM_ALLOW_ALL_USERS", "false").strip().lower()
        in ("1", "true", "yes")
    )
    telegram_polling: bool = field(
        default_factory=lambda: os.getenv("TELEGRAM_POLLING", "false").lower()
        in ("1", "true", "yes")
    )

    # Google Calendar & Sheets integration
    google_service_account_file: str = field(
        default_factory=lambda: os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    )
    google_service_account_json: str = field(
        default_factory=lambda: os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    )
    google_calendar_id: str = field(
        default_factory=lambda: os.getenv("GOOGLE_CALENDAR_ID", "primary").strip()
    )
    google_spreadsheet_id: str = field(
        default_factory=lambda: os.getenv("GOOGLE_SPREADSHEET_ID", "").strip()
    )
    google_default_sheet_range: str = field(
        default_factory=lambda: os.getenv(
            "GOOGLE_DEFAULT_SHEET_RANGE", "Sheet1!A:E"
        ).strip()
    )

    # Workspace time zone used by proactive briefs (IANA TZ database name)
    user_timezone: str = field(
        default_factory=lambda: os.getenv("USER_TIMEZONE", "Asia/Riyadh").strip() or "Asia/Riyadh"
    )

    # Database & Server
    sqlite_db_path: str = field(
        default_factory=lambda: os.getenv("SQLITE_DB_PATH", "data/agent.db").strip()
    )
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0").strip())
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))

    # Clinical Evidence APIs (optional — all work without keys at lower rate limits)
    ncbi_api_key: str = field(
        default_factory=lambda: os.getenv("NCBI_API_KEY", "").strip()
    )
    openfda_api_key: str = field(
        default_factory=lambda: os.getenv("OPENFDA_API_KEY", "").strip()
    )
    umls_api_key: str = field(
        default_factory=lambda: os.getenv("UMLS_API_KEY", "").strip()
    )
    clinical_evidence_cache_ttl: int = field(
        default_factory=lambda: int(os.getenv("CLINICAL_EVIDENCE_CACHE_TTL", "86400"))
    )
    retrieval_default_window_years: int = field(
        default_factory=lambda: int(os.getenv("RETRIEVAL_DEFAULT_WINDOW_YEARS", "5"))
    )


settings = Settings()
