"""HTTP API for the single-user screener (no authentication by design; bind to localhost)."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import BPIQ_RATE_LIMITS_PER_MIN, BPIQ_TRIAL_HORIZON_DAYS, REPO_ROOT, Settings, get_settings
from app.domain.models import CATALYST_TYPE_LABELS, DataMode, ScanCriteria
from app.fixtures.transport import DEMO_SCENARIOS, DemoScenario
from app.logging_setup import configure_logging, get_logger
from app.providers.factory import live_configuration_problems
from app.providers.financials import LIVE_RUNWAY_UNAVAILABLE
from app.scan.export import scan_to_csv
from app.scan.orchestrator import ScanOrchestrator
from app.screening.market_calendar import MarketCalendar, new_york_today
from app.storage.repository import ScanRepository

log = get_logger("api")

DIAGNOSTICS_FILE_NAME = "diagnostics.json"


class ScanRequest(BaseModel):
    criteria: ScanCriteria = Field(default_factory=ScanCriteria)
    demo_scenario: DemoScenario | None = None


@dataclass
class ScanJob:
    id: str
    status: Literal["running", "completed", "failed"] = "running"
    message: str = "Starting"
    step: int = 0
    total_steps: int = 6
    scan_id: str | None = None
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    task: asyncio.Task | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "message": self.message,
            "step": self.step,
            "total_steps": self.total_steps,
            "scan_id": self.scan_id,
            "error": self.error,
            "started_at": self.started_at.isoformat(),
        }


def create_app(settings: Settings | None = None, *, orchestrator: ScanOrchestrator | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    calendar = MarketCalendar()
    repository = ScanRepository(settings.database_path)
    orchestrator = orchestrator or ScanOrchestrator(settings=settings, calendar=calendar, repository=repository)
    jobs: dict[str, ScanJob] = {}
    log.info(
        "backend starting mode=%s log_level=%s db=%s bpiq_configured=%s alpaca_configured=%s",
        settings.app_mode.value,
        settings.log_level,
        settings.database_path,
        settings.bpiq_configured,
        settings.alpaca_configured,
    )

    app = FastAPI(title="Catalyst Screener API", docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        log.debug("GET /api/status")
        now = datetime.now(UTC)
        mode = orchestrator.mode
        return {
            "mode": mode.value,
            "scan_date": new_york_today(now).isoformat(),
            "latest_completed_session": calendar.latest_completed_session(now).isoformat(),
            "live_configuration_problems": live_configuration_problems(settings) if mode is DataMode.LIVE else [],
            "providers": {
                "bpiq": {
                    "name": "BPIQ Apex REST",
                    "configured": settings.bpiq_configured,
                    "access_tier": settings.bpiq_access_tier.value,
                    "rate_limit_per_min": BPIQ_RATE_LIMITS_PER_MIN[settings.bpiq_access_tier],
                    "trial_horizon_days": BPIQ_TRIAL_HORIZON_DAYS,
                },
                "alpaca": {
                    "name": "Alpaca Market Data + Trading API (assets)",
                    "configured": settings.alpaca_configured,
                    "account_type": settings.alpaca_account_type.value,
                    "feed": "sip",
                    "adjustment": "raw",
                },
            },
            "runway": {
                "available": mode is DataMode.DEMO,
                "source": "Demo mock financials" if mode is DataMode.DEMO else None,
                "note": None if mode is DataMode.DEMO else LIVE_RUNWAY_UNAVAILABLE,
            },
            "diagnostics": _read_diagnostics(settings),
            "defaults": ScanCriteria().model_dump(mode="json"),
            "catalyst_types": [{"id": t.value, "label": label} for t, label in CATALYST_TYPE_LABELS.items()],
            "demo_scenarios": [
                {"id": s.value, "label": label, "description": description}
                for s, (label, description) in DEMO_SCENARIOS.items()
            ]
            if mode is DataMode.DEMO
            else [],
        }

    @app.post("/api/scans", status_code=202)
    async def start_scan(request: ScanRequest) -> dict[str, Any]:
        if orchestrator.mode is DataMode.LIVE and request.demo_scenario is not None:
            raise HTTPException(400, "Demo scenarios are only available in demo mode.")
        if any(job.status == "running" for job in jobs.values()):
            log.warning("POST /api/scans rejected: a scan is already running")
            raise HTTPException(409, "A scan is already running.")
        job = ScanJob(id=uuid.uuid4().hex)
        jobs[job.id] = job
        log.info(
            "POST /api/scans job=%s scenario=%s catalyst_window=%s-%s",
            job.id[:8],
            request.demo_scenario.value if request.demo_scenario else "-",
            request.criteria.catalyst_min_days,
            request.criteria.catalyst_max_days,
        )

        def on_progress(message: str, step: int, total: int) -> None:
            job.message, job.step, job.total_steps = message, step, total
            log.info("job %s progress %s/%s %s", job.id[:8], step, total, message)

        async def execute() -> None:
            try:
                run = await orchestrator.run(
                    request.criteria, demo_scenario=request.demo_scenario, progress=on_progress
                )
                job.scan_id = run.id
                job.status = "completed"
                job.message = "Scan finished"
                log.info("job %s completed scan=%s outcome=%s", job.id[:8], run.id[:8], run.outcome.value)
            except Exception:
                log.exception("job %s failed unexpectedly", job.id[:8])
                job.status = "failed"
                job.error = "The scan failed unexpectedly. Check the backend log for details."

        job.task = asyncio.create_task(execute())
        _prune(jobs)
        return job.public()

    @app.get("/api/scans/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "Unknown scan job.")
        log.debug("GET /api/scans/jobs/%s status=%s step=%s", job_id[:8], job.status, job.step)
        return job.public()

    @app.get("/api/scans/latest")
    def latest() -> dict[str, Any]:
        log.debug("GET /api/scans/latest")
        mode = orchestrator.mode
        successful = repository.latest(mode, successful_only=True)
        attempt = repository.latest(mode, successful_only=False)
        return {
            "latest_successful": successful.model_dump(mode="json") if successful else None,
            "latest_attempt": attempt.model_dump(mode="json")
            if attempt and (successful is None or attempt.id != successful.id)
            else None,
        }

    @app.get("/api/scans")
    def history(limit: int = 20) -> list[dict[str, Any]]:
        capped = min(max(limit, 1), 100)
        rows = repository.history(orchestrator.mode, capped)
        log.debug("GET /api/scans limit=%s returned=%s", capped, len(rows))
        return rows

    @app.get("/api/scans/{scan_id}")
    def get_scan(scan_id: str) -> dict[str, Any]:
        run = repository.get(scan_id)
        if run is None or run.mode is not orchestrator.mode:
            log.warning("GET /api/scans/%s not found for mode=%s", scan_id[:8], orchestrator.mode.value)
            raise HTTPException(404, "Scan not found for the current data mode.")
        log.info("GET /api/scans/%s outcome=%s results=%s", scan_id[:8], run.outcome.value, len(run.results))
        return run.model_dump(mode="json")

    @app.get("/api/scans/{scan_id}/export.csv")
    def export_scan(scan_id: str) -> Response:
        run = repository.get(scan_id)
        if run is None or run.mode is not orchestrator.mode:
            raise HTTPException(404, "Scan not found for the current data mode.")
        filename = f"catalyst-scan-{run.mode.value}-{run.scan_date.isoformat()}-{run.id[:8]}.csv"
        return Response(
            scan_to_csv(run),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    dist = REPO_ROOT / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")

    return app


def _prune(jobs: dict[str, ScanJob], keep: int = 20) -> None:
    finished = [j for j in jobs.values() if j.status != "running"]
    for job in sorted(finished, key=lambda j: j.started_at)[:-keep]:
        jobs.pop(job.id, None)


def _read_diagnostics(settings: Settings) -> dict[str, Any] | None:
    path: Path = settings.database_path.parent / DIAGNOSTICS_FILE_NAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


app = create_app()
