#!/usr/bin/env python3
"""Stage end-to-end run of the SmartPMS integration in a real Home Assistant.

"Stage" for this repository is a throwaway, local Home Assistant container
with the branch under test mounted at /config/custom_components/smartpms.
The SmartPMS API is replaced by tests/stage/mock_smartpms.py (synthetic
data, fake credentials): nothing in this script talks to a real SmartPMS
environment, and every container / network it creates is removed at the end
(unless --keep).

Phases
  main  HA + mock over HTTP: onboarding, config flow, sensors, refresh,
        API errors, options, reconfigure, reauth, setup failure,
        diagnostics, anonymous access, log leak checks, stalled API.
  tls   A second HA with the UNMODIFIED component; `pms-api.smartness.com`
        resolves (Docker network alias) to a mock with a self-signed
        certificate. The config flow must fail with cannot_connect and the
        mock must receive no request (credentials never sent).

Checks are FUNC (behaviour that must hold on every branch) or SEC (security
expectations of epic SPCWR-141; several fail on the unfixed default branch).

Runtimes
  --runtime docker (default)  ghcr.io/home-assistant/home-assistant:<tag>
                              container + mock container on a private network.
  --runtime host              the same Home Assistant release run as a local
                              process from the test venv (`python -m
                              homeassistant`), mock as a local
                              process; the TLS phase points the stage copy at
                              https://localhost with a self-signed certificate.
                              Use it when Docker is unavailable.

Usage (from a checkout of the branch under test, test venv active):
  python tests/stage/e2e.py [--src .] [--runtime docker|host]
                              [--ha-image IMAGE] [--keep]
                              [--skip-tls] [--skip-slow] [--report FILE]
Exit code: 0 if every FUNC and SEC check passed, 1 if a FUNC check failed,
2 if only SEC checks failed.

Requires: docker, openssl, and Python with aiohttp (the test venv).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp

HA_IMAGE = "ghcr.io/home-assistant/home-assistant:2026.9.2"
MOCK_IMAGE = "python:3.14-alpine"
REAL_BASE = "https://pms-api.smartness.com/api/public/v2"

# Fresh synthetic credentials for every run (handed to the mock through its
# environment); the SENTINEL prefix makes any leak easy to spot.
EMAIL = "ha-test-user@example.invalid"
PASSWORD = "PW-SENTINEL-" + secrets.token_hex(12)
API_KEY = "APIKEY-SENTINEL-" + secrets.token_hex(12)
SENTINEL = "UPSTREAM-BODY-SENTINEL"
MOCK_ENV = {"MOCK_EMAIL": EMAIL, "MOCK_PASSWORD": PASSWORD, "MOCK_API_KEY": API_KEY}
IMAGE_RE = re.compile(r"ghcr\.io/home-assistant/home-assistant:[A-Za-z0-9._-]{1,64}")

ROOMS = [f"sensor.test_hotel_alpha_room_{n}" for n in (1, 2, 3)]
CONFIGURATION_YAML = """\
homeassistant:
  name: SmartPMS stage
  time_zone: Europe/Rome
  country: IT
  unit_system: metric
  currency: EUR
http:
  server_host: {host}
  server_port: {port}
api:
config:
diagnostics:
onboarding:
logger:
  default: info
  logs:
    custom_components.smartpms: debug
