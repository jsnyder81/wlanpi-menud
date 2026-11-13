"""Validate protocol samples against menu.schema.json and frames.schema.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from wlanpi_menud.manifest import (
    load_schema,
    schema_errors,
    semantic_errors,
    validate_files,
)

PROTOCOL = Path(__file__).resolve().parent.parent
SAMPLES = PROTOCOL / "samples"

MENU_SCHEMA = load_schema("menu.schema.json")
FRAMES_SCHEMA = load_schema("frames.schema.json")

MANIFESTS = sorted((SAMPLES / "manifests").glob("*.json"))
FRAMES = sorted((SAMPLES / "frames").rglob("*.json"))
BAD_MANIFESTS = sorted((SAMPLES / "invalid" / "manifests").glob("*.json"))
BAD_FRAMES = sorted((SAMPLES / "invalid" / "frames").glob("*.json"))

C2S = {"auth", "hello", "subscribe", "unsubscribe", "input", "invoke", "answer", "page", "cancel", "revoke", "nav",
       "complication.publish", "complication.remove", "complications.configure"}
S2C = {"welcome", "menu", "view", "prompt", "job", "event", "error", "complication.ack"}
BLOCKS = {"text", "kv", "table", "list", "qr", "status", "progress", "confirm"}
JOB_STATES = {"queued", "progress", "result", "error", "cancelled"}
PLAN_ERROR_CODES = {"AUTH_FAILED", "FORBIDDEN", "CONFLICT", "VERSION"}


def _load(path: Path):
    return json.loads(path.read_text())


def _rel(path: Path) -> str:
    return str(path.relative_to(SAMPLES))


def test_schemas_are_valid_draft_2020_12():
    for schema in (MENU_SCHEMA, FRAMES_SCHEMA):
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("path", MANIFESTS, ids=_rel)
def test_manifest_sample_is_valid(path):
    assert schema_errors(_load(path), MENU_SCHEMA, _rel(path)) == []


def test_manifest_set_is_consistent():
    builtin = SAMPLES / "manifests" / "builtin.json"
    errors = validate_files(MANIFESTS, builtin={builtin})
    assert [str(e) for e in errors] == []


def test_builtin_manifest_covers_poc_ids():
    ids = {n["id"] for n in _load(SAMPLES / "manifests" / "builtin.json")["nodes"]}
    for carried in ("network.lldp", "network.publicip6", "apps.scanner.scan", "system.reboot", "utils.cloud.mist"):
        assert carried in ids
    assert not any(i.startswith("system.updates") for i in ids), "system updates is dropped per the plan"


@pytest.mark.parametrize("path", BAD_MANIFESTS, ids=_rel)
def test_invalid_manifest_is_rejected(path):
    assert schema_errors(_load(path), MENU_SCHEMA, _rel(path)), f"{path.name} should fail"


def test_semantic_errors_name_file_and_field():
    docs = {
        "plugins.d/evil/menu.json": {
            "schema": 1,
            "plugin": "evil",
            "nodes": [
                {"id": "network.lldp", "label": "X", "parent": None, "order": 1},
                {"id": "plugin.evil.a", "label": "A", "parent": "plugin.evil.missing", "order": 1},
                {
                    "id": "plugin.evil.b", "label": "B", "parent": None, "order": 2,
                    "action": {"core": "GET /utils/cloud-test/{vendor}"},
                },
            ],
        },
        "menu.d/builtin.json": {
            "schema": 1,
            "nodes": [{"id": "network.lldp", "label": "LLDP", "parent": None, "order": 1}],
        },
    }
    messages = [str(e) for e in semantic_errors(docs, builtin_files={"menu.d/builtin.json"})]
    assert any("plugins.d/evil/menu.json: /nodes/0/id: plugin node ids must start with plugin.evil" in m for m in messages)
    assert any("unknown parent plugin.evil.missing" in m for m in messages)
    assert any("{vendor} is not bound" in m for m in messages)
    assert any("collides with a built-in id" in m for m in messages) or any("duplicate" in m for m in messages)


def test_cycle_detected():
    docs = {"a.json": {"schema": 1, "nodes": [
        {"id": "a", "label": "A", "parent": "b", "order": 1},
        {"id": "b", "label": "B", "parent": "a", "order": 1},
    ]}}
    assert any("cycle" in str(e) for e in semantic_errors(docs))


@pytest.mark.parametrize("path", FRAMES, ids=_rel)
def test_frame_sample_is_valid(path):
    frame = _load(path)
    errors = list(Draft202012Validator(FRAMES_SCHEMA).iter_errors(frame))
    assert errors == [], [e.message for e in errors]
    direction = path.parent.name
    assert frame["type"] in (C2S if direction == "c2s" else S2C), f"{frame['type']} filed under {direction}"


@pytest.mark.parametrize("path", BAD_FRAMES, ids=_rel)
def test_invalid_frame_is_rejected(path):
    assert not Draft202012Validator(FRAMES_SCHEMA).is_valid(_load(path)), f"{path.name} should fail"


def test_every_message_type_has_a_sample():
    seen = {_load(p)["type"] for p in FRAMES}
    assert seen == C2S | S2C


def test_samples_cover_blocks_job_states_and_errors():
    frames = [_load(p) for p in FRAMES]
    blocks = set()
    for f in frames:
        for b in f.get("blocks", []) + f.get("result", {}).get("blocks", []):
            blocks.add(b["type"])
    assert blocks == BLOCKS
    assert {f["state"] for f in frames if f["type"] == "job"} == JOB_STATES
    assert PLAN_ERROR_CODES <= {f["code"] for f in frames if f["type"] == "error"}


def test_schema_enumerates_plan_vocabulary():
    defs = FRAMES_SCHEMA["$defs"]
    assert set(defs["block_type"]["enum"]) == BLOCKS
    assert PLAN_ERROR_CODES <= set(defs["error_code"]["enum"])
    assert set(defs["job"]["properties"]["state"]["enum"]) == JOB_STATES
    assert set(defs["hello"]["properties"]["mode"]["enum"]) == {"thin", "rich"}
    assert "4401" in FRAMES_SCHEMA["x-close-codes"]
    for name in C2S | S2C:
        assert defs[name.replace(".", "_")]["properties"]["type"]["const"] == name
    for block in defs["block"]["oneOf"]:
        target = defs[block["$ref"].rsplit("/", 1)[-1]]
        assert "text" in target["required"], f"{block['$ref']} must carry a text fallback"


def test_frame_param_defs_match_manifest_defs():
    """menu_node.params, prompt.fields and confirm are typed with copies of the manifest defs."""
    for name in ("segment", "param", "param_enum", "param_integer", "param_string", "param_adapter", "confirm"):
        assert FRAMES_SCHEMA["$defs"][name] == MENU_SCHEMA["$defs"][name], name
    defs = FRAMES_SCHEMA["$defs"]
    assert defs["menu_node"]["properties"]["params"]["items"] == {"$ref": "#/$defs/param"}
    assert defs["prompt"]["properties"]["fields"]["items"] == {"$ref": "#/$defs/param"}
    assert defs["menu_node"]["properties"]["confirm"] == {"$ref": "#/$defs/confirm"}
    loose = {"v": 1, "type": "menu", "id": "s:1", "revision": 0, "nodes": [
        {"id": "a", "label": "A", "parent": None, "order": 1, "kind": "action",
         "params": [{"name": "x", "label": "X", "type": "enum"}]}]}  # enum without choices
    assert not Draft202012Validator(FRAMES_SCHEMA).is_valid(loose)


# ---------------------------------------------------------------------------- complications (additive)

COMPLICATIONS_SCHEMA = load_schema("complications.schema.json")
DECLARATIONS = sorted((SAMPLES / "complications").glob("declaration*.json"))
BAD_DECLARATIONS = sorted((SAMPLES / "invalid" / "complications").glob("*.json"))
VECTORS = _load(SAMPLES / "complications" / "resolve-vectors.json")["cases"]


def test_complications_schema_is_valid_draft_2020_12():
    assert COMPLICATIONS_SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(COMPLICATIONS_SCHEMA)


@pytest.mark.parametrize("path", DECLARATIONS, ids=_rel)
def test_declaration_sample_is_valid(path):
    assert schema_errors(_load(path), COMPLICATIONS_SCHEMA, _rel(path)) == []


@pytest.mark.parametrize("path", BAD_DECLARATIONS, ids=_rel)
def test_invalid_declaration_is_rejected(path, tmp_path):
    """Each fails the schema, or (wrong namespace) the loader's directory rule."""
    from wlanpi_menud.complications import load_declarations

    pdir = tmp_path / "acme"
    pdir.mkdir()
    (pdir / "complications.json").write_text(path.read_text())
    decls, errors = load_declarations(tmp_path)
    assert decls == {} and errors, f"{path.name} should be rejected"


