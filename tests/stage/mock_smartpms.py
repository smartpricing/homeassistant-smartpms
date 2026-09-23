#!/usr/bin/env python3
"""Mock of the SmartPMS public v2 API used by the integration (stage / QA only).

Implements exactly the three endpoints the integration calls, with the same
contract as cb-backend (api/public/v2):

  POST /api/public/v2/login                  {email, password} + X-API-KEY
  GET  /api/public/v2/automations/properties Bearer + X-API-KEY
  GET  /api/public/v2/automations/units      Bearer + X-API-KEY, ?date=Y-m-d

Only the Python standard library is used. Data is synthetic (tests/fixtures).
Nothing here talks to a real SmartPMS environment.

Control endpoints (QA):
  GET /__mock/scenario?login=401&units=500&properties=200&delay=0&units_status=occupied
  GET /__mock/requests   -> JSON list of received requests (secrets redacted)
  GET /__mock/tokens     -> synthetic tokens issued so far (to grep HA logs)
  GET /__mock/reset
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FIXTURES = Path(
    os.environ.get("MOCK_FIXTURES", Path(__file__).resolve().parents[1] / "fixtures")
)
PREFIX = "/api/public/v2"
SENTINEL_BODY = "UPSTREAM-BODY-SENTINEL internal error at /var/www/app.php:42"

EMAIL = os.environ.get("MOCK_EMAIL", "ha-test-user@example.invalid")
PASSWORD = os.environ.get("MOCK_PASSWORD", "PW-SENTINEL-fake-password")
API_KEY = os.environ.get("MOCK_API_KEY", "APIKEY-SENTINEL-fake-key")
# Real Passport access tokens are ~1000 characters; keep the mock realistic.
TOKEN_BYTES = int(os.environ.get("MOCK_TOKEN_BYTES", "740"))
TOKEN_TTL = int(os.environ.get("MOCK_TOKEN_TTL", "86400"))

_lock = threading.Lock()
_state: dict = {}
_requests: list[dict] = []
_tokens: dict[str, float] = {}
_refresh_tokens: list[str] = []


def _reset() -> None:
    with _lock:
        _state.clear()
        _state.update(
            {
                "login": 200,
                "units": 200,
                "properties": 200,
                "delay": 0.0,
                "units_status": None,
            }
        )
        _requests.clear()
        _tokens.clear()
        _refresh_tokens.clear()


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Handler(BaseHTTPRequestHandler):
    """Request handler for the mock API."""

    server_version = "smartpms-mock"
    sys_version = ""

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write("mock: " + (format % args) + "\n")

    # -- helpers -----------------------------------------------------------
    def _send(self, status: int, body) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _record(self, body: dict | None) -> None:
        url = urlparse(self.path)
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        with _lock:
            _requests.append(
                {
                    "ts": round(time.time(), 3),
                    "method": self.command,
                    "path": url.path,
                    "query": parse_qs(url.query),
                    "api_key_ok": self.headers.get("X-API-KEY") == API_KEY,
                    "bearer_ok": token in _tokens,
                    "user_agent": self.headers.get("User-Agent", ""),
                    "body_keys": sorted(body) if isinstance(body, dict) else None,
                }
            )

    def _error(self, status: int) -> None:
        # Error bodies deliberately contain a sentinel and echo a request
        # field, so QA can prove they never reach the HA UI / INFO+ logs.
        self._send(
            status,
            {
                "error": "Http Error",
                "code": status,
                "message": SENTINEL_BODY,
                "echo": {"email": EMAIL},
            },
        )

    def _unauthenticated(self) -> None:
        self._send(
            401,
            {
                "error": "Http Error",
                "code": 401,
                "message": "Not Authenticated",
                "errors": [],
            },
        )

    def _authorized(self) -> bool:
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        expiry = _tokens.get(token)
        return (
            self.headers.get("X-API-KEY") == API_KEY
            and expiry is not None
            and expiry > time.time()
        )

    # -- verbs -------------------------------------------------------------
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            body = None
        self._record(body)
        time.sleep(_state["delay"])
        if urlparse(self.path).path != f"{PREFIX}/login":
            self._send(405, {"message": "Method Not Allowed"})
            return
        if _state["login"] != 200:
            self._error(_state["login"])
            return
        if (
            not isinstance(body, dict)
            or body.get("email") != EMAIL
            or body.get("password") != PASSWORD
            or self.headers.get("X-API-KEY") != API_KEY
        ):
            self._unauthenticated()
            return
        token = secrets.token_urlsafe(TOKEN_BYTES)
        refresh = secrets.token_urlsafe(TOKEN_BYTES // 2)
        now = int(time.time())
        with _lock:
            _tokens[token] = now + TOKEN_TTL
            _refresh_tokens.append(refresh)
        self._send(
            200,
            {
                "success": True,
                "code": 200,
                "status": 200,
                "data": {
                    "type": "Bearer",
                    "token": token,
                    "expiresIn": TOKEN_TTL,
                    "expiresAt": now + TOKEN_TTL,
                    "refreshToken": refresh,
                    "refreshExpiresAt": now + 2678400,
                },
            },
        )

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path.startswith("/__mock/"):
            self._control(url)
            return
        self._record(None)
        time.sleep(_state["delay"])
        if url.path == "/api/public/up":
            self._send(200, {"status": "up"})
        elif not url.path.startswith(PREFIX):
            self._send(404, {"message": "Not Found"})
        elif url.path == f"{PREFIX}/login":
            self._send(405, {"message": "Method Not Allowed"})
        elif not self._authorized():
            self._unauthenticated()
        elif url.path == f"{PREFIX}/automations/properties":
            if _state["properties"] != 200:
                self._error(_state["properties"])
            else:
                self._send(200, _fixture("properties.json"))
        elif url.path == f"{PREFIX}/automations/units":
            if _state["units"] != 200:
                self._error(_state["units"])
                return
            body = _fixture("units.json")
            if _state["units_status"]:
                for unit in body["data"]:
                    unit["status"] = _state["units_status"]
            self._send(200, body)
        else:
            self._send(404, {"message": "Not Found"})

    def _control(self, url) -> None:
        if url.path == "/__mock/reset":
            _reset()
            self._send(200, {"ok": True})
        elif url.path == "/__mock/requests":
            with _lock:
                self._send(200, list(_requests))
        elif url.path == "/__mock/tokens":
            with _lock:
                self._send(
                    200, {"access": list(_tokens), "refresh": list(_refresh_tokens)}
                )
        elif url.path == "/__mock/scenario":
            query = {
                k: v[-1] for k, v in parse_qs(url.query, keep_blank_values=True).items()
            }
            with _lock:
                for key in ("login", "units", "properties"):
                    if key in query:
                        _state[key] = int(query[key])
                if "delay" in query:
                    _state["delay"] = float(query["delay"])
                if "units_status" in query:
                    _state["units_status"] = query["units_status"] or None
                self._send(200, dict(_state))
        else:
            self._send(404, {"message": "Not Found"})


def main() -> None:
    """Run the mock server."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--certfile", help="serve HTTPS with this certificate")
    parser.add_argument("--keyfile")
    args = parser.parse_args()
    _reset()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    if args.certfile:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(args.certfile, args.keyfile)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
    scheme = "https" if args.certfile else "http"
    print(
        f"mock SmartPMS v2 API on {scheme}://{args.host}:{args.port}{PREFIX}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
