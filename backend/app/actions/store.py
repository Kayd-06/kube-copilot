"""SQLite-backed store for action records and the append-only audit log."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.actions.models import ActionRecord, AuditEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    namespace TEXT NOT NULL,
    workload TEXT NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS actions_workload ON actions(namespace, workload);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    action_id TEXT,
    event TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_action ON audit(action_id);
"""


class ActionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.RLock()

    # ----- actions -----
    def save(self, record: ActionRecord) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO actions (id, created_at, namespace, workload, status, data) VALUES (?,?,?,?,?,?)",
                (
                    record.id,
                    record.created_at.isoformat(),
                    record.namespace,
                    record.workload,
                    record.status.value,
                    record.model_dump_json(),
                ),
            )

    def get(self, action_id: str) -> ActionRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT data FROM actions WHERE id = ?", (action_id,)).fetchone()
        return ActionRecord.model_validate_json(row[0]) if row else None

    def list(
        self, status: str | None = None, namespace: str | None = None, workload: str | None = None, limit: int = 100
    ) -> list[ActionRecord]:
        query = "SELECT data FROM actions WHERE 1=1"
        args: list[Any] = []
        if status:
            query += " AND status = ?"
            args.append(status)
        if namespace:
            query += " AND namespace = ?"
            args.append(namespace)
        if workload:
            query += " AND workload = ?"
            args.append(workload)
        query += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(query, args).fetchall()
        return [ActionRecord.model_validate_json(r[0]) for r in rows]

    # ----- audit -----
    def audit(self, event: str, actor: str, action_id: str | None = None, **detail: Any) -> AuditEvent:
        ts = datetime.now(timezone.utc)
        payload = json.dumps(detail, default=str)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO audit (ts, action_id, event, actor, detail) VALUES (?,?,?,?,?)",
                (ts.isoformat(), action_id, event, actor, payload),
            )
            event_id = int(cur.lastrowid)
        return AuditEvent(id=event_id, ts=ts, action_id=action_id, event=event, actor=actor, detail=detail)

    def audit_events(self, action_id: str | None = None, limit: int = 200) -> list[AuditEvent]:
        query = "SELECT id, ts, action_id, event, actor, detail FROM audit"
        args: list[Any] = []
        if action_id:
            query += " WHERE action_id = ? ORDER BY id ASC"
            args.append(action_id)
        else:
            query += " ORDER BY id DESC"
        query += " LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(query, args).fetchall()
        return [
            AuditEvent(
                id=r[0], ts=datetime.fromisoformat(r[1]), action_id=r[2], event=r[3], actor=r[4], detail=json.loads(r[5])
            )
            for r in rows
        ]
