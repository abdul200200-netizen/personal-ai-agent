"""Opt-in, persistent Telegram cadence jobs for the personal assistant."""

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import settings
from app.core.policy import policy_engine
from app.database import db
from app.services.google_workspace import google_workspace
from app.services.telegram import telegram_service

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 30
DELIVERY_GRACE = timedelta(minutes=10)
MAX_DELIVERY_ATTEMPTS = 3
DEFAULT_MORNING_TIME = "06:00"
DEFAULT_EVENING_TIME = "21:00"
WEEKLY_CALIBRATION_DAY = 3  # Thursday; Saudi work week ends Thursday.
WEEKLY_CALIBRATION_TIME = "20:00"
MONTHLY_AUDIT_TIME = "20:00"


class BriefScheduler:
    """Small SQLite-backed scheduler. Briefs are opt-in and idempotent per local date."""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None

    @property
    def is_running(self) -> bool:
        return bool(self._task and not self._task.done())

    async def start_if_enabled(self) -> None:
        if not telegram_service.is_configured:
            logger.info("Proactive Telegram briefs are unavailable without a bot token")
            return
        if self._task and not self._task.done():
            return
        logger.info("Starting opt-in Telegram brief scheduler")
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                logger.info("Telegram brief scheduler stopped")

    async def _run_loop(self) -> None:
        while True:
            try:
                await self.run_due_jobs_once()
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Brief scheduler iteration failed (%s)", type(exc).__name__)
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    def _timezone_for(self, user_id: str) -> ZoneInfo:
        timezone_name = db.get_preference(
            user_id, "timezone", settings.user_timezone or "Asia/Riyadh"
        )
        try:
            return ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, TypeError):
            logger.warning("Invalid user timezone preference; falling back to UTC")
            return ZoneInfo("UTC")

    @staticmethod
    def _parse_time(value: Optional[str], fallback: str) -> time:
        try:
            return datetime.strptime(value or fallback, "%H:%M").time()
        except (ValueError, TypeError):
            return datetime.strptime(fallback, "%H:%M").time()

    async def run_due_jobs_once(self, now_utc: Optional[datetime] = None) -> int:
        """Run currently due briefs once; exposed separately for deterministic tests."""
        now_utc = now_utc or datetime.now(timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        now_utc = now_utc.astimezone(timezone.utc)
        recipients = db.list_brief_recipients()
        allowed_ids = {str(item) for item in settings.telegram_allowed_user_ids}
        sent_or_skipped = 0

        for user_id in recipients:
            if not settings.telegram_allow_all_users and user_id not in allowed_ids:
                continue
            try:
                user_timezone = self._timezone_for(user_id)
                local_now = now_utc.astimezone(user_timezone)
                for job_key, occurrence_key, scheduled_at in self._jobs_due_for(
                    user_id, local_now
                ):
                    if local_now < scheduled_at:
                        continue
                    if not db.claim_scheduled_job(
                        user_id, job_key, occurrence_key, MAX_DELIVERY_ATTEMPTS
                    ):
                        continue
                    if local_now - scheduled_at > DELIVERY_GRACE:
                        db.finish_scheduled_job(
                            user_id, job_key, occurrence_key, "skipped"
                        )
                        sent_or_skipped += 1
                        continue
                    try:
                        text = await self._build_brief(job_key, user_timezone, now_utc)
                        response = await telegram_service.send_message(int(user_id), text)
                        status = "sent" if response.get("ok") else "failed"
                    except Exception as exc:
                        logger.warning(
                            "Brief delivery failed for user %s (%s)",
                            user_id,
                            type(exc).__name__,
                        )
                        status = "failed"
                    db.finish_scheduled_job(user_id, job_key, occurrence_key, status)
                    if status == "sent":
                        sent_or_skipped += 1
            except Exception as exc:
                logger.warning(
                    "Could not evaluate briefs for user %s (%s)", user_id, type(exc).__name__
                )
        return sent_or_skipped

    def _jobs_due_for(
        self, user_id: str, local_now: datetime
    ) -> List[Tuple[str, str, datetime]]:
        today = local_now.date()
        daily = [
            (
                "morning_intent",
                self._parse_time(
                    db.get_preference(user_id, "morning_brief_time"), DEFAULT_MORNING_TIME
                ),
            ),
            (
                "evening_ledger",
                self._parse_time(
                    db.get_preference(user_id, "evening_brief_time"), DEFAULT_EVENING_TIME
                ),
            ),
        ]
        due: List[Tuple[str, str, datetime]] = [
            (
                key,
                today.isoformat(),
                datetime.combine(today, scheduled_time, tzinfo=local_now.tzinfo),
            )
            for key, scheduled_time in daily
        ]
        if today.weekday() == WEEKLY_CALIBRATION_DAY:
            due.append(
                (
                    "weekly_calibration",
                    today.isoformat(),
                    datetime.combine(
                        today,
                        self._parse_time(None, WEEKLY_CALIBRATION_TIME),
                        tzinfo=local_now.tzinfo,
                    ),
                )
            )
        if (today + timedelta(days=1)).month != today.month:
            due.append(
                (
                    "monthly_audit",
                    today.strftime("%Y-%m"),
                    datetime.combine(
                        today,
                        self._parse_time(None, MONTHLY_AUDIT_TIME),
                        tzinfo=local_now.tzinfo,
                    ),
                )
            )
        return due

    async def _build_brief(
        self, job_key: str, user_timezone: ZoneInfo, now_utc: datetime
    ) -> str:
        local_now = now_utc.astimezone(user_timezone)
        if job_key == "morning_intent":
            tasks = db.list_tasks(status="pending")[:20]
            safe_tasks = []
            hidden_tasks = 0
            for task in tasks:
                title = task.get("title", "")
                if policy_engine.check_phi(title).get("phi_detected"):
                    hidden_tasks += 1
                    continue
                safe_tasks.append(task)

            try:
                calendar = await asyncio.to_thread(
                    google_workspace.list_calendar_events,
                    max_results=8,
                    time_min=now_utc.isoformat(),
                )
                events = calendar.get("events", [])
            except Exception:
                events = []
            safe_events = []
            hidden_events = 0
            for event in events:
                event_text = f"{event.get('summary', '')} {event.get('description', '')}"
                if policy_engine.check_phi(event_text).get("phi_detected"):
                    hidden_events += 1
                    continue
                safe_events.append(event)

            lines = [
                f"Morning Intent — {local_now:%A, %d %B} ({user_timezone.key})",
                "Pick one to three outcomes for today. For each, define what ‘done’ means and name the likeliest blocker.",
            ]
            if safe_tasks:
                lines.append("\nPending tasks:")
                lines.extend(
                    f"• {task['title']}" + (f" (due {task['due_date']})" if task.get("due_date") else "")
                    for task in safe_tasks[:5]
                )
            if safe_events:
                lines.append("\nUpcoming calendar:")
                lines.extend(
                    f"• {event.get('summary', 'Untitled')} — {event.get('start_time', 'time not set')}"
                    for event in safe_events[:5]
                )
            if hidden_tasks or hidden_events:
                lines.append("\nSome items were hidden by the privacy filter.")
            if not safe_tasks and not safe_events and not hidden_tasks and not hidden_events:
                lines.append("\nNo pending tasks or upcoming events were found.")
            return "\n".join(lines)

        if job_key == "evening_ledger":
            return (
                f"Evening Intellectual Ledger — {local_now:%A, %d %B} ({user_timezone.key})\n"
                "Take a few minutes to reflect:\n"
                "1. What mattered most today, and what moved forward?\n"
                "2. Which assumption held up—or failed?\n"
                "3. What is one concrete next action for tomorrow?\n\n"
                "Share a reflection if useful. I will not save it as durable memory without your approval."
            )

        if job_key == "weekly_calibration":
            return (
                f"Weekly Calibration — {local_now:%d %B %Y} ({user_timezone.key})\n"
                "Use this 20-minute review to check:\n"
                "• Which decisions were delayed, and why?\n"
                "• Where did your assumptions meet contrary evidence?\n"
                "• Which reading or insight translated into action?\n"
                "• What is one adjustment for next week?\n\n"
                "If you want a lasting lesson saved, ask me to propose it; you approve before it becomes memory."
            )

        return (
            f"Monthly Strategic Audit — {local_now:%B %Y} ({user_timezone.key})\n"
            "Use this 45-minute review to examine:\n"
            "• Progress against your most important goals\n"
            "• Decisions where skill and luck may have been confused\n"
            "• Repeated blockers, bias drift, and unfinished commitments\n"
            "• What to continue, stop, or change next month\n\n"
            "You remain in control of any memory or plan changes."
        )


brief_scheduler = BriefScheduler()
