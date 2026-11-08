"""Manifest loading: validate before registering, reject bad files by file and field, never crash."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import BUILTIN

from wlanpi_menud.core.generated import ROUTES
from wlanpi_menud.handlers import HANDLERS, handler_exists
from wlanpi_menud.loader import load_menu
from wlanpi_menud.manifest import load_schema, schema_errors
from wlanpi_menud.menu import DeviceContext, RouteTable

DATA = Path(__file__).parent / "data"
ALL_CAPS = frozenset({"transmit", "bluetooth", "battery", "monitor_adapter", "multiple_adapters", "ethernet",
                      "display", "gpio_buttons"})


def write(path: Path, doc) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return path


def node(nid, parent=None, order=10, **kw):
    return {"id": nid, "label": nid.rsplit(".", 1)[-1][:32], "parent": parent, "order": order, **kw}


def test_builtins_load_cleanly():
    res = load_menu(BUILTIN, None, None, handler_exists)
    assert [str(e) for e in res.errors] == []
    assert len(res.loaded) == len(list(BUILTIN.glob("*.json")))
    used = {n.action["handler"] for n in res.menu.nodes.values() if n.action and "handler" in n.action}
    assert used == set(HANDLERS), "every registered handler is used by a manifest, and vice versa"


def test_every_poc_action_is_mapped():
    """The PoC's 68 registry action ids (61 action_id= sites in menu_tree.py) all land in built-in manifests."""
    res = load_menu(BUILTIN, None, None, handler_exists)
    mapping = json.loads((DATA / "poc_action_map.json").read_text())["map"]
    assert len(mapping) == 68
    for poc_id, entry in mapping.items():
        if entry["node"] is None:
            assert poc_id.startswith("system.updates"), "only system updates is dropped (plan)"
            continue
        n = res.menu.get(entry["node"])
        assert n is not None and not n.is_branch, f"{poc_id} -> {entry['node']} missing"
    assert not any(nid.startswith("system.updates") for nid in res.menu.nodes)


def test_rebinds_from_the_plan():
    res = load_menu(BUILTIN, None, None, handler_exists)
    m = res.menu
    assert m.get("apps.profiler.purge").action["core"] == "POST /profiler/purge"
    assert not any("purge/files" in json.dumps(n.raw) or "purge/reports" in json.dumps(n.raw) for n in m.nodes.values())
    assert m.get("bluetooth.pair").action["core"] == "POST /bluetooth/pair"
    assert m.get("utils.port_blinker.start").action["core"].startswith("POST /utils/blinker/start")
    # Transmit units (hostapd, wpa_supplicant, Bluetooth) sit behind model capability rules.
    for nid in ("bluetooth.on", "bluetooth.pair", "modes.hotspot", "modes.server", "modes.bridge",
                "apps.profiler.start"):
        assert "transmit" in m.get(nid).visibility.get("capabilities", []), nid
    assert "bluetooth" in m.get("bluetooth").visibility["capabilities"]


