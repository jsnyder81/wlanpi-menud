"""Bounded per-client outbound queue.

Each client gets its own queue so a slow client never holds up the others.
The bound is ``max_frames`` frames and ``max_bytes`` bytes, whichever comes
first. When it is reached:

* topic updates (``event`` frames) for that client are dropped, and the
  topic is marked stale; further updates for a stale topic are dropped too;
* replies, prompts, views and job frames are never dropped: room is made by
  evicting queued topic updates (marking their topics stale), and if there
  are none the frame is queued over the bound;
* unprompted thin ``view`` frames coalesce: a newer one replaces a queued
  older one.

Once the queue drains, the client gets one ``snapshot: true`` event per
stale topic (built by ``resnapshot``).
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class _Item:
    data: str
    topic: str | None
    coalesce: str | None


def encode(frame: dict[str, Any]) -> str:
    return json.dumps(frame, separators=(",", ":"), ensure_ascii=False)


class OutQueue:
    def __init__(
        self,
        max_frames: int,
        max_bytes: int,
        resnapshot: Callable[[set[str]], list[dict[str, Any]]] | None = None,
    ):
        self.max_frames = max_frames
        self.max_bytes = max_bytes
        self._items: deque[_Item] = deque()
        self._bytes = 0
        self._event = asyncio.Event()
        self.stale: set[str] = set()
        self.resnapshot = resnapshot
        self.dropped = 0
        self.closed = False

    def __len__(self) -> int:
        return len(self._items)

    def _full(self, extra: int) -> bool:
        return len(self._items) >= self.max_frames or self._bytes + extra > self.max_bytes

    def _remove(self, item: _Item) -> None:
        self._items.remove(item)
        self._bytes -= len(item.data)

    def put_event(self, frame: dict[str, Any], topic: str) -> bool:
        """Queue a topic update. Returns False when it was dropped."""
        if self.closed:
            return False
        if topic in self.stale:
            self.dropped += 1
            return False
        data = encode(frame)
        if self._full(len(data)):
            self.stale.add(topic)
            # Older queued updates for this topic are now pointless too.
            for it in [i for i in self._items if i.topic == topic]:
                self._remove(it)
                self.dropped += 1
            self.dropped += 1
            return False
        self._append(_Item(data, topic, None))
        return True

    def put(self, frame: dict[str, Any], coalesce: str | None = None) -> None:
        """Queue a frame that must not be dropped."""
        if self.closed:
            return
        data = encode(frame)
        if coalesce is not None:
            for it in [i for i in self._items if i.coalesce == coalesce]:
                self._remove(it)
        while self._full(len(data)):
            victim = next((i for i in self._items if i.topic is not None), None)
            if victim is None:
                break  # only essential frames queued: go over the bound
            self._remove(victim)
            self.stale.add(victim.topic)  # type: ignore[arg-type]
            self.dropped += 1
        self._append(_Item(data, None, coalesce))

    def _append(self, item: _Item) -> None:
        self._items.append(item)
        self._bytes += len(item.data)
        self._event.set()

    def _maybe_resnapshot(self) -> None:
        if not self._items and self.stale and self.resnapshot is not None:
            stale, self.stale = self.stale, set()
            for frame in self.resnapshot(stale):
                self._append(_Item(encode(frame), None, None))

    async def get(self) -> str:
        while True:
            self._maybe_resnapshot()
            if self._items:
                item = self._items.popleft()
                self._bytes -= len(item.data)
                if not self._items:
                    self._event.clear()
                return item.data
            self._event.clear()
            await self._event.wait()

    def get_nowait(self) -> str | None:
        self._maybe_resnapshot()
        if not self._items:
            return None
        item = self._items.popleft()
        self._bytes -= len(item.data)
        return item.data

    async def wait(self) -> None:
        """Wait until there is something to send (or ``wake`` is called)."""
        if self._items or (self.stale and self.resnapshot is not None):
            return
        self._event.clear()
        await self._event.wait()

    def wake(self) -> None:
        self._event.set()

    def close(self) -> None:
        self.closed = True
        self._items.clear()
        self._bytes = 0
        self._event.set()