"""


@dataclass
class Report:
    results: list[dict] = field(default_factory=list)

    def check(self, kind: str, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append(
            {"kind": kind, "name": name, "ok": bool(ok), "detail": detail}
        )
        print(
            f"[{'PASS' if ok else 'FAIL'}] {kind:4} {name}"
            + (f"  ({detail})" if detail and not ok else ""),
            flush=True,
        )
        return ok

    def summary(self) -> dict:
        out = {}
        for kind in ("FUNC", "SEC"):
            rows = [r for r in self.results if r["kind"] == kind]
            out[kind] = {
                "passed": sum(r["ok"] for r in rows),
                "failed": sum(not r["ok"] for r in rows),
            }
        return out


def sh(*args: str, check: bool = True) -> str:
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **MOCK_ENV},
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])}... failed: {proc.stderr[-400:]}")
    return proc.stdout.strip()


class HA:
    """Minimal REST + websocket client for a local Home Assistant."""

    def __init__(self, session: aiohttp.ClientSession, base: str) -> None:
        self.session = session
        self.base = base
        self.token: str | None = None

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def wait_up(self, timeout: float = 900) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                async with self.session.get(f"{self.base}/api/onboarding") as r:
                    if r.status == 200:
                        return
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(2)
        raise TimeoutError("Home Assistant did not start")

    async def onboard(self) -> None:
        client_id = f"{self.base}/"
        async with self.session.post(
            f"{self.base}/api/onboarding/users",
            json={
                "client_id": client_id,
                "name": "QA",
                "username": "qa",
                "password": secrets.token_urlsafe(18),
                "language": "en",
            },
        ) as r:
            r.raise_for_status()
            code = (await r.json())["auth_code"]
        async with self.session.post(
            f"{self.base}/auth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
            },
        ) as r:
            r.raise_for_status()
            self.token = (await r.json())["access_token"]

    async def get(self, path: str, auth: bool = True):
        async with self.session.get(
            f"{self.base}{path}", headers=self.headers if auth else {}
        ) as r:
            text = await r.text()
            return r.status, text

    async def post(self, path: str, body: dict | None = None):
        async with self.session.post(
            f"{self.base}{path}", json=body or {}, headers=self.headers
        ) as r:
            text = await r.text()
            return r.status, text

    async def post_json(self, path: str, body: dict | None = None) -> dict:
        status, text = await self.post(path, body)
        if status >= 400:
            raise RuntimeError(f"POST {path} -> {status}: {text[:300]}")
        return json.loads(text) if text else {}

    async def state(self, entity_id: str) -> str | None:
        status, text = await self.get(f"/api/states/{entity_id}")
        return json.loads(text)["state"] if status == 200 else None

    async def entry(self) -> dict:
        _, text = await self.get("/api/config/config_entries/entry?domain=smartpms")
        entries = json.loads(text)
        return entries[0] if entries else {}

    async def flows_in_progress(self) -> list[dict]:
        ws_url = self.base.replace("http", "ws", 1) + "/api/websocket"
        async with self.session.ws_connect(ws_url) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": self.token})
            await ws.receive_json()
            await ws.send_json({"id": 1, "type": "config_entries/flow/progress"})
            while True:
                msg = await ws.receive_json()
                if msg.get("id") == 1:
                    return msg.get("result") or []

    async def loaded_version(self) -> str | None:
        ws_url = self.base.replace("http", "ws", 1) + "/api/websocket"
        async with self.session.ws_connect(ws_url) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": self.token})
            await ws.receive_json()
            await ws.send_json(
                {"id": 1, "type": "manifest/get", "integration": "smartpms"}
            )
            while True:
                msg = await ws.receive_json()
                if msg.get("id") == 1:
                    return (msg.get("result") or {}).get("version")

    async def refresh(self, wait: float = 11.5) -> None:
        """Force a coordinator refresh (debouncer cooldown is 10 s)."""
        await asyncio.sleep(wait)
        await self.post(
            "/api/services/homeassistant/update_entity", {"entity_id": ROOMS}
        )
        await asyncio.sleep(2)


class Stage:
    def __init__(self, args) -> None:
        self.args = args
        self.host_runtime = args.runtime == "host"
        self.src = Path(args.src).resolve()
        self.run_id = f"smartpms-e2e-{os.getpid()}"
        self.net = self.run_id
        self.workdir = Path(tempfile.mkdtemp(prefix="smartpms-stage-"))
        self.containers: list[str] = []
        self.procs: list[subprocess.Popen] = []

    # -- infrastructure --------------------------------------------------
    def docker_run(self, name: str, *args: str) -> None:
        sh("docker", "run", "-d", "--name", name, "--network", self.net, *args)
        self.containers.append(name)

    def spawn(self, name: str, *cmd: str, cwd: Path | None = None) -> None:
        log = open(self.workdir / f"{name}.log", "w")
        # Never run from the checkout: Python would import the checkout's own
        # custom_components/smartpms (real production URL) instead of the
        # stage copy. PYTHONSAFEPATH keeps cwd off sys.path as well.
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONSAFEPATH"] = "1"
        env.update(MOCK_ENV)
        self.procs.append(
            subprocess.Popen(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=cwd or self.workdir,
                env=env,
            )
        )

    def component_copy(self, dest: Path, base_url: str | None, port: int) -> None:
        target = dest / "custom_components" / "smartpms"
        shutil.copytree(
            self.src / "custom_components" / "smartpms",
            target,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        manifest_path = target / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["version"] = f"{manifest['version']}-stage"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        self.stage_version = manifest["version"]
        if base_url:
            const = target / "const.py"
            text = const.read_text()
            new, count = re.subn(
                r'API_BASE_URL = "[^"]+"', f'API_BASE_URL = "{base_url}"', text
            )
            if count != 1:
                raise RuntimeError("could not patch API_BASE_URL in stage copy")
            const.write_text(new)
        host = "127.0.0.1" if self.host_runtime else "0.0.0.0"
        inner_port = port if self.host_runtime else 8123
        (dest / "configuration.yaml").write_text(
            CONFIGURATION_YAML.format(host=host, port=inner_port)
        )

    def start_mock(self, name: str, alias: str, port: int, tls: bool) -> None:
        if self.host_runtime:
            cmd = [
                sys.executable,
                str(self.src / "tests/stage/mock_smartpms.py"),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ]
            if tls:
                cmd += [
                    "--certfile",
                    str(self.workdir / "tls" / "cert.pem"),
                    "--keyfile",
                    str(self.workdir / "tls" / "key.pem"),
                ]
            self.spawn(name, *cmd)
            time.sleep(1)
            return
        args = [
            "-v",
            f"{self.src / 'tests' / 'stage'}:/mock:ro",
            "-v",
            f"{self.src / 'tests' / 'fixtures'}:/fixtures:ro",
            "-e",
            "MOCK_FIXTURES=/fixtures",
            "--network-alias",
            alias,
        ]
        for key in MOCK_ENV:
            args += ["-e", key]  # value taken from this process' environment
        cmd = ["python", "/mock/mock_smartpms.py", "--port", str(port)]
        if tls:
            args += ["-v", f"{self.workdir / 'tls'}:/tls:ro"]
            cmd += ["--certfile", "/tls/cert.pem", "--keyfile", "/tls/key.pem"]
        else:
            args += ["-p", f"127.0.0.1:{self.args.mock_port}:{port}"]
        self.docker_run(name, *args, MOCK_IMAGE, *cmd)

    def mock_log(self, name: str) -> str:
        if self.host_runtime:
            path = self.workdir / f"{name}.log"
            return path.read_text(errors="replace") if path.exists() else ""
        return sh("docker", "logs", name, check=False)

    def start_ha(self, name: str, config: Path, port: int) -> None:
        if self.host_runtime:
            # Without --skip-pip: on first boot Home Assistant installs the
            # exact requirement pins of its default integrations (frontend,
            # backup, ...) into this venv, like the container image ships them.
            self.spawn(
                name,
                sys.executable,
                "-m",
                "homeassistant",
                "-c",
                str(config),
                cwd=config,
            )
            return
        self.docker_run(
            name,
            "-p",
            f"127.0.0.1:{port}:8123",
            "-v",
            f"{config}:/config",
            "-e",
            "TZ=Europe/Rome",
            self.args.ha_image,
        )

    def teardown(self) -> None:
        if self.args.keep:
            print(f"--keep: containers {self.containers}, dir {self.workdir}")
            return
        for proc in self.procs:
            proc.terminate()
            try:
                proc.wait(20)
            except subprocess.TimeoutExpired:
                proc.kill()
        for name in self.containers:
            sh("docker", "rm", "-f", name, check=False)
        if not self.host_runtime:
            sh("docker", "network", "rm", self.net, check=False)
        shutil.rmtree(self.workdir, ignore_errors=True)


def log_lines(config: Path) -> list[str]:
    path = config / "home-assistant.log"
    return path.read_text(errors="replace").splitlines() if path.exists() else []


def visible(lines: list[str]) -> str:
    """Log lines a user sees without debug logging (INFO and above)."""
    return "\n".join(line for line in lines if " DEBUG " not in line)


async def main_phase(stage: Stage, report: Report, http: aiohttp.ClientSession):
    config = stage.workdir / "main"
    if stage.host_runtime:
        mock_base = f"http://127.0.0.1:{stage.args.mock_port}/api/public/v2"
        mock_port = stage.args.mock_port
    else:
        mock_base, mock_port = "http://smartpms-mock:8080/api/public/v2", 8080
    stage.component_copy(config, mock_base, stage.args.ha_port)
    stage.start_mock(f"{stage.run_id}-mock", "smartpms-mock", mock_port, tls=False)
    stage.start_ha(f"{stage.run_id}-ha", config, stage.args.ha_port)
    ha = HA(http, f"http://127.0.0.1:{stage.args.ha_port}")
    mock = f"http://127.0.0.1:{stage.args.mock_port}"
    await ha.wait_up()
    await ha.onboard()
    loaded = await ha.loaded_version()
    if not report.check(
        "FUNC",
        "HA loaded the stage copy of the integration",
        loaded == stage.stage_version,
        f"{loaded} != {stage.stage_version}",
    ):
        raise SystemExit("aborting: wrong integration copy loaded")

    async def scenario(**kw) -> None:
        q = "&".join(f"{k}={v}" for k, v in kw.items())
        async with http.get(f"{mock}/__mock/scenario?{q}") as r:
            r.raise_for_status()

    # 1. config flow
    flow = await ha.post_json(
        "/api/config/config_entries/flow", {"handler": "smartpms"}
    )
    report.check(
        "FUNC",
        "config flow starts on the credentials form",
        flow.get("step_id") == "user",
        str(flow.get("step_id")),
    )
    flow = await ha.post_json(
        f"/api/config/config_entries/flow/{flow['flow_id']}",
        {"email": EMAIL, "password": PASSWORD, "api_key": API_KEY},
    )
    async with http.get(f"{mock}/__mock/requests") as r:
        seen = await r.json()
    if not report.check(
        "FUNC",
        "the login went to the mock API",
        any(q["method"] == "POST" for q in seen),
        f"{len(seen)} requests at the mock",
    ):
        raise SystemExit("aborting: login did not reach the mock")
    report.check(
        "FUNC",
        "valid credentials lead to property selection",
        flow.get("step_id") == "property",
        str(flow.get("errors")),
    )
    options = json.dumps(flow.get("data_schema"))
    report.check(
        "FUNC",
        "property selector lists both synthetic properties",
        "Test Hotel Alpha (3 units)" in options
        and "Test Hotel Beta (1 units)" in options,
    )
    flow = await ha.post_json(
        f"/api/config/config_entries/flow/{flow['flow_id']}",
        {"property_id": 101, "property_name": "Test Hotel Alpha"},
    )
    report.check(
        "FUNC",
        "entry created",
        flow.get("type") == "create_entry",
        str(flow.get("type")),
    )
    await asyncio.sleep(3)
    entry = await ha.entry()
    entry_id = entry.get("entry_id")
    report.check(
        "FUNC", "entry loaded", entry.get("state") == "loaded", str(entry.get("state"))
    )

    # 2. sensors
    states = [await ha.state(e) for e in ROOMS]
    report.check(
        "FUNC",
        "3 unit sensors free/occupied/blocked",
        states == ["free", "occupied", "blocked"],
        str(states),
    )
    status, _ = await ha.get("/api/states/sensor.test_hotel_beta_suite")
    report.check(
        "FUNC", "units of other properties are not exposed", status == 404, str(status)
    )

    # 3. refresh picks up changes
    await scenario(units_status="occupied")
    await ha.refresh(wait=1)
    states = [await ha.state(e) for e in ROOMS]
    report.check(
        "FUNC", "refresh updates states", states == ["occupied"] * 3, str(states)
    )
    await scenario(units_status="")

    # 4. API error -> unavailable, then recovery
    await scenario(units=500)
    await ha.refresh()
    report.check(
        "FUNC",
        "API 500 makes sensors unavailable",
        await ha.state(ROOMS[0]) == "unavailable",
    )
    await scenario(units=200)
    await ha.refresh()
    recovered = await ha.state(ROOMS[0])
    report.check(
        "FUNC",
        "sensors recover after the API recovers",
        recovered == "free",
        str(recovered),
    )

    # 5. options flow
    opt = await ha.post_json(
        "/api/config/config_entries/options/flow", {"handler": entry_id}
    )
    status, _ = await ha.post(
        f"/api/config/config_entries/options/flow/{opt['flow_id']}",
        {"scan_interval": 30},
    )
    report.check("FUNC", "options reject a 30 s interval", status == 400, str(status))
    opt = await ha.post_json(
        f"/api/config/config_entries/options/flow/{opt['flow_id']}",
        {"scan_interval": 120},
    )
    report.check("FUNC", "options accept 120 s", opt.get("type") == "create_entry")
    await asyncio.sleep(3)

    # 6. reconfigure (empty password keeps the stored one)
    flow = await ha.post_json(
        "/api/config/config_entries/flow", {"handler": "smartpms", "entry_id": entry_id}
    )
    report.check(
        "FUNC",
        "reconfigure form opens",
        flow.get("step_id") == "reconfigure",
        str(flow.get("step_id")),
    )
    report.check(
        "SEC",
        "reconfigure form does not send the stored API key",
        API_KEY not in json.dumps(flow),
    )
    report.check("SEC", "reconfigure API key field is masked", _masked(flow, "api_key"))
    flow = await ha.post_json(
        f"/api/config/config_entries/flow/{flow['flow_id']}",
        {"email": EMAIL, "password": "", "api_key": API_KEY},
    )
    report.check(
        "FUNC",
        "reconfigure with empty password validates",
        flow.get("step_id") == "reconfigure_property",
        str(flow.get("errors")),
    )
    flow = await ha.post_json(
        f"/api/config/config_entries/flow/{flow['flow_id']}",
        {"property_id": 101, "property_name": "Test Hotel Alpha"},
    )
    report.check(
        "FUNC",
        "reconfigure completes",
        flow.get("reason") == "reconfigure_successful",
        str(flow.get("reason")),
    )
    await asyncio.sleep(4)

    # 7. reauth when the credentials stop working
    await scenario(login=401, units=401)
    await ha.refresh()
    flows = [
        f
        for f in await ha.flows_in_progress()
        if f.get("handler") == "smartpms"
        and f.get("context", {}).get("source") == "reauth"
    ]
    report.check(
        "FUNC",
        "revoked credentials start a reauth flow",
        len(flows) == 1,
        f"{len(flows)} reauth flows",
    )
    await scenario(login=200, units=200)
    if flows:
        status, text = await ha.get(
            f"/api/config/config_entries/flow/{flows[0]['flow_id']}"
        )
        form = json.loads(text)
        report.check(
            "SEC", "reauth form does not send the stored API key", API_KEY not in text
        )
        report.check("SEC", "reauth API key field is masked", _masked(form, "api_key"))
        done = await ha.post_json(
            f"/api/config/config_entries/flow/{flows[0]['flow_id']}",
            {"email": EMAIL, "password": PASSWORD, "api_key": API_KEY},
        )
        report.check(
            "FUNC",
            "reauth completes",
            done.get("reason") == "reauth_successful",
            str(done.get("reason")),
        )
        await asyncio.sleep(4)
        report.check(
            "FUNC",
            "entry loaded after reauth",
            (await ha.entry()).get("state") == "loaded",
        )

    # 8. setup failure (reason shown in the UI)
    await scenario(login=500)
    await ha.post(f"/api/config/config_entries/entry/{entry_id}/reload")
    await asyncio.sleep(3)
    entry = await ha.entry()
    report.check(
        "FUNC",
        "login 500 at setup -> setup_retry",
        entry.get("state") == "setup_retry",
        str(entry.get("state")),
    )
    report.check(
        "SEC",
        "setup error shown in the UI has no upstream body",
        SENTINEL not in json.dumps(entry),
    )
    await scenario(login=200)
    await ha.post(f"/api/config/config_entries/entry/{entry_id}/reload")
    await asyncio.sleep(4)
    report.check(
        "FUNC",
        "entry recovers after reload",
        (await ha.entry()).get("state") == "loaded",
    )

    # 9. stalled API: bounded by a request timeout
    if not stage.args.skip_slow:
        await scenario(delay=45)
        started = time.monotonic()
        await ha.refresh(wait=11)
        while (await ha.state(ROOMS[0])) != "unavailable" and (
            time.monotonic() - started < 70
        ):
            await asyncio.sleep(2)
        elapsed = time.monotonic() - started
        report.check(
            "SEC",
            "a stalled API is abandoned within 45 s",
            await ha.state(ROOMS[0]) == "unavailable" and elapsed < 60,
            f"{elapsed:.0f}s",
        )
        await scenario(delay=0)
        await ha.refresh()

    # 10. diagnostics + anonymous access
    status, text = await ha.get(f"/api/diagnostics/config_entry/{entry_id}")
    diag = json.loads(text) if status == 200 else {}
    cfg = diag.get("data", {}).get("config_entry", {})
    report.check("FUNC", "diagnostics download works", status == 200, str(status))
    report.check(
        "SEC",
        "diagnostics redact email/password/api_key",
        all(cfg.get(k) == "**REDACTED**" for k in ("email", "password", "api_key")),
        str(cfg),
    )
    async with http.get(f"{mock}/__mock/tokens") as r:
        tokens = await r.json()
    issued = [t[:32] for t in tokens["access"] + tokens["refresh"]]
    report.check(
        "SEC",
        "diagnostics contain no credential or token",
        not any(s in text for s in (PASSWORD, API_KEY, EMAIL, *issued)),
    )
    for path in (
        "/api/states",
        f"/api/diagnostics/config_entry/{entry_id}",
        "/api/config/config_entries/entry",
    ):
        status, _ = await ha.get(path, auth=False)
        report.check(
            "FUNC",
            f"anonymous GET {path.split('/')[2]}... -> 401",
            status == 401,
            str(status),
        )

    # 11. what the integration sent to the API
    async with http.get(f"{mock}/__mock/requests") as r:
        requests = await r.json()
    paths = {(q["method"], q["path"]) for q in requests}
    report.check(
        "FUNC",
        "only login (POST) + properties/units (GET) are called",
        paths
        <= {
            ("POST", "/api/public/v2/login"),
            ("GET", "/api/public/v2/automations/properties"),
            ("GET", "/api/public/v2/automations/units"),
        },
        str(sorted(paths)),
    )
    report.check(
        "FUNC",
        "login body has exactly email + password",
        all(
            q["body_keys"] == ["email", "password"]
            for q in requests
            if q["method"] == "POST"
        ),
    )
    report.check(
        "FUNC", "every call carries the API key", all(q["api_key_ok"] for q in requests)
    )
    logins = sum(q["method"] == "POST" for q in requests)
    report.check(
        "FUNC", "logins stay bounded (no retry storm)", logins <= 12, f"{logins} logins"
    )

    # 12. logs
    lines = log_lines(config)
    text = "\n".join(lines)
    report.check("SEC", "HA log has no password", PASSWORD not in text)
    report.check("SEC", "HA log has no API key", API_KEY not in text)
    leaked = [t for t in issued if t in text]
    report.check(
        "SEC",
        "HA log has no access/refresh token (even at DEBUG)",
        not leaked,
        f"{len(leaked)} of {len(issued)} tokens found",
    )
    smartpms = "\n".join(line for line in lines if "custom_components.smartpms" in line)
    report.check(
        "SEC", "integration log lines have no account e-mail", EMAIL not in smartpms
    )
    report.check(
        "SEC",
        "no upstream error body at INFO+ in the HA log",
        SENTINEL not in visible(lines),
    )
    report.check("FUNC", "integration logged no traceback", "Traceback" not in smartpms)

    # 13. the read-only smoke script in `ha` mode against this instance
    secrets_file = stage.workdir / "secrets.txt"
    secrets_file.write_text(f"{PASSWORD}\n{API_KEY}\n")
    proc = subprocess.run(
        [str(stage.src / "scripts" / "smoke" / "smoke.sh"), "ha", ha.base],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "HA_TOKEN": ha.token or "",
            "SMARTPMS_SECRETS_FILE": str(secrets_file),
        },
    )
    failed = [line for line in proc.stdout.splitlines() if line.startswith("[FAIL]")]
    report.check(
        "SEC",
        "smoke.sh ha: no FAIL",
        proc.returncode == 0 and not failed,
        "; ".join(failed) or proc.stdout[-200:],
    )


def _masked(form: dict, name: str) -> bool:
    for fld in form.get("data_schema") or []:
        if fld.get("name") == name:
            return (fld.get("selector") or {}).get("text", {}).get("type") == "password"
    return False


async def tls_phase(stage: Stage, report: Report, http: aiohttp.ClientSession):
    tls = stage.workdir / "tls"
    tls.mkdir()
    host = "localhost" if stage.host_runtime else "pms-api.smartness.com"
    sh(
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "ec",
        "-pkeyopt",
        "ec_paramgen_curve:prime256v1",
        "-nodes",
        "-days",
        "1",
        "-subj",
        f"/CN={host}",
        "-addext",
        f"subjectAltName=DNS:{host}",
        "-keyout",
        str(tls / "key.pem"),
        "-out",
        str(tls / "cert.pem"),
    )
    os.chmod(tls / "key.pem", 0o644)  # read by the mock container user
    mock_name = f"{stage.run_id}-tls-mock"
    ha_name = f"{stage.run_id}-tls-ha"
    ha_port = stage.args.ha_port + 1
    config = stage.workdir / "tls-ha"
    if stage.host_runtime:
        tls_port = stage.args.mock_port + 1
        stage.start_mock(mock_name, host, tls_port, tls=True)
        # Same code, base URL pointed at the untrusted local HTTPS server.
        stage.component_copy(
            config, f"https://localhost:{tls_port}/api/public/v2", ha_port
        )
    else:
        stage.start_mock(mock_name, host, 443, tls=True)
        stage.component_copy(config, None, ha_port)  # unmodified production URL
    stage.start_ha(ha_name, config, ha_port)
    ha = HA(http, f"http://127.0.0.1:{ha_port}")
    await ha.wait_up()

    await ha.onboard()
    loaded = await ha.loaded_version()
    if not report.check(
        "FUNC",
        "TLS phase: HA loaded the stage copy",
        loaded == stage.stage_version,
        f"{loaded} != {stage.stage_version}",
    ):
        return
    if not stage.host_runtime:
        mock_ip = sh(
            "docker",
            "inspect",
            "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            mock_name,
        )
        resolved = sh(
            "docker",
            "exec",
            ha_name,
            "python3",
            "-c",
            "import socket;print(socket.gethostbyname('pms-api.smartness.com'))",
        )
        if not report.check(
            "FUNC",
            "TLS phase: API host resolves to the local mock",
            resolved == mock_ip,
            f"{resolved} != {mock_ip}",
        ):
            return  # never risk a request to the real production API
    flow = await ha.post_json(
        "/api/config/config_entries/flow", {"handler": "smartpms"}
    )
    flow = await ha.post_json(
        f"/api/config/config_entries/flow/{flow['flow_id']}",
        {"email": EMAIL, "password": PASSWORD, "api_key": API_KEY},
    )
    report.check(
        "SEC",
        "untrusted certificate -> cannot_connect",
        (flow.get("errors") or {}).get("base") == "cannot_connect",
        str(flow.get("errors")),
    )
    report.check(
        "SEC",
        "no request reached the untrusted server",
        "POST /api/public/v2/login" not in stage.mock_log(mock_name),
    )


async def run(args) -> int:
    stage = Stage(args)
    report = Report()
    if not stage.host_runtime:
        sh("docker", "network", "create", stage.net)
    try:
        timeout = aiohttp.ClientTimeout(total=90)
        async with aiohttp.ClientSession(timeout=timeout) as http:
            await main_phase(stage, report, http)
            if not args.skip_tls:
                await tls_phase(stage, report, http)
    finally:
        stage.teardown()
    summary = report.summary()
    print(json.dumps(summary))
    if args.report:
        Path(args.report).write_text(
            json.dumps(
                {
                    "src": str(stage.src),
                    "ha_image": args.ha_image,
                    "git": sh(
                        "git", "-C", str(stage.src), "rev-parse", "HEAD", check=False
                    ),
                    "runtime": args.runtime,
                    "summary": summary,
                    "results": report.results,
                },
                indent=2,
            )
        )
    if summary["FUNC"]["failed"]:
        return 1
    return 2 if summary["SEC"]["failed"] else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="SmartPMS stage E2E")
    parser.add_argument("--src", default=".", help="checkout under test")
    parser.add_argument("--runtime", choices=["docker", "host"], default="docker")
    parser.add_argument("--ha-image", default=HA_IMAGE)
    parser.add_argument("--ha-port", type=int, default=18123)
    parser.add_argument("--mock-port", type=int, default=18080)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--skip-tls", action="store_true")
    parser.add_argument("--skip-slow", action="store_true")
    parser.add_argument("--report", help="write a JSON report here")
    args = parser.parse_args()
    if not IMAGE_RE.fullmatch(args.ha_image):
        parser.error("--ha-image must be ghcr.io/home-assistant/home-assistant:<tag>")
    src = Path(args.src).resolve(strict=True)
    if not (src / "custom_components" / "smartpms" / "manifest.json").is_file():
        parser.error("--src must be a checkout of homeassistant-smartpms")
    args.src = str(src)
    if args.report:
        report = Path(args.report).resolve()
        if report.suffix != ".json" or not report.parent.is_dir():
            parser.error("--report must be a .json file in an existing directory")
        args.report = str(report)
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
