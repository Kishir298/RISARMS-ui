"""End-to-end messaging between two clients over real HTTP and WSS.

Unlike the unit tests, this launches an actual ``core web`` process and talks to
it over a loopback socket, so it covers the wire contract: session cookies,
CSRF headers, server-generated ids, idempotent retries, account isolation and
the single-use WebSocket ticket that carries ``message.received``.

It is skipped unless a CORE-HOST checkout is present (``RISARMS_CORE_HOST`` or
the sibling ``CORE/CORE-HOST`` directory), because it needs to start a server.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

import pytest

websockets = pytest.importorskip("websockets")

PASSWORD = "e2e-password-long-enough"
WORKSPACE = Path(__file__).resolve().parents[3]
CORE_HOST = Path(os.environ.get("RISARMS_CORE_HOST", WORKSPACE / "CORE" / "CORE-HOST"))

pytestmark = pytest.mark.skipif(
    not (CORE_HOST / "core" / "web" / "gateway.py").is_file(),
    reason="CORE-HOST checkout unavailable; set RISARMS_CORE_HOST to run this test",
)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def server_interpreter() -> str:
    """Prefer CORE-HOST's own virtualenv, otherwise the running interpreter."""
    for candidate in (CORE_HOST / ".venv" / "bin" / "python", CORE_HOST / ".venv" / "Scripts" / "python.exe"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


class CoreServer:
    """A real ``core web`` process with its event channel enabled."""

    def __init__(self, data_dir: Path):
        self.http_port = free_port()
        self.ws_port = free_port()
        interpreter = server_interpreter()
        env = {**os.environ, "CORE_WS_PORT": str(self.ws_port), "PYTHONUNBUFFERED": "1"}
        self.process = subprocess.Popen(
            [interpreter, "-m", "core", "web", "--port", str(self.http_port), "--data", str(data_dir)],
            cwd=str(CORE_HOST),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.output: list[str] = []
        self.setup_key: str | None = None
        self._pump = threading.Thread(target=self._read_output, daemon=True)
        self._pump.start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.http_port}"

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.ws_port}/api/ws"

    def _read_output(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.output.append(line.rstrip())
            if "First-run setup key:" in line:
                self.setup_key = line.split("First-run setup key:", 1)[1].strip()

    def wait_until_ready(self, timeout: float = 45.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise AssertionError("CORE-HOST exited during startup:\n" + "\n".join(self.output))
            try:
                with urllib.request.urlopen(self.base + "/api/health", timeout=1) as response:
                    if response.status == 200:
                        return
            except OSError:
                time.sleep(0.2)
        raise AssertionError("CORE-HOST did not become ready:\n" + "\n".join(self.output))

    def stop(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()


class CoreClient:
    """An independent client instance: its own cookie jar and CSRF token."""

    def __init__(self, server: CoreServer):
        self.server = server
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""
        self.profile: dict = {}

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        # The server rejects a POST without a JSON body, so an empty object is
        # sent for bodyless actions exactly like the real client does.
        if body is None and method != "GET":
            body = {}
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.server.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if self.csrf:
            request.add_header("X-Core-CSRF", self.csrf)
        try:
            with self.opener.open(request, timeout=15) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def sign_in(self, username: str) -> "CoreClient":
        status, body = self.call("POST", "/api/login", {"username": username, "password": PASSWORD, "remember_device": True})
        assert status == 200, (status, body)
        self.csrf = body["csrf"]
        self.profile = body["profile"]
        return self


@pytest.fixture()
def server(tmp_path):
    probe = subprocess.run(
        [server_interpreter(), "-c", "import yaml, websockets"], capture_output=True, text=True
    )
    if probe.returncode != 0:
        pytest.skip("CORE-HOST runtime (PyYAML + websockets) is unavailable in this interpreter")
    instance = CoreServer(tmp_path / "core-data")
    try:
        instance.wait_until_ready()
        assert instance.setup_key, "the launcher did not print a setup key:\n" + "\n".join(instance.output)
        yield instance
    finally:
        instance.stop()


def bootstrap(server: CoreServer) -> tuple[CoreClient, dict[str, CoreClient]]:
    """Create the owner plus two approved members, each with its own session."""
    owner = CoreClient(server)
    status, body = owner.call(
        "POST",
        "/api/setup",
        {"setup_key": server.setup_key, "username": "owner", "password": PASSWORD, "name": "Owner"},
    )
    assert status == 201, (status, body)
    owner.sign_in("owner")
    # Administrative routes are locked until the owner re-enters the password.
    status, body = owner.call("POST", "/api/reauth", {"password": PASSWORD})
    assert status == 200, (status, body)

    members: dict[str, CoreClient] = {}
    for name in ("alice", "bob"):
        status, body = owner.call(
            "POST", "/api/profiles", {"username": name, "password": PASSWORD, "name": name.title()}
        )
        assert status == 201, (status, body)
        members[name] = CoreClient(server).sign_in(name)
    return owner, members


def test_two_clients_exchange_messages_with_wss_push_and_idempotent_retry(server):
    owner, members = bootstrap(server)
    alice, bob = members["alice"], members["bob"]

    status, ticket_body = alice.call("POST", "/api/ws-ticket")
    assert status == 200, (status, ticket_body)
    ticket = ticket_body["ticket"]

    async def listen_for_push() -> dict:
        async with websockets.connect(f"{server.ws_url}?ticket={ticket}", open_timeout=15) as channel:
            # Bob sends while Alice is connected, so the event must arrive live.
            status, sent = bob.call(
                "POST",
                "/api/messages",
                {"recipient": "alice", "body": "hello over the wire", "client_id": "e2e-client-0001"},
            )
            assert status == 201, (status, sent)
            retry = bob.call(
                "POST",
                "/api/messages",
                {"recipient": "alice", "body": "hello over the wire", "client_id": "e2e-client-0001"},
            )
            assert retry[0] == 201 and retry[1]["id"] == sent["id"], "retry duplicated the message"
            # The channel greets with `channel.ready`; the push is the event
            # that carries the message identity.
            deadline = asyncio.get_running_loop().time() + 15
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                raw = await asyncio.wait_for(channel.recv(), timeout=max(remaining, 0.1))
                event = json.loads(raw)
                if event.get("type") == "message.received":
                    event["_sent_id"] = sent["id"]
                    return event

    event = asyncio.run(listen_for_push())
    assert event["type"] == "message.received"
    assert event["message_id"] == event["_sent_id"]
    assert event["from"] == bob.profile["id"]

    status, inbox = alice.call("GET", f"/api/messages?peer={bob.profile['id']}")
    assert status == 200
    assert [message["body"] for message in inbox["messages"]] == ["hello over the wire"]
    assert inbox["messages"][0]["sender"] == bob.profile["id"]
    assert inbox["messages"][0]["delivered"] is True

    # The owner is not a participant, so the conversation is invisible to them.
    status, owner_view = owner.call("GET", f"/api/messages?peer={alice.profile['id']}")
    assert status == 200 and owner_view["messages"] == []
    assert owner.call("GET", "/api/messages")[1]["threads"] == []

    # Read acknowledgement is recorded for the recipient only.
    message_id = inbox["messages"][0]["id"]
    assert alice.call("POST", f"/api/messages/{message_id}/read")[0] == 200
    threads = alice.call("GET", "/api/messages")[1]["threads"]
    assert [thread["peer"] for thread in threads] == [bob.profile["id"]]
    assert threads[0]["unread"] == 0
    # The sender is not the recipient: marking it read must be refused (the
    # server answers 400 "not found" so identifiers are not probed).
    assert bob.call("POST", f"/api/messages/{message_id}/read")[0] in (400, 403)


def test_websocket_ticket_is_single_use(server):
    _owner, members = bootstrap(server)
    ticket = members["alice"].call("POST", "/api/ws-ticket")[1]["ticket"]

    async def consume_twice() -> None:
        # The first connection consumes the ticket.
        async with websockets.connect(f"{server.ws_url}?ticket={ticket}", open_timeout=15):
            pass
        # A reused ticket completes the handshake but is closed immediately,
        # so the rejection has to be observed on the channel, not on connect.
        async with websockets.connect(f"{server.ws_url}?ticket={ticket}", open_timeout=15) as reused:
            with pytest.raises(websockets.exceptions.ConnectionClosed):
                await asyncio.wait_for(reused.recv(), timeout=5)

    asyncio.run(consume_twice())
