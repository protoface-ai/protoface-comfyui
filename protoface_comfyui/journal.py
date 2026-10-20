"""Crash-safe paid-request intent log. Contains no bearer keys or signed result URLs."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path


class Journal:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        self.path = directory / "jobs.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (intent TEXT PRIMARY KEY, token TEXT NOT NULL, created REAL NOT NULL, run_id TEXT, state TEXT NOT NULL, payload TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS uploads (namespace TEXT, digest TEXT, asset_id TEXT, PRIMARY KEY(namespace,digest))"
            )
        self.path.chmod(0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def intent(self, intent: str) -> dict:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO jobs(intent,token,created,state) VALUES(?,?,?,?)",
                (intent, str(uuid.uuid4()), time.time(), "created"),
            )
            db.row_factory = sqlite3.Row
            return dict(db.execute("SELECT * FROM jobs WHERE intent=?", (intent,)).fetchone())

    def update(self, intent: str, *, state: str, run_id: str | None = None, payload: dict | None = None):
        with self.connect() as db:
            if state == "submitting":
                db.execute(
                    "UPDATE jobs SET created=? WHERE intent=? AND state IN ('created','rejected')",
                    (time.time(), intent),
                )
            db.execute(
                "UPDATE jobs SET state=?,run_id=COALESCE(?,run_id),payload=COALESCE(?,payload) WHERE intent=?",
                (state, run_id, json.dumps(payload, sort_keys=True) if payload is not None else None, intent),
            )

    def upload(self, namespace: str, digest: str) -> str | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT asset_id FROM uploads WHERE namespace=? AND digest=?", (namespace, digest)
            ).fetchone()
            return row[0] if row else None

    def save_upload(self, namespace: str, digest: str, asset_id: str):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO uploads VALUES(?,?,?)", (namespace, digest, asset_id))

    def forget_upload(self, namespace: str, digest: str):
        with self.connect() as db:
            db.execute("DELETE FROM uploads WHERE namespace=? AND digest=?", (namespace, digest))
