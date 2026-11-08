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
from .menu import DeviceContext, Menu, RouteTable
from .nav import NavContext, PromptState, handle_input, normalize, path_to
from .outqueue import OutQueue
from .session import Session
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

    def menu_frame(self, session: Session, msg_id: str) -> dict[str, Any]:
        f: dict[str, Any] = {"v": 1, "type": "menu", "id": msg_id, "revision": self.menu_revision,
                             "nodes": session.menu_view.to_frame_nodes(self.menu.plugin_of)}
        if self.ctx.mode:
            f["device_mode"] = self.ctx.mode
        return f

    def _recompute(self, session: Session) -> bool:
        before = set(session.menu_view.nodes)
        session.menu_view = self.menu.view(self.ctx, session.principal.roles)
        return set(session.menu_view.nodes) != before

    def refresh_menus(self, force: bool = False) -> None:
        for s in list(self.sessions.values()):
            changed = self._recompute(s)
            if not s.connected:
                continue
            if changed or force:
                s.send(self.menu_frame(s, s.next_id()))
                s.menu_revision_sent = self.menu_revision
            if s.thin:
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

    def push_view(self, session: Session, msg_id: str | None = None) -> None:
        if not session.thin or not session.connected:
            return
        frame = render_view(session, msg_id or session.next_id(), self.jobs, self.hub.data)
        session.send(frame, coalesce=None if msg_id else "view")

    def _on_publish(self, topic: str, seq: int, data: Any) -> None:
        for s in list(self.sessions.values()):
            if not s.connected:
                continue
            if any(topic_matches(sub, topic) for sub in s.subscriptions):
                s.send_event(self.hub.event(s.next_id(), topic, snapshot=False), topic)
            if s.thin and s.cursor.screen == "home" and topic in HOME_TOPICS:
                self.push_view(s)

    def resnapshot(self, session: Session, topics: set[str]) -> list[dict[str, Any]]:
        return [self.hub.event(session.next_id(), t, snapshot=True) for t in sorted(topics) if t in self.hub.data]

    def _publish_jobs(self) -> None:
        self.hub.publish("jobs", self.jobs.summary())

    def job_finished(self, job: Job) -> None:
        # Mode switches, Bluetooth toggles and the like change the device
        # context; re-read it now instead of waiting for the slow tier.
        if job.node.mutates and self.poller is not None:
            self.poller.invalidate_context()

    def job_changed_for(self, session: Session, job: Job) -> None:
        if session.thin and session.cursor.job_id == job.id and session.cursor.screen in ("result", "prompt"):
            self.push_view(session)

    def prompt_opened(self, session: Session, p: Prompt) -> None:
        if not session.thin:
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
        if session.thin and session.cursor.prompt_id == p.id:
            session.cursor = replace(session.cursor, screen="result", prompt_id=None)
            self.push_view(session)

    # ------------------------------------------------------------------ sessions

    def sessions_on(self, socket_kind: str) -> int:
        return sum(1 for s in self.sessions.values() if s.connected and s.socket_kind == socket_kind)

    def hello(self, principal: Principal, frame: dict[str, Any], socket_kind: str,
              queue_factory, closer) -> tuple[Session, list[dict[str, Any]]]:
        """Handle a valid hello. Returns the session and the frames to send, in order."""
        common = sorted(set(frame["protocol"]) & set(protocol.SUPPORTED))
        if not common:
            raise ProtocolViolation(protocol.error(frame["id"], "VERSION", "no common protocol version",
                                                   supported=list(protocol.SUPPORTED)))
        version = common[-1]
        resume = frame.get("resume") or {}
        session = None
        resumed = False
        prior = self.sessions.get(resume.get("session_id", "")) if resume.get("epoch") == self.epoch else None
        if prior is not None and prior.principal.key == principal.key and prior.mode == frame["mode"] \
                and prior.socket_kind == socket_kind:
            if prior.connected:
                prior.close(1000, "replaced by a resumed connection")
                prior.detach()
            session = prior
            session.principal = principal  # fresh credentials (for example a re-checked JWT)
            seqs = resume.get("seq", {})
            resumed = all(self.hub.seq.get(t) == seqs.get(t) for t in session.subscriptions) and \
                all(self.hub.seq.get(t) == n for t, n in seqs.items())
        if session is None:
            session = Session(principal, frame["mode"], socket_kind, client=frame.get("client"),
                              capabilities=frame.get("capabilities"), validate=self.validator())
            self.sessions[session.id] = session
        session.attach(queue_factory(session), closer)
        self._recompute(session)
        if session.thin:
            session.cursor = normalize(session.cursor, session.menu_view)

        welcome = {"v": 1, "type": "welcome", "id": frame["id"], "session_id": session.id, "epoch": self.epoch,
                   "principal": principal.frame(), "roles": list(principal.roles), "version": version,
                   "mode": session.mode, "resumed": resumed,
                   "limits": {"queue_frames": self.config.queue_frames, "max_frame_bytes": self.config.max_frame_bytes}}
        out = [welcome]
        if not resumed or resume.get("menu_revision") != self.menu_revision:
            out.append(self.menu_frame(session, session.next_id()))
            session.menu_revision_sent = self.menu_revision
        if not resumed:
            for sub in sorted(session.subscriptions):
                for t in self.hub.topics_for(sub):
                    out.append(self.hub.event(session.next_id(), t, snapshot=True))
            for job in self.jobs.live_jobs_for(session):
                out.append(self.jobs.job_frame(job, session.next_id()))
            for p in self.jobs.prompts.values():
                if p.session_id == session.id:
                    out.append(dict(p.frame, id=session.next_id()))
        if session.thin:
            out.append(render_view(session, session.next_id(), self.jobs, self.hub.data))
        log.info("session %s %s (%s %s, %s) on %s", session.id, "resumed" if resumed else "started",
                 principal.kind, principal.name, session.mode, socket_kind)
        return session, out

    def disconnected(self, session: Session, queue: OutQueue) -> None:
        if session.queue is queue:  # a resumed connection may already own the session
            session.detach()

    def expire_sessions(self) -> None:
        now = time.time()
        for s in list(self.sessions.values()):
            if not s.connected and s.detached_at is not None and now - s.detached_at > self.config.resume_grace_sec:
                self.drop_session(s)

    def drop_session(self, s: Session) -> None:
        self.jobs.detach_all(s)
        for p in list(self.jobs.prompts.values()):
            if p.session_id == s.id:
                self.jobs.cancel_prompt(s, p.id)
        self.sessions.pop(s.id, None)

    def revoke_principal(self, key: str, reason: str) -> int:
        n = 0
        for s in list(self.sessions.values()):
            if s.principal.key == key:
                self._revoke(s, reason)
                n += 1
        return n

    def revoke_webui_session(self, webui_session: str) -> int:
        """WebUI logout: close every session carrying that WebUI session id (close 4401)."""
        n = 0
        for s in list(self.sessions.values()):
            if s.principal.kind == "webui" and s.principal.webui_session == webui_session:
                self._revoke(s, "WebUI session ended")
                n += 1
        return n

    def _revoke(self, s: Session, reason: str) -> None:
        if s.connected:
            s.send(protocol.error(s.next_id(), "AUTH_FAILED", reason))
            s.close(4401, reason)
        self.drop_session(s)

    # ------------------------------------------------------------------ frames from a session

    def _refuse(self, session: Session, msg_id: str, exc: InvokeRefused) -> None:
        extra = {"core_status": exc.core_status} if exc.core_status else {}
        session.send(protocol.error(msg_id, exc.code, exc.message, **extra))

    def handle_frame(self, session: Session, frame: dict[str, Any]) -> None:
        t, mid = frame["type"], frame["id"]
        try:
            if t == "subscribe":
                for sub in frame["topics"]:
                    if sub in session.subscriptions:
                        continue
                    session.subscriptions.add(sub)
                    for topic in self.hub.topics_for(sub):
                        session.send(self.hub.event(mid, topic, snapshot=True))
            elif t == "unsubscribe":
                session.subscriptions.difference_update(frame["topics"])
            elif t == "input":
                if not session.thin:
                    raise InvokeRefused("BAD_REQUEST", "input is for thin sessions; rich sessions send invoke")
                self._input(session, frame)
            elif t == "invoke":
                job = self.jobs.invoke(session, frame["node"], frame.get("params"), frame.get("confirmed", False), mid)
                if session.thin:
                    where = path_to(frame["node"], session.menu_view)
                    if where:
                        session.cursor = replace(session.cursor, screen="result", stack=where[0], index=where[1],
                                                 node=frame["node"], job_id=job.id, page=0)
                    self.push_view(session)
            elif t == "answer":
                self.jobs.answer(session, frame)
            elif t == "page":
                self.jobs.page(session, frame["job_id"], frame["cursor"], mid)
            elif t == "cancel":
                self.jobs.cancel(session, frame["job_id"], mid)
                if session.thin and session.cursor.job_id == frame["job_id"]:
                    session.cursor = replace(session.cursor, screen="menu", job_id=None, node=None)
                    self.push_view(session)
            elif t in ("hello", "auth"):
                raise InvokeRefused("BAD_REQUEST", f"{t} is only valid once, at the start")
        except InvokeRefused as exc:
            self._refuse(session, mid, exc)

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

    def _input(self, session: Session, frame: dict[str, Any]) -> None:
        mid = frame["id"]
        result = handle_input(session.cursor, frame["key"], self._nav_context(session))
        session.cursor = result.cursor
        for eff in result.effects:
            try:
                if eff.kind == "invoke":
                    job = self.jobs.invoke(session, eff.node, None, False, session.next_id())
                    session.cursor = replace(session.cursor, job_id=job.id)
                elif eff.kind == "answer":
                    p = self.jobs.prompts.get(eff.prompt_id)
                    if p is None:
                        continue
                    value = p.values[eff.value] if p.kind == "select" else eff.value
                    if p.kind == "confirm":
                        self.jobs.answer(session, {"prompt_id": p.id, "confirmed": bool(value)})
                    else:
                        self.jobs.answer(session, {"prompt_id": p.id, "value": value})
                elif eff.kind == "cancel_prompt":
                    self.jobs.cancel_prompt(session, eff.prompt_id)
                elif eff.kind == "detach" and eff.job_id:
                    self.jobs.cancel(session, eff.job_id, None)
            except InvokeRefused as exc:
                self._refuse(session, mid, exc)
                if eff.kind == "invoke":
                    session.cursor = replace(session.cursor, screen="menu", job_id=None)
        self.push_view(session, mid)

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
            if s.connected:
                s.close(1001, "service stopping")
        await self.jobs.shutdown()
        await self.core.aclose()

    def new_queue(self, session: Session) -> OutQueue:
        return OutQueue(self.config.queue_frames, self.config.queue_bytes,
                        resnapshot=lambda topics: self.resnapshot(session, topics))
