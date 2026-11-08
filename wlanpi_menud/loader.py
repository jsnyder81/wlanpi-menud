"""Load manifests from the three manifest roots into a Menu.

Order and rules:

1. ``builtin_dir/*.json`` (shipped in this repo under ``share/menu.d``).
2. ``plugins_dir/<name>/*.json``; the manifest's ``plugin`` must equal
   ``<name>``, every id lives under ``plugin.<name>``, ids that collide with
   anything already registered are refused, and Python handlers are not
   allowed (third parties use providers).
3. ``override_dir/*.json``: may add nodes or replace an existing built-in
   node by reusing its id. ``plugin.*`` ids are refused.

Each file is accepted or rejected as a whole. A rejected file never
registers a node and never stops the others from loading. Rejecting a file
can orphan nodes in another file (unknown parent), so the tree checks repeat
until no further file is rejected. Nothing in here raises on bad input.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .manifest import ManifestError, file_errors, load_schema, schema_errors, tree_errors
from .menu import Menu

log = logging.getLogger(__name__)


@dataclass
class LoadResult:
    menu: Menu
    errors: list[ManifestError] = field(default_factory=list)
    loaded: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


@dataclass
class _File:
    path: str
    kind: str  # builtin | plugin | override
    doc: dict[str, Any]


def _read(path: Path, schema: dict[str, Any], errors: list[ManifestError]) -> dict[str, Any] | None:
    try:
        doc = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(ManifestError(str(path), "/", f"unreadable: {exc}"))
        return None
    errs = schema_errors(doc, schema, str(path))
    if errs:
        errors.extend(errs)
        return None
    return doc


def _json_files(directory: Path) -> list[Path]:
    try:
        return sorted(p for p in directory.glob("*.json") if p.is_file())
    except OSError:
        return []


def load_menu(
    builtin_dir: Path,
    plugins_dir: Path | None = None,
    override_dir: Path | None = None,
    handler_exists: Callable[[str], bool] | None = None,
) -> LoadResult:
    errors: list[ManifestError] = []
    rejected: list[str] = []
    try:
        schema = load_schema("menu.schema.json")
    except Exception as exc:  # a broken install must still not crash the service
        log.error("menu schema unusable: %s", exc)
        return LoadResult(Menu({}), [ManifestError("menu.schema.json", "/", str(exc))])

    candidates: list[_File] = []

    def consider(path: Path, kind: str, plugin_name: str | None = None) -> None:
        doc = _read(path, schema, errors)
        if doc is None:
            rejected.append(str(path))
            return
        errs = file_errors(
            str(path),
            doc,
            is_plugin=kind == "plugin",
            plugin_dir_name=plugin_name,
            allow_plugin_ids=False,
            handler_exists=handler_exists,
        )
        if kind == "override" and doc.get("plugin"):
            errs.append(ManifestError(str(path), "/plugin", "override files cannot declare a plugin"))
        if errs:
            errors.extend(errs)
            rejected.append(str(path))
            return
        candidates.append(_File(str(path), kind, doc))

    for path in _json_files(builtin_dir):
        consider(path, "builtin")
    if plugins_dir is not None:
        try:
            plugin_dirs = sorted(p for p in plugins_dir.iterdir() if p.is_dir())
        except OSError:
            plugin_dirs = []
        for pdir in plugin_dirs:
            for path in _json_files(pdir):
                consider(path, "plugin", pdir.name)
    if override_dir is not None:
        for path in _json_files(override_dir):
            consider(path, "override")

    # Merge: built-ins, then plugins, then overrides.
    accepted = list(candidates)
    while True:
        nodes: dict[str, dict[str, Any]] = {}
        owner: dict[str, str] = {}
        kind_of: dict[str, str] = {}
        bad: dict[str, list[ManifestError]] = {}
        for f in accepted:
            for i, node in enumerate(f.doc["nodes"]):
                nid = node["id"]
                if nid in owner and f.kind != "override":
                    what = "collides with a built-in id" if kind_of[nid] == "builtin" else f"duplicate of {owner[nid]}"
                    bad.setdefault(f.path, []).append(ManifestError(f.path, f"/nodes/{i}/id", f"{nid} {what}"))
                    continue
                if nid in owner and f.kind == "override" and kind_of[nid] == "plugin":
                    bad.setdefault(f.path, []).append(
                        ManifestError(f.path, f"/nodes/{i}/id", f"{nid} belongs to a plugin and cannot be overridden"))
                    continue
                nodes[nid] = node
                owner[nid] = f.path
                kind_of.setdefault(nid, f.kind)
        if not bad:
            file_kind = {f.path: f.kind for f in accepted}
            for e in tree_errors(nodes, owner):
                # An override that turns a branch into an action is the
                # override's fault, not the fault of the branch's children.
                nid = e.field.removeprefix("id=").removesuffix("/parent")
                parent = nodes.get(nid, {}).get("parent")
                blame = e.file
                if parent in owner and file_kind.get(owner[parent]) == "override" and owner[parent] != e.file:
                    blame = owner[parent]
                    e = ManifestError(blame, f"id={parent}/action", f"{parent} has children and cannot become an action")
                bad.setdefault(blame, []).append(e)
        if not bad:
            break
        for path, errs in bad.items():
            errors.extend(errs)
            rejected.append(path)
        accepted = [f for f in accepted if f.path not in bad]

    for e in errors:
        log.warning("manifest rejected: %s", e)
    origin = {nid: kind_of[nid] for nid in nodes}
    plugin_of = {}
    for f in accepted:
        if f.kind == "plugin":
            for node in f.doc["nodes"]:
                plugin_of[node["id"]] = f.doc["plugin"]
    menu = Menu(nodes, origin=origin, plugin_of=plugin_of)
    return LoadResult(menu, errors, [f.path for f in accepted], rejected)
