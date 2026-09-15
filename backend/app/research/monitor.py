"""Daily watchlist refresh while the backend process is running.

There is no background service outside this process. When the computer is off or the backend is
stopped, nothing is checked. On the next start, if the most recent scheduled time was missed, one
catch-up refresh runs. Reminders whose date passed while the app was closed are then created.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any

from app.domain.models import DataMode
from app.logging_setup import get_logger
from app.research.service import ResearchService
from app.screening.market_calendar import NEW_YORK

log = get_logger("monitor")

TICK_SECONDS = 60
BEHAVIOUR = (
    "Checks run only while this backend process is running. Nothing is monitored while it is stopped or the "
    "computer is off; on the next start, one catch-up refresh runs if a scheduled check was missed. "
    "Notifications are shown in this app only (no email or messaging)."
)


class Monitor:
    def __init__(self, service: ResearchService, *, enabled: bool, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._service = service
        self._enabled = enabled
        self._clock = clock
        self._task: asyncio.Task | None = None
        self.running_refresh = False
        self.last_error: str | None = None

    def last_scheduled(self, now: datetime) -> datetime:
        settings = self._service.monitoring_settings()
        hh, mm = (int(x) for x in settings.daily_refresh_time_ny.split(":"))
        local = now.astimezone(NEW_YORK)
        today_at = datetime.combine(local.date(), time(hh, mm), tzinfo=NEW_YORK)
        return today_at if local >= today_at else today_at - timedelta(days=1)

    def status(self) -> dict[str, Any]:
        mode: DataMode = self._service.mode
        settings = self._service.monitoring_settings()
        now = self._clock()
        last = self._service.repo.get_value(mode, "monitor_last_run")
        return {
            "process_scheduler_enabled": self._enabled,
            "daily_refresh_enabled": settings.daily_refresh_enabled,
            "daily_refresh_time_ny": settings.daily_refresh_time_ny,
            "next_scheduled_at": (self.last_scheduled(now) + timedelta(days=1)).isoformat(),
            "last_run": last,
            "running": self.running_refresh,
            "last_error": self.last_error,
            "last_manual_or_scheduled_refresh": self._service.repo.get_value(mode, "last_watch_refresh"),
            "behaviour": BEHAVIOUR,
        }

    def start(self) -> None:
        if self._enabled and self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_if_due()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # keep the loop alive; the failure is visible in status
                log.exception("scheduled refresh failed")
                self.last_error = f"{type(exc).__name__}: scheduled refresh failed; see backend log."
            await asyncio.sleep(TICK_SECONDS)

    async def run_if_due(self) -> bool:
        mode = self._service.mode
        settings = self._service.monitoring_settings()
        if not settings.daily_refresh_enabled or not self._service.repo.list_watch(mode):
            return False
        now = self._clock()
        scheduled = self.last_scheduled(now)
        last = self._service.repo.get_value(mode, "monitor_last_run")
        if last and datetime.fromisoformat(last["scheduled_for"]) >= scheduled:
            return False
        self.running_refresh = True
        try:
            report = await self._service.refresh_watchlist()
        finally:
            self.running_refresh = False
        ok = not report.get("issues") and all(r.get("ok") for r in report.get("tickers", {}).values())
        self._service.repo.set_value(
            mode, "monitor_last_run", {"scheduled_for": scheduled.isoformat(), "ran_at": now.isoformat(), "ok": ok}
        )
        self.last_error = None if ok else "Last scheduled refresh had errors; see the watchlist."
        return True
