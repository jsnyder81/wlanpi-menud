"""Menu manifest validation.

Every manifest file is validated against ``protocol/menu.schema.json`` and
then checked as a set (unique ids, parents exist, no cycles, plugin
namespaces) before any node is registered. Errors name the file and the
field, so a bad plugin is rejected instead of crashing the service.

``loader.py`` builds on these checks to decide, file by file, what gets
registered.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

PROTOCOL_DIR = Path(__file__).resolve().parent.parent / "protocol"
if not PROTOCOL_DIR.is_dir():  # installed wheel: schemas ship inside the package
    PROTOCOL_DIR = Path(__file__).resolve().parent / "protocol"

_PLACEHOLDER = re.compile(r"\{([^}]*)\}")
_SEGMENT = re.compile(r"^[a-z][a-z0-9_]*$")
MUTATING = ("POST ", "PUT ", "PATCH ", "DELETE ")

_schema_cache: dict[tuple[str, Path], dict[str, Any]] = {}


@dataclass(frozen=True)
class ManifestError:
    file: str
    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.file}: {self.field}: {self.message}"


def load_schema(name: str, protocol_dir: Path = PROTOCOL_DIR) -> dict[str, Any]:
    key = (name, protocol_dir)
    if key not in _schema_cache:
        schema = json.loads((protocol_dir / name).read_text())
        Draft202012Validator.check_schema(schema)
        _schema_cache[key] = schema
    return _schema_cache[key]


def _pointer(path: Any) -> str:
    return "/" + "/".join(str(p) for p in path) if path else "/"


def schema_errors(doc: Any, schema: dict[str, Any], file: str) -> list[ManifestError]:
    validator = Draft202012Validator(schema)
    return [
        ManifestError(file, _pointer(e.absolute_path), e.message)
        for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    ]


def is_mutation(action: dict[str, Any]) -> bool:
    if "core" in action:
        return action["core"].startswith(MUTATING)
    return bool(action.get("mutates", False))


def file_errors(
    file: str,
    doc: dict[str, Any],
    *,
    is_plugin: bool = False,
    plugin_dir_name: str | None = None,
    allow_plugin_ids: bool = False,
    handler_exists: Callable[[str], bool] | None = None,
) -> list[ManifestError]:
    """Checks that need only one schema-valid file."""
    errors: list[ManifestError] = []
    plugin = doc.get("plugin")
    if is_plugin and not plugin:
        errors.append(ManifestError(file, "/plugin", "files under plugins.d/ must name their plugin"))
    if plugin and plugin_dir_name is not None and plugin != plugin_dir_name:
        errors.append(ManifestError(file, "/plugin", f"plugin {plugin} must live in plugins.d/{plugin}/"))
    seen: set[str] = set()
    for i, node in enumerate(doc["nodes"]):
        nid, where = node["id"], f"/nodes/{i}"
        if nid in seen:
            errors.append(ManifestError(file, f"{where}/id", f"{nid} appears twice in this file"))
        seen.add(nid)
        if plugin and not (nid == f"plugin.{plugin}" or nid.startswith(f"plugin.{plugin}.")):
            errors.append(ManifestError(file, f"{where}/id", f"plugin node ids must start with plugin.{plugin}"))
        if not plugin and not allow_plugin_ids and nid.startswith("plugin."):
            errors.append(ManifestError(file, f"{where}/id", "plugin.* ids require a plugin manifest"))

        params = [p["name"] for p in node.get("params", [])]
        if len(params) != len(set(params)):
            errors.append(ManifestError(file, f"{where}/params", "parameter names must be unique"))
        action = node.get("action", {})
        for name in action.get("bind", {}):
            if name not in params:
                errors.append(ManifestError(file, f"{where}/action/bind/{name}", "binds an undeclared parameter"))
        if "core" in action:
            path_params = {k for k, v in action.get("bind", {}).items() if v == "path"}
            holes = set(_PLACEHOLDER.findall(action["core"]))
            for hole in sorted(holes - path_params):
                if not _SEGMENT.match(hole):
                    errors.append(ManifestError(file, f"{where}/action/core", f"{{{hole}}} is not a valid placeholder name"))
                else:
                    errors.append(ManifestError(file, f"{where}/action/core", f"{{{hole}}} is not bound to a path parameter"))
            for name, where_to in action.get("bind", {}).items():
                if where_to == "body" and action["core"].startswith("GET "):
                    errors.append(ManifestError(file, f"{where}/action/bind/{name}", "a GET request has no body"))
        if "handler" in action:
            if plugin:
                errors.append(ManifestError(file, f"{where}/action/handler", "plugins cannot use Python handlers; use a provider"))
            elif handler_exists is not None and not handler_exists(action["handler"]):
                errors.append(ManifestError(file, f"{where}/action/handler", f"unknown handler {action['handler']}"))
        if action:
            secret = any(p.get("secret") for p in node.get("params", []))
            if secret and action.get("shareable"):
                errors.append(ManifestError(file, f"{where}/action/shareable", "a node with a secret parameter is never shareable"))
            if "handler" in action and action.get("mutates") and action.get("shareable"):
                errors.append(ManifestError(file, f"{where}/action/shareable", "a mutating handler is never shareable"))
    return errors


def tree_errors(nodes: dict[str, dict[str, Any]], owner: dict[str, str]) -> list[ManifestError]:
    """Parent and cycle checks over a merged node set."""
    errors: list[ManifestError] = []
    for nid, node in nodes.items():
        parent = node["parent"]
        if parent is not None and parent not in nodes:
            errors.append(ManifestError(owner[nid], f"id={nid}/parent", f"unknown parent {parent}"))
        elif parent is not None and "action" in nodes[parent]:
            errors.append(ManifestError(owner[nid], f"id={nid}/parent", f"parent {parent} is an action, not a branch"))
    for nid in nodes:
        seen, cur = set(), nid
        while cur is not None and cur in nodes:
            if cur in seen:
                errors.append(ManifestError(owner[nid], f"id={nid}/parent", "parent chain forms a cycle"))
                break
            seen.add(cur)
            cur = nodes[cur]["parent"]
    return errors


def semantic_errors(
    manifests: dict[str, dict[str, Any]], builtin_files: set[str] | None = None
) -> list[ManifestError]:
    """Cross-node checks over a set of schema-valid manifests keyed by file name."""
    errors: list[ManifestError] = []
    builtin_files = builtin_files or set()
    owner: dict[str, str] = {}
    nodes: dict[str, dict[str, Any]] = {}

    for file, doc in manifests.items():
        errors.extend(file_errors(file, doc, allow_plugin_ids=file in builtin_files))
        for i, node in enumerate(doc["nodes"]):
            nid, where = node["id"], f"/nodes/{i}"
            if nid in owner:
                if owner[nid] == file:
                    continue  # reported by file_errors
                what = "collides with a built-in id" if owner[nid] in builtin_files else f"duplicate of {owner[nid]}"
                errors.append(ManifestError(file, f"{where}/id", f"{nid} {what}"))
                continue
            owner[nid] = file
            nodes[nid] = node
    errors.extend(tree_errors(nodes, owner))
    return errors


def validate_files(paths: list[Path], builtin: set[Path] | None = None) -> list[ManifestError]:
    schema = load_schema("menu.schema.json")
    errors: list[ManifestError] = []
    docs: dict[str, dict[str, Any]] = {}
    for path in paths:
        try:
            doc = json.loads(path.read_text())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(ManifestError(str(path), "/", f"unreadable: {exc}"))
            continue
        errs = schema_errors(doc, schema, str(path))
        errors.extend(errs)
        if not errs:
            docs[str(path)] = doc
    errors.extend(semantic_errors(docs, {str(p) for p in (builtin or set())}))
    return errors
