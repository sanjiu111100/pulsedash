"""SQLite persistence with idempotent writes.

The unique index on (source, metric, ts) makes every collector safe to re-run:
re-collecting the same observation is a no-op instead of a duplicate row.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS points (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT    NOT NULL,
    metric TEXT    NOT NULL,
    ts     INTEGER NOT NULL,
    value  REAL    NOT NULL,
    meta   TEXT    NOT NULL DEFAULT '',
    UNIQUE (source, metric, ts)
);
CREATE INDEX IF NOT EXISTS idx_points_metric_ts ON points (metric, ts);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    INTEGER NOT NULL,
    finished_at   INTEGER NOT NULL,
    ok_sources    INTEGER NOT NULL,
    failed_sources INTEGER NOT NULL,
    inserted      INTEGER NOT NULL,
    detail        TEXT    NOT NULL DEFAULT ''
);
"""


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- writes
    def add_points(self, points) -> int:
        """Insert observations, ignoring ones already stored. Returns new rows."""
        if not points:
            return 0
        rows = [(p.source, p.metric, int(p.ts), float(p.value), p.meta or "") for p in points]
        before = self.conn.total_changes
        self.conn.executemany(
            "INSERT OR IGNORE INTO points (source, metric, ts, value, meta) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        self.conn.commit()
        return self.conn.total_changes - before

    def log_run(self, started_at: int, finished_at: int, ok_sources: int,
                failed_sources: int, inserted: int, detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO runs (started_at, finished_at, ok_sources, failed_sources, inserted, detail)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (started_at, finished_at, ok_sources, failed_sources, inserted, detail),
        )
        self.conn.commit()

    # ---------------------------------------------------------------- reads
    def series(self, metric: str, limit: int = 500) -> list[tuple[int, float]]:
        rows = self.conn.execute(
            "SELECT ts, value FROM points WHERE metric = ? ORDER BY ts ASC", (metric,)
        ).fetchall()
        return [(int(r["ts"]), float(r["value"])) for r in rows][-limit:]

    def latest(self, metric: str):
        row = self.conn.execute(
            "SELECT ts, value, meta FROM points WHERE metric = ? ORDER BY ts DESC LIMIT 1", (metric,)
        ).fetchone()
        return (int(row["ts"]), float(row["value"]), row["meta"]) if row else None

    def metric_names(self, source: str | None = None) -> list[str]:
        if source:
            rows = self.conn.execute(
                "SELECT DISTINCT metric FROM points WHERE source = ? ORDER BY metric", (source,)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT DISTINCT metric FROM points ORDER BY metric").fetchall()
        return [r["metric"] for r in rows]

    def stats(self) -> dict:
        row = self.conn.execute("SELECT COUNT(*) AS n, MIN(ts) AS lo, MAX(ts) AS hi FROM points").fetchone()
        runs = self.conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()
        return {
            "points": int(row["n"]),
            "earliest": int(row["lo"]) if row["lo"] else None,
            "latest": int(row["hi"]) if row["hi"] else None,
            "runs": int(runs["n"]),
        }

    def recent_runs(self, limit: int = 10) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def utc_text(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def now_ts() -> int:
    return int(time.time())
