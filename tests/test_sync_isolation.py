"""Queue failures remain isolated and retry with backoff."""
from __future__ import annotations

import time
from pathlib import Path

from risarms_ui.remote import RemoteError, sync_files, sync_records
from risarms_ui.store import LocalStore

PASSWORD = "long-enough-passphrase"


class FakeRemote:
    def __init__(self, fail=()):
        self.url = "https://core.example"
        self.profile = {"id": "u1", "role": "member"}
        self.fail = set(fail)
        self.calls = []

    def request(self, method, path, body=None, idempotency_key=None):
        self.calls.append((method, path, body))
        marker = (body or {}).get("key") or (body or {}).get("name") or (body or {}).get("recipient")
        if marker in self.fail:
            raise RemoteError("temporary failure", 503)
        if path == "/api/health":
            return {"capabilities": ["conditional_records_v1"]}
        if path == "/api/records":
            return {"value": body["value"], "revision": "r1"}
        if path == "/api/files":
            return {"id": "file-1"}
        return {"id": "message-1", "status": "delivered"}


def make_store(tmp_path):
    store = LocalStore(tmp_path / "data")
    profile = store.create("tester", PASSWORD)
    store.bind(profile, "https://core.example", "u1", "member")
    return store, profile


def test_record_failure_does_not_block_other_records(tmp_path):
    store, profile = make_store(tmp_path)
    try:
        store.save(profile, "asis", "first", {"text": "one"})
        store.save(profile, "asis", "second", {"text": "two"})
        remote = FakeRemote(fail={"first"})

        result = sync_records(store, profile, remote)

        assert result["saved"] == 1
        assert result["failed_records"] == ["asis/first"]
        assert [row["key"] for row in store.records(profile, pending=True)] == ["first"]
        assert store.pending_records(profile) == []
    finally:
        store.close()


def test_file_failure_does_not_block_other_files(tmp_path):
    store, profile = make_store(tmp_path)
    try:
        first = store.add_file(profile, "first.txt", b"one")
        second = store.add_file(profile, "second.txt", b"two")
        remote = FakeRemote(fail={"first.txt"})

        result = sync_files(store, profile, remote)

        assert result["uploaded"] == 1
        assert result["failed_files"] == ["first.txt"]
        assert [row["id"] for row in store.pending_files(profile)] == []
        remaining = [row for row in store.files(profile) if row["pending"]]
        assert [row["id"] for row in remaining] == [first]
        assert second
    finally:
        store.close()


def test_message_failure_backs_off_without_losing_message(tmp_path):
    store, profile = make_store(tmp_path)
    try:
        identity = store.queue_message(profile, "alice", "retry me")
        remote = FakeRemote(fail={"alice"})

        assert store.flush_messages(profile, remote) == 0
        assert store.pending_message_count(profile) == 1
        assert store.pending_messages(profile) == []
        row = store.messages(profile, "alice")[0]
        assert row["id"] == identity
        assert row["attempts"] == 1
        assert row["next_attempt"] > time.time()
    finally:
        store.close()


def test_missing_file_is_retried_without_aborting_other_uploads(tmp_path):
    store, profile = make_store(tmp_path)
    try:
        missing = store.add_file(profile, "missing.txt", b"gone")
        keep = store.add_file(profile, "keep.txt", b"present")
        missing_row = next(row for row in store.files(profile) if row["id"] == missing)
        Path(missing_row["path"]).unlink()
        remote = FakeRemote()

        result = sync_files(store, profile, remote)

        assert result["uploaded"] == 1
        assert result["failed_files"] == ["missing.txt"]
        rows = {row["id"]: row for row in store.files(profile)}
        assert rows[missing]["pending"] == 1
        assert rows[missing]["attempts"] == 1
        assert rows[keep]["pending"] == 0
    finally:
        store.close()
