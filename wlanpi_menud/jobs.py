"""Jobs: every action runs as one.

Rules (plan: "Jobs, sharing and conflicts"; PROTOCOL.md "Jobs", "Prompts"):

* Only reads marked ``shareable`` are shared or cached (``fresh_sec``). A
  result that contains a secret is never shared or cached, whatever the
  manifest says. Mutations are never shared, deduplicated or cached: every
  invoke of a mutation is a new job.
* A prompt (parameter, confirmation or mid-job selection) is bound to the
  session and principal that invoked the job. Anyone else gets FORBIDDEN.
* ``cancel`` detaches the caller. The job stops only when no session is
  still attached and the action is cancellable. A job that has not started
  executing (still collecting parameters or a confirmation) has done
  nothing yet, so it always stops.
* Core is the authority on busy: a core 409 becomes CONFLICT with core's
  message.
* Results are paged: the first page goes out with a cursor; ``page`` (or a
  thin client's up/down) fetches the next from the held result.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from . import blocks as B
from .core import CoreAuthRejected, CoreError, CoreUnavailable
from .handlers import HandlerContext, PromptCancelled, get_handler
from .handlers.base import BoundCore
from .menu import Node

if TYPE_CHECKING:
    from .service import Service
    from .session import Session

log = logging.getLogger(__name__)
audit = logging.getLogger("wlanpi_menud.audit")

LIVE = ("queued", "progress")
STATUS_CODES = {400: "BAD_REQUEST", 403: "FORBIDDEN", 404: "NOT_FOUND", 409: "CONFLICT", 422: "BAD_REQUEST"}


class InvokeRefused(Exception):
    def __init__(self, code: str, message: str, core_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.core_status = core_status


@dataclass
class Prompt:
    id: str
    session_id: str
    principal_key: str
    job_id: str
    node: str
    kind: str
    frame: dict[str, Any]
    values: list[Any]  # select: option values, by index
    future: asyncio.Future
    fields: list[dict[str, Any]] = field(default_factory=list)  # form / single param definition
    timer: asyncio.TimerHandle | None = None


@dataclass
class Job:
    id: str
    node: Node
    params: dict[str, Any]
    owner: str  # session id
    principal_key: str
    token: str | None  # remote user's JWT, or None for the service token
    confirmed: bool
    rich: bool
    state: str = "queued"
    attached: set[str] = field(default_factory=set)
    task: asyncio.Task | None = None
    executing: bool = False
    share_key: str | None = None
    secret: bool = False
    title: str | None = None
    pages: list[list[dict[str, Any]]] = field(default_factory=list)
    total: int = 0
    error: dict[str, Any] | None = None
    progress: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    timeout_cm: Any = None
    prompts: set[str] = field(default_factory=set)

    @property
    def shared(self) -> bool:
        return self.share_key is not None and len(self.attached) > 1

    @property
    def live(self) -> bool:
        return self.state in LIVE


def _new_id(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(9).replace("-", "_")


def encode_cursor(job_id: str, page: int) -> str:
    return "c" + base64.urlsafe_b64encode(f"{job_id}:{page}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, int] | None:
    try:
        raw = cursor[1:] + "=" * (-len(cursor[1:]) % 4)
        job_id, page = base64.urlsafe_b64decode(raw).decode().rsplit(":", 1)
        return job_id, int(page)
    except Exception:
        return None


def json_pointer(doc: Any, pointer: str) -> Any:
    if pointer in ("", "/"):
        return doc
    cur = doc
    for raw in pointer.split("/")[1:]:
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def validate_param(p: dict[str, Any], value: Any) -> tuple[bool, Any, str | None]:
    t = p["type"]
    if t == "enum":
        allowed = [c["value"] for c in p["choices"]]
        return (True, value, None) if value in allowed else (False, None, f"{p['name']}: not one of the choices")
    if t == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            return False, None, f"{p['name']}: must be an integer"
        if "minimum" in p and value < p["minimum"] or "maximum" in p and value > p["maximum"]:
            return False, None, f"{p['name']}: out of range"
        return True, value, None
    if t == "string":
        if not isinstance(value, str):
            return False, None, f"{p['name']}: must be a string"
        if len(value) < p.get("min_length", 0) or ("max_length" in p and len(value) > p["max_length"]):
            return False, None, f"{p['name']}: wrong length"
        if "pattern" in p and not re.search(p["pattern"], value):
            return False, None, f"{p['name']}: does not match the pattern"
        return True, value, None
    if t == "adapter":
        if not isinstance(value, dict) or not isinstance(value.get("iface"), str):
            return False, None, f"{p['name']}: must be {{iface, namespace}}"
        return True, {"iface": value["iface"], "namespace": value.get("namespace")}, None
    return False, None, f"{p['name']}: unknown parameter type"


def build_request(node: Node, params: dict[str, Any]) -> tuple[str, str, dict[str, Any], Any]:
    action = node.action or {}
    method, _, path = action["core"].partition(" ")
    query: dict[str, Any] = dict(action.get("query", {}))
    body: Any = dict(action["body"]) if "body" in action else None
    defs = {p["name"]: p for p in node.params}
    for name, where in action.get("bind", {}).items():
        if name not in params or params[name] is None:
            continue
        value = params[name]
        if where == "path":
            path = path.replace("{" + name + "}", quote(str(value), safe=""))
        elif where == "query":
            if defs.get(name, {}).get("type") == "adapter":
                query["iface"] = value["iface"]
                if value.get("namespace"):
                    query["namespace"] = value["namespace"]
            else:
                query[name] = value
        else:
            body = dict(body or {})
            body[name] = value
    return method, path, query, body


class JobManager:
    def __init__(self, service: "Service"):
        self.service = service
        self.jobs: dict[str, Job] = {}
        self.prompts: dict[str, Prompt] = {}
        self._share: dict[str, str] = {}  # share key -> job id
        self.on_change: Callable[[], None] | None = None

    # ------------------------------------------------------------------ frames

    def job_frame(self, job: Job, msg_id: str, page: int = 0, *, shared: bool | None = None) -> dict[str, Any]:
        f: dict[str, Any] = {"v": 1, "type": "job", "id": msg_id, "job_id": job.id, "node": job.node.id,
                             "state": job.state}
        is_shared = job.shared if shared is None else shared
        if is_shared:
            f["shared"] = True
        if job.state == "result":
            f["result"] = self.result_page(job, page)
            if is_shared and job.finished_at:
                f["cached_at"] = datetime.fromtimestamp(job.finished_at, UTC).isoformat().replace("+00:00", "Z")
        elif job.state == "error":
            f["error"] = job.error
        elif job.state == "progress":
            f["progress"] = job.progress or {"pct": None}
        return f

    def result_page(self, job: Job, page: int) -> dict[str, Any]:
        page = max(0, min(page, len(job.pages) - 1))
        r: dict[str, Any] = {"blocks": job.pages[page] if job.pages else [], "total": job.total,
                             "cursor": encode_cursor(job.id, page + 1) if page + 1 < len(job.pages) else None}
        if job.title:
            r["title"] = job.title[:64]
        return r

    def _broadcast(self, job: Job) -> None:
        for sid in list(job.attached):
            s = self.service.sessions.get(sid)
            if s is not None:
                s.send(self.job_frame(job, s.next_id()))
                self.service.job_changed_for(s, job)
        if self.on_change:
            self.on_change()

    # ------------------------------------------------------------------ invoke

    def invoke(self, session: "Session", node_id: str, params: dict[str, Any] | None, confirmed: bool,
               msg_id: str) -> Job:
        """Start (or attach to) a job. Sends the queued/shared reply with ``msg_id``. Raises InvokeRefused."""
        node = self.service.menu.get(node_id)
        if node is None:
            raise InvokeRefused("NOT_FOUND", f"unknown node {node_id}")
        if not session.menu_view.visible(node_id):
            raise InvokeRefused("FORBIDDEN", f"{node_id} is not available to this session")
        if node.is_branch:
            raise InvokeRefused("BAD_REQUEST", f"{node_id} is a branch, not an action")
        params = dict(params or {})
        defs = {p["name"]: p for p in node.params}
        for name in params:
            if name not in defs:
                raise InvokeRefused("BAD_REQUEST", f"{node_id} has no parameter {name}")
        for name, value in list(params.items()):
            ok, coerced, err = validate_param(defs[name], value)
            if not ok:
                raise InvokeRefused("BAD_REQUEST", err or "invalid parameter")
            params[name] = coerced

        missing = [p for p in node.params if p["name"] not in params and p.get("required", True)]
        principal = session.principal
        audit.info("invoke %s by %s (%s) session %s", node_id, principal.name, principal.kind, session.id)

        share_key = None
        if node.shareable and not missing:
            share_key = node.id + json.dumps(params, sort_keys=True, default=str)
            existing = self.jobs.get(self._share.get(share_key, ""))
            if existing is not None and not existing.secret:
                fresh = (existing.state == "result" and existing.finished_at is not None
                         and time.time() - existing.finished_at < node.fresh_sec)
                if existing.live or fresh:
                    existing.attached.add(session.id)
                    session.jobs.add(existing.id)
                    session.send(self.job_frame(existing, msg_id, shared=True))
                    if self.on_change:
                        self.on_change()
                    return existing

        job = Job(
            id=_new_id("j"), node=node, params=params, owner=session.id, principal_key=principal.key,
            token=principal.token if principal.kind == "jwt" else None,
            confirmed=confirmed and session.mode == "rich", rich=session.mode == "rich", share_key=share_key,
        )
        job.attached.add(session.id)
        session.jobs.add(job.id)
        self.jobs[job.id] = job
        if share_key:
            self._share[share_key] = job.id
        session.send(self.job_frame(job, msg_id))
        job.task = asyncio.get_running_loop().create_task(self._run(job), name=f"job {job.id} {node.id}")
        if self.on_change:
            self.on_change()
        return job

    # ------------------------------------------------------------------ prompts

    async def prompt(self, job: Job, kind: str, title: str, *, message: str | None = None,
                     options: list[dict[str, Any]] | None = None, fields: list[dict[str, Any]] | None = None,
                     param: dict[str, Any] | None = None, confirm: dict[str, Any] | None = None) -> Any:
        owner = self.service.sessions.get(job.owner)
        if owner is None or owner.id not in job.attached:
            raise PromptCancelled("invoking session has left")
        # A job that needs one person's answer is no longer a neutral read
        # others may attach to (the result now depends on the answer).
        if job.share_key and self._share.get(job.share_key) == job.id:
            del self._share[job.share_key]
        pid = _new_id("p")
        frame: dict[str, Any] = {"v": 1, "type": "prompt", "id": "", "prompt_id": pid, "job_id": job.id,
                                 "node": job.node.id, "kind": kind, "title": title[:64],
                                 "expires_sec": self.service.config.prompt_expires_sec}
        if message:
            frame["message"] = message[:256]
        values: list[Any] = []
        if kind == "select":
            opts = []
            for o in options or []:
                item = {"value": o["value"], "label": str(o["label"])[:64]}
                if o.get("detail"):
                    item["detail"] = str(o["detail"])[:64]
                opts.append(item)
                values.append(o["value"])
            frame["options"] = opts
        if kind == "form":
            frame["fields"] = fields or []
        if confirm:
            frame["confirm_label"] = confirm.get("confirm_label", "Confirm")[:16]
            frame["cancel_label"] = confirm.get("cancel_label", "Cancel")[:16]
        if param is not None:
            for k in ("minimum", "maximum"):
                if k in param:
                    frame[k] = param[k]
            if "default" in param:
                frame["default"] = param["default"]
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        p = Prompt(pid, owner.id, owner.principal.key, job.id, job.node.id, kind, frame, values, fut,
                   fields=fields or ([param] if param else []))
        p.timer = loop.call_later(self.service.config.prompt_expires_sec, self._expire, pid)
        self.prompts[pid] = p
        job.prompts.add(pid)
        # Waiting for a person does not count against the action's timeout.
        cm = job.timeout_cm
        remaining = None
        if cm is not None and cm.when() is not None:
            remaining = cm.when() - loop.time()
            cm.reschedule(None)
        try:
            frame["id"] = owner.next_id()
            owner.send(frame)
            self.service.prompt_opened(owner, p)
            return await fut
        finally:
            if p.timer:
                p.timer.cancel()
            self.prompts.pop(pid, None)
            job.prompts.discard(pid)
            if cm is not None and remaining is not None:
                cm.reschedule(loop.time() + max(remaining, 0.0))
            s = self.service.sessions.get(p.session_id)
            if s is not None:
                self.service.prompt_closed(s, p)

    def _expire(self, pid: str) -> None:
        p = self.prompts.get(pid)
        if p and not p.future.done():
            p.future.set_exception(PromptCancelled("prompt expired"))

    def answer(self, session: "Session", frame: dict[str, Any]) -> None:
        """Raises InvokeRefused for a bad answer; the prompt stays open then."""
        p = self.prompts.get(frame["prompt_id"])
        if p is None or p.future.done():
            raise InvokeRefused("NOT_FOUND", "unknown or closed prompt")
        if p.session_id != session.id or p.principal_key != session.principal.key:
            raise InvokeRefused("FORBIDDEN", "this prompt belongs to another session")
        if frame.get("cancel"):
            p.future.set_exception(PromptCancelled("cancelled"))
            return
        if p.kind == "confirm":
            if "confirmed" not in frame:
                raise InvokeRefused("BAD_REQUEST", "a confirm prompt takes confirmed")
            if frame["confirmed"]:
                p.future.set_result(True)
            else:
                p.future.set_exception(PromptCancelled("not confirmed"))
            return
        if p.kind == "form":
            values = frame.get("values")
            if not isinstance(values, dict):
                raise InvokeRefused("BAD_REQUEST", "a form prompt takes values")
            out = {}
            for fd in p.fields:
                if fd["name"] not in values:
                    if fd.get("required", True):
                        raise InvokeRefused("BAD_REQUEST", f"{fd['name']} is required")
                    continue
                ok, v, err = validate_param(fd, values[fd["name"]])
                if not ok:
                    raise InvokeRefused("BAD_REQUEST", err or "invalid")
                out[fd["name"]] = v
            p.future.set_result(out)
            return
        if "value" not in frame:
            raise InvokeRefused("BAD_REQUEST", "this prompt takes value")
        value = frame["value"]
        if p.kind == "select":
            if value not in p.values:
                raise InvokeRefused("BAD_REQUEST", "not one of the offered options")
        elif p.fields:
            ok, value, err = validate_param(p.fields[0], value)
            if not ok:
                raise InvokeRefused("BAD_REQUEST", err or "invalid")
        p.future.set_result(value)

    def cancel_prompt(self, session: "Session", pid: str) -> None:
        p = self.prompts.get(pid)
        if p and p.session_id == session.id and not p.future.done():
            p.future.set_exception(PromptCancelled("cancelled"))

    async def _collect_params(self, job: Job) -> dict[str, Any]:
        params = dict(job.params)
        missing = [p for p in job.node.params if p["name"] not in params and p.get("required", True)]
        if not missing:
            return params
        if job.rich:
            values = await self.prompt(job, "form", job.node.label, fields=missing)
            params.update(values)
            return params
        for p in missing:
            params[p["name"]] = await self._param_prompt(job, p)
        return params

    async def _param_prompt(self, job: Job, p: dict[str, Any]) -> Any:
        t = p["type"]
        if t == "enum":
            return await self.prompt(job, "select", p["label"], options=p["choices"], param=p)
        if t == "integer":
            return await self.prompt(job, "integer", p["label"], param=p)
        if t == "string":
            return await self.prompt(job, "string", p["label"], param=p)
        options = await self._adapter_options(job, p)
        if not options:
            raise CoreUnavailable("no suitable adapter found")
        return await self.prompt(job, "select", p["label"], options=options, param=None)

    async def _adapter_options(self, job: Job, p: dict[str, Any]) -> list[dict[str, Any]]:
        status = await BoundCore(self.service.core, job.token).get_network_config_status()
        kind = p.get("kind", "wlan")
        out = []
        for ns, ifaces in (status or {}).items():
            if not isinstance(ifaces, dict) or "error" in ifaces and isinstance(ifaces["error"], str):
                continue
            for name, info in ifaces.items():
                is_wlan = name.startswith("wlan") or bool((info or {}).get("wiphy"))
                if kind == "wlan" and not is_wlan or kind == "ethernet" and is_wlan:
                    continue
                out.append({"value": {"iface": name, "namespace": None if ns == "root" else ns},
                            "label": name, "detail": ns})
        return out

    # ------------------------------------------------------------------ run

    async def _run(self, job: Job) -> None:
        try:
            params = await self._collect_params(job)
            confirm = job.node.confirm
            if confirm and not job.confirmed:
                await self.prompt(job, "confirm", confirm.get("title") or job.node.label, message=confirm["message"],
                                  confirm=confirm)
            job.executing = True
            job.state = "progress"
            job.progress = {"pct": None, "message": "Running"}
            self._broadcast(job)
            async with asyncio.timeout(job.node.timeout_sec) as cm:
                job.timeout_cm = cm
                title, blocks = await self._execute(job, params)
            job.timeout_cm = None
            if B.has_secret(blocks):
                job.secret = True
                if job.share_key and self._share.get(job.share_key) == job.id:
                    del self._share[job.share_key]
            job.title = title
            job.pages, job.total = B.paginate(blocks, self.service.config.page_size)
            self._finish(job, "result")
        except PromptCancelled as exc:
            log.info("job %s cancelled: %s", job.id, exc)
            self._finish(job, "cancelled")
        except asyncio.CancelledError:
            self._finish(job, "cancelled")
        except TimeoutError:
            self._fail(job, "TIMEOUT", f"{job.node.label} took longer than {job.node.timeout_sec} s")
        except CoreAuthRejected:
            self._fail(job, "AUTH_FAILED", "core refused this session's token")
            self.service.revoke_principal(job.principal_key, "core refused the token")
        except CoreUnavailable as exc:
            self._fail(job, "CORE_UNAVAILABLE", exc.message, exc.status or None)
        except CoreError as exc:
            self._fail(job, STATUS_CODES.get(exc.status, "INTERNAL"), exc.message, exc.status)
        except Exception as exc:  # a handler bug must not take the service down
            log.exception("job %s (%s) failed", job.id, job.node.id)
            self._fail(job, "INTERNAL", f"{exc.__class__.__name__}: {exc}"[:512])

    async def _execute(self, job: Job, params: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        node = job.node
        action = node.action or {}
        core = BoundCore(self.service.core, job.token)
        if "handler" in action:
            fn = get_handler(action["handler"])
            if fn is None:
                raise CoreUnavailable(f"handler {action['handler']} is not available")

            async def ask(kind: str, title: str, **kw: Any) -> Any:
                return await self.prompt(job, kind, title, **kw)

            async def report(pct: float | None, message: str | None) -> None:
                job.progress = {"pct": pct} | ({"message": message[:128]} if message else {})
                self._broadcast(job)

            res = await fn(HandlerContext(core, node.id, params, ask, report))
            return res.title or node.label, res.blocks
        selections = 0
        while True:
            method, path, query, body = build_request(node, params)
            data = await core.request(method, path, query=query, body=body, timeout=node.timeout_sec)
            if isinstance(data, dict) and data.get("needsSelection") and data.get("candidates") and selections < 2:
                selections += 1
                options = [{"value": {"iface": c["iface"], "namespace": c.get("namespace")},
                            "label": c.get("label") or c["iface"], "detail": c.get("namespace")}
                           for c in data["candidates"] if isinstance(c, dict) and c.get("iface")]
                choice = await self.prompt(job, "select", "Adapter", options=options,
                                           message="Several adapters can scan. Pick one.")
                adapter_param = next((p["name"] for p in node.params if p["type"] == "adapter"
                                      and action.get("bind", {}).get(p["name"]) == "query"), None)
                if adapter_param is None:
                    # No declared adapter parameter: pass the choice straight through as query.
                    return node.label, await self._rerun_with(core, node, params, choice)
                params[adapter_param] = choice
                continue
            if "select" in action:
                data = json_pointer(data, action["select"])
            return node.label, B.render(data, action.get("view"), node.label)

    async def _rerun_with(self, core: BoundCore, node: Node, params: dict[str, Any],
                          choice: dict[str, Any]) -> list[dict[str, Any]]:
        method, path, query, body = build_request(node, params)
        query["iface"] = choice["iface"]
        if choice.get("namespace"):
            query["namespace"] = choice["namespace"]
        data = await core.request(method, path, query=query, body=body, timeout=node.timeout_sec)
        if "select" in (node.action or {}):
            data = json_pointer(data, node.action["select"])
        return B.render(data, (node.action or {}).get("view"), node.label)

    def _fail(self, job: Job, code: str, message: str, core_status: int | None = None) -> None:
        job.error = {"code": code, "message": message[:512] or code}
        if core_status:
            job.error["core_status"] = core_status
        self._finish(job, "error")

    def _finish(self, job: Job, state: str) -> None:
        job.state = state
        job.finished_at = time.time()
        job.timeout_cm = None
        for pid in list(job.prompts):
            p = self.prompts.get(pid)
            if p and not p.future.done():
                p.future.cancel()
        if state != "result" and job.share_key and self._share.get(job.share_key) == job.id:
            del self._share[job.share_key]
        self._broadcast(job)

    # ------------------------------------------------------------------ cancel, page, cleanup

    def cancel(self, session: "Session", job_id: str, msg_id: str | None) -> None:
        job = self.jobs.get(job_id)
        if job is None or session.id not in job.attached:
            raise InvokeRefused("NOT_FOUND", f"no job {job_id} attached to this session")
        job.attached.discard(session.id)
        session.jobs.discard(job_id)
        if msg_id is not None:
            f = self.job_frame(job, msg_id)
            f["state"] = "cancelled"
            for k in ("result", "error", "progress", "cached_at"):
                f.pop(k, None)
            session.send(f)
        for pid in list(job.prompts):
            p = self.prompts.get(pid)
            if p and p.session_id == session.id and not p.future.done():
                p.future.set_exception(PromptCancelled("invoking session detached"))
        if not job.attached and job.live and job.task and (job.node.cancellable or not job.executing):
            job.task.cancel()
        if self.on_change:
            self.on_change()

    def detach_all(self, session: "Session") -> None:
        for job_id in list(session.jobs):
            try:
                self.cancel(session, job_id, None)
            except InvokeRefused:
                pass

    def page(self, session: "Session", job_id: str, cursor: str, msg_id: str) -> None:
        job = self.jobs.get(job_id)
        decoded = decode_cursor(cursor)
        if job is None or session.id not in job.attached:
            raise InvokeRefused("NOT_FOUND", f"no job {job_id} attached to this session")
        if job.state != "result" or decoded is None or decoded[0] != job_id or not 0 <= decoded[1] < len(job.pages):
            raise InvokeRefused("NOT_FOUND", "unknown cursor")
        session.send(self.job_frame(job, msg_id, decoded[1]))

    def live_jobs_for(self, session: "Session") -> list[Job]:
        return [self.jobs[j] for j in session.jobs if j in self.jobs]

    def summary(self) -> list[dict[str, Any]]:
        return [{"job_id": j.id, "node": j.node.id, "state": j.state, "sessions": len(j.attached)}
                for j in self.jobs.values() if j.live]

    def prune(self, retention_sec: float) -> None:
        now = time.time()
        for jid, job in list(self.jobs.items()):
            if job.finished_at is None:
                continue
            keep = max(retention_sec, job.node.fresh_sec)
            if now - job.finished_at > keep and (not job.attached or now - job.finished_at > 4 * keep):
                del self.jobs[jid]
                if job.share_key and self._share.get(job.share_key) == jid:
                    del self._share[job.share_key]
                for sid in job.attached:
                    s = self.service.sessions.get(sid)
                    if s:
                        s.jobs.discard(jid)

    async def shutdown(self) -> None:
        tasks = [j.task for j in self.jobs.values() if j.task and not j.task.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
