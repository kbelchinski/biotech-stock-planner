"""SQLite persistence of scan inputs and results, for reproducibility and history."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from app.domain.models import DataMode, ScanOutcome, ScanRun, ScanSummary
from app.logging_setup import get_logger

log = get_logger("storage")

SUCCESSFUL_OUTCOMES = frozenset({ScanOutcome.SUCCESS, ScanOutcome.NO_MATCHES})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    outcome TEXT NOT NULL,
    successful INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    scan_date TEXT NOT NULL,
    criteria_json TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_scans_mode_finished ON scans (mode, finished_at DESC);
"""


class ScanRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)
        log.info("SQLite ready at %s", path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, run: ScanRun) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO scans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.id,
                    run.mode.value,
                    run.outcome.value,
                    int(run.outcome in SUCCESSFUL_OUTCOMES),
                    run.started_at.isoformat(),
                    run.finished_at.isoformat(),
                    run.scan_date.isoformat(),
                    run.criteria.model_dump_json(),
                    run.summary.model_dump_json(),
                    run.model_dump_json(),
                ),
            )
        log.info("saved scan %s outcome=%s mode=%s", run.id[:8], run.outcome.value, run.mode.value)

    def get(self, scan_id: str) -> ScanRun | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT payload_json FROM scans WHERE id = ?", (scan_id,)).fetchone()
        return ScanRun.model_validate_json(row["payload_json"]) if row else None

    def latest(self, mode: DataMode, *, successful_only: bool) -> ScanRun | None:
        query = "SELECT payload_json FROM scans WHERE mode = ?"
        if successful_only:
            query += " AND successful = 1"
        query += " ORDER BY finished_at DESC, rowid DESC LIMIT 1"
        with closing(self._connect()) as conn:
            row = conn.execute(query, (mode.value,)).fetchone()
        return ScanRun.model_validate_json(row["payload_json"]) if row else None

    def history(self, mode: DataMode, limit: int = 20) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, outcome, successful, started_at, finished_at, scan_date, summary_json "
                "FROM scans WHERE mode = ? ORDER BY finished_at DESC LIMIT ?",
                (mode.value, limit),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "outcome": row["outcome"],
                "successful": bool(row["successful"]),
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "scan_date": row["scan_date"],
                "summary": ScanSummary.model_validate_json(row["summary_json"]).model_dump(),
            }
            for row in rows
        ]
