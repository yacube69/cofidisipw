"""Results kept across restarts (CIPW-60): one SQLite file with the results, their PDFs and corrections, and the users
and sessions of the sign-in (CIPW-62, src/auth.py).

The file is `settings.storage.path` (default `outputs/fides.db`; `docker compose` keeps `outputs/` on the host). Only
the standard library is used. Every call opens its own connection, because the API answers requests in threads, and
WAL mode lets reading and writing run side by side. A result is stored as compressed JSON, with the PDF it came from;
`storage.keep_days` deletes old ones.

Backup: copy the file while Fides is stopped, or `sqlite3 outputs/fides.db ".backup fides-copy.db"` while it runs.
"""

from __future__ import annotations

import sqlite3
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.result import Result

SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    id TEXT PRIMARY KEY,
    file TEXT NOT NULL,
    company TEXT,
    ico TEXT,
    owner TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    result BLOB NOT NULL,
    pdf BLOB
);
CREATE INDEX IF NOT EXISTS results_by_time ON results (created_at);
CREATE TABLE IF NOT EXISTS users (
    name TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    password TEXT NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user TEXT NOT NULL,
    created_at REAL NOT NULL,
    seen_at REAL NOT NULL
);
"""


@dataclass
class StoredResult:
    result: Result
    pdf: bytes | None
    owner: str | None
    created_at: str


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """A connection that commits on success and is always closed."""
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    # ------------------------------------------------------------------------------------------------ results

    def save(self, result_id: str, result: Result, pdf: bytes | None, owner: str | None) -> None:
        """Store a new result, or the new state of one (after a correction): its owner, time and PDF stay."""
        data = zlib.compress(result.model_dump_json().encode("utf-8"), 6)
        meta = result.statements.metadata
        now = _now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO results (id, file, company, ico, owner, created_at, updated_at, result, pdf)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET company = excluded.company, ico = excluded.ico,
                       updated_at = excluded.updated_at, result = excluded.result,
                       pdf = COALESCE(excluded.pdf, results.pdf)""",
                (result_id, result.file, result.display_name, meta.ico, owner, now, now, data, pdf),
            )

    def load(self, result_id: str) -> StoredResult | None:
        with self.connect() as db:
            row = db.execute("SELECT result, pdf, owner, created_at FROM results WHERE id = ?", (result_id,)).fetchone()
        if row is None:
            return None
        result = Result.model_validate_json(zlib.decompress(row["result"]))
        return StoredResult(result, row["pdf"], row["owner"], row["created_at"])

    def list(self, owner: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """The stored results, newest first, without their data; only one owner's when `owner` is given."""
        where, args = ("WHERE owner = ?", [owner]) if owner is not None else ("", [])
        with self.connect() as db:
            rows = db.execute(
                f"SELECT id, file, company, ico, owner, created_at, updated_at FROM results {where} "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                [*args, limit],
            ).fetchall()
        return [dict(r) for r in rows]

    def owner(self, result_id: str) -> tuple[bool, str | None]:
        """(stored, owner) of a result."""
        with self.connect() as db:
            row = db.execute("SELECT owner FROM results WHERE id = ?", (result_id,)).fetchone()
        return (row is not None, row["owner"] if row else None)

    def delete(self, result_id: str) -> bool:
        with self.connect() as db:
            return db.execute("DELETE FROM results WHERE id = ?", (result_id,)).rowcount > 0

    def purge(self, keep_days: int | None) -> int:
        """Delete the results stored more than `keep_days` ago; None keeps everything. Returns how many."""
        if keep_days is None:
            return 0
        cutoff = (datetime.now().astimezone() - timedelta(days=keep_days)).isoformat(timespec="seconds")
        with self.connect() as db:
            return db.execute("DELETE FROM results WHERE created_at < ?", (cutoff,)).rowcount
