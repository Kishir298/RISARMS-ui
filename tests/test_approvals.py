"""Loopback approval broker contract."""
from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from risarms_ui.approvals import ApprovalBroker, approval_signature


def post(url, token, payload):
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-RISARMS-Approval-Token": token},
    )
    with urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read(8192))


def test_broker_returns_ui_decision_and_env():
    seen = []
    broker = ApprovalBroker(lambda payload: seen.append(payload) or {"decision": "allow_once"})
    try:
        env = broker.env("job-1")
        assert env["RISARMS_APPROVAL_JOB"] == "job-1"
        assert env["AGENT_APPROVAL"] == "1"
        status, body = post(env["RISARMS_APPROVAL_URL"], env["RISARMS_APPROVAL_TOKEN"], {"action": "run"})
        assert status == 200
        assert body["decision"] == "allow_once"
        assert seen == [{"action": "run"}]
    finally:
        broker.close()


def test_broker_rejects_missing_token():
    broker = ApprovalBroker(lambda _payload: {"decision": "allow_once"})
    try:
        with pytest.raises(HTTPError) as exc:
            post(broker.env()["RISARMS_APPROVAL_URL"], "wrong", {"action": "run"})
        assert exc.value.code == 403
    finally:
        broker.close()


def test_broker_denies_when_ui_fails():
    broker = ApprovalBroker(lambda _payload: (_ for _ in ()).throw(RuntimeError("ui gone")))
    try:
        status, body = post(broker.env()["RISARMS_APPROVAL_URL"], broker.token, {"action": "run"})
        assert status == 200
        assert body["decision"] == "deny"
    finally:
        broker.close()


def test_session_signature_scopes_tool_arguments_and_ignores_job():
    first = {
        "service": "asis", "action": "HIGH", "command": "write_file /a",
        "workspace": "/workspace", "details": {"job": "one", "arguments": {"path": "/a"}},
    }
    same_scope = {
        **first, "details": {"job": "two", "arguments": {"path": "/a"}},
    }
    other_path = {
        **first, "details": {"job": "one", "arguments": {"path": "/b"}},
    }
    assert approval_signature(first) == approval_signature(same_scope)
    assert approval_signature(first) != approval_signature(other_path)
