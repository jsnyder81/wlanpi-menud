#!/usr/bin/env python3
"""Demo: two thin clients on one wlanpi-menud, navigating independently.

Both ask for independent sessions (``hello.session: "new"``); without it a
thin client attaches to the shared default session and they would mirror.

Starts the real service and server on temporary unix sockets against the
fake core used by the tests, connects two thin clients (as a TUI would) over
local.sock, interleaves their button presses, and prints what each screen
shows after every press. Run from the repo root:

    .venv/bin/python demo/two_thin_clients.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from fakecore import BASE, FakeCore  # noqa: E402
from websockets.asyncio.client import unix_connect  # noqa: E402

from wlanpi_menud import protocol  # noqa: E402
from wlanpi_menud.config import Config  # noqa: E402
from wlanpi_menud.core import CoreClient, TokenSource  # noqa: E402
from wlanpi_menud.poller import Poller  # noqa: E402
from wlanpi_menud.server import Server  # noqa: E402
from wlanpi_menud.service import Service  # noqa: E402


def screen_text(view: dict) -> list[str]:
    head = view.get("title", "")
    crumb = " > ".join(view.get("breadcrumb", []))
    lines = [f"[{view['screen']}] {head}" + (f"   ({crumb})" if crumb else "")]
    for b in view["blocks"]:
        lines.extend("    " + ln for ln in b["text"].splitlines()[:9])
    if view.get("page"):
        p = view["page"]
        lines.append(f"    page {p['index'] + 1}/{p['count']}")
    return lines


class ThinClient:
    def __init__(self, name: str, ws):
        self.name, self.ws, self.n = name, ws, 0
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.reader = asyncio.create_task(self._read())

    async def _read(self):
        async for raw in self.ws:
            frame = json.loads(raw)
            assert not protocol.validate_any(frame), frame  # every frame obeys frames.schema.json
            await self.inbox.put(frame)

    async def send(self, type_: str, **body) -> str:
        self.n += 1
        mid = f"{self.name}{self.n}"
        await self.ws.send(json.dumps({"v": 1, "type": type_, "id": mid, **body}))
        return mid

    async def until(self, pred, timeout=3.0):
        async def wait():
            while True:
                f = await self.inbox.get()
                if pred(f):
                    return f
        return await asyncio.wait_for(wait(), timeout)

    async def press(self, key: str) -> dict:
        mid = await self.send("input", key=key)
        return await self.until(lambda f: f["type"] == "view" and f["id"] == mid)


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="menud-demo-"))
    fake = FakeCore()
    fake.scan_networks = 26
    config = Config(
        builtin_dir=ROOT / "share" / "menu.d", plugins_dir=tmp / "plugins.d", override_dir=tmp / "etc",
        local_sock=tmp / "local.sock", proxy_sock=tmp / "proxy.sock", local_sock_group=None, proxy_sock_group=None,
        local_uids={os.getuid(): ("admin",)}, local_gids={}, local_user_names={}, local_group_names={},
        core_cert=None, poll_interval_sec=3600, validate_outbound=True,
    )
    tokens = TokenSource(("get-core-token",), runner=fake.token_runner())
    service = Service(config, core=CoreClient(BASE, tokens, transport=fake.transport()), tokens=tokens)
    await Poller(service).poll_once()
    server = Server(service)
    await server.start()
    print(f"wlanpi-menud epoch {service.epoch}; sockets in {tmp}")
    print(f"menu: {len(service.menu)} nodes registered, {len(service.load.rejected)} files rejected\n")

    clients = {}
    for name in ("A", "B"):
        ws = await unix_connect(str(config.local_sock), uri="ws://localhost/ui/ws")
        c = ThinClient(name, ws)
        await c.send("hello", mode="thin", session="new", protocol=[1], client={"name": f"demo-tui-{name}"},
                     capabilities={"screen": {"cols": 16, "rows": 8}, "blocks": ["text", "kv", "list", "table"]})
        w = await c.until(lambda f: f["type"] == "welcome")
        home = await c.until(lambda f: f["type"] == "view")
        print(f"{name}: {w['session']} session {w['session_id']} as {w['principal']['kind']} uid {w['principal']['uid']} "
              f"roles {w['roles']}")
        for ln in screen_text(home):
            print(f"  {name} | {ln}")
        clients[name] = c
    print()

    script = [
        ("A", "down"), ("B", "down"),
        ("A", "center"),            # A enters Network
        ("B", "down"),              # B moves to Bluetooth
        ("A", "down"), ("A", "down"), ("A", "down"), ("A", "down"), ("A", "down"),  # A -> LLDP Neighbour
        ("B", "center"),            # B enters Bluetooth
        ("A", "center"),            # A runs LLDP
        ("B", "down"),              # B on Turn On
        ("B", "left"), ("B", "down"), ("B", "down"),  # B back up, to Apps
        ("B", "center"), ("B", "down"), ("B", "down"), ("B", "center"),  # Apps > Scanner
        ("B", "center"),            # B runs Scan (26 networks -> 2 pages)
        ("A", "left"),              # A back to the Network list, still on LLDP
        ("B", "down"),              # B pages the scan result
    ]
    for name, key in script:
        c = clients[name]
        view = await c.press(key)
        if view["screen"] == "busy":  # an action started; wait for its result view
            view = await c.until(lambda f: f["type"] == "view" and f["screen"] in ("result", "prompt"))
        print(f"{name} presses {key}:")
        for ln in screen_text(view):
            print(f"  {name} | {ln}")
    print()
    for name in clients:
        s = next(s for s in service.sessions.values()
                 if any(a.client.get("name") == f"demo-tui-{name}" for a in s.attachments))
        print(f"{name} cursor: screen={s.cursor.screen} stack={list(s.cursor.stack)} index={s.cursor.index} "
              f"node={s.cursor.node} page={s.cursor.page}")
    print(f"core calls: LLDP/network info x{fake.calls['GET /network/info/']}, "
          f"scan x{fake.calls['GET /utils/wlan/scan']}")
    for c in clients.values():
        await c.ws.close()
        c.reader.cancel()
    await service.aclose()
    await server.close()
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
