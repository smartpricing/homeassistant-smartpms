"""Real TLS behaviour (no transport mocks).

A local HTTPS server with a self-signed certificate stands in for a
man-in-the-middle or a misconfigured ingress (the stage cb-backend ingress
served the ingress-nginx "Fake Certificate" on 2026-09-23). The integration
must refuse to send credentials to it.
"""

from __future__ import annotations

import datetime
import ipaddress
import json
import ssl
import threading
import urllib.request
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import aiohttp
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.smartpms.const import CONF_API_KEY, DOMAIN
from custom_components.smartpms.coordinator import SmartPMSApiClient

from .const import API_KEY, EMAIL, PASSWORD


class _Server:
    def __init__(self, base_url: str, cafile: Path) -> None:
        self.base_url = base_url
        self.cafile = cafile
        self.requests: list[str] = []


def _self_signed(tmp_path: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


@pytest.fixture
def untrusted_https_api(tmp_path: Path, socket_enabled: None) -> Generator[_Server]:
    """HTTPS server with a self-signed cert answering like the login API.

    ``socket_enabled`` lifts pytest-socket's block for this test only; the
    127.0.0.1-only allow-list set up by the HA test plugin stays in force.
    """
    cert_path, key_path = _self_signed(tmp_path)
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            return None

        def _reply(self) -> None:
            seen.append(f"{self.command} {self.path}")
            body = json.dumps({"data": {"token": "t", "expiresAt": 9999999999}})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body.encode())

        do_GET = _reply
        do_POST = _reply

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_path, key_path)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    server = _Server(
        f"https://127.0.0.1:{httpd.server_address[1]}/api/public/v2", cert_path
    )
    server.requests = seen
    try:
        yield server
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)


def test_control_server_works_when_its_certificate_is_trusted(
    untrusted_https_api: _Server,
) -> None:
    """Sanity check: the server is healthy, only its certificate is untrusted,
    so the failures below are caused by certificate verification."""
    ctx = ssl.create_default_context(cafile=str(untrusted_https_api.cafile))
    with urllib.request.urlopen(
        f"{untrusted_https_api.base_url}/login", context=ctx, timeout=5
    ) as resp:
        assert resp.status == 200
    assert untrusted_https_api.requests == ["GET /api/public/v2/login"]


async def test_login_to_untrusted_certificate_is_refused(
    hass: HomeAssistant, untrusted_https_api: _Server, monkeypatch
) -> None:
    """HA's shared session verifies the chain: no request reaches the server,
    so the password is never sent, and the error maps to UpdateFailed."""
    monkeypatch.setattr(
        "custom_components.smartpms.coordinator.API_BASE_URL",
        untrusted_https_api.base_url,
    )
    client = SmartPMSApiClient(
        session=async_get_clientsession(hass),
        email=EMAIL,
        password=PASSWORD,
        api_key=API_KEY,
    )
    with pytest.raises(UpdateFailed) as err:
        await client.authenticate()
    assert isinstance(err.value.__cause__, aiohttp.ClientConnectorCertificateError)
    assert untrusted_https_api.requests == []


async def test_config_flow_with_untrusted_certificate_cannot_connect(
    hass: HomeAssistant, untrusted_https_api: _Server, monkeypatch
) -> None:
    """End to end through the config flow: the user sees cannot_connect."""
    monkeypatch.setattr(
        "custom_components.smartpms.coordinator.API_BASE_URL",
        untrusted_https_api.base_url,
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD, CONF_API_KEY: API_KEY},
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert untrusted_https_api.requests == []
