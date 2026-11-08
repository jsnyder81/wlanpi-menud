"""Topic state and fan-out. Publishes only on change; seq is per topic per epoch."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

KNOWN_TOPICS = ("device.summary", "complications", "alerts", "jobs")


def topic_matches(subscription: str, topic: str) -> bool:
    if subscription.endswith(".*"):
        return topic.startswith(subscription[:-1])
    return subscription == topic


class TopicHub:
    def __init__(self) -> None:
        self.seq: dict[str, int] = {}
        self.data: dict[str, Any] = {}
        self._listeners: list[Callable[[str, int, Any], None]] = []
        # Every topic starts with an empty value at seq 1 so a subscriber
        # always gets a snapshot, even before the first poll.
        for t in KNOWN_TOPICS:
            self.seq[t] = 1
            self.data[t] = {} if t == "device.summary" else []

    def on_publish(self, fn: Callable[[str, int, Any], None]) -> None:
        self._listeners.append(fn)

    def publish(self, topic: str, data: Any) -> bool:
        """Store and fan out ``data`` if it differs from the current value."""
        if topic in self.data and self.data[topic] == data:
            return False
        self.seq[topic] = self.seq.get(topic, 0) + 1
        self.data[topic] = copy.deepcopy(data)
        for fn in list(self._listeners):
            fn(topic, self.seq[topic], self.data[topic])
        return True

    def topics_for(self, subscription: str) -> list[str]:
        if subscription.endswith(".*"):
            return sorted(t for t in self.data if topic_matches(subscription, t))
        if subscription not in self.data:
            self.seq[subscription] = 1
            self.data[subscription] = None
        return [subscription]

    def event(self, msg_id: str, topic: str, snapshot: bool) -> dict[str, Any]:
        f: dict[str, Any] = {"v": 1, "type": "event", "id": msg_id, "topic": topic,
                             "seq": self.seq[topic], "data": self.data[topic]}
        if snapshot:
            f["snapshot"] = True
        return f
