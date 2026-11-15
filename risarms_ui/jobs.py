"""Background job orchestration shared by task and service workspaces."""
from __future__ import annotations

import threading
import time
import uuid

from .connection.api import APIError
from .local import LocalRunner


class JobSession:
    def __init__(self, app, on_output, on_done, on_error):
        self.app = app
        self.on_output = on_output
        self.on_done = on_done
        self.on_error = on_error
        self.runner = LocalRunner()
        self.job_id = ""
        self.cancelled = threading.Event()
        self.thread = None
        if hasattr(app, "active_jobs"):
            app.active_jobs.add(self)

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start_server(self, service: str, prompt: str, mode: str, persist: bool = True):
        def work():
            api = self.app.require_remote()
            job = api.request(
                "POST", "/api/jobs",
                {"service": service, "prompt": prompt, "mode": mode, "persist": persist},
                idempotency_key=uuid.uuid4().hex,
            )
            self.job_id = job["id"]
            after = 0
            unknown = 0
            while not self.cancelled.is_set():
                try:
                    data = api.request("GET", f"/api/jobs/{self.job_id}?after={after}")
                    unknown = 0
                except APIError:
                    unknown += 1
                    if unknown >= 3:
                        raise
                    time.sleep(1)
                    continue
                for event in data.get("events", []):
                    after = event["id"]
                    self.on_output(event.get("text", ""))
                if data.get("status") not in ("queued", "starting", "running"):
                    return data.get("status", "completed")
                time.sleep(0.5)
            return "cancelled"

        self._start(work)

    def start_local_model(self, model: str, prompt: str, history=None):
        def work():
            return self.runner.model(model, prompt, self.on_output, history)

        self._start(work)

    def start_local_service(self, service: str, root: str, home: str, prompt: str, model: str, mode: str):
        self.job_id = uuid.uuid4().hex
        extra_env = self.app.approval_env(self.job_id) if hasattr(self.app, "approval_env") else None

        def work():
            return self.runner.service(service, root, home, prompt, model, self.on_output, mode, extra_env)

        self._start(work)

    def _start(self, work):
        def run():
            try:
                result = work()
                if not self.cancelled.is_set():
                    self.on_done(result)
            except Exception as exc:
                if not self.cancelled.is_set():
                    self.on_error(exc)
            finally:
                if hasattr(self.app, "active_jobs"):
                    self.app.active_jobs.discard(self)

        self.thread = threading.Thread(target=run, daemon=True, name="risarms-job")
        self.thread.start()

    def stop(self):
        self.cancelled.set()
        self.runner.stop()
        if self.job_id and self.app.connection.state.name == "ONLINE":
            try:
                self.app.require_remote().request("POST", f"/api/jobs/{self.job_id}/stop")
            except Exception:
                pass

    def wait(self, timeout=None):
        if self.thread:
            self.thread.join(timeout)
