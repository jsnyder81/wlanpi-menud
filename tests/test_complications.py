"""Complications (docs/complications-spec.md, test plan 6.1).

Front door (publish, remove, namespaces, quotas, TTL), topics (legacy frozen,
live, catalog), built-ins and the Wi-Fi link, display config per surface
profile, the thin home view, ephemeral sessions and the CLI.
"""

from __future__ import annotations

import asyncio
import json
import os
import pwd
from types import SimpleNamespace
from typing import Any

import pytest

from wlanpi_menud import cli
from wlanpi_menud import complications as C
from wlanpi_menud.auth import Principal
from wlanpi_menud.jobs import InvokeRefused
from wlanpi_menud.menu import RouteTable

ME = pwd.getpwuid(os.getuid()).pw_name
SEG = C.map_username(ME)
MINE = f"local.{SEG}.vpn"
WEBUI = {"X-WebUI-User": "alice", "X-WebUI-Session": "W1"}


# ---------------------------------------------------------------------------- helpers

async def provider(h, sock: str = "local", headers: dict[str, str] | None = None, **hello: Any):
    c = await h.connect(sock, headers=headers, origin="http://wlanpi.local" if headers else None)
    w = await c.hello("rich", session="new", **hello)
    await c.expect(type="menu")
    return c, w


async def publish(c, app_id: str, **body: Any) -> dict[str, Any]:
    body.setdefault("value", "up")
    mid = await c.send("complication.publish", app_id=app_id, **body)
    return await c.expect(lambda f: f["id"] == mid and f["type"] in ("complication.ack", "error"))


async def remove(c, app_id: str) -> dict[str, Any]:
    mid = await c.send("complication.remove", app_id=app_id)
    return await c.expect(lambda f: f["id"] == mid and f["type"] in ("complication.ack", "error"))


async def configure(c, surface: str, **body: Any) -> dict[str, Any]:
    mid = await c.send("complications.configure", surface=surface, **body)
    return await c.expect(lambda f: f["id"] == mid and f["type"] in ("event", "error"))


async def subscribe(c, *topics: str) -> None:
    mid = await c.send("subscribe", topics=list(topics))
    for _ in topics:
        await c.expect(lambda f: f["id"] == mid, type="event")


async def next_event(c, topic: str, timeout: float = 3.0) -> dict[str, Any]:
    return await c.expect(lambda f: f["topic"] == topic and not f.get("snapshot"), type="event", timeout=timeout)


def live_ids(h) -> list[str]:
    return [c["app_id"] for c in h.service.hub.data["complications.live"]]


def declare(plugins_dir, name: str, user: str, ids=("uplink",), **extra: Any) -> None:
    d = plugins_dir / name
    d.mkdir(parents=True, exist_ok=True)
    doc = {"schema": 1, "publisher": {"user": user},
           "complications": [{"id": f"plugin.{name}.{i}", "title": f"{name} {i}"[:24], "label": i[:8], **extra}
                             for i in ids]}
    (d / "complications.json").write_text(json.dumps(doc))


def fake_attachment(name: str = "root", uid: int = 0, roles=("admin",), sock: str = "local"):
    return SimpleNamespace(socket_kind=sock, principal=Principal("local", name, tuple(roles), uid=uid))


# ---------------------------------------------------------------------------- handshake

async def test_welcome_announces_the_feature(harness):
    _, w = await provider(harness)
    assert w["features"] == ["complications.2"]


async def test_ephemeral_refused_on_the_default_session(harness):
    c = await harness.connect()
    await c.send("hello", mode="thin", protocol=[1], session="default", ephemeral=True)
    err = await c.expect(type="error")
    assert err["code"] == "BAD_REQUEST" and err["close_code"] == 4400


async def test_ephemeral_session_is_gone_at_disconnect(harness):
    c, w = await provider(harness, ephemeral=True)
    assert w["session_id"] in harness.service.sessions
    await c.close()
    for _ in range(50):
        if w["session_id"] not in harness.service.sessions:
            break
        await asyncio.sleep(0.02)
    assert w["session_id"] not in harness.service.sessions


async def test_100_one_shot_publishes_never_reach_the_session_limit(harnesses):
    h = await harnesses(sessions_per_socket=4)
    for i in range(100):
        c, _ = await provider(h, ephemeral=True)
        ack = await publish(c, MINE, label="VPN", value=str(i))
        assert ack["type"] == "complication.ack", ack
        await c.close()
        h.clients.remove(c)
    await asyncio.sleep(0.05)
    assert len(h.service.sessions) <= 2  # the default session, and at most one closing


# ---------------------------------------------------------------------------- authorization

async def test_admin_and_provider_on_local_sock_may_publish(harnesses):
    for role in ("admin", "provider"):
        h = await harnesses(local_uids={os.getuid(): (role,)})
        c, _ = await provider(h)
        ack = await publish(c, MINE, label="VPN", status="ok", ttl_sec=60)
        assert ack == {"v": 1, "type": "complication.ack", "id": ack["id"], "app_id": MINE, "state": "live",
                       "ttl_sec": 60}
        await h.stop()


@pytest.mark.parametrize("role", ["operator", "viewer"])
async def test_operator_and_viewer_may_not_publish(harnesses, role):
    h = await harnesses(local_uids={os.getuid(): (role,)})
    c, _ = await provider(h)
    err = await publish(c, MINE, label="VPN")
    assert err["type"] == "error" and err["code"] == "FORBIDDEN"


async def test_proxy_sock_may_never_publish(harness):
    c, _ = await provider(harness, "proxy", headers=WEBUI)
    assert "admin" in harness.service.config.webui_roles
    err = await publish(c, "local.alice.x", label="X")
    assert err["code"] == "FORBIDDEN" and "local.sock" in err["message"]


