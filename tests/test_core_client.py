"""Core client: service token lifecycle, re-mint rules, error mapping, cert pinning."""

from __future__ import annotations

import json
import shutil
import ssl
import subprocess
import time

import pytest
from fakecore import BASE, FakeCore, make_jwt

from wlanpi_menud.core import CoreClient, CoreError, CoreUnavailable, TokenError, TokenSource
from wlanpi_menud.core.client import pinned_ssl_context
from wlanpi_menud.core.generated import CORE_COMMIT, ROUTES
from wlanpi_menud.core.token import parse_helper_output


def client(fake: FakeCore, runner=None) -> tuple[CoreClient, TokenSource]:
    tokens = TokenSource(("get-core-token",), runner=runner or fake.token_runner())
    return CoreClient(BASE, tokens, transport=fake.transport()), tokens


def test_helper_output_forms():
    tok = make_jwt()
    assert parse_helper_output(json.dumps({"access_token": tok})) == tok
    assert parse_helper_output(f"export WLANPI_TOKEN={tok}\n") == tok
    assert parse_helper_output(tok + "\n") == tok
    with pytest.raises(TokenError):
        parse_helper_output("API Error: nope")


async def test_token_fetched_once_and_kept_in_memory(fake):
    c, tokens = client(fake)
    await c.request("GET", "/system/device/info")
    await c.request("GET", "/system/device/info")
    assert tokens.mints == 1 and tokens.expires_at > time.time()


async def test_refresh_before_exp(fake):
    now = [1000.0]
    issued = []

    async def runner(cmd):
        tok = make_jwt(exp=now[0] + 600, jti=f"t{len(issued)}")
        issued.append(tok)
        fake.valid_tokens = {tok}
        return json.dumps({"access_token": tok})
    tokens = TokenSource(("x",), refresh_margin_sec=300, runner=runner, clock=lambda: now[0])
    c = CoreClient(BASE, tokens, transport=fake.transport())
    await c.request("GET", "/system/device/model")
    now[0] += 200  # 400 s left: still fresh
    await c.request("GET", "/system/device/model")
    assert tokens.mints == 1
    now[0] += 150  # 250 s left: inside the margin, refresh before exp
    await c.request("GET", "/system/device/model")
    assert tokens.mints == 2


async def test_remint_on_401(fake):
    c, tokens = client(fake)
    await c.request("GET", "/system/device/model")
    fake.reject_next = ["401"]
    assert await c.request("GET", "/system/device/model") == {"model": "R4"}
    assert tokens.mints == 2


async def test_remint_on_auth_clock_not_set(fake):
    c, tokens = client(fake)
    fake.reject_next = ["clock"]
    assert await c.request("GET", "/system/device/model") == {"model": "R4"}
    assert tokens.mints == 2


async def test_clock_still_wrong_after_remint_is_core_unavailable(fake):
    c, tokens = client(fake)
    fake.reject_next = ["clock", "clock"]
    with pytest.raises(CoreUnavailable) as exc:
        await c.request("GET", "/system/device/model")
    assert exc.value.error == "AUTH_CLOCK_NOT_SET"


async def test_helper_failure_is_core_unavailable(fake):
    async def broken(cmd):
        raise TokenError("sudo: a password is required")
    c, _ = client(fake, runner=broken)
    with pytest.raises(CoreUnavailable, match="no core token"):
        await c.request("GET", "/system/device/model")


async def test_error_mapping(fake):
    c, _ = client(fake)
    with pytest.raises(CoreError) as exc:
        await c.request("POST", "/profiler/purge")
    assert exc.value.status == 409 and exc.value.message == "Stop the profiler before purging"
    fake.down = True
    with pytest.raises(CoreUnavailable, match="unreachable"):
        await c.request("GET", "/system/device/model")


async def test_generated_methods(fake):
    c, _ = client(fake)
    assert (await c.get_system_device_model())["model"] == "R4"
    scan = await c.get_utils_wlan_scan(iface="wlan1", hidden=False)
    assert scan["selectedAdapter"]["iface"] == "wlan1"
    req = [x for x in fake.router.calls if "scan" in str(x.request.url)][-1].request
    assert req.url.params["hidden"] == "false"
    assert CORE_COMMIT == "df7ac8a"
    assert ("POST", "/profiler/purge") in ROUTES and ("POST", "/profiler/purge/files") not in ROUTES


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")
def test_pinned_context(tmp_path):
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out",
                    str(cert), "-days", "1", "-subj", "/CN=wlanpi-test.local"], check=True, capture_output=True)
    ctx = pinned_ssl_context(cert)
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is False
    assert [c["subject"] for c in ctx.get_ca_certs()] == [((("commonName", "wlanpi-test.local"),),)]


async def test_missing_pinned_cert_never_falls_back(tmp_path, fake):
    tokens = TokenSource(("x",), runner=fake.token_runner())
    c = CoreClient(BASE, tokens, cert=tmp_path / "missing.cert", transport=fake.transport())
    with pytest.raises(CoreUnavailable, match="pinned core certificate"):
        await c.request("GET", "/system/device/model")
