#!/usr/bin/env python3
"""Run the real wlanpi-menud service on temporary sockets against the fake core.

For surface development and live integration tests (wlanpi-menu-tui's
tests/test_live_menud.py): no device, no wlanpi-core, no root. The calling
uid is allowed on local.sock as admin. Prints one line, ``READY <local.sock>``,
once both sockets listen, then serves until SIGTERM/SIGINT.

    .venv/bin/python demo/serve_fake.py /tmp/menud-dev
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from fakecore import BASE, FakeCore  # noqa: E402

from wlanpi_menud.config import Config  # noqa: E402
from wlanpi_menud.core import CoreClient, TokenSource  # noqa: E402
from wlanpi_menud.poller import Poller  # noqa: E402
from wlanpi_menud.server import Server  # noqa: E402
from wlanpi_menud.service import Service  # noqa: E402


async def main(sockdir: Path) -> None:
    sockdir.mkdir(parents=True, exist_ok=True)
    fake = FakeCore()
    config = Config(
        builtin_dir=ROOT / "share" / "menu.d", plugins_dir=sockdir / "plugins.d", override_dir=sockdir / "etc-menu.d",
        local_sock=sockdir / "local.sock", proxy_sock=sockdir / "proxy.sock", local_sock_group=None,
        proxy_sock_group=None, local_uids={os.getuid(): ("admin",)}, local_gids={}, local_user_names={},
        local_group_names={}, core_cert=None, token_command=("get-core-token",), poll_interval_sec=3600,
        validate_outbound=True,
    )
    tokens = TokenSource(config.token_command, runner=fake.token_runner())
    service = Service(config, core=CoreClient(BASE, tokens, transport=fake.transport()), tokens=tokens)
    await Poller(service).poll_once()
    server = Server(service)
    await server.start()
    print(f"READY {config.local_sock}", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await service.aclose()
    await server.close()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/menud-dev")))
