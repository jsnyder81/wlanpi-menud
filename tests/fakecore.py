"""A stateful fake wlanpi-core for tests and the demo.

Routes are a respx router served through an httpx transport, so the real
``CoreClient`` (auth header, re-mint, error mapping) is exercised end to end
without the network. Response shapes follow core's openapi.json at the
pinned commit.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from collections import Counter
from typing import Any

import httpx
import respx

BASE = "https://127.0.0.1:31415"
PREFIX = "/api/v1"


def make_jwt(did: str = "wlanpi-menud", exp: float | None = None, jti: str = "t1") -> str:
    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    claims = {"did": did, "iat": int(time.time()), "exp": int(exp if exp is not None else time.time() + 3600),
              "jti": jti}
    return f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64(claims)}.c2ln"


class FakeCore:
    def __init__(self) -> None:
        self.mode = "classic"
        self.model = "R4"
        self.hostname = "wlanpi-test"
        self.valid_tokens: set[str] = set()
        self.remote_tokens: dict[str, dict[str, Any]] = {}  # introspection answers
        self.introspection = True
        self.calls: Counter[str] = Counter()
        self.reject_next: list[str] = []  # queue of "401" / "clock" for service-token calls
        self.scan_candidates: list[dict[str, Any]] | None = None
        self.scan_networks = 3
        self.scan_conflict = False
        self.scan_delay = 0.0
        self.speedtest_delay = 0.0
        self.speedtest_started = asyncio.Event()
        self.speedtest_cancelled = False
        self.reboots = 0
        self.timezone = "UTC"
        self.down = False
        self.cpu_temp = "48.0C"
        self.router = respx.Router(assert_all_called=False)
        self._routes()

    # ------------------------------------------------------------------ plumbing

    def transport(self) -> httpx.AsyncBaseTransport:
        return httpx.MockTransport(self.router.async_handler)

    def _auth(self, request: httpx.Request) -> httpx.Response | None:
        auth = request.headers.get("Authorization", "")
        token = auth.removeprefix("Bearer ")
        if token in self.remote_tokens:
            return None if self.remote_tokens[token].get("valid") else httpx.Response(401, json={"detail": "Unauthorized"})
        if self.reject_next:
            kind = self.reject_next.pop(0)
            if kind == "clock":
                return httpx.Response(503, json={"error": "AUTH_CLOCK_NOT_SET", "message": "NTP needs set; cannot proceed"})
            return httpx.Response(401, json={"detail": "Unauthorized"})
        if token not in self.valid_tokens:
            return httpx.Response(401, json={"detail": "Unauthorized"})
        return None

    def _on(self, method: str, path: str, fn) -> None:
        key = f"{method} {path}"

        async def side_effect(request: httpx.Request, **kw: Any) -> httpx.Response:
            self.calls[key] += 1
            if self.down:
                raise httpx.ConnectError("connection refused")
            denied = self._auth(request)
            if denied is not None:
                return denied
            out = fn(request)
            if asyncio.iscoroutine(out):
                out = await out
            if isinstance(out, httpx.Response):
                return out
            return httpx.Response(200, json=out)

        self.router.route(method=method, host="127.0.0.1", path__regex=f"^{PREFIX}{re.escape(path)}$").mock(side_effect=side_effect)

    # ------------------------------------------------------------------ routes

    def _routes(self) -> None:
        on = self._on
        on("GET", "/openapi.json", lambda r: self.openapi())
        on("GET", "/system/device/info", lambda r: {
            "model": self.model, "name": "wlanpi-test", "hostname": self.hostname, "software_version": "3.4.0",
            "mode": self.mode, "wlan_management": "auto"})
        on("GET", "/system/device/model", lambda r: {"model": self.model})
        on("GET", "/system/device/stats", lambda r: {
            "ip": "192.0.2.10", "cpu": "3%", "ram": "200/1000MB", "disk": "2/16GB", "cpu_temp": self.cpu_temp,
            "uptime": "1h"})
        on("GET", "/system/battery", lambda r: {"present": False})
        on("GET", "/bluetooth/status", lambda r: {"name": "wlanpi", "alias": "wlanpi", "addr": "00:00:00:00:00:01",
                                                    "power": "On", "paired_devices": []})
        on("GET", "/profiler/status", lambda r: {"running": False})
        on("GET", "/network/config/status", lambda r: {"root": {
            "wlan0": {"type": "managed", "addr": "02:00:00:00:00:00", "wiphy": "0"},
            "wlan1": {"type": "monitor", "addr": "02:00:00:00:00:01", "wiphy": "1"}}})
        on("GET", "/network/interfaces", lambda r: {"eth0": [
            {"ifname": "eth0", "operstate": "UP", "address": "dc:a6:32:00:00:01",
             "addr_info": [{"family": "inet", "local": "192.0.2.10", "prefixlen": 24}]}],
            "wlan0": [{"ifname": "wlan0", "operstate": "DOWN", "address": "02:00:00:00:00:00", "addr_info": []}]})
        on("GET", "/network/info/", lambda r: {
            "interfaces": {}, "wlan_interfaces": {}, "eth0_ipconfig_info": {"info": ["IP: 192.0.2.10", "Mask: 24"]},
            "vlan_info": {}, "lldp_neighbour_info": {"info": ["Name: sw1", "Port: Gi1/0/7"]},
            "cdp_neighbour_info": {"error": "No CDP neighbour"}, "public_ip": {"info": ["203.0.113.9"]}})
        on("GET", "/utils/reachability", lambda r: {"ping_google": "OK", "browse_google": "OK", "dns_google": "OK"})
        on("GET", "/utils/usb", lambda r: {"interfaces": [f"USB device {i}" for i in range(3)]})
        on("GET", "/utils/ufw", lambda r: {"status": "active", "ports": []})
        on("GET", "/utils/speedtest", self._speedtest)
        on("GET", "/utils/wlan/scan", self._scan)
        on("POST", "/system/reboot", self._reboot)
        on("POST", "/system/shutdown", lambda r: {"success": True, "message": "Shutting down"})
        on("POST", "/profiler/purge", lambda r: httpx.Response(409, json={"detail": "Stop the profiler before purging"}))
        on("POST", "/profiler/start", lambda r: {"success": True, "reason": None, "message": "Profiler started"})
        on("GET", "/system/timezone/list", lambda r: {"timezones": ["America/Chicago", "America/New_York", "Europe/Prague"]})
        on("POST", "/system/timezone/set", self._tz_set)
        on("GET", "/system/hotspot/ssid-passphrase", lambda r: (
            {"mode": "hotspot", "ssid": "WLAN Pi 1", "passphrase": "s3cret-pass"} if self.mode == "hotspot"
            else httpx.Response(409, json={"detail": "Not in hotspot mode"})))
        on("POST", "/system/reg-domain/set", lambda r: {"success": True, "message": "Set; reboot to apply"})
        on("POST", "/auth/introspect", self._introspect)

    def openapi(self) -> dict[str, Any]:
        from wlanpi_menud.core.generated import ROUTES
        paths: dict[str, dict[str, Any]] = {}
        for method, path in ROUTES:
            paths.setdefault(PREFIX + path, {})[method.lower()] = {}
        if self.introspection:
            paths[PREFIX + "/auth/introspect"] = {"post": {}}
        return {"openapi": "3.1.0", "info": {"version": "fake"}, "paths": paths}

    async def _speedtest(self, request: httpx.Request) -> Any:
        self.speedtest_started.set()
        try:
            await asyncio.sleep(self.speedtest_delay)
        except asyncio.CancelledError:
            self.speedtest_cancelled = True
            raise
        return {"ip_address": "203.0.113.9", "download_speed": "500 Mbps", "upload_speed": "40 Mbps"}

    async def _scan(self, request: httpx.Request) -> Any:
        if self.scan_delay:
            await asyncio.sleep(self.scan_delay)
        if self.scan_conflict:
            return httpx.Response(409, json={"detail": "A scan is already running on wlan1"})
        iface = request.url.params.get("iface")
        if self.scan_candidates and not iface:
            return {"detail": "short", "selectedAdapter": None, "networks": [], "scannedAt": None,
                    "needsSelection": True, "candidates": self.scan_candidates}
        nets = [{"ssid": f"net{i:03d}", "bssid": f"02:00:00:00:{i // 256:02x}:{i % 256:02x}", "channel": 1 + i % 11,
                 "signal": -40 - i % 50} for i in range(self.scan_networks)]
        return {"detail": "short", "selectedAdapter": {"iface": iface or "wlan1", "namespace": "root",
                                                       "label": iface or "wlan1"},
                "networks": nets, "scannedAt": "2026-11-08T12:00:00Z", "needsSelection": False, "candidates": []}

    def _reboot(self, request: httpx.Request) -> Any:
        self.reboots += 1
        return {"success": True, "message": "Rebooting"}

    def _tz_set(self, request: httpx.Request) -> Any:
        self.timezone = json.loads(request.content)["timezone"]
        return {"timezone": self.timezone}

    def _introspect(self, request: httpx.Request) -> Any:
        if not self.introspection:
            return httpx.Response(404, json={"detail": "Not Found"})
        token = json.loads(request.content)["token"]
        info = self.remote_tokens.get(token)
        if info is None:
            return {"valid": False}
        return {"valid": info.get("valid", True), "did": info.get("did"), "exp": info.get("exp"), "jti": info.get("jti")}

    # ------------------------------------------------------------------ token helper

    def token_runner(self):
        """A stand-in for ``sudo get-core-token``: mints a fresh valid token."""
        counter = {"n": 0}

        async def run(command) -> str:
            counter["n"] += 1
            tok = make_jwt(exp=time.time() + 3600, jti=f"svc{counter['n']}")
            self.valid_tokens = {tok}  # re-minting revokes the old one, as getjwt does per did
            return json.dumps({"access_token": tok, "token_type": "bearer"})
        return run
