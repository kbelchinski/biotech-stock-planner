"""SQLite persistence for research features. Every row is scoped by data mode."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from app.domain.models import DataMode
from app.domain.research import (
    CatalystRevision,
    JournalEntry,
    MonitoringSettings,
    Notification,
    TrackedCatalyst,
    Trade,
    TradeKind,
    WatchItem,
)
from app.logging_setup import get_logger

log = get_logger("storage.research")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    mode TEXT NOT NULL, ticker TEXT NOT NULL, payload_json TEXT NOT NULL,
    PRIMARY KEY (mode, ticker)
);
CREATE TABLE IF NOT EXISTS tracked_catalysts (
    mode TEXT NOT NULL, event_id TEXT NOT NULL, ticker TEXT NOT NULL, payload_json TEXT NOT NULL,
    PRIMARY KEY (mode, event_id)
);
CREATE INDEX IF NOT EXISTS ix_tracked_ticker ON tracked_catalysts (mode, ticker);
CREATE TABLE IF NOT EXISTS catalyst_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT NOT NULL, event_id TEXT NOT NULL, ticker TEXT NOT NULL,
    observed_at TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_revisions_ticker ON catalyst_revisions (mode, ticker, observed_at);
CREATE TABLE IF NOT EXISTS notifications (
    mode TEXT NOT NULL, id TEXT NOT NULL, created_at TEXT NOT NULL, read INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (mode, id)
);
CREATE INDEX IF NOT EXISTS ix_notifications_created ON notifications (mode, created_at DESC);
CREATE TABLE IF NOT EXISTS settings (
    mode TEXT NOT NULL, key TEXT NOT NULL, payload_json TEXT NOT NULL,
    PRIMARY KEY (mode, key)
);
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY, mode TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, ticker TEXT NOT NULL,
    updated_at TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_trades_mode_kind ON trades (mode, kind);
CREATE TABLE IF NOT EXISTS journal (
    id TEXT PRIMARY KEY, mode TEXT NOT NULL, ticker TEXT NOT NULL, created_at TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT NOT NULL, ticker TEXT NOT NULL, created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_analyses_ticker ON analyses (mode, ticker, created_at DESC);
CREATE TABLE IF NOT EXISTS ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT NOT NULL, created_at TEXT NOT NULL, model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, estimated_cost_usd REAL NOT NULL
);
"""


class ResearchRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------ watchlist

    def list_watch(self, mode: DataMode) -> list[WatchItem]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT payload_json FROM watchlist WHERE mode = ? ORDER BY ticker", (mode.value,)).fetchall()
        return [WatchItem.model_validate_json(r["payload_json"]) for r in rows]

    def get_watch(self, mode: DataMode, ticker: str) -> WatchItem | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT payload_json FROM watchlist WHERE mode = ? AND ticker = ?", (mode.value, ticker)).fetchone()
        return WatchItem.model_validate_json(row["payload_json"]) if row else None

    def upsert_watch(self, item: WatchItem) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO watchlist VALUES (?, ?, ?) ON CONFLICT (mode, ticker) DO UPDATE SET payload_json = excluded.payload_json",
                (item.mode.value, item.ticker, item.model_dump_json()),
            )

    def remove_watch(self, mode: DataMode, ticker: str) -> bool:
        """Stops watching. Tracked catalysts and revision history are kept."""
        with closing(self._connect()) as conn, conn:
            cur = conn.execute("DELETE FROM watchlist WHERE mode = ? AND ticker = ?", (mode.value, ticker))
        return cur.rowcount > 0

    # ------------------------------------------------------------ catalysts and revisions

    def list_catalysts(self, mode: DataMode, ticker: str | None = None) -> list[TrackedCatalyst]:
        query, args = "SELECT payload_json FROM tracked_catalysts WHERE mode = ?", [mode.value]
        if ticker:
            query += " AND ticker = ?"
            args.append(ticker)
        with closing(self._connect()) as conn:
            rows = conn.execute(query, args).fetchall()
        cats = [TrackedCatalyst.model_validate_json(r["payload_json"]) for r in rows]
        return sorted(cats, key=lambda c: (c.ticker, c.catalyst_date is None, c.catalyst_date or datetime.max.date(), c.provider_record_id))

    def save_catalysts(self, catalysts: list[TrackedCatalyst]) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                "INSERT INTO tracked_catalysts VALUES (?, ?, ?, ?) "
                "ON CONFLICT (mode, event_id) DO UPDATE SET ticker = excluded.ticker, payload_json = excluded.payload_json",
                [(c.mode.value, c.event_id, c.ticker, c.model_dump_json()) for c in catalysts],
            )

    def add_revisions(self, mode: DataMode, revisions: list[CatalystRevision]) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                "INSERT INTO catalyst_revisions (mode, event_id, ticker, observed_at, payload_json) VALUES (?, ?, ?, ?, ?)",
                [(mode.value, r.event_id, r.ticker, r.observed_at.isoformat(), r.model_dump_json()) for r in revisions],
            )

    def list_revisions(self, mode: DataMode, ticker: str) -> list[CatalystRevision]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT payload_json FROM catalyst_revisions WHERE mode = ? AND ticker = ? ORDER BY observed_at, id",
                (mode.value, ticker),
            ).fetchall()
        return [CatalystRevision.model_validate_json(r["payload_json"]) for r in rows]

    # ------------------------------------------------------------ notifications

    def add_notifications(self, notifications: list[Notification]) -> int:
        """Insert new notifications; an existing id (same dedupe key) is ignored. Returns inserted count."""
        inserted = 0
        with closing(self._connect()) as conn, conn:
            for n in notifications:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO notifications (mode, id, created_at, read, payload_json) VALUES (?, ?, ?, 0, ?)",
                    (n.mode.value, n.id, n.created_at.isoformat(), n.model_dump_json()),
                )
                inserted += cur.rowcount
        return inserted

    def list_notifications(self, mode: DataMode, *, limit: int = 100, unread_only: bool = False) -> list[Notification]:
        query = "SELECT payload_json, read FROM notifications WHERE mode = ?"
        if unread_only:
            query += " AND read = 0"
        query += " ORDER BY created_at DESC LIMIT ?"
        with closing(self._connect()) as conn:
            rows = conn.execute(query, (mode.value, limit)).fetchall()
        return [Notification.model_validate_json(r["payload_json"]).model_copy(update={"read": bool(r["read"])}) for r in rows]

    def unread_count(self, mode: DataMode) -> int:
        with closing(self._connect()) as conn:
            return conn.execute("SELECT COUNT(*) FROM notifications WHERE mode = ? AND read = 0", (mode.value,)).fetchone()[0]

    def mark_read(self, mode: DataMode, ids: list[str] | None) -> None:
        with closing(self._connect()) as conn, conn:
            if ids is None:
                conn.execute("UPDATE notifications SET read = 1 WHERE mode = ?", (mode.value,))
            else:
                conn.executemany("UPDATE notifications SET read = 1 WHERE mode = ? AND id = ?", [(mode.value, i) for i in ids])

    # ------------------------------------------------------------ settings / key-value

    def get_value(self, mode: DataMode, key: str) -> Any | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT payload_json FROM settings WHERE mode = ? AND key = ?", (mode.value, key)).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def set_value(self, mode: DataMode, key: str, value: Any) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO settings VALUES (?, ?, ?) ON CONFLICT (mode, key) DO UPDATE SET payload_json = excluded.payload_json",
                (mode.value, key, json.dumps(value, default=str)),
            )

    def monitoring_settings(self, mode: DataMode) -> MonitoringSettings:
        stored = self.get_value(mode, "monitoring")
        return MonitoringSettings.model_validate(stored) if stored else MonitoringSettings()

    def save_monitoring_settings(self, mode: DataMode, value: MonitoringSettings) -> None:
        self.set_value(mode, "monitoring", value.model_dump(mode="json"))

    # ------------------------------------------------------------ trades and journal

    def save_trade(self, trade: Trade) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET "
                "status = excluded.status, updated_at = excluded.updated_at, payload_json = excluded.payload_json",
                (
                    trade.id,
                    trade.mode.value,
                    trade.kind.value,
                    trade.status.value,
                    trade.ticker,
                    trade.updated_at.isoformat(),
                    trade.model_dump_json(),
                ),
            )

    def get_trade(self, mode: DataMode, trade_id: str) -> Trade | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT payload_json FROM trades WHERE mode = ? AND id = ?", (mode.value, trade_id)).fetchone()
        return Trade.model_validate_json(row["payload_json"]) if row else None

    def list_trades(self, mode: DataMode, kind: TradeKind | None = None) -> list[Trade]:
        query, args = "SELECT payload_json FROM trades WHERE mode = ?", [mode.value]
        if kind:
            query += " AND kind = ?"
            args.append(kind.value)
        with closing(self._connect()) as conn:
            rows = conn.execute(query + " ORDER BY updated_at DESC", args).fetchall()
        return [Trade.model_validate_json(r["payload_json"]) for r in rows]

    def delete_trade(self, mode: DataMode, trade_id: str) -> bool:
        with closing(self._connect()) as conn, conn:
            return conn.execute("DELETE FROM trades WHERE mode = ? AND id = ?", (mode.value, trade_id)).rowcount > 0

    def add_journal(self, entry: JournalEntry) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO journal VALUES (?, ?, ?, ?, ?)",
                (entry.id, entry.mode.value, entry.ticker, entry.created_at.isoformat(), entry.model_dump_json()),
            )

    def list_journal(self, mode: DataMode, ticker: str | None = None) -> list[JournalEntry]:
        query, args = "SELECT payload_json FROM journal WHERE mode = ?", [mode.value]
        if ticker:
            query += " AND ticker = ?"
            args.append(ticker)
        with closing(self._connect()) as conn:
            rows = conn.execute(query + " ORDER BY created_at DESC", args).fetchall()
        return [JournalEntry.model_validate_json(r["payload_json"]) for r in rows]

    def delete_journal(self, mode: DataMode, entry_id: str) -> bool:
        with closing(self._connect()) as conn, conn:
            return conn.execute("DELETE FROM journal WHERE mode = ? AND id = ?", (mode.value, entry_id)).rowcount > 0

    # ------------------------------------------------------------ saved analyses (append-only)

    def save_analysis(self, mode: DataMode, ticker: str, created_at: datetime, payload: dict[str, Any]) -> int:
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "INSERT INTO analyses (mode, ticker, created_at, payload_json) VALUES (?, ?, ?, ?)",
                (mode.value, ticker, created_at.isoformat(), json.dumps(payload, default=str)),
            )
        return int(cur.lastrowid)

    def latest_analyses(self, mode: DataMode, ticker: str, limit: int = 2) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, created_at, payload_json FROM analyses WHERE mode = ? AND ticker = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                (mode.value, ticker, limit),
            ).fetchall()
        return [{"id": r["id"], "created_at": r["created_at"], "payload": json.loads(r["payload_json"])} for r in rows]

    # ------------------------------------------------------------ AI usage ledger

    def record_ai_usage(self, *, month: str, created_at: datetime, model: str, input_tokens: int, output_tokens: int, cost: float) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO ai_usage (month, created_at, model, input_tokens, output_tokens, estimated_cost_usd) VALUES (?, ?, ?, ?, ?, ?)",
                (month, created_at.isoformat(), model, input_tokens, output_tokens, cost),
            )

    def ai_month_spend(self, month: str) -> float:
        with closing(self._connect()) as conn:
            return float(conn.execute("SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM ai_usage WHERE month = ?", (month,)).fetchone()[0])
