"""SQLite persistence of scan inputs and results, for reproducibility and history.

Scan snapshots are immutable: a scan id is inserted once and never updated. Each snapshot holds
the normalized provider records used (catalysts, company profile values, daily bars), the
screening-rule version, every eligibility decision with observed values and thresholds, source
timestamps, data-quality issues, and derived calculations (e.g. weekly turnover).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from app.domain.models import DataMode, Eligibility, ScanOutcome, ScanRun, ScanSummary
from app.domain.research import FirstSeen
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
CREATE TABLE IF NOT EXISTS scan_company_index (
    scan_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    ticker TEXT NOT NULL,
    eligibility TEXT NOT NULL,
    scan_date TEXT NOT NULL,
    price_session TEXT,
    price REAL,
    finished_at TEXT NOT NULL,
    PRIMARY KEY (scan_id, ticker)
);
CREATE INDEX IF NOT EXISTS ix_company_index_ticker ON scan_company_index (mode, ticker, finished_at);
CREATE TRIGGER IF NOT EXISTS scans_immutable BEFORE UPDATE ON scans
BEGIN
    SELECT RAISE(ABORT, 'scan snapshots are immutable');
END;
"""


class ImmutableScanError(Exception):
    pass


class ScanRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)
        self._backfill_index()
        log.info("SQLite ready at %s", path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, run: ScanRun) -> None:
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute(
                    "INSERT INTO scans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                self._index(conn, run)
        except sqlite3.IntegrityError as exc:
            raise ImmutableScanError(f"Scan {run.id} already exists and cannot be overwritten.") from exc
        log.info("saved scan %s outcome=%s mode=%s", run.id[:8], run.outcome.value, run.mode.value)

    @staticmethod
    def _index(conn: sqlite3.Connection, run: ScanRun) -> None:
        conn.executemany(
            "INSERT OR IGNORE INTO scan_company_index VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    run.id,
                    run.mode.value,
                    r.ticker,
                    r.eligibility.value,
                    run.scan_date.isoformat(),
                    r.price_date.isoformat() if r.price_date else None,
                    r.price,
                    run.finished_at.isoformat(),
                )
                for r in run.results
            ],
        )

    def _backfill_index(self) -> None:
        with closing(self._connect()) as conn, conn:
            rows = conn.execute(
                "SELECT payload_json FROM scans WHERE id NOT IN (SELECT DISTINCT scan_id FROM scan_company_index)"
            ).fetchall()
            for row in rows:
                self._index(conn, ScanRun.model_validate_json(row["payload_json"]))

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
                "SELECT id, outcome, successful, started_at, finished_at, scan_date, summary_json, "
                "json_extract(payload_json, '$.rule_version') AS rule_version, "
                "json_array_length(payload_json, '$.issues') AS issue_count "
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
                "rule_version": row["rule_version"] or "unversioned (saved before 2026-09-14)",
                "issue_count": row["issue_count"] or 0,
            }
            for row in rows
        ]

    def company_history(self, mode: DataMode, ticker: str, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT scan_id, eligibility, scan_date, price_session, price, finished_at FROM scan_company_index "
                "WHERE mode = ? AND ticker = ? ORDER BY finished_at DESC LIMIT ?",
                (mode.value, ticker, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def first_seen(self, mode: DataMode, ticker: str) -> FirstSeen | None:
        """First saved scan where the company qualified; otherwise the first scan that evaluated it."""
        with closing(self._connect()) as conn:
            for basis, clause in (("first_qualified", "AND eligibility = ?"), ("first_evaluated", "")):
                args: list[Any] = [mode.value, ticker] + ([Eligibility.QUALIFIES.value] if clause else [])
                row = conn.execute(
                    "SELECT scan_id, scan_date, price_session, price FROM scan_company_index "
                    f"WHERE mode = ? AND ticker = ? {clause} ORDER BY finished_at ASC LIMIT 1",
                    args,
                ).fetchone()
                if row:
                    return FirstSeen(
                        basis=basis,  # type: ignore[arg-type]
                        scan_id=row["scan_id"],
                        scan_date=row["scan_date"],
                        price_session=row["price_session"],
                        close=row["price"],
                    )
        return None
