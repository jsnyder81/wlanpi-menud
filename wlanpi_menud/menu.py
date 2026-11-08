"""The registered menu and per-session visibility.

A ``Menu`` is immutable once loaded. ``Menu.view(ctx)`` resolves it for one
session: hidden nodes (visibility rules, role, missing core routes) and
their subtrees are removed, and branches left with no visible children are
hidden too. The result is a ``MenuView`` the navigator and the ``menu``
frame are built from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .manifest import is_mutation

ROLE_RANK = {"viewer": 0, "provider": 0, "operator": 1, "admin": 2}


def role_rank(roles: tuple[str, ...] | list[str]) -> int:
    return max((ROLE_RANK.get(r, 0) for r in roles), default=0)


@dataclass(frozen=True)
class DeviceContext:
    """What the service knows about the device; drives visibility."""

    mode: str | None = None
    capabilities: frozenset[str] = frozenset()
    core_routes: "RouteTable | None" = None


class RouteTable:
    """Core routes from an OpenAPI document, matched with path templates.

    ``GET /utils/cloud-test/arista`` matches core's
    ``/utils/cloud-test/{vendor}``; ``GET /utils/cloud-test/{vendor}`` in a
    manifest matches it too.
    """

    def __init__(self, routes: set[tuple[str, str]]):
        self.routes = {(m.upper(), self._norm(p)) for m, p in routes}
        self._patterns = [
            (m, re.compile("^" + re.sub(r"\\\{[^}]*\\\}", "[^/]+", re.escape(p)) + "$"))
            for m, p in self.routes
        ]

    @staticmethod
    def _norm(path: str) -> str:
        path = re.sub(r"\{[^}]*\}", "{}", path)
        return path.rstrip("/") or "/"

    @classmethod
    def from_openapi(cls, doc: dict[str, Any], prefix: str = "/api/v1") -> "RouteTable":
        routes = set()
        for path, item in doc.get("paths", {}).items():
            rel = path[len(prefix):] if path.startswith(prefix) else path
            for method in item:
                if method.lower() in ("get", "post", "put", "patch", "delete"):
                    routes.add((method.upper(), rel))
        return cls(routes)

    def has(self, ref: str) -> bool:
        method, _, path = ref.partition(" ")
        norm = self._norm(path)
        if (method, norm) in self.routes:
            return True
        concrete = path.rstrip("/") or "/"
        if "{" not in concrete:
            return any(m == method and pat.match(concrete) for m, pat in self._patterns)
        return False


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    parent: str | None
    order: int
    raw: dict[str, Any]

    @property
    def action(self) -> dict[str, Any] | None:
        return self.raw.get("action")

    @property
    def is_branch(self) -> bool:
        return "action" not in self.raw

    @property
    def params(self) -> list[dict[str, Any]]:
        return self.raw.get("params", [])

    @property
    def visibility(self) -> dict[str, Any]:
        return self.raw.get("visibility", {})

    @property
    def mutates(self) -> bool:
        return bool(self.action) and is_mutation(self.action)

    @property
    def min_role(self) -> str:
        return self.visibility.get("min_role") or ("operator" if self.mutates else "viewer")

    @property
    def shareable(self) -> bool:
        a = self.action or {}
        return bool(a.get("shareable")) and not self.mutates and not any(p.get("secret") for p in self.params)

    @property
    def fresh_sec(self) -> int:
        return int((self.action or {}).get("fresh_sec", 0)) if self.shareable else 0

    @property
    def cancellable(self) -> bool:
        return bool((self.action or {}).get("cancellable", False))

    @property
    def timeout_sec(self) -> int:
        return int((self.action or {}).get("timeout_sec", 30))

    @property
    def confirm(self) -> dict[str, Any] | None:
        return (self.action or {}).get("confirm")

    def required_routes(self) -> list[str]:
        req = self.visibility.get("requires_core")
        if req is None:
            return []
        return [req] if isinstance(req, str) else list(req)


class Menu:
    def __init__(
        self,
        nodes: dict[str, dict[str, Any]],
        origin: dict[str, str] | None = None,
        plugin_of: dict[str, str] | None = None,
    ):
        self.nodes: dict[str, Node] = {
            nid: Node(nid, raw["label"], raw["parent"], raw["order"], raw) for nid, raw in nodes.items()
        }
        self.origin = origin or {}
        self.plugin_of = plugin_of or {}
        self.children: dict[str | None, list[str]] = {}
        for node in self.nodes.values():
            self.children.setdefault(node.parent, []).append(node.id)
        for kids in self.children.values():
            kids.sort(key=lambda nid: (self.nodes[nid].order, nid))

    def __len__(self) -> int:
        return len(self.nodes)

    def get(self, nid: str) -> Node | None:
        return self.nodes.get(nid)

    def _node_visible(self, node: Node, ctx: DeviceContext, rank: int) -> bool:
        vis = node.visibility
        if ROLE_RANK[node.min_role] > rank:
            return False
        if "modes" in vis and ctx.mode not in vis["modes"]:
            return False
        if "not_modes" in vis and ctx.mode in vis["not_modes"]:
            return False
        if any(c not in ctx.capabilities for c in vis.get("capabilities", [])):
            return False
        routes = node.required_routes()
        if node.action and "core" in node.action:
            routes = routes + [node.action["core"]]
        if routes and ctx.core_routes is not None and not all(ctx.core_routes.has(r) for r in routes):
            return False
        return True

    def view(self, ctx: DeviceContext, roles: tuple[str, ...] | list[str]) -> "MenuView":
        rank = role_rank(roles)
        visible: dict[str, Node] = {}

        def walk(parent: str | None) -> list[str]:
            shown = []
            for nid in self.children.get(parent, []):
                node = self.nodes[nid]
                if not self._node_visible(node, ctx, rank):
                    continue
                if node.is_branch:
                    kids = walk(nid)
                    if not kids:
                        continue
                visible[nid] = node
                shown.append(nid)
            return shown

        roots = walk(None)
        children = {nid: [c for c in self.children.get(nid, []) if c in visible] for nid in visible}
        children[None] = roots
        return MenuView(visible, children)


@dataclass
class MenuView:
    nodes: dict[str, Node]
    children: dict[str | None, list[str]] = field(default_factory=dict)

    def kids(self, parent: str | None) -> list[str]:
        return self.children.get(parent, [])

    def visible(self, nid: str) -> bool:
        return nid in self.nodes

    def breadcrumb(self, stack: tuple[str, ...]) -> list[str]:
        return [self.nodes[n].label for n in stack if n in self.nodes]

    def to_frame_nodes(self, provider_of: dict[str, str] | None = None) -> list[dict[str, Any]]:
        out = []
        for nid, node in self.nodes.items():
            item: dict[str, Any] = {
                "id": nid,
                "label": node.label,
                "parent": node.parent,
                "order": node.order,
                "kind": "branch" if node.is_branch else "action",
            }
            if not node.is_branch:
                if node.params:
                    item["params"] = node.params
                if node.confirm:
                    item["confirm"] = node.confirm
                if node.action.get("view"):
                    item["view"] = node.action["view"]
                item["cancellable"] = node.cancellable
                item["mutates"] = node.mutates
            if provider_of and nid in provider_of:
                item["provider"] = provider_of[nid]
            out.append(item)
        out.sort(key=lambda n: (n["parent"] or "", n["order"], n["id"]))
        return out
