"""sd_notify readiness for Type=notify."""

from __future__ import annotations

import socket
import tempfile
from pathlib import Path

from wlanpi_menud.sdnotify import notify


def test_notify_without_systemd_is_a_no_op(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert notify("READY=1") is False


def test_notify_sends_datagram(monkeypatch):
    d = Path(tempfile.mkdtemp(prefix="sdn-"))
    path = d / "notify"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.bind(str(path))
        monkeypatch.setenv("NOTIFY_SOCKET", str(path))
        assert notify("READY=1\nSTATUS=ok") is True
        assert s.recv(256) == b"READY=1\nSTATUS=ok"
    path.unlink()
    d.rmdir()


def test_notify_abstract_namespace(monkeypatch):
    name = f"menud-test-{id(monkeypatch)}"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.bind("\0" + name)
        monkeypatch.setenv("NOTIFY_SOCKET", "@" + name)
        assert notify("STOPPING=1") is True
        assert s.recv(64) == b"STOPPING=1"


def test_notify_dead_socket_does_not_raise(monkeypatch):
    monkeypatch.setenv("NOTIFY_SOCKET", "/nonexistent/notify")
    assert notify("READY=1") is False
