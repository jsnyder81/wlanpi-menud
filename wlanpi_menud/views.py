"""Thin-mode ``view`` frames: the session's current screen as view blocks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import blocks as B
from .nav import selected_node

if TYPE_CHECKING:
    from .jobs import Job, JobManager, Prompt
    from .session import Session

HINTS = {
    "home": {"down": "Menu"},
    "menu": {"left": "Back", "center": "Select"},
    "result": {"left": "Back", "center": "Again"},
    "busy": {"left": "Back"},
    "prompt": {"left": "Cancel", "center": "OK"},
    "unavailable": {},
}


def home_blocks(summary: dict[str, Any], alerts: list[Any], complications: list[Any]) -> tuple[str, list[dict[str, Any]]]:
    if not summary or summary.get("core") == "unavailable":
        out = [B.status("error", "wlanpi-core unavailable", "Core")]
        if summary.get("ip"):
            out.append(B.kv([("IP", summary["ip"])]))
        return "unavailable", out
    items = [(k, summary.get(v)) for k, v in (("Host", "hostname"), ("Mode", "mode"), ("IP", "ip"),
                                               ("Model", "model"), ("Temp", "cpu_temp")) if summary.get(v)]
    out = [B.kv(items)] if items else []
    for a in alerts:
        out.append(B.status("warn", str(a.get("message", a)) if isinstance(a, dict) else str(a)))
    for c in complications:
        level = {"ok": "ok", "warning": "warn", "error": "error"}.get(c.get("status"), "info")
        out.append(B.status(level, f"{c.get('label')}: {c.get('value')}", c.get("label")))
    return "home", out or [B.text("WLAN Pi")]


def prompt_blocks(p: "Prompt", session: "Session") -> list[dict[str, Any]]:
    f = p.frame
    out: list[dict[str, Any]] = []
    if f.get("message") and p.kind != "confirm":
        out.append(B.text(f["message"]))
    if p.kind == "select":
        out.append(B.list_block([{"label": o["label"], "detail": o.get("detail")} for o in f["options"]],
                                selected=min(session.cursor.prompt_index, max(len(f["options"]) - 1, 0))))
    elif p.kind == "confirm":
        out.append(B.confirm(p.id, f.get("message") or f["title"], f.get("confirm_label", "Confirm"),
                             f.get("cancel_label", "Cancel"),
                             selected="confirm" if session.cursor.prompt_index == 1 else "cancel"))
    elif p.kind == "integer":
        value = session.cursor.prompt_value
        if value is None:
            value = f.get("default", f.get("minimum", 0))
        out.append(B.text(f"{f['title']}: {value}", "heading"))
    else:
        out.append(B.text(f"{f['title']}: answer on the client keyboard"))
    return out


def render_view(session: "Session", msg_id: str, jobs: "JobManager", hub_data: dict[str, Any]) -> dict[str, Any]:
    c = session.cursor
    menu = session.menu_view
    frame: dict[str, Any] = {"v": 1, "type": "view", "id": msg_id}
    if c.screen == "home":
        screen, blocks = home_blocks(hub_data.get("device.summary") or {}, hub_data.get("alerts") or [],
                                     hub_data.get("complications") or [])
        frame.update(screen=screen, node=None, title="WLAN Pi", blocks=blocks)
    elif c.screen == "menu":
        parent = c.stack[-1] if c.stack else None
        kids = menu.kids(parent)
        sel = selected_node(c, menu)
        items = [{"label": menu.nodes[k].label, "node": k, "branch": menu.nodes[k].is_branch} for k in kids]
        title = menu.nodes[parent].label if parent in menu.nodes else "Menu"
        frame.update(screen="menu", node=sel, title=title[:64], breadcrumb=menu.breadcrumb(c.stack),
                     blocks=[B.list_block(items, selected=kids.index(sel) if sel in kids else None)]
                     if items else [B.text("Nothing here")])
    elif c.screen == "prompt" and c.prompt_id in jobs.prompts:
        p = jobs.prompts[c.prompt_id]
        frame.update(screen="prompt", node=p.node, title=p.frame["title"], blocks=prompt_blocks(p, session),
                     job_id=p.job_id)
    else:  # result (or a prompt that just closed)
        job: Job | None = jobs.jobs.get(c.job_id) if c.job_id else None
        node = c.node
        label = menu.nodes[node].label if node in menu.nodes else (node or "")
        if job is None:
            frame.update(screen="busy", node=node, title=label[:64], blocks=[B.progress(None, "Starting…")])
        elif job.live:
            p = job.progress or {}
            frame.update(screen="busy", node=node, title=label[:64], job_id=job.id,
                         blocks=[B.progress(p.get("pct"), p.get("message") or "Working…")])
        elif job.state == "result":
            page = max(0, min(c.page, len(job.pages) - 1))
            frame.update(screen="result", node=node, title=(job.title or label)[:64], job_id=job.id,
                         blocks=job.pages[page] if job.pages else [B.text("No data")],
                         page={"index": page, "count": max(len(job.pages), 1), "more": page + 1 < len(job.pages)})
        elif job.state == "error":
            err = job.error or {}
            frame.update(screen="result", node=node, title=label[:64], job_id=job.id,
                         blocks=[B.status("error", err.get("message", "Failed"), err.get("code"))])
        else:
            frame.update(screen="result", node=node, title=label[:64], job_id=job.id,
                         blocks=[B.status("info", "Cancelled")])
    frame["hints"] = HINTS.get(frame["screen"], {})
    if c.stack and "breadcrumb" not in frame:
        frame["breadcrumb"] = menu.breadcrumb(c.stack)
    return frame
