"""``wlanpi-complication``: publish, remove and inspect complications from a shell.

One invocation is one connection on ``local.sock``: ``hello`` (rich, new,
ephemeral), one request, wait for its reply, close. A thin wrapper around
the ``complication.*`` and ``complications.configure`` frames, so the
service has one code path to authorize.

Exit codes: 0 done, 2 usage error, 3 refused by the service (message on
stderr), 4 service unreachable.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI
from websockets.sync.client import unix_connect

DEFAULT_SOCK = "/run/wlanpi-menud/local.sock"
FEATURE = "complications.2"
TIMEOUT = 10.0

EXIT_OK, EXIT_USAGE, EXIT_REFUSED, EXIT_UNREACHABLE = 0, 2, 3, 4


class Refused(Exception):
    pass


class Unreachable(Exception):
    pass


class Session:
    def __init__(self, path: str, timeout: float = TIMEOUT):
        self.timeout = timeout
        try:
            self.ws = unix_connect(path, uri="ws://localhost/", open_timeout=timeout, close_timeout=1)
        except (OSError, InvalidHandshake, InvalidURI, TimeoutError) as exc:
            raise Unreachable(f"cannot connect to {path}: {exc}") from exc
        self._n = 0
        w = self.request("hello", {"mode": "rich", "session": "new", "ephemeral": True, "protocol": [1],
                                   "client": {"name": "wlanpi-complication", "version": "1"}},
                         expect=("welcome",))
        self.welcome = w
        if FEATURE not in (w.get("features") or []):
            self.close()
            raise Refused("this wlanpi-menud does not support complications (no complications.2 feature)")

    def _send(self, type_: str, body: dict[str, Any]) -> str:
        self._n += 1
        mid = f"cli{self._n}"
        self.ws.send(json.dumps({"v": 1, "type": type_, "id": mid, **body}))
        return mid

    def request(self, type_: str, body: dict[str, Any], expect: tuple[str, ...],
                topic: str | None = None) -> dict[str, Any]:
        try:
            mid = self._send(type_, body)
            while True:
                f = json.loads(self.ws.recv(timeout=self.timeout))
                if f.get("type") == "error" and (f.get("id") == mid or f.get("fatal")):
                    raise Refused(f"{f.get('code')}: {f.get('message')}")
                if f.get("id") != mid or f.get("type") not in expect:
                    continue
                if topic is not None and f.get("topic") != topic:
                    continue
                return f
        except (ConnectionClosed, OSError, TimeoutError) as exc:
            raise Unreachable(f"connection to wlanpi-menud lost: {exc}") from exc

    def snapshot(self, topic: str) -> Any:
        return self.request("subscribe", {"topics": [topic]}, ("event",), topic=topic)["data"]

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="wlanpi-complication", description="Publish and inspect WLAN Pi complications "
                                 "(small home-screen status tiles). Never publish secrets: every surface sees them.")
    ap.add_argument("--socket", default=os.environ.get("WLANPI_MENUD_SOCK", DEFAULT_SOCK), help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("publish", help="create or replace a complication")
    p.add_argument("app_id", help="local.<your user>.<name> or a declared plugin.<name>.<id>")
    p.add_argument("--value", required=True)
    p.add_argument("--label")
    p.add_argument("--short")
    p.add_argument("--status", choices=["ok", "info", "warn", "error", "off", "unknown"])
    p.add_argument("--detail")
    p.add_argument("--icon")
    p.add_argument("--ttl", type=int, help="seconds, 5 to 3600 (default 30)")
    r = sub.add_parser("remove", help="remove a live complication")
    r.add_argument("app_id")
    for name, hlp in (("list", "the live set"), ("catalog", "every complication that could be shown")):
        q = sub.add_parser(name, help=hlp)
        q.add_argument("--json", action="store_true", help="print the raw topic data")
    s = sub.add_parser("show", help="a surface profile's show list (panel, panel.strip, tui, webui)")
    s.add_argument("surface")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--set", metavar="IDS", help="comma-separated ids and families, e.g. wlanpi.mode,plugin.*")
    g.add_argument("--reset", action="store_true", help="back to the site or shipped default")
    s.add_argument("--json", action="store_true")
    return ap


def _publish_body(a: argparse.Namespace) -> dict[str, Any]:
    body: dict[str, Any] = {"app_id": a.app_id, "value": a.value}
    for k in ("label", "short", "status", "detail", "icon"):
        v = getattr(a, k)
        if v is not None:
            body[k] = v
    if a.ttl is not None:
        body["ttl_sec"] = a.ttl
    return body


def run(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    try:
        s = Session(a.socket)
    except Unreachable as exc:
        print(f"wlanpi-complication: {exc}", file=sys.stderr)
        return EXIT_UNREACHABLE
    except Refused as exc:
        print(f"wlanpi-complication: refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    try:
        if a.cmd == "publish":
            ack = s.request("complication.publish", _publish_body(a), ("complication.ack",))
            print(f"{ack['app_id']} live, ttl {ack.get('ttl_sec')}s")
        elif a.cmd == "remove":
            ack = s.request("complication.remove", {"app_id": a.app_id}, ("complication.ack",))
            print(f"{ack['app_id']} removed")
        elif a.cmd in ("list", "catalog"):
            data = s.snapshot(f"complications.{'live' if a.cmd == 'list' else 'catalog'}") or []
            if a.json:
                print(json.dumps(data, indent=2))
            elif a.cmd == "list":
                for c in data:
                    print(f"{c['app_id']:<28} {c['label']:<8} {c['value']:<20} {c['status']}")
            else:
                live = {c["app_id"] for c in (s.snapshot("complications.live") or [])}
                for c in data:
                    mark = "reporting" if c["app_id"] in live else "-"
                    print(f"{c['app_id']:<28} {c['source']:<8} {c['title']:<24} {mark}")
        else:
            if a.set is not None or a.reset:
                body: dict[str, Any] = {"surface": a.surface}
                if a.reset:
                    body["reset"] = True
                else:
                    body["show"] = [e.strip() for e in a.set.split(",") if e.strip()]
                data = s.request("complications.configure", body, ("event",), topic="complications.config")["data"]
            else:
                data = s.snapshot("complications.config")
            prof = (data or {}).get("surfaces", {}).get(a.surface)
            if prof is None:
                default = (data or {}).get("surfaces", {}).get("default", {"show": [], "source": "default"})
                prof = {"show": default["show"], "source": "default"}
            if a.json:
                print(json.dumps({a.surface: prof}, indent=2))
            else:
                print(f"{a.surface} ({prof['source']}):")
                for i, e in enumerate(prof["show"], 1):
                    print(f"  {i:>2} {e}")
        return EXIT_OK
    except Refused as exc:
        print(f"wlanpi-complication: refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except Unreachable as exc:
        print(f"wlanpi-complication: {exc}", file=sys.stderr)
        return EXIT_UNREACHABLE
    finally:
        s.close()


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
