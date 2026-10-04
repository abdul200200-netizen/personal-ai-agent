import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.database import db

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/spreadsheets",
]


class GoogleWorkspaceService:
    """Google Calendar & Google Sheets integration with SQLite fallback when unconfigured."""

    def _get_credentials(self):
        try:
            from google.oauth2 import service_account
        except ImportError:
            return None

        if settings.google_service_account_json:
            try:
                info = json.loads(settings.google_service_account_json)
                return service_account.Credentials.from_service_account_info(
                    info, scopes=SCOPES
                )
            except Exception:
                return None

        if settings.google_service_account_file and os.path.exists(
            settings.google_service_account_file
        ):
            try:
                return service_account.Credentials.from_service_account_file(
                    settings.google_service_account_file, scopes=SCOPES
                )
            except Exception:
                return None

        return None

    @property
    def is_live_configured(self) -> bool:
        return self._get_credentials() is not None

    # -------------------------------------------------------------------------
    # Google Calendar
    # -------------------------------------------------------------------------
    def list_calendar_events(
        self, max_results: int = 10, time_min: Optional[str] = None
    ) -> Dict[str, Any]:
        creds = self._get_credentials()
        if creds:
            try:
                from googleapiclient.discovery import build

                service = build("calendar", "v3", credentials=creds, cache_discovery=False)
                now_iso = time_min or datetime.now(timezone.utc).isoformat()
                events_result = (
                    service.events()
                    .list(
                        calendarId=settings.google_calendar_id or "primary",
                        timeMin=now_iso,
                        maxResults=max_results,
                        singleEvents=True,
                        orderBy="startTime",
                    )
                    .execute()
                )
                items = events_result.get("items", [])
                formatted = [
                    {
                        "id": ev.get("id"),
                        "summary": ev.get("summary", "(No title)"),
                        "start_time": ev.get("start", {}).get(
                            "dateTime", ev.get("start", {}).get("date", "")
                        ),
                        "end_time": ev.get("end", {}).get(
                            "dateTime", ev.get("end", {}).get("date", "")
                        ),
                        "description": ev.get("description", ""),
                        "location": ev.get("location", ""),
                        "htmlLink": ev.get("htmlLink", ""),
                    }
                    for ev in items
                ]
                return {"source": "google_calendar", "events": formatted}
            except Exception as exc:
                return {
                    "source": "google_calendar_error",
                    "error": str(exc),
                    "events": self._list_local_events(max_results),
                }

        return {
            "source": "local_sqlite",
            "events": self._list_local_events(max_results),
        }

    def create_calendar_event(
        self,
        summary: str,
        start_time: str,
        end_time: str,
        description: str = "",
        location: str = "",
    ) -> Dict[str, Any]:
        creds = self._get_credentials()
        if creds:
            try:
                from googleapiclient.discovery import build

                service = build("calendar", "v3", credentials=creds, cache_discovery=False)
                event_body = {
                    "summary": summary,
                    "location": location,
                    "description": description,
                    "start": {"dateTime": start_time, "timeZone": "UTC"},
                    "end": {"dateTime": end_time, "timeZone": "UTC"},
                }
                created = (
                    service.events()
                    .insert(
                        calendarId=settings.google_calendar_id or "primary",
                        body=event_body,
                    )
                    .execute()
                )
                return {
                    "source": "google_calendar",
                    "event": {
                        "id": created.get("id"),
                        "summary": created.get("summary"),
                        "start_time": start_time,
                        "end_time": end_time,
                        "description": description,
                        "location": location,
                        "htmlLink": created.get("htmlLink", ""),
                    },
                }
            except Exception as exc:
                local_ev = self._create_local_event(
                    summary, start_time, end_time, description, location
                )
                return {
                    "source": "local_sqlite_fallback",
                    "warning": f"Google Calendar API error ({exc}); stored locally.",
                    "event": local_ev,
                }

        local_ev = self._create_local_event(
            summary, start_time, end_time, description, location
        )
        return {"source": "local_sqlite", "event": local_ev}

    def _list_local_events(self, limit: int = 10) -> List[Dict[str, Any]]:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM local_calendar_events ORDER BY start_time ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def _create_local_event(
        self,
        summary: str,
        start_time: str,
        end_time: str,
        description: str = "",
        location: str = "",
    ) -> Dict[str, Any]:
        ev_id = f"evt_{uuid.uuid4().hex[:10]}"
        now = datetime.now(timezone.utc).isoformat()
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO local_calendar_events (id, summary, start_time, end_time, description, location, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ev_id, summary, start_time, end_time, description, location, now),
            )
        return {
            "id": ev_id,
            "summary": summary,
            "start_time": start_time,
            "end_time": end_time,
            "description": description,
            "location": location,
            "created_at": now,
        }

    # -------------------------------------------------------------------------
    # Google Sheets
    # -------------------------------------------------------------------------
    def read_sheet_rows(
        self, spreadsheet_id: Optional[str] = None, range_name: Optional[str] = None
    ) -> Dict[str, Any]:
        target_sheet = spreadsheet_id or settings.google_spreadsheet_id
        target_range = range_name or settings.google_default_sheet_range
        creds = self._get_credentials()

        if creds and target_sheet:
            try:
                from googleapiclient.discovery import build

                service = build("sheets", "v4", credentials=creds, cache_discovery=False)
                result = (
                    service.spreadsheets()
                    .values()
                    .get(spreadsheetId=target_sheet, range=target_range)
                    .execute()
                )
                rows = result.get("values", [])
                return {
                    "source": "google_sheets",
                    "spreadsheet_id": target_sheet,
                    "range": target_range,
                    "rows": rows,
                }
            except Exception as exc:
                return {
                    "source": "google_sheets_error",
                    "error": str(exc),
                    "range": target_range,
                    "rows": self._read_local_sheet_rows(target_range),
                }

        return {
            "source": "local_sqlite",
            "range": target_range,
            "rows": self._read_local_sheet_rows(target_range),
        }

    def append_sheet_row(
        self,
        values: List[Any],
        spreadsheet_id: Optional[str] = None,
        range_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_sheet = spreadsheet_id or settings.google_spreadsheet_id
        target_range = range_name or settings.google_default_sheet_range
        creds = self._get_credentials()

        if creds and target_sheet:
            try:
                from googleapiclient.discovery import build

                service = build("sheets", "v4", credentials=creds, cache_discovery=False)
                body = {"values": [values]}
                result = (
                    service.spreadsheets()
                    .values()
                    .append(
                        spreadsheetId=target_sheet,
                        range=target_range,
                        valueInputOption="USER_ENTERED",
                        body=body,
                    )
                    .execute()
                )
                return {
                    "source": "google_sheets",
                    "spreadsheet_id": target_sheet,
                    "range": target_range,
                    "updated_range": result.get("updates", {}).get("updatedRange"),
                    "values": values,
                }
            except Exception as exc:
                local_row = self._append_local_sheet_row(target_range, values)
                return {
                    "source": "local_sqlite_fallback",
                    "warning": f"Google Sheets API error ({exc}); stored locally.",
                    "row": local_row,
                }

        local_row = self._append_local_sheet_row(target_range, values)
        return {"source": "local_sqlite", "row": local_row}

    def _read_local_sheet_rows(self, sheet_range: str) -> List[List[Any]]:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT values_json FROM local_sheet_rows WHERE sheet_range = ? ORDER BY id ASC",
                (sheet_range,),
            ).fetchall()
        return [json.loads(r["values_json"]) for r in rows]

    def _append_local_sheet_row(
        self, sheet_range: str, values: List[Any]
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        with db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO local_sheet_rows (sheet_range, values_json, created_at) VALUES (?, ?, ?)",
                (sheet_range, json.dumps(values), now),
            )
            row_id = cur.lastrowid
        return {
            "id": row_id,
            "range": sheet_range,
            "values": values,
            "created_at": now,
        }


google_workspace = GoogleWorkspaceService()
