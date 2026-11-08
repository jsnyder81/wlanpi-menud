"""One connected (or briefly detached, resumable) surface."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from typing import Any

from .auth import Principal
from .menu import MenuView
from .nav import Cursor
from .outqueue import OutQueue


class Session:
    def __init__(
        self,
        principal: Principal,
        mode: str,
        socket_kind: str,
        *,
        client: dict[str, Any] | None = None,
        capabilities: dict[str, Any] | None = None,
        validate: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.id = "S" + secrets.token_urlsafe(12).replace("-", "_")
        self.principal = principal
        self.mode = mode
        self.socket_kind = socket_kind
        self.client = client or {}
        self.capabilities = capabilities or {}
        self.cursor = Cursor()
        self.subscriptions: set[str] = set()
        self.jobs: set[str] = set()
        self.menu_view: MenuView = MenuView({}, {None: []})
        self.menu_revision_sent = -1
        self.queue: OutQueue | None = None
        self.connected = False
        self.detached_at: float | None = None
        self.closer: Callable[[int, str], None] | None = None  # set by the connection
        self._sid = 0
        self._validate = validate

    @property
    def thin(self) -> bool:
        return self.mode == "thin"

    def next_id(self) -> str:
        self._sid += 1
        return f"s:{self._sid}"

    def attach(self, queue: OutQueue, closer: Callable[[int, str], None]) -> None:
        self.queue = queue
        self.closer = closer
        self.connected = True
        self.detached_at = None

    def detach(self) -> None:
        self.connected = False
        self.detached_at = time.time()
        if self.queue is not None:
            self.queue.close()
        self.queue = None
        self.closer = None

    def send(self, frame: dict[str, Any], coalesce: str | None = None) -> None:
        if self._validate:
            self._validate(frame)
        if self.queue is not None:
            self.queue.put(frame, coalesce=coalesce)

    def send_event(self, frame: dict[str, Any], topic: str) -> None:
        if self._validate:
            self._validate(frame)
        if self.queue is not None:
            self.queue.put_event(frame, topic)

    def close(self, code: int, reason: str = "") -> None:
        if self.closer is not None:
            self.closer(code, reason)
