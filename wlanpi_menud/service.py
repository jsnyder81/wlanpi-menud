"""The service: menu, sessions, jobs, topics and device context in one place.

Transport-agnostic: ``server.py`` owns the sockets and calls ``hello`` and
``handle_frame``; everything a session receives goes through its own
bounded ``OutQueue``.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import protocol
from .auth import Introspector, LocalPolicy, Principal
from .config import Config
from .core import CoreClient, TokenSource
from .core.generated import ROUTES
from .handlers import handler_exists
from .jobs import InvokeRefused, Job, JobManager, Prompt
from .loader import LoadResult, load_menu
from .menu import DeviceContext, Menu, Node, RouteTable
from .nav import NavContext, PromptState, handle_input, normalize, path_to
from .outqueue import OutQueue
from .session import DEFAULT, NEW, Attachment, Session
from .topics import TopicHub, topic_matches
from .views import render_view

log = logging.getLogger(__name__)

HOME_TOPICS = ("device.summary", "alerts", "complications")


class ProtocolViolation(Exception):
    def __init__(self, frame: dict[str, Any]):
        super().__init__(frame["message"])
        self.frame = frame


class Service:
    def __init__(self, config: Config, core: CoreClient | None = None, tokens: TokenSource | None = None):
        self.config = config
        self.epoch = secrets.token_urlsafe(12).replace("-", "_")
        self.started_at = time.time()
        self.hub = TopicHub()
        self.hub.on_publish(self._on_publish)
        self.jobs = JobManager(self)
        self.jobs.on_change = self._publish_jobs
        self.sessions: dict[str, Session] = {}
        # The default session exists from start and never ends; the panel wakes up to it.
        self.default_session = Session(DEFAULT, "thin", validate=self.validator())
        self.sessions[self.default_session.id] = self.default_session
        self.tokens = tokens or TokenSource(config.token_command, config.token_refresh_margin_sec)
        self.core = core or CoreClient(config.core_base_url, self.tokens, prefix=config.core_api_prefix,
                                       cert=config.core_cert, timeout=config.core_timeout_sec,
                                       keepalive=config.core_keepalive_sec)
        self.introspector = Introspector(self.core, config.introspect_path, config.introspect_cache_sec)
        self.local_policy = LocalPolicy(config.local_uids, config.local_gids, config.provider_uids,
                                        config.local_user_names, config.local_group_names)
        self.routes = self._vendored_routes()
        self.ctx = DeviceContext(None, frozenset(), self.routes)
        self.menu_revision = 0
        self.load: LoadResult = self._load_menu()
        self.complications: dict[str, dict[str, Any]] = {}
        self.poller: Any = None  # the Poller sets itself here

    # ------------------------------------------------------------------ menu

    def _vendored_routes(self) -> RouteTable:
        snap = self.config.openapi_snapshot
        if snap is not None:
            try:
                return RouteTable.from_openapi(json.loads(Path(snap).read_text()), self.config.core_api_prefix)
            except (OSError, ValueError) as exc:
                log.warning("openapi snapshot %s unusable (%s); using the generated route table", snap, exc)
        return RouteTable(set(ROUTES))

    def _load_menu(self) -> LoadResult:
        res = load_menu(self.config.builtin_dir, self.config.plugins_dir, self.config.override_dir, handler_exists)
        log.info("menu: %d nodes from %d files, %d files rejected", len(res.menu), len(res.loaded), len(res.rejected))
        return res

    @property
    def menu(self) -> Menu:
        return self.load.menu

    def reload_menu(self) -> LoadResult:
        self.load = self._load_menu()
        self.menu_revision += 1
        self.refresh_menus(force=True)
        return self.load

    def set_context(self, ctx: DeviceContext) -> None:
        if ctx == self.ctx:
            return
        log.info("device context: mode=%s capabilities=%s", ctx.mode, sorted(ctx.capabilities))
        self.ctx = ctx
        self.menu_revision += 1
        self.refresh_menus()

    def set_routes(self, routes: RouteTable) -> None:
        self.routes = routes
        self.set_context(replace(self.ctx, core_routes=routes))

    def menu_frame(self, a: Attachment, msg_id: str) -> dict[str, Any]:
        f: dict[str, Any] = {"v": 1, "type": "menu", "id": msg_id, "revision": self.menu_revision,
                             "nodes": a.menu_view.to_frame_nodes(self.menu.plugin_of)}
        if self.ctx.mode:
            f["device_mode"] = self.ctx.mode
        return f

    def _recompute(self, a: Attachment) -> bool:
        before = set(a.menu_view.nodes)
        a.menu_view = self.menu.view(self.ctx, a.principal.roles)
        return set(a.menu_view.nodes) != before

    def _nav_view(self, session: Session) -> None:
        """The tree the shared cursor moves in: the roles of whoever drove it last."""
        roles = session.driver.principal.roles if session.driver else \
            (session.attachments[0].principal.roles if session.attachments else ("admin",))
        session.menu_view = self.menu.view(self.ctx, roles)

    def refresh_menus(self, force: bool = False) -> None:
        for s in list(self.sessions.values()):
            for a in list(s.attachments):
                changed = self._recompute(a)
                if changed or force:
                    a.send(self.menu_frame(a, a.next_id()))
                    a.menu_revision_sent = self.menu_revision
            if s.thin:
                self._nav_view(s)
                s.cursor = normalize(s.cursor, s.menu_view)
                self.push_view(s)

    # ------------------------------------------------------------------ outbound

    def validator(self):
        if not self.config.validate_outbound:
            return None

        def check(frame: dict[str, Any]) -> None:
            errs = protocol.frame_errors(frame, protocol.S2C)
            if errs:
                raise AssertionError(f"outbound {frame.get('type')} frame breaks frames.schema.json: {errs}")
        return check

    def push_view(self, session: Session, msg_id: str | None = None, reply_to: Attachment | None = None) -> None:
        """Render the session's screen for every attachment. ``reply_to`` gets it with ``msg_id``."""
        if not session.thin and reply_to is None and not (session.cursor.job_id and session.cursor.screen in ("result", "busy")):
            return
        for a in list(session.attachments):
            reply = msg_id is not None and (reply_to is None or a is reply_to)
            if not session.thin and not reply and not (session.cursor.job_id and session.cursor.screen in ("result", "busy")):
                continue
            frame = render_view(session, a, msg_id if reply else a.next_id(), self.jobs, self.hub.data)
            a.send(frame, coalesce=None if reply else "view")

    def _on_publish(self, topic: str, seq: int, data: Any) -> None:
        for s in list(self.sessions.values()):
            for a in list(s.attachments):
                if any(topic_matches(sub, topic) for sub in a.subscriptions):
                    a.send_event(self.hub.event(a.next_id(), topic, snapshot=False), topic)
            if s.thin and s.connected and s.cursor.screen == "home" and topic in HOME_TOPICS:
                self.push_view(s)

    def resnapshot(self, a: Attachment, topics: set[str]) -> list[dict[str, Any]]:
        return [self.hub.event(a.next_id(), t, snapshot=True) for t in sorted(topics) if t in self.hub.data]

    def _publish_jobs(self) -> None:
        self.hub.publish("jobs", self.jobs.summary())

    def job_finished(self, job: Job) -> None:
        # Mode switches, Bluetooth toggles and the like change the device
        # context; re-read it now instead of waiting for the slow tier.
        if job.node.mutates and self.poller is not None:
            self.poller.invalidate_context()

    def job_changed_for(self, session: Session, job: Job) -> None:
        if session.cursor.job_id == job.id and session.cursor.screen in ("result", "prompt"):
            self.push_view(session)

    def prompt_opened(self, session: Session, p: Prompt) -> None:
        if not session.thin and not session.cursor.job_id:
            return
        default = 0
        if p.kind == "select":
            d = p.frame.get("default")
            if d in p.values:
                default = p.values.index(d)
        session.cursor = replace(session.cursor, screen="prompt", prompt_id=p.id, prompt_index=default,
                                 prompt_value=p.frame.get("default", p.frame.get("minimum")) if p.kind == "integer" else None,
                                 job_id=p.job_id, return_screen="result")
        self.push_view(session)

    def prompt_closed(self, session: Session, p: Prompt) -> None:
        if (session.thin or session.cursor.job_id) and session.cursor.prompt_id == p.id:
            session.cursor = replace(session.cursor, screen="result", prompt_id=None)
            self.push_view(session)

    # ------------------------------------------------------------------ sessions

    def sessions_on(self, socket_kind: str) -> int:
        """Connections on a socket (the per-socket limit counts connections, not sessions)."""
        return sum(1 for s in self.sessions.values() for a in s.attachments if a.socket_kind == socket_kind)

    def hello(self, principal: Principal, frame: dict[str, Any], socket_kind: str,
              queue_factory, closer) -> tuple[Attachment, list[dict[str, Any]]]:
        """Handle a valid hello. Returns the new attachment and the frames to send, in order."""
        common = sorted(set(frame["protocol"]) & set(protocol.SUPPORTED))
        if not common:
            raise ProtocolViolation(protocol.error(frame["id"], "VERSION", "no common protocol version",
                                                   supported=list(protocol.SUPPORTED)))
        version = common[-1]
        mode = frame["mode"]
        kind = frame.get("session") or (DEFAULT if mode == "thin" else NEW)
        if kind == DEFAULT and mode != "thin":
            raise ProtocolViolation(protocol.error(
                frame["id"], "BAD_REQUEST", "the default session is thin: rich clients navigate locally "
                "(send session: new, or mode: thin to mirror the default screen)", close_code=4400))
        resume = frame.get("resume") or {}
        same_epoch = resume.get("epoch") == self.epoch
        prior = self.sessions.get(resume.get("session_id", "")) if same_epoch else None
        resumed = False
        subs: set[str] = set()
        seqs: dict[str, int] = resume.get("seq", {})
        if kind == DEFAULT:
            session = self.default_session
            if prior is session:
                # The cursor lives in the service; what resumes is this surface's topic stream.
                subs = set(seqs)
                resumed = all(self.hub.seq.get(t) == n for t, n in seqs.items())
        else:
            session = None
            if prior is not None and not prior.default and prior.owner_key == principal.key \
                    and prior.mode == mode and prior.socket_kind == socket_kind:
                for old in list(prior.attachments):
                    old.close(1000, "replaced by a resumed connection")
                    self._detach(old)
                session = prior
                subs = set(prior.saved_subscriptions)
                resumed = all(self.hub.seq.get(t) == seqs.get(t) for t in subs) and \
                    all(self.hub.seq.get(t) == n for t, n in seqs.items())
            if session is None:
                session = Session(NEW, mode, validate=self.validator(), owner_key=principal.key,
                                  socket_kind=socket_kind)
                self.sessions[session.id] = session
        a = Attachment(session, principal, socket_kind, client=frame.get("client"),
                       capabilities=frame.get("capabilities"), validate=self.validator())
        a.subscriptions = subs
        a.open(queue_factory(a), closer)
        session.attach(a)
        self._recompute(a)
        if session.thin:
            self._nav_view(session)
            session.cursor = normalize(session.cursor, session.menu_view)

        welcome = {"v": 1, "type": "welcome", "id": frame["id"], "session_id": session.id, "session": session.kind,
                   "epoch": self.epoch, "principal": principal.frame(), "roles": list(principal.roles),
                   "version": version, "mode": session.mode, "resumed": resumed,
                   "limits": {"queue_frames": self.config.queue_frames, "max_frame_bytes": self.config.max_frame_bytes}}
        out = [welcome]
        if not resumed or resume.get("menu_revision") != self.menu_revision:
            out.append(self.menu_frame(a, a.next_id()))
            a.menu_revision_sent = self.menu_revision
        if not resumed:
            for sub in sorted(a.subscriptions):
                for t in self.hub.topics_for(sub):
                    out.append(self.hub.event(a.next_id(), t, snapshot=True))
            for job in self.jobs.live_jobs_for(session):
                out.append(self.jobs.job_frame(job, a.next_id()))
            for p in self.jobs.prompts.values():
                if p.session_id == session.id:
                    out.append(dict(p.frame, id=a.next_id()))
        if session.thin:
            out.append(render_view(session, a, a.next_id(), self.jobs, self.hub.data))
        log.info("%s %s session %s (%s %s, %s) on %s; %d attached", "resumed" if resumed else "attached",
                 session.kind, session.id, principal.kind, principal.name, session.mode, socket_kind,
                 len(session.attachments))
        return a, out

    def disconnected(self, a: Attachment, queue: OutQueue) -> None:
        if a.queue is queue:  # a resumed connection may already have replaced it
            self._detach(a)

    def _detach(self, a: Attachment) -> None:
        session = a.session
        session.detach(a)
        if session.default:
            # A confirmation only its invoker can answer must not strand the
            # shared screen once no connection of that principal is left.
            keys = session.principal_keys()
            for p in list(self.jobs.prompts.values()):
                if p.session_id == session.id and p.kind == "confirm" and p.principal_key not in keys:
                    self.jobs.abort_prompt(p.id, "invoker detached")

    def expire_sessions(self) -> None:
        now = time.time()
        for s in list(self.sessions.values()):
            if s.default or s.connected or s.detached_at is None:
                continue
            if now - s.detached_at > self.config.resume_grace_sec:
                self.drop_session(s)

    def drop_session(self, s: Session) -> None:
        if s.default:
            return  # the default session never ends
        self.jobs.detach_all(s)
        for p in list(self.jobs.prompts.values()):
            if p.session_id == s.id:
                self.jobs.abort_prompt(p.id, "session ended")
        self.sessions.pop(s.id, None)

    def revoke_principal(self, key: str, reason: str) -> int:
        n = 0
        for s in list(self.sessions.values()):
            for a in list(s.attachments):
                if a.principal.key == key:
                    self.revoke_attachment(a, reason)
                    n += 1
            if not s.default and s.owner_key == key and s.id in self.sessions:
                self.drop_session(s)
        return n

    def revoke_webui_session(self, webui_session: str) -> int:
        """WebUI logout: close every connection carrying that WebUI session id (close 4401)."""
        n = 0
        for s in list(self.sessions.values()):
            for a in list(s.attachments):
                if a.principal.kind == "webui" and a.principal.webui_session == webui_session:
                    self.revoke_attachment(a, "WebUI session ended")
                    n += 1
        return n

    def revoke_attachment(self, a: Attachment, reason: str) -> None:
        """Close one connection 4401. Its independent session ends with it; the default session stays."""
        # The connection detaches itself once the writer has flushed the error and closed.
        if a.connected:
            a.send(protocol.error(a.next_id(), "AUTH_FAILED", reason))
            a.close(4401, reason)
        else:
            self._detach(a)
        if not a.session.default:
            self.drop_session(a.session)

    # ------------------------------------------------------------------ frames from a connection

    def _refuse(self, a: Attachment, msg_id: str, exc: InvokeRefused) -> None:
        extra = {"core_status": exc.core_status} if exc.core_status else {}
        a.send(protocol.error(msg_id, exc.code, exc.message, **extra))

    def handle_frame(self, a: Attachment, frame: dict[str, Any]) -> None:
        t, mid = frame["type"], frame["id"]
        session = a.session
        try:
            if t == "subscribe":
                for sub in frame["topics"]:
                    if sub in a.subscriptions:
                        continue
                    a.subscriptions.add(sub)
                    for topic in self.hub.topics_for(sub):
                        a.send(self.hub.event(mid, topic, snapshot=True))
            elif t == "unsubscribe":
                a.subscriptions.difference_update(frame["topics"])
            elif t == "input":
                if not session.thin:
                    raise InvokeRefused("BAD_REQUEST", "input is for thin sessions; rich sessions send invoke")
                self._input(a, frame)
            elif t == "invoke":
                if session.thin:
                    self._guard_confirm(a)
                job = self.jobs.invoke(session, a, frame["node"], frame.get("params"), frame.get("confirmed", False),
                                       mid)
                if session.thin:
                    session.drove(a)
                    where = path_to(frame["node"], a.menu_view)
                    if where:
                        session.cursor = replace(session.cursor, screen="result", stack=where[0], index=where[1],
                                                 node=frame["node"], job_id=job.id, page=0)
                    self.push_view(session)
            elif t == "answer":
                self.jobs.answer(session, a, frame)
                session.drove(a)
            elif t == "page":
                self.jobs.page(session, a, frame["job_id"], frame["cursor"], mid)
            elif t == "cancel":
                self.jobs.cancel(session, frame["job_id"], mid, a)
                if session.thin and session.cursor.job_id == frame["job_id"]:
                    session.drove(a)
                    session.cursor = replace(session.cursor, screen="menu", job_id=None, node=None)
                    self.push_view(session)
            elif t == "nav":
                if a not in session.attachments:
                    raise InvokeRefused("FORBIDDEN", "connection is detached")
                if session.thin:
                    self._guard_confirm(a)
                nid = frame["node"]
                node = self.menu.get(nid)
                if node is None:
                    raise InvokeRefused("NOT_FOUND", f"unknown node {nid}")
                if not a.menu_view.visible(nid):
                    raise InvokeRefused("FORBIDDEN", f"{nid} is not available to this session")
                self._nav(a, mid, node)
            elif t == "revoke":
                if a.socket_kind != "local" or "admin" not in a.principal.roles:
                    raise InvokeRefused("FORBIDDEN", "revoke is restricted to local admin principals on local.sock")
                target = frame.get("webui_session")
                if not isinstance(target, str) or not target:
                    raise InvokeRefused("BAD_REQUEST", "webui_session is required")
                self.revoke_webui_session(target)
            elif t in ("hello", "auth"):
                raise InvokeRefused("BAD_REQUEST", f"{t} is only valid once, at the start")
        except InvokeRefused as exc:
            self._refuse(a, mid, exc)

    def _nav(self, a: Attachment, mid: str, node: Node) -> None:
        session = a.session
        nid = node.id
        session.drove(a)
        session.menu_view = a.menu_view
        if node.is_branch:
            cur: str | None = nid
            chain: list[str] = []
            while cur is not None:
                chain.append(cur)
                cur = a.menu_view.nodes[cur].parent if cur in a.menu_view.nodes else None
            stack = tuple(reversed(chain))
            session.cursor = replace(
                session.cursor,
                screen="menu",
                stack=stack,
                index=0,
                job_id=None,
                node=None,
                prompt_id=None,
                return_screen="menu",
            )
            self.push_view(session, mid, reply_to=a)
        else:
            where = path_to(nid, a.menu_view)
            stack = where[0] if where else ()
            index = where[1] if where else 0
            job = self.jobs.invoke(session, a, nid, None, False, mid)
            session.cursor = replace(
                session.cursor,
                screen="result",
                stack=stack,
                index=index,
                node=nid,
                job_id=job.id,
                page=0,
                prompt_id=None,
                return_screen="menu",
            )
            self.push_view(session, mid, reply_to=a)

    def _guard_confirm(self, a: Attachment) -> None:
        """On a shared screen, a confirmation belongs to the principal that invoked it:
        nobody else answers it, cancels it or navigates away from it."""
        c = a.session.cursor
        p = self.jobs.prompts.get(c.prompt_id) if c.screen == "prompt" and c.prompt_id else None
        if p is not None and not self.jobs.may_answer(a.session, a, p):
            raise InvokeRefused("FORBIDDEN", "this confirmation belongs to the principal that invoked it")

    def _nav_context(self, session: Session) -> NavContext:
        c = session.cursor
        job = self.jobs.jobs.get(c.job_id) if c.job_id else None
        prompt = None
        if c.prompt_id and c.prompt_id in self.jobs.prompts:
            p = self.jobs.prompts[c.prompt_id]
            fd = p.fields[0] if p.fields else {}
            prompt = PromptState(p.id, p.kind, len(p.values), fd.get("minimum"), fd.get("maximum"), fd.get("step", 1))
        return NavContext(session.menu_view, page_count=max(len(job.pages), 1) if job else 1,
                          job_running=bool(job and job.live), prompt=prompt)

    def _input(self, a: Attachment, frame: dict[str, Any]) -> None:
        mid = frame["id"]
        session = a.session
        try:
            self._guard_confirm(a)
        except InvokeRefused as exc:
            self._refuse(a, mid, exc)
            self.push_view(session, mid, reply_to=a)
            return
        # Last write wins: this connection drives, in the tree its principal sees.
        session.drove(a)
        session.menu_view = a.menu_view
        session.cursor = normalize(session.cursor, a.menu_view)
        result = handle_input(session.cursor, frame["key"], self._nav_context(session))
        session.cursor = result.cursor
        for eff in result.effects:
            try:
                if eff.kind == "invoke":
                    job = self.jobs.invoke(session, a, eff.node, None, False, a.next_id())
                    session.cursor = replace(session.cursor, job_id=job.id)
                elif eff.kind == "answer":
                    p = self.jobs.prompts.get(eff.prompt_id)
                    if p is None:
                        continue
                    value = p.values[eff.value] if p.kind == "select" else eff.value
                    if p.kind == "confirm":
                        self.jobs.answer(session, a, {"prompt_id": p.id, "confirmed": bool(value)})
                    else:
                        self.jobs.answer(session, a, {"prompt_id": p.id, "value": value})
                elif eff.kind == "cancel_prompt":
                    self.jobs.cancel_prompt(session, a, eff.prompt_id)
                elif eff.kind == "detach" and eff.job_id:
                    self.jobs.cancel(session, eff.job_id, None, a)
            except InvokeRefused as exc:
                self._refuse(a, mid, exc)
                if eff.kind == "invoke":
                    session.cursor = replace(session.cursor, screen="menu", job_id=None)
        self.push_view(session, mid, reply_to=a)

    # ------------------------------------------------------------------ complications (provider publish)

    def publish_complication(self, app_id: str, label: str, value: str, status: str = "unknown",
                             ttl_seconds: int = 30) -> None:
        self.complications[app_id] = {"app_id": app_id, "label": label[:8], "value": value, "status": status,
                                      "expires_at": time.time() + ttl_seconds}
        self.publish_complications()

    def publish_complications(self) -> None:
        now = time.time()
        for k in [k for k, c in self.complications.items() if c["expires_at"] <= now]:
            del self.complications[k]
        self.hub.publish("complications", [
            {k: v for k, v in c.items() if k != "expires_at"} for _, c in sorted(self.complications.items())
        ])

    # ------------------------------------------------------------------ lifecycle

    async def aclose(self) -> None:
        for s in list(self.sessions.values()):
            s.close(1001, "service stopping")
        await self.jobs.shutdown()
        await self.core.aclose()

    def new_queue(self, a: Attachment) -> OutQueue:
        return OutQueue(self.config.queue_frames, self.config.queue_bytes,
                        resnapshot=lambda topics: self.resnapshot(a, topics))
