import json
import stat
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from protoface_comfyui.journal import Journal


def test_parallel_intents_and_restart_reuse_one_token(tmp_path):
    journal = Journal(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as executor:
        records = list(executor.map(lambda _: journal.intent("same-intent"), range(24)))
    assert len({record["token"] for record in records}) == 1
    assert len({record["created"] for record in records}) == 1
    assert Journal(tmp_path).intent("same-intent")["token"] == records[0]["token"]


def test_restart_recovers_job_and_isolates_uploaded_assets(tmp_path):
    journal = Journal(tmp_path)
    original = journal.intent("recover")
    journal.update("recover", state="submitting")
    journal.update("recover", state="submitted", run_id="run_mock", payload={"model": "mock"})
    journal.save_upload("account-a", "same-digest", "asset_a")
    journal.save_upload("account-b", "same-digest", "asset_b")
    restarted = Journal(tmp_path)
    record = restarted.intent("recover")
    assert record["token"] == original["token"]
    assert record["run_id"] == "run_mock"
    assert record["state"] == "submitted"
    assert json.loads(record["payload"]) == {"model": "mock"}
    assert restarted.upload("account-a", "same-digest") == "asset_a"
    assert restarted.upload("account-b", "same-digest") == "asset_b"
    restarted.forget_upload("account-a", "same-digest")
    assert restarted.upload("account-a", "same-digest") is None
    assert restarted.upload("account-b", "same-digest") == "asset_b"


def test_connection_factory_preserves_rollback(tmp_path):
    journal = Journal(tmp_path)
    journal.intent("recover")
    journal.update("recover", state="submitted")
    with pytest.raises(RuntimeError):
        with journal.connect() as connection:
            connection.execute("UPDATE jobs SET state=? WHERE intent=?", ("bad", "recover"))
            raise RuntimeError("rollback fixture")
    assert journal.intent("recover")["state"] == "submitted"


def test_wal_and_private_file_permissions(tmp_path):
    journal = Journal(tmp_path)
    with journal.open_database() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    if sys.platform != "win32":
        assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700
        assert stat.S_IMODE(journal.path.stat().st_mode) == 0o600