def test_new_frame_types_and_topics_are_typed():
    defs = FRAMES_SCHEMA["$defs"]
    bound = {b["if"]["properties"]["topic"]["const"]: b["then"]["properties"]["data"]["$ref"]
             for b in defs["event"]["allOf"] if "if" in b}
    assert bound["complications.live"] == "#/$defs/topic_complications_live"
    assert bound["complications.catalog"] == "#/$defs/topic_complications_catalog"
    assert bound["complications.config"] == "#/$defs/topic_complications_config"
    assert set(defs["complication_status"]["enum"]) == {"ok", "info", "warn", "error", "off", "unknown"}
    refs = {r["$ref"] for r in FRAMES_SCHEMA["oneOf"]}
    for name in ("complication_publish", "complication_remove", "complications_configure", "complication_ack"):
        assert f"#/$defs/{name}" in refs


def test_legacy_complications_and_summary_are_frozen():
    """The legacy topic and device.summary keep their exact pre-complications shape."""
    defs = FRAMES_SCHEMA["$defs"]
    legacy = defs["topic_complications"]["items"]
    assert legacy["required"] == ["app_id", "label", "value", "status"]
    assert legacy["properties"] == {"app_id": {"type": "string"}, "label": {"type": "string", "maxLength": 8},
                                    "value": {"type": "string"}, "status": {"type": "string"}}
    assert set(defs["topic_device_summary"]["properties"]) == {
        "core", "hostname", "model", "mode", "software_version", "ip", "eth0", "wlan0", "cpu", "cpu_temp",
        "uptime", "battery", "bluetooth_on", "profiler_running", "reachable", "wlan_adapters", "hotspot_clients"}