def test_bad_files_rejected_with_file_and_field_others_load(tmp_path):
    plugins = tmp_path / "plugins.d"
    etc = tmp_path / "etc"
    write(plugins / "good" / "menu.json", {"schema": 1, "plugin": "good", "nodes": [
        node("plugin.good", "utils", 900), node("plugin.good.leases", "plugin.good",
                                                action={"core": "GET /network/dhcp/leases", "view": "table"})]})
    write(plugins / "broken" / "menu.json", "{not json")
    write(plugins / "schema" / "menu.json", {"schema": 1, "plugin": "schema", "nodes": [
        {"id": "plugin.schema.x", "label": "X", "parent": None, "order": 1, "action": {"core": "GET /x", "loop": True}}]})
    write(plugins / "squat" / "menu.json", {"schema": 1, "plugin": "squat", "nodes": [node("network.lldp", "network")]})
    write(plugins / "collide" / "menu.json", {"schema": 1, "plugin": "collide", "nodes": [
        node("plugin.collide", None, 900)]})
    write(plugins / "wrongdir" / "menu.json", {"schema": 1, "plugin": "other", "nodes": [node("plugin.other", None)]})
    write(plugins / "code" / "menu.json", {"schema": 1, "plugin": "code", "nodes": [
        node("plugin.code", None, 1, action={"handler": "network.interfaces"})]})
    write(plugins / "orphan" / "menu.json", {"schema": 1, "plugin": "orphan", "nodes": [
        node("plugin.orphan.a", "plugin.orphan.missing")]})
    write(plugins / "holes" / "menu.json", {"schema": 1, "plugin": "holes", "nodes": [
        node("plugin.holes", None, 1, action={"core": "GET /utils/cloud-test/{Vendor}"})]})
    write(etc / "local.json", {"schema": 1, "nodes": [node("local.thing", "system", 99,
                                                          action={"handler": "no.such_handler"})]})
    res = load_menu(BUILTIN, plugins, etc, handler_exists)
    errs = [str(e) for e in res.errors]

    def has(file_part, field_part, msg_part):
        return any(file_part in e and field_part in e and msg_part in e for e in errs)

    assert has("broken/menu.json", ": /:", "unreadable")
    assert has("schema/menu.json", "/nodes/0/action", "")
    assert has("squat/menu.json", "/nodes/0/id", "plugin node ids must start with plugin.squat")
    assert has("wrongdir/menu.json", "/plugin", "must live in plugins.d/other/")
    assert has("code/menu.json", "/nodes/0/action/handler", "plugins cannot use Python handlers")
    assert has("orphan/menu.json", "id=plugin.orphan.a/parent", "unknown parent plugin.orphan.missing")
    assert has("holes/menu.json", "/nodes/0/action/core", "{Vendor} is not a valid placeholder name")
    assert has("local.json", "/nodes/0/action/handler", "unknown handler no.such_handler")
    # Nothing from a rejected file is registered; everything else is.
    assert res.menu.get("plugin.good.leases") is not None
    assert res.menu.get("plugin.collide") is not None
    assert res.menu.get("network.lldp").raw["parent"] == "network"
    for bad in ("plugin.schema.x", "plugin.other", "plugin.code", "plugin.orphan.a", "plugin.holes", "local.thing"):
        assert res.menu.get(bad) is None
    assert len(res.loaded) == len(list(BUILTIN.glob("*.json"))) + 2
    assert res.menu.plugin_of["plugin.good.leases"] == "good"


def test_plugin_collision_with_builtin_and_cascade(tmp_path):
    plugins = tmp_path / "plugins.d"
    write(plugins / "a" / "menu.json", {"schema": 1, "plugin": "a", "nodes": [
        node("plugin.a", "utils", 900), node("plugin.a.x", "plugin.a", action={"core": "GET /utils/usb"})]})
    # b reuses a's id (refused), and c hangs off b's branch, so c goes too.
    write(plugins / "b" / "menu.json", {"schema": 1, "plugin": "b", "nodes": [node("plugin.b", "utils", 910)]})
    write(plugins / "b" / "extra.json", {"schema": 1, "plugin": "b", "nodes": [node("plugin.b", "utils", 911)]})
    res = load_menu(BUILTIN, plugins, None, handler_exists)
    errs = [str(e) for e in res.errors]
    assert any("extra.json" in e and "duplicate of" in e for e in errs)
    assert res.menu.get("plugin.a.x") is not None
    assert res.menu.get("plugin.b") is not None  # the first b file still loads


def test_overrides_replace_builtins_but_cannot_break_them(tmp_path):
    etc = tmp_path / "etc"
    write(etc / "10-rename.json", {"schema": 1, "nodes": [
        {**node("network.lldp", "network", 5), "label": "LLDP"}]})
    write(etc / "20-bad.json", {"schema": 1, "nodes": [
        node("network", None, 10, action={"core": "GET /network/info/"})]})
    write(etc / "30-plugin-id.json", {"schema": 1, "nodes": [node("plugin.x", None)]})
    res = load_menu(BUILTIN, None, etc, handler_exists)
    errs = [str(e) for e in res.errors]
    assert res.menu.get("network.lldp").label == "LLDP"
    assert any("20-bad.json" in e and "cannot become an action" in e for e in errs)
    assert res.menu.get("network").is_branch, "the built-in branch survives a bad override"
    assert res.menu.get("network.interfaces") is not None
    assert any("30-plugin-id.json" in e and "plugin.* ids require a plugin manifest" in e for e in errs)


