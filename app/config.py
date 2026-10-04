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

    # Database & Server
    sqlite_db_path: str = field(
        default_factory=lambda: os.getenv("SQLITE_DB_PATH", "data/agent.db").strip()
    )
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0").strip())
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").strip().upper()
    )

    @property
    def uses_anonymous_opencode_key(self) -> bool:
        """True when no real OpenCode Zen key is configured.

        OpenCode Zen closed anonymous free-tier access to third-party clients on
        2026-09-23, so the placeholder key ``public`` (and an empty key) can no
        longer complete chat requests from this app.
        """
        return self.opencode_api_key in ("", "public")


settings = Settings()