@pytest.mark.parametrize("case", VECTORS, ids=lambda c: c["name"])
def test_resolve_vectors(case):
    from wlanpi_menud.complications import resolve

    got = resolve(case["show"], case["live"])
    if "capacity" in case:
        got = got[: case["capacity"]]
    assert got == case["expect"]


# Each new invalid sample, validated against its own $def, fails at this path (or with this message).
INTENDED = {
    "complication-publish-bad-namespace.json": "/app_id",
    "complication-publish-uppercase-id.json": "/app_id",
    "complication-publish-label-9-chars.json": "/label",
    "complication-publish-value-21-chars.json": "/value",
    "complication-publish-short-5-chars.json": "/short",
    "complication-publish-bad-status.json": "/status",
    "complication-publish-ttl-too-low.json": "/ttl_sec",
    "complication-publish-ttl-too-high.json": "/ttl_sec",
    "complication-publish-unknown-key.json": "colour",
    "complication-remove-no-app-id.json": "app_id",
    "complications-configure-show-and-reset.json": "not valid under any of the given schemas",
    "complications-configure-neither.json": "not valid under any of the given schemas",
    "complications-configure-25-entries.json": "/show",
    "complications-configure-bad-pattern.json": "/show/1",
    "event-complications-live-bad-status.json": "/data/0/status",
    "event-complications-live-drift.json": "expires_at",
    "event-complications-config-bad-source.json": "/data/surfaces/panel/source",
    "welcome-features-not-strings.json": "/features/0",
    "hello-ephemeral-not-boolean.json": "/ephemeral",
    "view-kv-bad-level.json": "/blocks/0",
}


@pytest.mark.parametrize("name", sorted(INTENDED))
def test_new_invalid_frame_fails_for_the_intended_reason(name):
    from wlanpi_menud import protocol

    frame = _load(SAMPLES / "invalid" / "frames" / name)
    errs = protocol.frame_errors(frame, protocol.C2S + protocol.S2C)
    assert errs, name
    assert any(INTENDED[name] in e for e in errs), errs
