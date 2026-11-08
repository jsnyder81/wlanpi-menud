"""``wlanpi-menud``: run the menu service."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
from pathlib import Path

from .config import DEFAULT_CONFIG_FILE, load_config
from .poller import Poller
from .server import Server
from .service import Service

log = logging.getLogger("wlanpi_menud")


async def run(config_path: Path) -> None:
    config = load_config(config_path)
    service = Service(config)
    for e in service.load.errors:
        log.warning("%s", e)
    poller = Poller(service)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(poller.poll_once(), 15)  # learn mode and capabilities before serving
    server = Server(service)
    await server.start()
    tasks = [asyncio.create_task(poller.run(), name="poller"),
             asyncio.create_task(service.tokens.refresh_loop(), name="token refresh")]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    loop.add_signal_handler(signal.SIGINT, stop.set)
    loop.add_signal_handler(signal.SIGHUP, service.reload_menu)
    log.info("wlanpi-menud running, epoch %s", service.epoch)
    await stop.wait()
    log.info("stopping")
    await service.aclose()
    await server.close()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    ap = argparse.ArgumentParser(prog="wlanpi-menud", description="WLAN Pi menu service")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG_FILE)
    ap.add_argument("--check", action="store_true", help="validate the manifests and exit")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")
    if args.check:
        from .handlers import handler_exists
        from .loader import load_menu
        cfg = load_config(args.config)
        res = load_menu(cfg.builtin_dir, cfg.plugins_dir, cfg.override_dir, handler_exists)
        for e in res.errors:
            print(e)
        print(f"{len(res.menu)} nodes from {len(res.loaded)} files; {len(res.rejected)} files rejected")
        raise SystemExit(1 if res.errors else 0)
    asyncio.run(run(args.config))


if __name__ == "__main__":
    main()
