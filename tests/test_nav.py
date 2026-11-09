"""Per-session navigation: a pure navigator and one cursor per session."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from conftest import BUILTIN

from wlanpi_menud.core.generated import ROUTES
from wlanpi_menud.handlers import handler_exists
from wlanpi_menud.loader import load_menu
from wlanpi_menud.menu import DeviceContext, RouteTable
from wlanpi_menud.nav import Cursor, NavContext, PromptState, handle_input, normalize

CAPS = frozenset({"transmit", "bluetooth", "monitor_adapter", "ethernet"})


def view(mode="classic"):
    menu = load_menu(BUILTIN, None, None, handler_exists).menu
    return menu.view(DeviceContext(mode, CAPS, RouteTable(set(ROUTES))), ("admin",))


def press(cursor, keys, ctx):
    effects = []
    for k in keys:
        r = handle_input(cursor, k, ctx)
        cursor = r.cursor
        effects.extend(r.effects)
    return cursor, effects


def test_handle_input_is_pure():
    ctx = NavContext(view())
    c0 = Cursor()
    r1 = handle_input(c0, "down", ctx)
    r2 = handle_input(c0, "down", ctx)
    assert c0 == Cursor(), "the input cursor is never mutated"
    assert r1 == r2


def test_menu_walk_and_invoke_effect():
    v = view()
    ctx = NavContext(v)
    c, eff = press(Cursor(), ["down"], ctx)
    assert c.screen == "menu" and c.stack == () and v.kids(None)[c.index] == "network"
    c, eff = press(c, ["center"], ctx)
    assert c.stack == ("network",) and c.index == 0
    c, eff = press(c, ["down", "down", "down", "down", "down"], ctx)  # interfaces, wlan, adapters, eth0 ip, vlan, lldp
    assert v.kids("network")[c.index] == "network.lldp"
    c, eff = press(c, ["right"], ctx)
    assert c.screen == "result" and c.node == "network.lldp"
    assert [(e.kind, e.node) for e in eff] == [("invoke", "network.lldp")]
    c, eff = press(c, ["left"], ctx)
    assert c.screen == "menu" and c.stack == ("network",) and v.kids("network")[c.index] == "network.lldp"
    c, _ = press(c, ["left"], ctx)
    assert c.stack == () and v.kids(None)[c.index] == "network"
    c, _ = press(c, ["left"], ctx)
    assert c.screen == "home"


def test_wraparound_and_shortcuts():
    v = view()
    ctx = NavContext(v)
    c, _ = press(Cursor(), ["down", "up"], ctx)
    assert v.kids(None)[c.index] == v.kids(None)[-1]
    c, eff = press(Cursor(), ["key1"], ctx)
    assert c.node == "utils.reachability" and eff[0].kind == "invoke"
    c, eff = press(c, ["key1"], NavContext(v))
    assert c.node == "network.lldp"
    c, eff = press(c, ["key3"], ctx)
    assert eff[-1].node == "system.shutdown"
    c, eff = press(c, ["key3"], ctx)
    assert eff[-1].node == "system.reboot"


def test_result_paging_and_detach():
    v = view()
    c = replace(Cursor(), screen="result", node="apps.scanner.scan", job_id="jabcd")
    ctx = NavContext(v, page_count=3)
    c, eff = press(c, ["down", "down", "down"], ctx)
    assert c.page == 2 and [e.page for e in eff] == [1, 2]
    c, eff = press(c, ["up"], ctx)
    assert c.page == 1
    _, eff = press(c, ["left"], NavContext(v, job_running=True))
    assert eff[0].kind == "detach" and eff[0].job_id == "jabcd"


def test_prompt_navigation():
    v = view()
    c = replace(Cursor(), screen="prompt", prompt_id="pabcd", job_id="jabcd", return_screen="result")
    sel = NavContext(v, prompt=PromptState("pabcd", "select", option_count=3))
    c2, eff = press(c, ["down", "down", "down", "down", "center"], sel)
    assert c2.prompt_index == 1 and eff[-1].kind == "answer" and eff[-1].value == 1
    conf = NavContext(v, prompt=PromptState("pabcd", "confirm"))
    _, eff = press(c, ["center"], conf)
    assert eff[-1].value is False, "confirm defaults to cancel"
    _, eff = press(c, ["down", "center"], conf)
    assert eff[-1].value is True
    num = NavContext(v, prompt=PromptState("pabcd", "integer", minimum=1, maximum=3))
    c3, eff = press(c, ["up", "up", "up", "up", "center"], num)
    assert eff[-1].value == 3
    _, eff = press(c, ["left"], sel)
    assert eff[-1].kind == "cancel_prompt"


def test_normalize_after_menu_change():
    classic, hotspot = view("classic"), view("hotspot")
    c = Cursor(screen="menu", stack=("apps", "apps.profiler"), index=3)
    n = normalize(c, hotspot)
    assert n.stack == () and n.index < len(hotspot.kids(None))
    assert normalize(c, classic) == c


# ---------------------------------------------------------------------------- over the socket

async def test_two_thin_sessions_navigate_independently(harness):
    a, b = await harness.connect(), await harness.connect()
    wa, wb = await a.hello(session="new"), await b.hello(session="new")
    assert wa["session_id"] != wb["session_id"] and wa["session"] == wb["session"] == "new"
    for c in (a, b):
        await c.expect(type="view")

    va = await a.input("down")  # A: top menu
    await a.input("center")  # A: into Network
    va = await a.input("down")  # A: Network > WLAN Interfaces
    vb = await b.input("down")  # B: top menu, untouched by A
    vb = await b.input("down")  # B: second top-level entry
    assert va["screen"] == "menu" and va["breadcrumb"] == ["Network"] and va["node"] == "network.wlan_interfaces"
    assert vb["screen"] == "menu" and vb.get("breadcrumb", []) == [] and vb["node"] == "bluetooth"
    sa = harness.service.sessions[wa["session_id"]].cursor
    sb = harness.service.sessions[wb["session_id"]].cursor
    assert sa != sb and sa.stack == ("network",) and sb.stack == ()

    # A runs an action; B stays on its menu and receives no view for A's job.
    await a.input("center")
    res = await a.expect(lambda f: f["screen"] == "result", type="view")
    assert res["node"] == "network.wlan_interfaces"
    await asyncio.sleep(0.1)
    b_views = [f for f in b.frames if f["type"] == "view"]
    assert b_views[-1]["node"] == "bluetooth" and all(f.get("screen") != "result" for f in b_views)


async def test_rich_session_gets_tree_and_no_views(harness):
    r = await harness.connect()
    await r.hello("rich")
    menu = await r.expect(type="menu")
    ids = {n["id"] for n in menu["nodes"]}
    assert "network.lldp" in ids and "utils.cloud" not in ids
    lldp = next(n for n in menu["nodes"] if n["id"] == "network.lldp")
    assert lldp["kind"] == "action" and "core" not in lldp and lldp["mutates"] is False
    mid = await r.send("input", key="down")
    err = await r.expect(type="error")
    assert err["id"] == mid and err["code"] == "BAD_REQUEST"
    mid = await r.send("invoke", node="network.lldp")
    q = await r.expect(lambda f: f["id"] == mid, type="job")
    assert q["state"] == "queued"
    res = await r.expect(lambda f: f["state"] == "result", type="job")
    assert res["result"]["blocks"][0]["type"] == "kv"
    assert {"key": "Name", "value": "sw1"} in res["result"]["blocks"][0]["items"]
    await asyncio.sleep(0.05)
    assert not any(f["type"] == "view" for f in r.frames)


async def test_menu_follows_device_mode(harness):
    c = await harness.connect()
    await c.hello("rich")
    first = await c.expect(type="menu")
    assert any(n["id"] == "apps" for n in first["nodes"])
    harness.fake.mode = "hotspot"
    harness.poller.invalidate_context()  # a mode switch made outside the menu
    await harness.poller.poll_once()
    m = await c.expect(type="menu")
    ids = {n["id"] for n in m["nodes"]}
    assert m["revision"] > first["revision"] and m["device_mode"] == "hotspot"
    assert "apps" not in ids and "utils.ssid_passphrase" in ids


async def test_nav_to_branch(harness):
    c = await harness.connect()
    w = await c.hello("thin", session="new")
    await c.expect(type="view")  # Initial home view

    # Nav to branch "network"
    mid = await c.send("nav", node="network")
    v = await c.expect(lambda f: f["id"] == mid, type="view")
    assert v["screen"] == "menu"
    assert v["breadcrumb"] == ["Network"]
    assert v["title"] == "Network"
    cur = harness.service.sessions[w["session_id"]].cursor
    assert cur.screen == "menu" and cur.stack == ("network",) and cur.index == 0


async def test_nav_to_view_action_renders_view(harness):
    c = await harness.connect()
    w = await c.hello("thin", session="new")
    await c.expect(type="view")  # Initial home view

    # Nav to view-action "network.lldp"
    mid = await c.send("nav", node="network.lldp")
    # Resulting view frame is pushed with the action result blocks
    v = await c.expect(lambda f: f.get("screen") == "result" and f.get("node") == "network.lldp", type="view")
    assert v["screen"] == "result"
    assert v["node"] == "network.lldp"
    assert any(b.get("type") in ("kv", "table") for b in v.get("blocks", []))
    cur = harness.service.sessions[w["session_id"]].cursor
    assert cur.screen == "result" and cur.stack == ("network",) and cur.node == "network.lldp"


async def test_nav_invalid_node_and_detached(harness):
    c = await harness.connect()
    w = await c.hello("thin", session="new")
    await c.expect(type="view")

    # Unknown node -> NOT_FOUND
    mid1 = await c.send("nav", node="nonexistent.node")
    err1 = await c.expect(lambda f: f["id"] == mid1, type="error")
    assert err1["code"] == "NOT_FOUND"

    # Detached attachment -> FORBIDDEN
    session = harness.service.sessions[w["session_id"]]
    att = session.attachments[0]
    session.detach(att)
    sent: list[dict[str, Any]] = []
    att.send = lambda f, **kw: sent.append(f)
    harness.service.handle_frame(att, {"v": 1, "type": "nav", "id": "nav_det", "node": "network"})
    assert len(sent) == 1 and sent[0]["type"] == "error" and sent[0]["code"] == "FORBIDDEN"


async def test_nav_visibility_guard(harness):
    c = await harness.connect()
    w = await c.hello("thin", session="new")
    await c.expect(type="view")

    # 1. Hidden branch node (route-gated: utils.cloud requires missing core endpoint)
    # The node exists in menu, but is not visible to this session
    assert harness.service.menu.get("utils.cloud") is not None
    assert not harness.service.sessions[w["session_id"]].attachments[0].menu_view.visible("utils.cloud")
    mid_branch = await c.send("nav", node="utils.cloud")
    err_branch = await c.expect(lambda f: f["id"] == mid_branch, type="error")
    assert err_branch["code"] == "FORBIDDEN"
    assert "not available to this session" in err_branch["message"]

    # 2. Hidden action node (route-gated: utils.cloud.mist)
    assert harness.service.menu.get("utils.cloud.mist") is not None
    assert not harness.service.sessions[w["session_id"]].attachments[0].menu_view.visible("utils.cloud.mist")
    mid_leaf = await c.send("nav", node="utils.cloud.mist")
    err_leaf = await c.expect(lambda f: f["id"] == mid_leaf, type="error")
    assert err_leaf["code"] == "FORBIDDEN"

    # 3. Mode-filtered branch node (apps is hidden in hotspot mode)
    harness.fake.mode = "hotspot"
    harness.poller.invalidate_context()
    await harness.poller.poll_once()
    att = harness.service.sessions[w["session_id"]].attachments[0]
    assert harness.service.menu.get("apps") is not None
    assert not att.menu_view.visible("apps")
    mid_mode = await c.send("nav", node="apps")
    err_mode = await c.expect(lambda f: f["id"] == mid_mode, type="error")
    assert err_mode["code"] == "FORBIDDEN"

    # Verify session cursor did not navigate to any forbidden node
    cur = harness.service.sessions[w["session_id"]].cursor
    assert "utils.cloud" not in cur.stack and cur.node != "utils.cloud"
    assert "apps" not in cur.stack


