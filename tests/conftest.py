from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, unix_connect

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakecore import BASE, FakeCore  # noqa: E402

from wlanpi_menud import protocol  # noqa: E402
from wlanpi_menud.config import Config  # noqa: E402
from wlanpi_menud.core import CoreClient, TokenSource  # noqa: E402
from wlanpi_menud.poller import Poller  # noqa: E402
from wlanpi_menud.server import Server  # noqa: E402
from wlanpi_menud.service import Service  # noqa: E402

BUILTIN = ROOT / "share" / "menu.d"


class Client:
    """A test surface. Every frame it receives is validated against frames.schema.json."""

    def __init__(self, ws: ClientConnection):
        self.ws = ws
        self.frames: list[dict[str, Any]] = []
        self._n = 0
        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader = asyncio.create_task(self._read())
        self.closed = asyncio.Event()

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                frame = json.loads(raw)
                errs = protocol.validate_any(frame)
                assert not errs, f"server sent an invalid {frame.get('type')} frame: {errs}\n{frame}"
                self.frames.append(frame)
                await self.inbox.put(frame)
        except Exception:
            pass
        finally:
            self.closed.set()

    def mid(self) -> str:
        self._n += 1
        return f"c{self._n}"

    async def send(self, type_: str, **body: Any) -> str:
        mid = body.pop("id", None) or self.mid()
        await self.ws.send(json.dumps({"v": 1, "type": type_, "id": mid, **body}))
        return mid

    async def raw(self, data: str) -> None:
        await self.ws.send(data)

    async def expect(self, pred=None, *, type: str | None = None, timeout: float = 3.0) -> dict[str, Any]:
        """Next frame matching ``type`` and ``pred`` (frames in between are kept in ``frames``)."""
        async def wait():
            while True:
                f = await self.inbox.get()
                if type is not None and f["type"] != type:
                    continue
                if pred is None or pred(f):
                    return f
        return await asyncio.wait_for(wait(), timeout)

    async def hello(self, mode: str = "thin", **extra: Any) -> dict[str, Any]:
        await self.send("hello", mode=mode, protocol=[1], client={"name": "pytest"},
                        capabilities={"blocks": ["text", "kv", "list"], "screen": {"rows": 8, "cols": 16}}, **extra)
        return await self.expect(type="welcome")

    async def input(self, key: str) -> dict[str, Any]:
        mid = await self.send("input", key=key)
        return await self.expect(lambda f: f["id"] == mid, type="view")

    async def wait_closed(self, timeout: float = 3.0) -> int | None:
        await asyncio.wait_for(self.closed.wait(), timeout)
        return self.ws.close_code

    async def close(self) -> None:
        await self.ws.close()
        self._reader.cancel()


class Harness:
    def __init__(self, tmp: Path, fake: FakeCore, **overrides: Any):
        self.tmp = tmp
        self.fake = fake
        uid = os.getuid()
        defaults = dict(
            builtin_dir=BUILTIN,
            plugins_dir=tmp / "plugins.d",
            override_dir=tmp / "etc-menu.d",
            local_sock=tmp / "run" / "local.sock",
            proxy_sock=tmp / "run" / "proxy.sock",
            local_sock_group=None,
            proxy_sock_group=None,
            local_uids={uid: ("admin",)},
            local_gids={},
            local_user_names={},
            local_group_names={},
            core_cert=None,
            token_command=("get-core-token",),
            poll_interval_sec=3600,
            validate_outbound=True,
            auth_timeout_sec=1.0,
            resume_grace_sec=30,
        )
        defaults.update(overrides)
        self.config = Config(**defaults)
        self.service: Service | None = None
        self.server: Server | None = None
        self.clients: list[Client] = []

    async def start(self) -> "Harness":
        tokens = TokenSource(self.config.token_command, runner=self.fake.token_runner())
        core = CoreClient(BASE, tokens, transport=self.fake.transport())
        self.service = Service(self.config, core=core, tokens=tokens)
        self.poller = Poller(self.service)
        await self.poller.poll_once()
        self.server = Server(self.service)
        await self.server.start()
        return self

    async def stop(self) -> None:
        for c in self.clients:
            await c.close()
        self.clients.clear()
        if self.service:
            await self.service.aclose()
        if self.server:
            await self.server.close()

    async def connect(self, sock: str = "local", headers: dict[str, str] | None = None,
                      path: str = "/ui/ws", origin: str | None = None) -> Client:
        p = self.config.local_sock if sock == "local" else self.config.proxy_sock
        ws = await unix_connect(str(p), uri=f"ws://wlanpi.local{path}", additional_headers=headers or {},
                                origin=origin)
        c = Client(ws)
        self.clients.append(c)
        return c


@pytest.fixture
def sockdir():
    # unix socket paths are limited to 108 bytes; pytest's tmp_path can be longer.
    d = Path(tempfile.mkdtemp(prefix="menud-"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def fake():
    return FakeCore()


@pytest.fixture
async def harness(sockdir, fake):
    h = await Harness(sockdir, fake).start()
    yield h
    await h.stop()


@pytest.fixture
async def harnesses(sockdir, fake):
    made: list[Harness] = []

    async def make(**overrides: Any) -> Harness:
        h = await Harness(sockdir, fake, **overrides).start()
        made.append(h)
        return h
    yield make
    for h in made:
        await h.stop()