async def test_wlanpi_namespace_is_reserved_even_for_root(harness):
    with pytest.raises(InvokeRefused) as exc:
        harness.service._provider_check(fake_attachment(), "wlanpi.mode")
    assert exc.value.code == "FORBIDDEN"
    c, _ = await provider(harness)
    assert (await publish(c, "wlanpi.mode", label="Mode"))["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------- namespaces

async def test_local_namespace_is_bound_to_the_callers_username(harness):
    c, _ = await provider(harness)
    assert (await publish(c, MINE, label="VPN"))["type"] == "complication.ack"
    err = await publish(c, "local.someoneelse.vpn", label="VPN")
    assert err["code"] == "FORBIDDEN" and f"local.{SEG}" in err["message"]
    # root may publish under any local.*
    assert harness.service._provider_check(fake_attachment(), "local.someoneelse.vpn") is None


async def test_local_id_needs_a_label(harness):
    c, _ = await provider(harness)
    err = await publish(c, MINE)
    assert err["code"] == "BAD_REQUEST" and "label" in err["message"]


def test_username_mapping():
    assert C.map_username("wlanpi") == "wlanpi"
    assert C.map_username("Jo.Smith-2") == "jo_smith_2"
    assert C.map_username("1abc") == "u1abc"
    assert C.ID_RE.match(f"local.{C.map_username('Jo.Smith-2')}.x")


async def test_plugin_namespace_only_for_the_declared_user(harnesses, sockdir):
    declare(sockdir / "plugins.d", "mine", ME)
    declare(sockdir / "plugins.d", "theirs", "nobody")
    h = await harnesses()
    c, _ = await provider(h)
    ack = await publish(c, "plugin.mine.uplink", value="412 Mb/s")
    assert ack["type"] == "complication.ack"
    assert h.service.complications["plugin.mine.uplink"]["label"] == "uplink"  # the declared label
    err = await publish(c, "plugin.theirs.uplink")
    assert err["code"] == "FORBIDDEN" and "nobody" in err["message"]
    err = await publish(c, "plugin.mine.undeclared")
    assert err["code"] == "BAD_REQUEST" and "not declared" in err["message"]
    err = await publish(c, "plugin.ghost.x")
    assert err["code"] == "BAD_REQUEST"
    # root publishes any declared plugin id
    assert h.service._provider_check(fake_attachment(), "plugin.theirs.uplink") is not None


# ---------------------------------------------------------------------------- declarations

async def test_declaration_grants_the_provider_role(harnesses, sockdir):
    declare(sockdir / "plugins.d", "mine", ME)
    h = await harnesses(local_uids={})
    c, w = await provider(h)
    assert w["roles"] == ["provider"]
    assert (await publish(c, "plugin.mine.uplink"))["type"] == "complication.ack"


async def test_bad_declaration_rejected_whole_and_menu_still_loads(harnesses, sockdir):
    d = sockdir / "plugins.d" / "acme"
    d.mkdir(parents=True)
    (d / "complications.json").write_text(json.dumps({"schema": 1, "publisher": {"user": ME}, "complications": [
        {"id": "plugin.acme.ok", "title": "OK", "label": "OK"},
        {"id": "plugin.other.x", "title": "X", "label": "X"}]}))
    (d / "menu.json").write_text(json.dumps({"schema": 1, "plugin": "acme", "nodes": [
        {"id": "plugin.acme.status", "label": "Acme", "parent": None, "order": 90,
         "action": {"core": "GET /system/device/info"}}]}))
    h = await harnesses()
    errs = [str(e) for e in h.service.declaration_errors]
    assert any("/complications/1/id" in e and "plugin.acme." in e for e in errs), errs
    assert not any(c["source"] == "plugin" for c in h.service.hub.data["complications.catalog"])
    assert "plugin.acme.status" in h.service.menu.nodes, "the plugin's menu manifest still loads"
    assert not h.service.load.rejected, "complications.json is not read as a menu manifest"


async def test_declaration_reload_and_removal(harnesses, sockdir):
    h = await harnesses(local_uids={})
    assert h.service.local_policy.declared_uids == {}
    declare(sockdir / "plugins.d", "mine", ME, icon="globe", description="Uplink speed", node="plugin.mine.status")
    h.service.reload_menu()  # SIGHUP / the dpkg trigger
    cat = {c["app_id"]: c for c in h.service.hub.data["complications.catalog"]}
    assert cat["plugin.mine.uplink"] == {"app_id": "plugin.mine.uplink", "title": "mine uplink", "label": "uplink",
                                         "source": "plugin", "icon": "globe", "description": "Uplink speed",
                                         "node": "plugin.mine.status"}
    assert os.getuid() in h.service.local_policy.declared_uids
    (sockdir / "plugins.d" / "mine" / "complications.json").unlink()
    h.service.reload_menu()
    assert "plugin.mine.uplink" not in {c["app_id"] for c in h.service.hub.data["complications.catalog"]}
    assert h.service.local_policy.declared_uids == {}


# ---------------------------------------------------------------------------- replace, TTL, hold

async def test_publish_replaces_the_whole_record(harnesses, sockdir):
    declare(sockdir / "plugins.d", "mine", ME, icon="globe")
    h = await harnesses()
    c, _ = await provider(h)
    await publish(c, "plugin.mine.uplink", value="412 Mb/s", short="412", detail="WAN2", status="ok",
                  label="Speed", icon="radio")
    rec = {x["app_id"]: x for x in h.service.hub.data["complications.live"]}["plugin.mine.uplink"]
    assert rec["label"] == "Speed" and rec["short"] == "412" and rec["icon"] == "radio"
    await publish(c, "plugin.mine.uplink", value="9 Mb/s")
    rec = {x["app_id"]: x for x in h.service.hub.data["complications.live"]}["plugin.mine.uplink"]
    assert rec == {"app_id": "plugin.mine.uplink", "label": "uplink", "value": "9 Mb/s", "status": "unknown",
                   "icon": "globe"}, "omitted fields return to defaults; declared label and icon are the defaults"


async def test_ttl_default_and_bounds(harness):
    c, _ = await provider(harness)
    ack = await publish(c, MINE, label="VPN")
    assert ack["ttl_sec"] == 30
    for bad in (4, 3601):
        err = await publish(c, MINE, label="VPN", ttl_sec=bad)
        assert err["code"] == "BAD_REQUEST" and "ttl_sec" in err["message"]


async def test_expiry_runs_on_a_timer_not_the_poller(harness):
    """poll_interval_sec is 3600 in the harness: only the store's own timer can expire this."""
    c, _ = await provider(harness)
    await subscribe(c, "complications.live")
    harness.service.complication_store.put(MINE, {"label": "VPN", "value": "up", "status": "ok"}, ttl=0.3)
    ev = await next_event(c, "complications.live")
    assert MINE in [x["app_id"] for x in ev["data"]]
    ev = await next_event(c, "complications.live", timeout=1.3)  # within 1 s of the deadline
    assert MINE not in [x["app_id"] for x in ev["data"]]


def test_expiry_with_a_fake_clock():
    now = [100.0]
    out: dict[str, Any] = {}
    store = C.ComplicationStore(lambda t, d: out.__setitem__(t, d), fanout_sec=0, clock=lambda: now[0])
    store.put(MINE, {"label": "VPN", "value": "up", "status": "ok"}, ttl=5)
    now[0] = 104.9
    assert not store.expire()
    now[0] = 105.0
    assert store.expire() and store.providers == {}
    store.put("plugin.mine.uplink", {"label": "Up", "value": "1", "status": "ok"}, ttl=5)
    store.flush()
    assert out["complications.catalog"] == [], "an undeclared plugin id never enters the catalog"


async def test_no_change_publish_extends_ttl_without_an_event(harness):
    c, _ = await provider(harness)
    await publish(c, MINE, label="VPN", status="ok")
    store = harness.service.complication_store
    first = store.providers[MINE]["expires_at"]
    seq = dict(harness.service.hub.seq)
    await asyncio.sleep(0.02)
    await publish(c, MINE, label="VPN", status="ok")
    assert store.providers[MINE]["expires_at"] > first
    assert harness.service.hub.seq == seq


async def test_hold_connection_removes_on_close(harness):
    c, _ = await provider(harness)
    await publish(c, MINE, label="VPN", hold="connection", ttl_sec=600)
    await publish(c, f"local.{SEG}.other", label="Other", ttl_sec=600)
    await c.close()
    for _ in range(50):
        if MINE not in live_ids(harness):
            break
        await asyncio.sleep(0.02)
    assert MINE not in live_ids(harness)
    assert f"local.{SEG}.other" in live_ids(harness), "hold: ttl survives the connection"


async def test_remove_and_not_found(harness):
    c, _ = await provider(harness)
    await publish(c, MINE, label="VPN")
    ack = await remove(c, MINE)
    assert ack["state"] == "removed" and MINE not in live_ids(harness)
    err = await remove(c, MINE)
    assert err["code"] == "NOT_FOUND"
    assert (await remove(c, "wlanpi.mode"))["code"] == "FORBIDDEN"


# ---------------------------------------------------------------------------- quotas, sanitizing

async def test_quotas(harnesses):
    h = await harnesses(complication_quota_per_uid=2, complication_quota_total=3)
    c, _ = await provider(h)
    for i in range(2):
        assert (await publish(c, f"local.{SEG}.t{i}", label="T"))["type"] == "complication.ack"
    assert (await publish(c, f"local.{SEG}.t0", label="T", value="again"))["type"] == "complication.ack", \
        "a replace is not counted"
    err = await publish(c, f"local.{SEG}.t2", label="T")
    assert err["code"] == "FORBIDDEN" and "per uid" in err["message"]
    h.service.complication_store.put("local.other.x", {"label": "X", "value": "1", "status": "ok"}, ttl=30, uid=12345)
    with pytest.raises(C.Refused, match="in total"):
        h.service.complication_store.put("local.other.y", {"label": "Y", "value": "1", "status": "ok"}, ttl=30,
                                         uid=12346)


async def test_sanitizing_and_status_alias(harness):
    c, _ = await provider(harness)
    ack = await publish(c, MINE, label="VP\x07N", value="\u202e\x1b[31mup\x1b[0m\x1b]0;t\x07",
                        detail="a\nb", status="warning")
    assert ack["type"] == "complication.ack", ack
    rec = {x["app_id"]: x for x in harness.service.hub.data["complications.live"]}[MINE]
    assert rec["label"] == "VPN" and rec["value"] == "up" and rec["detail"] == "ab"
    assert rec["status"] == "warn"


def test_sanitize_applies_limits_after_stripping():
    assert C.sanitize("\x1b[1m" + "x" * 30, 20) == "x" * 20
    assert C.sanitize("a\u2066b\u2069c\u200f") == "abc"


# ---------------------------------------------------------------------------- topics

async def test_legacy_topic_holds_provider_records_only_in_the_old_shape(harness):
    c, _ = await provider(harness)
    await subscribe(c, "complications", "complications.live")
    await publish(c, MINE, label="VPN", status="ok", short="UP", detail="tun0", icon="shield")
    legacy = harness.service.hub.data["complications"]
    assert legacy == [{"app_id": MINE, "label": "VPN", "value": "up", "status": "ok"}]
    ids = live_ids(harness)
    assert MINE in ids and "wlanpi.mode" in ids and "wlanpi.ip" in ids
    assert not any(x["app_id"].startswith("wlanpi.") for x in legacy)


async def test_local_id_enters_and_leaves_the_catalog(harness):
    c, _ = await provider(harness)
    await publish(c, MINE, label="VPN", icon="shield")
    cat = {x["app_id"]: x for x in harness.service.hub.data["complications.catalog"]}
    assert cat[MINE] == {"app_id": MINE, "title": "VPN", "label": "VPN", "source": "local", "icon": "shield"}
    await remove(c, MINE)
    assert MINE not in {x["app_id"] for x in harness.service.hub.data["complications.catalog"]}


async def test_changes_inside_the_fanout_window_collapse(harnesses):
    h = await harnesses(complications_fanout_sec=0.3)
    c, _ = await provider(h)
    await subscribe(c, "complications.live")
    await asyncio.sleep(0.35)
    await publish(c, MINE, label="VPN", value="1")  # leading edge: published now
    ev1 = await next_event(c, "complications.live")
    await publish(c, MINE, label="VPN", value="2")
    await publish(c, MINE, label="VPN", value="3")
    await publish(c, f"local.{SEG}.b", label="B")
    ev2 = await next_event(c, "complications.live")
    assert ev2["seq"] == ev1["seq"] + 1, "three changes inside the window give one event"
    data = {x["app_id"]: x for x in ev2["data"]}
    assert data[MINE]["value"] == "3" and f"local.{SEG}.b" in data


async def test_in_process_api_still_feeds_the_legacy_topic(harness):
    harness.service.publish_complication("gps", "GPS-receiver", "8 sats", "ok")
    assert harness.service.hub.data["complications"] == [
        {"app_id": "gps", "label": "GPS-rece", "value": "8 sats", "status": "ok"}]
    assert "gps" not in live_ids(harness), "an id outside the namespaces stays legacy-only"


# ---------------------------------------------------------------------------- built-ins

SUMMARY = {"hostname": "wlanpi-test", "model": "R4", "mode": "classic", "software_version": "3.4.0",
           "ip": "192.0.2.10", "eth0": "192.0.2.10", "wlan0": None, "cpu": "3%", "cpu_temp": "48.0C",
           "uptime": "1h", "battery": None, "bluetooth_on": True, "profiler_running": False, "reachable": True,
           "wlan_adapters": ["wlan0"], "hotspot_clients": None}


def _b(summary=None, wifi=None, caps=("ethernet", "bluetooth"), profiler=True, **over):
    s = dict(SUMMARY if summary is None else summary, **over)
    return C.builtin_complications(s, wifi, capabilities=caps, profiler_route=profiler)


@pytest.mark.parametrize("over,app_id,expect", [
    ({}, "wlanpi.mode", {"value": "Classic", "short": "CLS", "status": "info", "icon": "dot"}),
    ({"mode": None}, "wlanpi.mode", {"value": "--", "status": "unknown"}),
    ({}, "wlanpi.ip", {"value": "192.0.2.10", "status": "ok", "icon": "ethernet"}),
    ({"ip": None}, "wlanpi.ip", {"value": "none", "status": "warn"}),
    ({}, "wlanpi.eth0", {"value": "192.0.2.10", "status": "ok"}),
    ({"eth0": None}, "wlanpi.eth0", {"value": "no IP", "status": "off"}),
    ({"wlan0": "198.51.100.7"}, "wlanpi.wlan0", {"value": "198.51.100.7", "status": "ok", "icon": "wifi"}),
    ({}, "wlanpi.wlan0", {"value": "no IP", "status": "off"}),
    ({}, "wlanpi.internet", {"value": "up", "status": "ok", "short": "NET", "icon": "globe"}),
    ({"reachable": False}, "wlanpi.internet", {"value": "down", "status": "error"}),
    ({"reachable": None}, "wlanpi.internet", {"value": "--", "status": "unknown"}),
    ({}, "wlanpi.bluetooth", {"value": "on", "status": "ok", "short": "BT"}),
    ({"bluetooth_on": False}, "wlanpi.bluetooth", {"value": "off", "status": "off"}),
    ({"bluetooth_on": None}, "wlanpi.bluetooth", {"status": "unknown"}),
    ({"battery": {"present": True, "percent": 85, "status": "Charging"}}, "wlanpi.battery",
     {"value": "85% charging", "short": "85%", "status": "ok", "icon": "bolt"}),
    ({"battery": {"present": True, "percent": 21, "status": "Discharging"}}, "wlanpi.battery",
     {"value": "21%", "status": "ok", "icon": "battery"}),
    ({"battery": {"present": True, "percent": 20, "status": "Discharging"}}, "wlanpi.battery", {"status": "warn"}),
    ({"battery": {"present": True, "percent": 11, "status": "Discharging"}}, "wlanpi.battery", {"status": "warn"}),
    ({"battery": {"present": True, "percent": 10, "status": "Discharging"}}, "wlanpi.battery", {"status": "error"}),
    ({"battery": {"present": True, "percent": 5, "status": "Charging"}}, "wlanpi.battery", {"status": "warn"}),
    ({"battery": {"present": True, "percent": None, "status": None}}, "wlanpi.battery", {"status": "unknown"}),
    ({}, "wlanpi.cpu_temp", {"value": "48C", "short": "48C", "status": "ok"}),
    ({"cpu_temp": "69.6C"}, "wlanpi.cpu_temp", {"value": "70C", "status": "warn"}),
    ({"cpu_temp": "79.4C"}, "wlanpi.cpu_temp", {"value": "79C", "status": "warn"}),
    ({"cpu_temp": 80}, "wlanpi.cpu_temp", {"value": "80C", "status": "error"}),
    ({"cpu_temp": None}, "wlanpi.cpu_temp", {"status": "unknown"}),
    ({}, "wlanpi.cpu", {"value": "3%", "short": "3%", "status": "info", "icon": "cpu"}),
    ({}, "wlanpi.profiler", {"value": "stopped", "status": "off", "short": "PRF"}),
    ({"profiler_running": True}, "wlanpi.profiler", {"value": "running", "status": "info"}),
    ({"mode": "hotspot", "hotspot_clients": 3}, "wlanpi.hotspot_clients", {"value": "3", "status": "info",
                                                                          "icon": "users"}),
    ({"mode": "hotspot", "hotspot_clients": 0}, "wlanpi.hotspot_clients", {"value": "0", "status": "off"}),
])
def test_builtin_rows(over, app_id, expect):
    rec = _b(**over)[app_id]
    assert rec["label"] == C.BUILTINS[app_id].label
    for k, v in expect.items():
        assert rec[k] == v, (k, rec)


@pytest.mark.parametrize("kw,absent", [
    ({"caps": ()}, "wlanpi.eth0"),
    ({"caps": ()}, "wlanpi.bluetooth"),
    ({"wlan_adapters": []}, "wlanpi.wlan0"),
    ({}, "wlanpi.battery"),
    ({"battery": {"present": False}}, "wlanpi.battery"),
    ({"profiler": False}, "wlanpi.profiler"),
    ({}, "wlanpi.hotspot_clients"),
    ({}, "wlanpi.wifi"),
])
def test_builtin_absent_when_the_device_cannot_produce_it(kw, absent):
    assert absent not in _b(**kw)


def test_builtins_empty_when_core_unavailable():
    assert C.builtin_complications({"core": "unavailable"}) == {}
    assert C.builtin_complications({}) == {}


def test_builtin_values_fit_the_schema():
    long = _b(ip="2001:db8:1234:5678:9abc:def0:1234:5678")
    assert len(long["wlanpi.ip"]["value"]) == 20 and long["wlanpi.ip"]["value"].endswith("…")


async def test_all_builtins_removed_while_core_is_unavailable(harness, fake):
    assert any(i.startswith("wlanpi.") for i in live_ids(harness))
    fake.down = True
    await harness.poller.poll_once()
    assert not any(i.startswith("wlanpi.") for i in live_ids(harness))
    assert any(c["source"] == "builtin" for c in harness.service.hub.data["complications.catalog"]), \
        "the catalog keeps what the device can produce"
    fake.down = False
    await harness.poller.poll_once()
    assert "wlanpi.mode" in live_ids(harness)


async def test_device_summary_unchanged_for_the_same_fake_core(harness):
    assert harness.service.hub.data["device.summary"] == {
        "hostname": "wlanpi-test", "model": "R4", "mode": "classic", "software_version": "3.4.0",
        "ip": "192.0.2.10", "eth0": "192.0.2.10", "wlan0": None, "cpu": "3%", "cpu_temp": "48.0C", "uptime": "1h",
        "battery": {"present": False, "percent": None, "status": None}, "bluetooth_on": True,
        "profiler_running": False, "reachable": True, "wlan_adapters": ["wlan0", "wlan1"], "hotspot_clients": None}


# ---------------------------------------------------------------------------- Wi-Fi link

LINK = {"interface": "wlan0", "connected": True, "ssid": "HomeNet", "bssid": "02:00:00:00:00:01",
        "freq_mhz": 5180.0, "signal_dbm": -52.0, "links": []}


@pytest.mark.parametrize("freq,ch", [(2412, 1), (2437, 6), (2472, 13), (2484, 14), (5180, 36), (5825, 165),
                                     (5955, 1), (6115, 33), (7115, 233), (5935, 2), (None, None)])
def test_channel_from_frequency(freq, ch):
    assert C.channel_of(freq) == ch


@pytest.mark.parametrize("link,expect", [
    (LINK, {"value": "HomeNet -52", "short": "-52", "status": "ok", "detail": "ch 36, 5180 MHz, 02:00:00:00:00:01"}),
    (dict(LINK, signal_dbm=-67), {"status": "ok"}),
    (dict(LINK, signal_dbm=-68), {"status": "warn"}),
    (dict(LINK, signal_dbm=-75), {"status": "warn"}),
    (dict(LINK, signal_dbm=-76), {"status": "error"}),
    (dict(LINK, signal_dbm=None), {"value": "HomeNet", "status": "unknown"}),
    ({"interface": "wlan0", "connected": False, "links": []}, {"value": "not connected", "status": "off"}),
    (dict(LINK, freq_mhz=None, links=[{"link_id": 0, "freq_mhz": 5180}, {"link_id": 1, "freq_mhz": 6115}]),
     {"detail": "links 5180/6115 MHz, 02:00:00:00:00:01"}),
    (dict(LINK, ssid="A-very-long-network-name"), {"value": "A-very-long-net… -52"}),
    (C.WIFI_UNKNOWN, {"value": "--", "status": "unknown"}),
])
def test_wifi_mapping(link, expect):
    rec = C.wifi_record(link)
    for k, v in expect.items():
        assert rec[k] == v, (k, rec)
    assert len(rec["value"]) <= 20


def test_wifi_hysteresis():
    prev = C.wifi_record(LINK)
    assert C.wifi_hysteresis(prev, C.wifi_record(dict(LINK, signal_dbm=-54))) is prev, "2 dB is noise"
    moved = C.wifi_record(dict(LINK, signal_dbm=-55))
    assert C.wifi_hysteresis(prev, moved) is moved, "3 dB publishes"
    edge = C.wifi_record(dict(LINK, signal_dbm=-66))
    near = C.wifi_record(dict(LINK, signal_dbm=-68))
    assert C.wifi_hysteresis(edge, near) is near, "a status band change publishes"
    other = C.wifi_record(dict(LINK, ssid="Other", signal_dbm=-53))
    assert C.wifi_hysteresis(prev, other) is other


async def test_wifi_tile_and_cadence(harnesses, fake):
    fake.wlan_link = dict(LINK)
    h = await harnesses(context_interval_sec=3600)
    key = "GET /network/interfaces/wlan0/wlan-link"
    assert fake.calls[key] == 1, "read with the first context"
    rec = {x["app_id"]: x for x in h.service.hub.data["complications.live"]}["wlanpi.wifi"]
    assert rec["value"] == "HomeNet -52" and rec["status"] == "ok"
    for _ in range(4):
        await h.poller.poll_once()
    assert fake.calls[key] == 3, "connected: every second fast tick"
    fake.wlan_link = {"interface": "wlan0", "connected": False, "links": []}
    await h.poller.poll_once()
    await h.poller.poll_once()
    n = fake.calls[key]
    for _ in range(4):
        await h.poller.poll_once()
    assert fake.calls[key] == n, "not connected: only on context ticks"
    h.poller.invalidate_context()
    await h.poller.poll_once()
    assert fake.calls[key] == n + 1, "a mutating job's context invalidation reads it early"
    assert {x["app_id"]: x for x in h.service.hub.data["complications.live"]}["wlanpi.wifi"]["status"] == "off"


async def test_wifi_jitter_does_not_republish(harness, fake):
    fake.wlan_link = dict(LINK)
    harness.poller.invalidate_context()
    await harness.poller.poll_once()
    seq = harness.service.hub.seq["complications.live"]
    fake.wlan_link = dict(LINK, signal_dbm=-54)
    harness.poller.invalidate_context()
    await harness.poller.poll_once()
    assert harness.service.hub.seq["complications.live"] == seq
    fake.wlan_link = dict(LINK, signal_dbm=-49)
    harness.poller.invalidate_context()
    await harness.poller.poll_once()
    assert harness.service.hub.seq["complications.live"] == seq + 1


async def test_wifi_failed_read_is_unknown(harness, fake):
    fake.wlan_link = 500
    harness.poller.invalidate_context()
    await harness.poller.poll_once()
    assert {x["app_id"]: x for x in harness.service.hub.data["complications.live"]}["wlanpi.wifi"]["status"] \
        == "unknown"


async def test_wifi_skipped_in_hotspot_mode_without_adapter_or_route(harnesses, fake):
    key = "GET /network/interfaces/wlan0/wlan-link"
    fake.mode = "hotspot"
    h = await harnesses()
    assert fake.calls[key] == 0 and "wlanpi.wifi" not in live_ids(h)
    assert "wlanpi.hotspot_clients" in {c["app_id"] for c in h.service.hub.data["complications.catalog"]}
    await h.stop()
    fake.mode = "classic"
    h2 = await harnesses()
    n = fake.calls[key]
    h2.service.set_routes(RouteTable({r for r in h2.service.routes.routes if "wlan-link" not in r[1]}))
    h2.poller._routes_at = 1e12  # keep the narrowed table
    h2.poller.invalidate_context()
    await h2.poller.poll_once()
    assert fake.calls[key] == n and "wlanpi.wifi" not in live_ids(h2)
    assert "wlanpi.wifi" not in {c["app_id"] for c in h2.service.hub.data["complications.catalog"]}


# ---------------------------------------------------------------------------- display config

async def test_config_topic_defaults(harness):
    data = harness.service.hub.data["complications.config"]["surfaces"]
    assert data["panel.strip"] == {"show": ["wlanpi.battery", "wlanpi.bluetooth", "wlanpi.internet"],
                                   "source": "default"}
    assert data["tui"]["show"] == ["wlanpi.mode", "wlanpi.eth0", "wlanpi.ip", "wlanpi.cpu_temp", "plugin.*",
                                   "local.*"]
    assert {"panel", "panel.strip", "tui", "webui", "default"} <= set(data)
    assert all(s["show"][-2:] == ["plugin.*", "local.*"] for p, s in data.items() if p != "panel.strip")


async def test_configure_reply_event_and_persistence(harness, sockdir):
    a, _ = await provider(harness)  # admin, not subscribed
    b, _ = await provider(harness)
    await subscribe(b, "complications.config")
    show = ["plugin.acme.uplink", "wlanpi.mode", "local.*"]
    reply = await configure(a, "panel", show=show)
    assert reply["type"] == "event" and reply["snapshot"] is True and reply["topic"] == "complications.config"
    assert reply["data"]["surfaces"]["panel"] == {"show": show, "source": "user"}, "unknown ids are kept"
    ev = await next_event(b, "complications.config")
    assert ev["data"] == reply["data"]
    saved = json.loads((sockdir / "state" / "complications.json").read_text())
    assert saved == {"schema": 1, "surfaces": {"panel": {"show": show}}}
    assert not list((sockdir / "state").glob(".*.tmp")), "written atomically through a temp file"
    await subscribe(a, "complications.config")
    reply = await configure(a, "panel", reset=True)
    assert reply["data"]["surfaces"]["panel"]["source"] == "default"
    await asyncio.sleep(0.05)
    assert sum(1 for f in a.frames if f["type"] == "event" and f["topic"] == "complications.config"
               and f["id"] == reply["id"]) == 1, "the caller gets the change once, as its reply"
    assert json.loads((sockdir / "state" / "complications.json").read_text())["surfaces"] == {}


@pytest.mark.parametrize("role", ["viewer", "provider"])
async def test_configure_needs_operator(harnesses, role):
    h = await harnesses(local_uids={os.getuid(): (role,)})
    c, _ = await provider(h)
    err = await configure(c, "panel", show=["wlanpi.mode"])
    assert err["type"] == "error" and err["code"] == "FORBIDDEN"


async def test_configure_operator_on_proxy_sock(harnesses):
    h = await harnesses(webui_roles=("operator",))
    c, _ = await provider(h, "proxy", headers=WEBUI)
    reply = await configure(c, "panel", show=["wlanpi.ip"])
    assert reply["type"] == "event" and reply["data"]["surfaces"]["panel"]["show"] == ["wlanpi.ip"]


async def test_configure_bad_entry_is_bad_request(harness):
    c, _ = await provider(harness)
    err = await configure(c, "panel", show=["plugin.*.x"])
    assert err["code"] == "BAD_REQUEST"


def test_layer_precedence_per_profile(tmp_path):
    site = tmp_path / "site.json"
    user = tmp_path / "user.json"
    site.write_text(json.dumps({"schema": 1, "surfaces": {"tui": {"show": ["wlanpi.cpu"]},
                                                          "panel": {"show": ["wlanpi.ip"]}}}))
    user.write_text(json.dumps({"schema": 1, "surfaces": {"panel": {"show": ["wlanpi.mode"]}}}))
    d = C.DisplayConfig(None, site, user)
    assert d.entry("panel") == (["wlanpi.mode"], "user")
    assert d.entry("tui") == (["wlanpi.cpu"], "site"), "a user list for panel does not hide the site list for tui"
    assert d.entry("webui")[1] == "default"
    assert d.entry("newsurface") == (C.DEFAULT_SHOW["default"], "default")
    d.reset("panel")
    assert d.entry("panel") == (["wlanpi.ip"], "site")


def test_corrupt_user_file_is_renamed_aside(tmp_path, caplog):
    user = tmp_path / "complications.json"
    user.write_text("{not json")
    d = C.DisplayConfig(None, None, user)
    assert d.user == {} and not user.exists()
    assert list(tmp_path.glob("complications.json.bad-*"))


async def test_corrupt_user_file_does_not_stop_the_service(harnesses, sockdir):
    (sockdir / "state").mkdir()
    (sockdir / "state" / "complications.json").write_text("[1, 2")
    h = await harnesses()
    assert h.service.hub.data["complications.config"]["surfaces"]["panel"]["source"] == "default"


def test_profile_mapping():
    assert C.profile_for(None, "wlanpi-fpms") == "panel"
    assert C.profile_for(None, "wlanpi-menu-tui") == "tui"
    assert C.profile_for(None, "wlanpi-webui") == "webui"
    assert C.profile_for(None, "something") == "default"
    assert C.profile_for("tui", "wlanpi-fpms") == "tui"


# ---------------------------------------------------------------------------- home view

def home_rows(view) -> list[str]:
    kv = [b for b in view["blocks"] if b["type"] == "kv"]
    return [i["key"] for i in kv[0]["items"]] if kv else []


async def thin(h, name="pytest", screen=None, **hello):
    c = await h.connect()
    await c.send("hello", mode="thin", protocol=[1], client={"name": name},
                 capabilities={"blocks": ["text", "kv", "status"], "screen": screen or {"width_px": 128,
                                                                                       "height_px": 128}},
                 **hello)
    await c.expect(type="welcome")
    return c, await c.expect(type="view")


async def test_default_session_home_uses_the_panel_profile(harness):
    _, v = await thin(harness, "wlanpi-menu-tui")
    assert v["screen"] == "home" and v["title"] == "wlanpi-test"
    assert home_rows(v) == ["Mode", "IP", "Wi-Fi", "Temp"], "the panel defaults, in order"
    items = v["blocks"][0]["items"]
    assert items[0] == {"key": "Mode", "value": "Classic", "level": "info"}
    assert "Host" not in home_rows(v) and "Model" not in home_rows(v)


async def test_independent_session_uses_hello_surface(harness):
    _, v = await thin(harness, "pytest", session="new", surface="tui")
    assert home_rows(v)[:3] == ["Mode", "eth0", "IP"]


async def test_independent_session_maps_client_name(harness):
    _, v = await thin(harness, "wlanpi-menu-tui", session="new")
    assert "eth0" in home_rows(v)


async def test_home_capacity_at_128_and_64_px(harness):
    c, _ = await provider(harness)
    reply = await configure(c, "panel", show=["local.*"])
    assert reply["type"] == "event"
    for i in range(9):
        await publish(c, f"local.{SEG}.t{i}", label=f"T{i}", ttl_sec=600)
    _, v128 = await thin(harness, screen={"width_px": 128, "height_px": 128})
    assert home_rows(v128) == [f"T{i}" for i in range(7)], "8 body rows minus the strip row"
    _, v64 = await thin(harness, screen={"width_px": 128, "height_px": 64})
    assert home_rows(v64) == ["T0", "T1"], "the prefix that fits, nothing reordered"


async def test_alerts_take_rows_from_the_end(harness):
    c, _ = await provider(harness)
    await configure(c, "panel", show=["local.*"])
    for i in range(9):
        await publish(c, f"local.{SEG}.t{i}", label=f"T{i}", ttl_sec=600)
    harness.service.hub.publish("alerts", [{"level": "warn", "message": "NO INTERNET"},
                                           {"level": "warn", "message": "NO WI-FI ADAPTER"},
                                           {"level": "info", "message": "PROFILER ACTIVE"}])
    _, v = await thin(harness)
    assert home_rows(v) == [f"T{i}" for i in range(5)], "at most two alert rows count"
    assert [b["text"] for b in v["blocks"] if b["type"] == "status"][:2] == ["NO INTERNET", "NO WI-FI ADAPTER"]


async def test_home_repaints_when_the_layout_changes_elsewhere(harness):
    t, _ = await thin(harness)
    w, _ = await provider(harness, "proxy", headers=WEBUI)
    await configure(w, "panel", show=["wlanpi.ip", "wlanpi.mode"])
    v = await t.expect(lambda f: f["screen"] == "home" and home_rows(f) == ["IP", "Mode"], type="view")
    assert v["blocks"][0]["items"][0]["level"] == "ok"


async def test_unknown_status_is_drawn_as_dashes(harness):
    c, _ = await provider(harness)
    await configure(c, "panel", show=[MINE])
    await publish(c, MINE, label="VPN", value="maybe")
    _, v = await thin(harness)
    assert v["blocks"][0]["items"] == [{"key": "VPN", "value": "--", "level": "info"}]


# ---------------------------------------------------------------------------- CLI

async def run_cli(h, *args: str) -> tuple[int, str, str]:
    import contextlib
    import io

    def go():
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.run(["--socket", str(h.config.local_sock), *args])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()
    return await asyncio.to_thread(go)


async def test_cli_publish_list_remove(harness):
    code, out, _ = await run_cli(harness, "publish", MINE, "--label", "VPN", "--value", "up", "--status", "ok",
                                 "--ttl", "120")
    assert code == 0 and "live, ttl 120s" in out
    assert harness.service.complications[MINE]["value"] == "up"
    code, out, _ = await run_cli(harness, "list")
    assert code == 0 and MINE in out and "wlanpi.mode" in out
    code, out, _ = await run_cli(harness, "list", "--json")
    assert any(x["app_id"] == MINE for x in json.loads(out))
    code, out, _ = await run_cli(harness, "catalog")
    assert code == 0 and "reporting" in out
    code, out, _ = await run_cli(harness, "remove", MINE)
    assert code == 0 and MINE not in harness.service.complications
    assert len(harness.service.sessions) == 1, "every one-shot session is gone"


async def test_cli_refusals_and_usage(harness):
    code, _, err = await run_cli(harness, "publish", "wlanpi.mode", "--value", "x")
    assert code == 3 and "FORBIDDEN" in err
    code, _, err = await run_cli(harness, "remove", MINE)
    assert code == 3 and "NOT_FOUND" in err
    code, _, err = await run_cli(harness, "publish", MINE, "--value", "x", "--label", "VPN", "--ttl", "4")
    assert code == 3 and "BAD_REQUEST" in err
    code, _, _ = await run_cli(harness, "publish", MINE)
    assert code == 2


async def test_cli_show_set_reset(harness):
    code, out, _ = await run_cli(harness, "show", "panel")
    assert code == 0 and "panel (default)" in out and "wlanpi.mode" in out
    code, out, _ = await run_cli(harness, "show", "panel", "--set", "wlanpi.mode,wlanpi.ip,plugin.*")
    assert code == 0 and "panel (user)" in out
    assert harness.service.display.show_for("panel") == ["wlanpi.mode", "wlanpi.ip", "plugin.*"]
    code, out, _ = await run_cli(harness, "show", "panel", "--reset", "--json")
    assert code == 0 and json.loads(out)["panel"]["source"] == "default"


async def test_cli_service_unreachable(tmp_path):
    code = await asyncio.to_thread(cli.run, ["--socket", str(tmp_path / "nope.sock"), "list"])
    assert code == 4


# ---------------------------------------------------------------------------- customize node

async def test_customize_node_hide_show_move_reset_and_viewer_hidden(harness):
    # 1. Viewer cannot see system.home_tiles in menu; operator can
    ctx = harness.service.ctx
    viewer_view = harness.service.menu.view(ctx, ("viewer",))
    operator_view = harness.service.menu.view(ctx, ("operator",))
    assert not viewer_view.visible("system.home_tiles")
    assert operator_view.visible("system.home_tiles")
    assert "system.home_tiles" not in viewer_view.kids("system")
    assert "system.home_tiles" in operator_view.kids("system")

    # 2. Thin operator session invokes system.home_tiles
    c, _ = await thin(harness)
    await c.send("invoke", node="system.home_tiles")

    # Screen 1: Home tiles top-level
    p1 = await c.expect(type="prompt")
    assert p1["title"] == "Home tiles"
    assert any(o["value"] == "profile:panel" for o in p1["options"])
    assert any(o["value"] == "reset" for o in p1["options"])

    # Pick "Rows" (panel profile)
    await c.send("answer", prompt_id=p1["prompt_id"], value="profile:panel")

    # Screen 2: Tile picker
    p2 = await c.expect(type="prompt")
    assert p2["title"] == "Rows"
    assert any(o["value"] == "tile:wlanpi.mode" for o in p2["options"])

    # Pick wlanpi.mode
    await c.send("answer", prompt_id=p2["prompt_id"], value="tile:wlanpi.mode")

    # Screen 3: Action picker (hide, up, down, back)
    p3 = await c.expect(type="prompt")
    assert p3["title"] == "Mode"
    assert [o["value"] for o in p3["options"]] == ["hide", "up", "down", "back"]

    # Action: Hide
    await c.send("answer", prompt_id=p3["prompt_id"], value="hide")

    # Screen 2 again: cursor defaulted to tile:wlanpi.mode
    p4 = await c.expect(type="prompt")
    assert "wlanpi.mode" not in harness.service.display.show_for("panel")
    assert p4["title"] == "Rows"
    assert p4.get("default") == "tile:wlanpi.mode"
    mode_opt = next(o for o in p4["options"] if o["value"] == "tile:wlanpi.mode")
    assert mode_opt.get("detail") == "hidden"

    # Pick tile:wlanpi.mode again to show it
    await c.send("answer", prompt_id=p4["prompt_id"], value="tile:wlanpi.mode")
    p5 = await c.expect(type="prompt")
    assert [o["value"] for o in p5["options"]] == ["show", "back"]
    await c.send("answer", prompt_id=p5["prompt_id"], value="show")

    # Screen 2 again: pick tile:wlanpi.mode to move up
    p6 = await c.expect(type="prompt")
    assert "wlanpi.mode" in harness.service.display.show_for("panel")
    assert harness.service.display.show_for("panel")[-1] == "wlanpi.mode"
    await c.send("answer", prompt_id=p6["prompt_id"], value="tile:wlanpi.mode")
    p7 = await c.expect(type="prompt")
    await c.send("answer", prompt_id=p7["prompt_id"], value="up")

    # Screen 2 again: pick tile:wlanpi.mode to move down
    p8 = await c.expect(type="prompt")
    assert harness.service.display.show_for("panel")[-2] == "wlanpi.mode"
    await c.send("answer", prompt_id=p8["prompt_id"], value="tile:wlanpi.mode")
    p9 = await c.expect(type="prompt")
    await c.send("answer", prompt_id=p9["prompt_id"], value="down")

    # Cancel Screen 2 to return to Screen 1
    p10 = await c.expect(type="prompt")
    assert harness.service.display.show_for("panel")[-1] == "wlanpi.mode"
    await c.send("answer", prompt_id=p10["prompt_id"], cancel=True)

    # Screen 1: Reset to default
    p11 = await c.expect(type="prompt")
    assert p11["title"] == "Home tiles"
    await c.send("answer", prompt_id=p11["prompt_id"], value="reset")

    # Cancel Screen 1 to finish
    p12 = await c.expect(type="prompt")
    assert harness.service.display.entry("panel")[1] == "default"
    await c.send("answer", prompt_id=p12["prompt_id"], cancel=True)
    res = await c.expect(lambda f: f.get("state") in ("result", "error"), type="job")
    assert res["state"] == "result"

