"""Validate protocol samples against menu.schema.json and frames.schema.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from wlanpi_menud.manifest import load_schema, schema_errors, semantic_errors, validate_files

PROTOCOL = Path(__file__).resolve().parent.parent
SAMPLES = PROTOCOL / "samples"

MENU_SCHEMA = load_schema("menu.schema.json")
FRAMES_SCHEMA = load_schema("frames.schema.json")

MANIFESTS = sorted((SAMPLES / "manifests").glob("*.json"))
FRAMES = sorted((SAMPLES / "frames").rglob("*.json"))
BAD_MANIFESTS = sorted((SAMPLES / "invalid" / "manifests").glob("*.json"))
BAD_FRAMES = sorted((SAMPLES / "invalid" / "frames").glob("*.json"))

C2S = {"auth", "hello", "subscribe", "unsubscribe", "input", "invoke", "answer", "page", "cancel"}
S2C = {"welcome", "menu", "view", "prompt", "job", "event", "error"}
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
        assert defs[name]["properties"]["type"]["const"] == name
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
