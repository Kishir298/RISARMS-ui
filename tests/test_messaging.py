"""Local messaging store: durable offline queue, idempotency and isolation."""
from __future__ import annotations

import time

from risarms_ui.remote import RemoteError
from risarms_ui.store import LocalStore

PASSWORD = "long-enough-passphrase"


class FakeRemote:
    def __init__(self, fail_status=None):
        self.url = "https://core.example"
        self.profile = {"id": "u1", "role": "member"}
        self.calls = []
        self.fail_status = fail_status

    def request(self, method, path, body=None, idempotency_key=None):
        self.calls.append({"method": method, "path": path, "body": body, "key": idempotency_key})
        if self.fail_status is not None:
            raise RemoteError("rejected", self.fail_status)
        return {"id": "srv-" + str(len(self.calls)), "status": "delivered"}


def make_store(tmp_path):
    store = LocalStore(tmp_path / "data")
    profile = store.create("tester", PASSWORD)
    store.bind(profile, "https://core.example", "u1", "member")
    return store, profile


def test_queued_message_is_not_reported_as_delivered(tmp_path):
    store, profile = make_store(tmp_path)
    try:
        store.queue_message(profile, "alice", "hello")
        rows = store.messages(profile, "alice")
        assert rows[0]["status"] == "queued"
        assert store.pending_message_count(profile) == 1
    finally:
        store.close()


def test_flush_sends_once_and_marks_delivered(tmp_path):
    store, profile = make_store(tmp_path)
    try:
        store.queue_message(profile, "alice", "hello")
        remote = FakeRemote()
        assert store.flush_messages(profile, remote) == 1
        assert store.pending_message_count(profile) == 0
        row = store.messages(profile, "alice")[0]
        assert row["status"] == "delivered"
        assert row["remote_id"] == "srv-1"
        # The local id is the idempotency key so a retry cannot duplicate.
        assert remote.calls[0]["key"] == row["id"]
        assert remote.calls[0]["body"]["client_id"] == row["id"]
    finally:
        store.close()


def test_transport_failure_keeps_message_queued(tmp_path):
    store, profile = make_store(tmp_path)
    try:
        store.queue_message(profile, "alice", "hello")
        remote = FakeRemote(fail_status=0)  # transport failure
        assert store.flush_messages(profile, remote) == 0
        assert store.pending_message_count(profile) == 1
        assert store.messages(profile, "alice")[0]["status"] == "queued"
    finally:
        store.close()


def test_explicit_rejection_marks_message_failed(tmp_path):
    store, profile = make_store(tmp_path)
    try:
        store.queue_message(profile, "alice", "hello")
        store.flush_messages(profile, FakeRemote(fail_status=403))
        row = store.messages(profile, "alice")[0]
        assert row["status"] == "failed"
        assert "rejected" in row["error"]
        assert store.pending_message_count(profile) == 0
    finally:
        store.close()


def test_incoming_messages_are_cached_without_duplicates(tmp_path):
    store, profile = make_store(tmp_path)
    try:
        rows = [
            {"id": "m1", "body": "hi", "created": time.time(), "read": False},
            {"id": "m2", "body": "again", "created": time.time(), "read": True},
        ]
        assert store.record_incoming(profile, "alice", rows) == 2
        assert store.record_incoming(profile, "alice", rows) == 0
        cached = store.messages(profile, "alice")
        assert [row["body"] for row in cached] == ["hi", "again"]
        assert cached[0]["direction"] == "in"
        assert cached[1]["status"] == "read"
    finally:
        store.close()


def test_messages_are_isolated_per_local_profile(tmp_path):
    store = LocalStore(tmp_path / "data")
    try:
        first = store.create("first", PASSWORD)
        second = store.create("second", PASSWORD)
        store.queue_message(first, "alice", "private")
        assert store.messages(second) == []
        assert store.pending_message_count(second) == 0
        assert len(store.messages(first)) == 1
    finally:
        store.close()


def test_threads_report_unread_and_last_body(tmp_path):
    store, profile = make_store(tmp_path)
    try:
        store.record_incoming(profile, "alice", [{"id": "m1", "body": "first", "created": time.time()}])
        store.record_incoming(profile, "alice", [{"id": "m2", "body": "second", "created": time.time() + 1}])
        threads = store.threads(profile)
        assert len(threads) == 1
        assert threads[0]["peer"] == "alice"
        assert threads[0]["unread"] >= 1
        store.mark_thread_read(profile, "alice")
        assert store.threads(profile)[0]["unread"] == 0
    finally:
        store.close()


def test_empty_message_is_rejected(tmp_path):
    store, profile = make_store(tmp_path)
    try:
        for peer, body in (("alice", "   "), ("", "hello")):
            try:
                store.queue_message(profile, peer, body)
            except ValueError:
                continue
            raise AssertionError("invalid message was accepted")
    finally:
        store.close()
