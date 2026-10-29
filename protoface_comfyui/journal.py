"""Crash-safe paid-request intent log. Contains no bearer keys or signed result URLs."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from sqlite3 import connect as connect_sqlite_database

ENABLE_WAL = "PRAGMA journal_mode=WAL"
CREATE_JOBS_TABLE = (
    "CREATE TABLE IF NOT EXISTS jobs (intent TEXT PRIMARY KEY, token TEXT NOT NULL, "
    "created REAL NOT NULL, run_id TEXT, state TEXT NOT NULL, payload TEXT)"
)
CREATE_UPLOADS_TABLE = (
    "CREATE TABLE IF NOT EXISTS uploads (namespace TEXT, digest TEXT, asset_id TEXT, "
    "PRIMARY KEY(namespace,digest))"
)
INSERT_JOB_INTENT = "INSERT OR IGNORE INTO jobs(intent,token,created,state) VALUES(?,?,?,?)"
SELECT_JOB_INTENT = "SELECT * FROM jobs WHERE intent=?"
RESET_SUBMISSION_TIME = "UPDATE jobs SET created=? WHERE intent=? AND state IN ('created','rejected')"
UPDATE_JOB_STATE = (
    "UPDATE jobs SET state=?,run_id=COALESCE(?,run_id),payload=COALESCE(?,payload) WHERE intent=?"
)
SELECT_CACHED_UPLOAD = "SELECT asset_id FROM uploads WHERE namespace=? AND digest=?"
SAVE_CACHED_UPLOAD = "INSERT OR REPLACE INTO uploads VALUES(?,?,?)"
DELETE_CACHED_UPLOAD = "DELETE FROM uploads WHERE namespace=? AND digest=?"


class Journal:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        self.path = directory / "jobs.sqlite3"
        with self.open_database() as db:
            db.execute(ENABLE_WAL)
            db.execute(CREATE_JOBS_TABLE)
            db.execute(CREATE_UPLOADS_TABLE)
        self.path.chmod(0o600)

    def open_database(self):
        return connect_sqlite_database(self.path, timeout=30)

    # Retain the factory name for existing callers while using the descriptive name internally.
    connect = open_database

    def intent(self, intent: str) -> dict:
        with self.open_database() as db:
            db.execute(INSERT_JOB_INTENT, (intent, str(uuid.uuid4()), time.time(), "created"))
            db.row_factory = sqlite3.Row
            return dict(db.execute(SELECT_JOB_INTENT, (intent,)).fetchone())

    def update(self, intent: str, *, state: str, run_id: str | None = None, payload: dict | None = None):
        with self.open_database() as db:
            if state == "submitting":
                db.execute(RESET_SUBMISSION_TIME, (time.time(), intent))
            db.execute(
                UPDATE_JOB_STATE,
                (state, run_id, json.dumps(payload, sort_keys=True) if payload is not None else None, intent),
            )

    def upload(self, namespace: str, digest: str) -> str | None:
        with self.open_database() as db:
            row = db.execute(SELECT_CACHED_UPLOAD, (namespace, digest)).fetchone()
            return row[0] if row else None

    def save_upload(self, namespace: str, digest: str, asset_id: str):
        with self.open_database() as db:
            db.execute(SAVE_CACHED_UPLOAD, (namespace, digest, asset_id))

    def forget_upload(self, namespace: str, digest: str):
        with self.open_database() as db:
            db.execute(DELETE_CACHED_UPLOAD, (namespace, digest))
