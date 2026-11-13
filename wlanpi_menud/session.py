"""Sessions and the connections attached to them.

A **session** owns menu state: the thin-mode cursor, the jobs it is attached
to and the prompts bound to it. An **attachment** is one connection on a
session: who is connected (principal), what it can see (menu view), what it
subscribed to and its bounded outbound queue.

* The **default session** exists from service start and never ends. Any
  number of connections attach to it (``hello.session: "default"``); input
  from any of them moves the one cursor and the resulting ``view`` fans out
  to all of them. Its state survives connections coming and going.
* An **independent session** (``hello.session: "new"``) has its own cursor
  and normally one attachment. It ends when its last connection detaches and
  the resume grace runs out.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .auth import Principal
from .menu import MenuView
from .nav import Cursor
from .outqueue import OutQueue

DEFAULT = "default"
NEW = "new"

Validator = Callable[[dict[str, Any]], None]


def _token(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(12).replace("-", "_")


@dataclass(frozen=True)
class Driver:
    """Who produced the session's current state (named in each default-session view)."""

    attachment_id: str
    principal: Principal
    surface: str | None


class Attachment:
    """One connection on a session."""

    def __init__(
        self,
        session: Session,
        principal: Principal,
        socket_kind: str,
        *,
        client: dict[str, Any] | None = None,
        capabilities: dict[str, Any] | None = None,
        validate: Validator | None = None,
    ):
        self.id = _token("A")
        self.session = session
        self.principal = principal
        self.socket_kind = socket_kind
        self.client = client or {}
        self.capabilities = capabilities or {}
        self.subscriptions: set[str] = set()
        self.menu_view: MenuView = MenuView({}, {None: []})
        self.menu_revision_sent = -1
        self.queue: OutQueue | None = None
        self.closer: Callable[[int, str], None] | None = None  # set by the connection
        self.connected = False
        self._validate = validate

    @property
    def mode(self) -> str:
        return self.session.mode

    @property
    def thin(self) -> bool:
        return self.session.thin

    @property
    def surface(self) -> str | None:
        return self.client.get("name")

    def next_id(self) -> str:
        return self.session.next_id()

    def open(self, queue: OutQueue, closer: Callable[[int, str], None]) -> None:
        self.queue = queue
        self.closer = closer
        self.connected = True

    def shut(self) -> None:
        self.connected = False
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


class Session:
    def __init__(self, kind: str, mode: str, *, validate: Validator | None = None,
                 owner_key: str | None = None, socket_kind: str | None = None):
        self.id = _token("S")
        self.kind = kind  # "default" | "new"
        self.mode = mode
        self.owner_key = owner_key  # independent: the principal that may resume it
        self.socket_kind = socket_kind  # independent: the socket it was opened on
        self.cursor = Cursor()
        self.jobs: set[str] = set()
        self.menu_view: MenuView = MenuView({}, {None: []})  # what the cursor navigates
        self.attachments: list[Attachment] = []
        self.driver: Driver | None = None
        self.detached_at: float | None = None
        self.saved_subscriptions: set[str] = set()  # independent: carried across a resume
        self.ephemeral = False  # independent: dropped at disconnect, no resume grace
        self.profile = "panel"  # complication display profile for the home view
        self._sid = 0
        self._validate = validate

    @property
    def default(self) -> bool:
        return self.kind == DEFAULT

    @property
    def thin(self) -> bool:
        return self.mode == "thin"

    @property
    def connected(self) -> bool:
        return bool(self.attachments)

    def principal_keys(self) -> set[str]:
        return {a.principal.key for a in self.attachments}

    def next_id(self) -> str:
        self._sid += 1
        return f"s:{self._sid}"

    def attach(self, a: Attachment) -> None:
        self.attachments.append(a)
        self.detached_at = None

    def detach(self, a: Attachment) -> None:
        if a in self.attachments:
            self.attachments.remove(a)
            self.saved_subscriptions = set(a.subscriptions)
        a.shut()  # the driver record stays: the view still names who drove it last
        if not self.attachments:
            self.detached_at = time.time()

    def drove(self, a: Attachment) -> None:
        self.driver = Driver(a.id, a.principal, a.surface)

    def send(self, frame: dict[str, Any], coalesce: str | None = None, *, reply_to: Attachment | None = None) -> None:
        """Send to every attachment. ``reply_to`` gets ``frame`` as is (a reply);
        the others get a copy with a fresh server id."""
        if self._validate:
            self._validate(frame)
        for a in list(self.attachments):
            if a.queue is None:
                continue
            f = frame if reply_to is None or a is reply_to else dict(frame, id=self.next_id())
            a.queue.put(f, coalesce=coalesce)

    def close(self, code: int, reason: str = "") -> None:
        for a in list(self.attachments):
            a.close(code, reason)
