#!/usr/bin/env python3
"""Generate wlanpi_menud/core/generated.py from wlanpi-core's docs/openapi.json.

Usage:
    python tools/gen_core_client.py /path/to/wlanpi-core [--commit <sha>]

The output is a route table (used for requires_core and implicit endpoint
checks) and one thin async method per core operation. Request and response
bodies stay plain JSON; the service renders them into view blocks. Re-run
this whenever the pinned core version moves, and commit the result.
"""

from __future__ import annotations

import argparse
import json
import keyword
import re
import subprocess
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "wlanpi_menud" / "core" / "generated.py"
PREFIX = "/api/v1"


def method_name(method: str, path: str) -> str:
    parts = []
    for seg in path.strip("/").split("/"):
        if not seg:
            continue
        m = re.fullmatch(r"\{(\w+)\}", seg)
        if m:
            parts.append(f"by_{m.group(1)}")
        else:
            parts.append(re.sub(r"[^a-z0-9]+", "_", re.sub(r"(?<!^)([A-Z])", r"_\1", seg).lower()).strip("_"))
    return "_".join([method.lower(), *parts])


def py_ident(name: str) -> str:
    name = re.sub(r"\W", "_", name)
    return name + "_" if keyword.iskeyword(name) else name


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("core_repo", type=Path)
    ap.add_argument("--commit")
    args = ap.parse_args()
    spec = json.loads((args.core_repo / "docs" / "openapi.json").read_text())
    commit = args.commit or subprocess.run(
        ["git", "-C", str(args.core_repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()

    routes = []
    methods = []
    for path, item in sorted(spec["paths"].items()):
        rel = path[len(PREFIX):] if path.startswith(PREFIX) else path
        for method, op in item.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            routes.append((method.upper(), rel))
            params = op.get("parameters", [])
            path_params = [p["name"] for p in params if p["in"] == "path"]
            query_params = [p["name"] for p in params if p["in"] == "query"]
            has_body = "requestBody" in op
            resp = op.get("responses", {}).get("200", {}).get("content", {}).get("application/json", {}).get("schema", {})
            resp_name = resp.get("$ref", "").rsplit("/", 1)[-1] or resp.get("type", "any")
            sig = ["self"] + [f"{py_ident(p)}: str" for p in path_params]
            if query_params or has_body:
                sig.append("*")
            sig += [f"{py_ident(q)}: Any = None" for q in query_params]
            if has_body:
                sig.append("body: Any = None")
            sig.append("_timeout: float | None = None")
            fmt = rel
            for p in path_params:
                fmt = fmt.replace("{" + p + "}", "{_q(" + py_ident(p) + ")}")
            query = "{" + ", ".join(f"{q!r}: {py_ident(q)}" for q in query_params) + "}"
            summary = (op.get("summary") or op.get("operationId", "")).replace('"', "'")
            body_lines = [
                f"    async def {method_name(method, rel)}({', '.join(sig)}) -> Any:",
                f'        """{method.upper()} {rel}: {summary}. Returns {resp_name}."""',
                f"        return await self.request({method.upper()!r}, f{fmt!r}, query={query}"
                + (", body=body" if has_body else "") + ", timeout=_timeout)",
                "",
            ]
            methods.append("\n".join(body_lines))

    out = [
        '"""Generated from wlanpi-core docs/openapi.json. Do not edit; run tools/gen_core_client.py."""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Any",
        "from urllib.parse import quote",
        "",
        f"CORE_COMMIT = {commit!r}",
        f"CORE_VERSION = {spec['info']['version']!r}",
        "",
        "ROUTES: frozenset[tuple[str, str]] = frozenset({",
        *[f"    ({m!r}, {p!r})," for m, p in routes],
        "})",
        "",
        "",
        "def _q(value: str) -> str:",
        '    return quote(str(value), safe="")',
        "",
        "",
        "class GeneratedCoreAPI:",
        '    """One method per core operation. Subclasses provide ``request``."""',
        "",
        "    async def request(self, method: str, path: str, *, query: dict[str, Any] | None = None,",
        "                      body: Any = None, timeout: float | None = None) -> Any:",
        "        raise NotImplementedError",
        "",
        *methods,
    ]
    OUT.write_text("\n".join(out).rstrip() + "\n")
    print(f"wrote {OUT} ({len(routes)} routes, core {spec['info']['version']} @ {commit})")


if __name__ == "__main__":
    main()
