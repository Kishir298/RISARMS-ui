"""HTTPS JSON client for the C.O.R.E. gateway.

Cookies, CSRF tokens and session state stay in memory. Remote redirects,
environment proxies and plaintext non-loopback origins are refused.
"""
from __future__ import annotations

import json
import platform
import socket
import threading
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)

MAX_JSON = 8 * 1024 * 1024
MAX_FILE = 12 * 1024 * 1024


class APIError(ConnectionError):
    def __init__(self, message: str, status: int = 0, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code

    @property
    def authentication_expired(self) -> bool:
        return self.status == 401

    @property
    def forbidden(self) -> bool:
        return self.status in (401, 403)

    @property
    def conflict(self) -> bool:
        return self.status == 409


def validate_origin(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Enter the C.O.R.E. server HTTPS origin.")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Use an http:// (loopback only) or https:// server origin.")
    if parsed.username or parsed.password:
        raise ValueError("Do not put credentials in the server URL.")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Use the server origin without a path, query or fragment.")
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "http" and host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Remote servers require HTTPS.")
    if not host:
        raise ValueError("Server host is missing.")
    _ = parsed.port
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise APIError("Server redirects are refused. Configure the final HTTPS origin.", status=0, code="REDIRECT_REFUSED")


class RemoteAPI:
    def __init__(self, url: str, timeout: float = 10.0):
        self.url = validate_origin(url)
        self.timeout = float(timeout)
        self.cookies = CookieJar()
        self.csrf = ""
        self.profile: dict | None = None
        self._lock = threading.RLock()
        self._opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPCookieProcessor(self.cookies))

    def request(self, method: str, path: str, body=None, idempotency_key: str | None = None):
        if not path.startswith("/api/") or path.startswith("//"):
            raise ValueError("Only CORE API paths are allowed.")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": self.url,
            "X-Core-CSRF": self.csrf,
        }
        if idempotency_key:
            headers["Idempotency-Key"] = str(idempotency_key)
        data = json.dumps(body or {}).encode("utf-8") if method != "GET" else None
        limit = MAX_FILE if path.startswith("/api/files/") else MAX_JSON
        try:
            with self._lock, self._opener.open(
                Request(self.url + path, data=data, method=method, headers=headers), timeout=self.timeout
            ) as response:
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise APIError("Server response exceeded the application limit.", code="RESPONSE_TOO_LARGE")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read(65536))
                message = payload.get("error") or payload.get("detail") or "Request rejected"
                code = payload.get("code", "")
            except (ValueError, AttributeError):
                message, code = "Request rejected", ""
            raise APIError(f"{message} (HTTP {exc.code})", exc.code, code) from exc
        except (URLError, OSError, ValueError) as exc:
            raise APIError("CORE server is unavailable; local data remains available.", code="UNAVAILABLE") from exc

    def health(self):
        return self.request("GET", "/api/health")

    def capabilities(self):
        health = self.health()
        return {
            "api": health.get("api", 1),
            "capabilities": list(health.get("capabilities", [])),
            "ws": bool(health.get("ws", False)),
            "version": health.get("version", ""),
        }

    def login(self, username: str, password: str, remember: bool = True):
        result = self.request("POST", "/api/login", {
            "username": username,
            "password": password,
            "remember_device": bool(remember),
            "device_name": socket.gethostname()[:80] or "Desktop",
            "device_type": "desktop",
            "platform": f"{platform.system()} {platform.release()} ({platform.machine()})"[:120],
        })
        self.csrf = result.get("csrf", "")
        self.profile = result.get("profile")
        return result

    def login_device(self, device_credential: str):
        result = self.request("POST", "/api/login/device", {"credential": device_credential})
        self.csrf = result.get("csrf", "")
        self.profile = result.get("profile")
        return result

    def register(self, username: str, password: str, name: str):
        return self.request("POST", "/api/register", {"username": username, "password": password, "name": name})

    def setup_owner(self, setup_key: str, username: str, password: str, name: str):
        return self.request("POST", "/api/setup", {"setup_key": setup_key, "username": username, "password": password, "name": name})

    def change_password(self, current: str, new: str):
        result = self.request("POST", "/api/password", {"current_password": current, "new_password": new})
        return result

    def logout(self):
        try:
            if self.csrf:
                self.request("POST", "/api/logout")
        except APIError:
            pass
        finally:
            self.cookies.clear()
            self.csrf = ""
            self.profile = None

    def renew(self):
        result = self.request("POST", "/api/session/renew")
        self.csrf = result.get("csrf", self.csrf)
        if result.get("profile"):
            self.profile = result["profile"]
        return result

    def ws_ticket(self):
        return self.request("POST", "/api/ws-ticket")

    def close(self):
        self.cookies.clear()
        self.csrf = ""
        self.profile = None