def test_missing_and_garbage_dirs_never_crash(tmp_path):
    (tmp_path / "plugins.d" / "bin").mkdir(parents=True)
    (tmp_path / "plugins.d" / "bin" / "x.json").write_bytes(b"\xff\xfe\x00garbage")
    res = load_menu(tmp_path / "nope", tmp_path / "plugins.d", tmp_path / "also-nope", handler_exists)
    assert len(res.menu) == 0
    assert any("unreadable" in str(e) for e in res.errors)


def test_handler_flags_extension_in_schema():
    """Schema gap closed in this change: handler_action gains mutates / shareable / fresh_sec."""
    schema = load_schema("menu.schema.json")
    ok = {"schema": 1, "nodes": [node("a", None, action={"handler": "x.y", "shareable": True, "fresh_sec": 5})]}
    assert schema_errors(ok, schema, "f") == []
    for bad in ({"handler": "x.y", "mutates": True, "shareable": True},
                {"handler": "x.y", "fresh_sec": 5},
                {"handler": "x.y", "mutates": True, "fresh_sec": 5, "shareable": False}):
        doc = {"schema": 1, "nodes": [node("a", None, action=bad)]}
        assert schema_errors(doc, schema, "f"), bad


def test_route_table_templates():
    rt = RouteTable(set(ROUTES))
    assert rt.has("GET /network/info/")
    assert rt.has("GET /network/info")
    assert rt.has("POST /bluetooth/power/on")  # concrete value against /bluetooth/power/{action}
    assert rt.has("POST /profiler/purge")
    assert not rt.has("POST /profiler/purge/files")
    assert not rt.has("GET /utils/cloud-test/{vendor}")
    assert not rt.has("POST /system/mode/switch")
    assert not rt.has("GET /system/updates")


def _view(mode="classic", caps=ALL_CAPS, roles=("admin",), routes=None):
    res = load_menu(BUILTIN, None, None, handler_exists)
    return res.menu.view(DeviceContext(mode, caps, routes or RouteTable(set(ROUTES))), roles)


def test_visibility_requires_core_hides_missing_endpoints():
    v = _view()
    for hidden in ("utils.cloud", "utils.cloud.mist", "modes", "modes.hotspot", "apps.scanner.csv", "apps.scanner.pcap"):
        assert not v.visible(hidden), hidden
    routes = RouteTable(set(ROUTES) | {("GET", "/utils/cloud-test/{vendor}"), ("POST", "/system/mode/switch")})
    v = _view(routes=routes)
    assert v.visible("utils.cloud.mist") and v.visible("modes.hotspot") and v.visible("modes.server")
    assert not v.visible("modes.classic")  # classic mode: only the switch away from it
    # A core without the purge route hides the purge node (implicit check on the action's route).
    v = _view(routes=RouteTable(set(ROUTES) - {("POST", "/profiler/purge")}))
    assert not v.visible("apps.profiler.purge") and v.visible("apps.profiler.stop")


def test_visibility_mode_capability_role():
    hot = _view("hotspot", routes=RouteTable(set(ROUTES) | {("POST", "/system/mode/switch")}))
    assert not hot.visible("apps") and hot.visible("utils.ssid_passphrase") and hot.visible("modes.classic")
    assert not _view("classic").visible("utils.ssid_passphrase")
    go = _view(caps=frozenset({"monitor_adapter", "ethernet"}))  # a Go: no transmit, no Bluetooth
    assert not go.visible("bluetooth") and not go.visible("apps.profiler.start")
    assert not go.visible("apps.profiler.start_other") and go.visible("apps.profiler.status")
    viewer = _view(roles=("viewer",))
    assert viewer.visible("network.lldp")
    assert not viewer.visible("system.reboot") and not viewer.visible("apps.profiler.stop")
    assert not viewer.visible("utils.ssid_passphrase")
    op = _view(roles=("operator",))
    assert op.visible("system.reboot") and not op.visible("apps.profiler.purge")  # purge is admin
