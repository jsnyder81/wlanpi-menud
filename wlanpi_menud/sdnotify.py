"""Minimal sd_notify(3): tell systemd (Type=notify) when the service is ready or stopping.

A datagram to $NOTIFY_SOCKET; no libsystemd dependency. Outside systemd it is a no-op.
"""

from __future__ import annotations

import logging
import os
import socket

log = logging.getLogger(__name__)


def notify(state: str) -> bool:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    if addr.startswith("@"):  # abstract namespace
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC) as s:
            s.connect(addr)
            s.sendall(state.encode())
        return True
    except OSError as exc:
        log.warning("sd_notify %r failed: %s", state.split("\n", 1)[0], exc)
        return False
