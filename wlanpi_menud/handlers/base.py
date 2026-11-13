"""Handler registry and the context a handler runs with."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..core.generated import GeneratedCoreAPI


class PromptCancelled(Exception):
    """The session cancelled (or let expire) a prompt the handler was waiting on."""


@dataclass
class HandlerResult:
    blocks: list[dict[str, Any]]
    title: str | None = None


class BoundCore(GeneratedCoreAPI):
    """The generated core API bound to the token a job runs with."""

    def __init__(self, core: Any, token: str | None):
        self._core = core
        self._token = token

    async def request(self, method: str, path: str, *, query: dict[str, Any] | None = None,
                      body: Any = None, timeout: float | None = None) -> Any:
        return await self._core.request(method, path, query=query, body=body, timeout=timeout, token=self._token)


PromptFn = Callable[..., Awaitable[Any]]
ProgressFn = Callable[[float | None, str | None], Awaitable[None]]


@dataclass
class HandlerContext:
    core: GeneratedCoreAPI
    node_id: str
    params: dict[str, Any] = field(default_factory=dict)
    prompt: PromptFn | None = None
    progress: ProgressFn | None = None
    service: Any = None
    session_id: str | None = None

    async def ask(self, kind: str, title: str, **kw: Any) -> Any:
        """Prompt the invoking session mid-job and wait for its answer."""
        if self.prompt is None:
            raise PromptCancelled("no session to ask")
        return await self.prompt(kind, title, **kw)

    async def report(self, pct: float | None, message: str | None = None) -> None:
        if self.progress is not None:
            await self.progress(pct, message)


Handler = Callable[[HandlerContext], Awaitable[HandlerResult]]
HANDLERS: dict[str, Handler] = {}


def handler(name: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        if name in HANDLERS:
            raise ValueError(f"handler {name} registered twice")
        HANDLERS[name] = fn
        return fn
    return deco


def handler_exists(name: str) -> bool:
    return name in HANDLERS


def get_handler(name: str) -> Handler | None:
    return HANDLERS.get(name)
