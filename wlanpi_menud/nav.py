"""Pure thin-mode navigation.

``handle_input(cursor, key, ctx) -> NavResult`` takes one session's cursor,
a button and what the session can currently see, and returns the new
cursor plus at most one effect for the session layer to carry out. It has
no side effects and holds no global state: every thin session owns its own
``Cursor``, so two sessions never share a position (the PoC kept one
global ``NavLocation``).

The cursor addresses the menu by node id (the stack of branches entered
plus an index among the current branch's visible children), so it survives
a menu change better than the PoC's index paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

from .menu import MenuView

Screen = Literal["home", "menu", "result", "prompt"]

# FPMS key1 cycles these result screens (parity list, "FPMS behaviour outside the menu tree").
KEY1_CYCLE = ("utils.reachability", "network.lldp", "network.eth0_ipconfig")
# FPMS key3 toggles between the reboot and shutdown confirm screens.
KEY3_TOGGLE = ("system.shutdown", "system.reboot")


@dataclass(frozen=True)
class Cursor:
    screen: Screen = "home"
    stack: tuple[str, ...] = ()  # branches entered, outermost first
    index: int = 0  # selection among the visible children of stack[-1] (or roots)
    job_id: str | None = None  # result screen: the job shown
    node: str | None = None  # result screen: the node the job ran
    page: int = 0  # result screen: page index
    prompt_id: str | None = None
    prompt_index: int = 0  # select: highlighted option; confirm: 0=cancel 1=confirm
    prompt_value: int | None = None  # integer prompts
    return_screen: Screen = "menu"  # where left goes from a prompt or result


@dataclass(frozen=True)
class PromptState:
    """The open prompt as the navigator needs it."""

    prompt_id: str
    kind: str  # select | confirm | integer | string | form
    option_count: int = 0
    minimum: int | None = None
    maximum: int | None = None
    step: int = 1


@dataclass(frozen=True)
class NavContext:
    menu: MenuView
    page_count: int = 1  # result screen
    job_running: bool = False
    prompt: PromptState | None = None


@dataclass(frozen=True)
class Effect:
    kind: Literal["invoke", "answer", "cancel_prompt", "detach", "page"]
    node: str | None = None
    job_id: str | None = None
    prompt_id: str | None = None
    value: object = None  # answer: option index, bool for confirm, int for integer
    page: int | None = None


@dataclass(frozen=True)
class NavResult:
    cursor: Cursor
    effects: tuple[Effect, ...] = field(default_factory=tuple)


def _siblings(cursor: Cursor, menu: MenuView) -> list[str]:
    return menu.kids(cursor.stack[-1] if cursor.stack else None)


def selected_node(cursor: Cursor, menu: MenuView) -> str | None:
    sib = _siblings(cursor, menu)
    if not sib:
        return None
    return sib[min(cursor.index, len(sib) - 1)]


def normalize(cursor: Cursor, menu: MenuView) -> Cursor:
    """Repair a cursor after the menu changed (mode switch, plugin removed, role change)."""
    stack = list(cursor.stack)
    while stack and (not menu.visible(stack[-1]) or not menu.kids(stack[-1])):
        stack.pop()
    # Every entry must still be a visible child of the one before it.
    fixed: list[str] = []
    for nid in stack:
        if nid in menu.kids(fixed[-1] if fixed else None):
            fixed.append(nid)
        else:
            break
    sib = menu.kids(fixed[-1] if fixed else None)
    index = min(cursor.index, max(len(sib) - 1, 0))
    if tuple(fixed) != cursor.stack and cursor.screen == "menu":
        index = 0
    return replace(cursor, stack=tuple(fixed), index=index)


def path_to(node_id: str, menu: MenuView) -> tuple[tuple[str, ...], int] | None:
    """Stack and index that select ``node_id``, or None when it is hidden."""
    if not menu.visible(node_id):
        return None
    chain = []
    cur = menu.nodes[node_id].parent
    while cur is not None:
        chain.append(cur)
        cur = menu.nodes[cur].parent if cur in menu.nodes else None
    stack = tuple(reversed(chain))
    sib = menu.kids(stack[-1] if stack else None)
    return stack, sib.index(node_id)


def _run(cursor: Cursor, node_id: str, menu: MenuView) -> NavResult:
    where = path_to(node_id, menu)
    if where is None:
        return NavResult(cursor)
    stack, index = where
    c = replace(cursor, screen="result", stack=stack, index=index, node=node_id, job_id=None, page=0,
                prompt_id=None, return_screen="menu")
    return NavResult(c, (Effect("invoke", node=node_id),))


def handle_input(cursor: Cursor, key: str, ctx: NavContext) -> NavResult:
    menu = ctx.menu

    if key == "key1":
        current = cursor.node if cursor.screen == "result" else None
        nxt = KEY1_CYCLE[0]
        if current in KEY1_CYCLE:
            nxt = KEY1_CYCLE[(KEY1_CYCLE.index(current) + 1) % len(KEY1_CYCLE)]
        effects: tuple[Effect, ...] = ()
        if cursor.screen == "result" and cursor.job_id and ctx.job_running:
            effects = (Effect("detach", job_id=cursor.job_id),)
        r = _run(cursor, nxt, menu)
        return NavResult(r.cursor, effects + r.effects)
    if key == "key3":
        current = cursor.node if cursor.screen in ("result", "prompt") else None
        target = KEY3_TOGGLE[1] if current == KEY3_TOGGLE[0] else KEY3_TOGGLE[0]
        effects = ()
        if cursor.screen == "prompt" and cursor.prompt_id:
            effects = (Effect("cancel_prompt", prompt_id=cursor.prompt_id),)
        r = _run(cursor, target, menu)
        return NavResult(r.cursor, effects + r.effects)
    if key == "key2":
        # Display rotation is panel-local in FPMS; the service ignores it.
        return NavResult(cursor)

    if cursor.screen == "home":
        if key in ("down", "right", "center"):
            return NavResult(replace(cursor, screen="menu", stack=(), index=0))
        return NavResult(cursor)

    if cursor.screen == "menu":
        sib = _siblings(cursor, menu)
        if key in ("up", "down"):
            if not sib:
                return NavResult(cursor)
            step = 1 if key == "down" else -1
            return NavResult(replace(cursor, index=(cursor.index + step) % len(sib)))
        if key == "left":
            if not cursor.stack:
                return NavResult(replace(cursor, screen="home", index=0))
            parent = cursor.stack[-1]
            stack = cursor.stack[:-1]
            up = menu.kids(stack[-1] if stack else None)
            return NavResult(replace(cursor, stack=stack, index=up.index(parent) if parent in up else 0))
        if key in ("right", "center"):
            nid = selected_node(cursor, menu)
            if nid is None:
                return NavResult(cursor)
            node = menu.nodes[nid]
            if node.is_branch:
                return NavResult(replace(cursor, stack=cursor.stack + (nid,), index=0))
            return _run(cursor, nid, menu)
        return NavResult(cursor)

    if cursor.screen == "result":
        if key == "left":
            effects = (Effect("detach", job_id=cursor.job_id),) if cursor.job_id and ctx.job_running else ()
            return NavResult(replace(cursor, screen="menu", job_id=None, node=None, page=0), effects)
        if key in ("up", "down"):
            if ctx.page_count <= 1:
                return NavResult(cursor)
            page = cursor.page + (1 if key == "down" else -1)
            page = max(0, min(page, ctx.page_count - 1))
            if page == cursor.page:
                return NavResult(cursor)
            return NavResult(replace(cursor, page=page), (Effect("page", job_id=cursor.job_id, page=page),))
        if key in ("right", "center"):
            if ctx.job_running or not cursor.node:
                return NavResult(cursor)
            return _run(cursor, cursor.node, menu)
        return NavResult(cursor)

    if cursor.screen == "prompt":
        p = ctx.prompt
        if p is None or p.prompt_id != cursor.prompt_id:
            return NavResult(replace(cursor, screen=cursor.return_screen, prompt_id=None))
        if key == "left":
            return NavResult(cursor, (Effect("cancel_prompt", prompt_id=p.prompt_id),))
        if p.kind == "select":
            if key in ("up", "down") and p.option_count:
                step = 1 if key == "down" else -1
                return NavResult(replace(cursor, prompt_index=(cursor.prompt_index + step) % p.option_count))
            if key in ("right", "center") and p.option_count:
                return NavResult(cursor, (Effect("answer", prompt_id=p.prompt_id, value=cursor.prompt_index),))
        elif p.kind == "confirm":
            if key in ("up", "down"):
                return NavResult(replace(cursor, prompt_index=1 - cursor.prompt_index))
            if key in ("right", "center"):
                return NavResult(cursor, (Effect("answer", prompt_id=p.prompt_id, value=cursor.prompt_index == 1),))
        elif p.kind == "integer":
            value = cursor.prompt_value if cursor.prompt_value is not None else (p.minimum or 0)
            if key in ("up", "down"):
                value += p.step if key == "up" else -p.step
                if p.minimum is not None:
                    value = max(p.minimum, value)
                if p.maximum is not None:
                    value = min(p.maximum, value)
                return NavResult(replace(cursor, prompt_value=value))
            if key in ("right", "center"):
                return NavResult(cursor, (Effect("answer", prompt_id=p.prompt_id, value=value),))
        # string and form prompts need a keyboard: the client answers with an answer frame.
        return NavResult(cursor)

    return NavResult(cursor)
