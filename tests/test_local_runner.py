"""Built-in Ollama streaming must work without CORE-CLIENT installed."""
from __future__ import annotations

import io
import json
import os
from urllib.error import URLError

import pytest

from risarms_ui import local


class FakeResponse(io.BytesIO):
    def __init__(self, events):
        super().__init__("".join(json.dumps(event) + "\n" for event in events).encode("utf-8"))
        self.closed_by_stream = False

    def close(self):
        self.closed_by_stream = True
        super().close()


def test_ollama_stream_posts_chat_request_and_yields_events(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse([
            {"message": {"content": "hel"}, "done": False},
            {"message": {"content": "lo"}, "done": False},
            {"message": {"content": ""}, "done": True},
        ])

    monkeypatch.setattr(local, "urlopen", fake_urlopen)
    with local.OllamaChatStream("llama3", [{"role": "user", "content": "hi"}], timeout=7) as stream:
        chunks = [event["message"]["content"] for event in stream]

    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["body"]["stream"] is True
    assert captured["body"]["model"] == "llama3"
    assert captured["timeout"] == 7
    assert "".join(chunks) == "hello"


def test_ollama_stream_reports_malformed_json(monkeypatch):
    def fake_urlopen(request, timeout):
        return io.BytesIO(b"not-json\n")

    monkeypatch.setattr(local, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="malformed"):
        with local.OllamaChatStream("llama3", []) as stream:
            list(stream)


def test_ollama_stream_reports_connection_failure(monkeypatch):
    def fake_urlopen(request, timeout):
        raise URLError("connection refused")

    monkeypatch.setattr(local, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="Cannot reach local Ollama"):
        with local.OllamaChatStream("llama3", []):
            pass


def test_ollama_host_rejects_credentials(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://user:pass@127.0.0.1:11434")
    with pytest.raises(ValueError, match="credentials"):
        local._ollama_host()


def test_local_runner_streams_without_core_client(monkeypatch):
    def fake_urlopen(request, timeout):
        return FakeResponse([
            {"message": {"content": "answer"}, "done": True},
        ])

    monkeypatch.setattr(local, "urlopen", fake_urlopen)
    output = []
    runner = local.LocalRunner()
    result = runner.model("llama3", "question", output.append)

    assert result == "completed"
    assert output == ["answer"]


def test_local_runner_passes_approval_environment(tmp_path):
    root = tmp_path / "ascs"
    (root / "agent").mkdir(parents=True)
    # The runner resolves the venv interpreter per platform; mirror that here.
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("")

    _argv, _cwd, env = local.LocalRunner.command(
        "ascs", root, tmp_path / "home", "task", "", "plan",
        {"RISARMS_APPROVAL_URL": "http://127.0.0.1:1/approve", "RISARMS_APPROVAL_TOKEN": "t"},
    )
    assert env["RISARMS_APPROVAL_URL"] == "http://127.0.0.1:1/approve"
    assert env["RISARMS_APPROVAL_TOKEN"] == "t"
    assert env["AGENT_NONINTERACTIVE"] == "1"
