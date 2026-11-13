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

from . import complications as C
from . import protocol
from .auth import Introspector, LocalPolicy, Principal
from .config import Config
from .core import CoreClient, TokenSource
from .core.generated import ROUTES
from .handlers import handler_exists
from .jobs import InvokeRefused, Job, JobManager, Prompt
from .loader import LoadResult, load_menu
from .menu import DeviceContext, Menu, Node, RouteTable, role_rank
from .nav import NavContext, PromptState, handle_input, normalize, path_to
from .outqueue import OutQueue
from .session import DEFAULT, NEW, Attachment, Session
from .topics import TopicHub, topic_matches
from .views import render_view

log = logging.getLogger(__name__)

HOME_TOPICS = ("device.summary", "alerts", "complications.live", "complications.config")


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
        self.poller: Any = None  # the Poller sets itself here
        self._reply_skip: Attachment | None = None
        # Complications: provider and built-in records, and display config per surface profile.
        self.complication_store = C.ComplicationStore(
            self.hub.publish, fanout_sec=config.complications_fanout_sec,
            quota_per_uid=config.complication_quota_per_uid, quota_total=config.complication_quota_total)
        self.display = C.DisplayConfig(config.complications_defaults_file, config.complications_site_file,
                                       config.complications_user_file)
        self.hub.publish("complications.config", self.display.data())
        self.declaration_errors: list[Any] = []
        self._load_declarations()

    @property
    def complications(self) -> dict[str, dict[str, Any]]:
        """Provider records by app_id (the in-process view of the store)."""
        return self.complication_store.providers

    def _load_declarations(self) -> None:
        decls, errors = C.load_declarations(self.config.plugins_dir)
        self.declaration_errors = errors
        self.local_policy.set_declared_providers({d.user for d in decls.values()})
        self.complication_store.set_declarations(decls)
        if decls or errors:
            log.info("complications: %d declarations loaded, %d errors", len(decls), len(errors))

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
        self._load_declarations()
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
            frame = render_view(session, a, msg_id if reply else a.next_id(), self.jobs, self.hub.data,
                                self.home_tiles)
            a.send(frame, coalesce=None if reply else "view")

    def _on_publish(self, topic: str, seq: int, data: Any) -> None:
        for s in list(self.sessions.values()):
            for a in list(s.attachments):
                if a is self._reply_skip:
                    continue  # it gets this change as the reply to its own request
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
        if frame.get("ephemeral") and kind == DEFAULT:
            raise ProtocolViolation(protocol.error(
                frame["id"], "BAD_REQUEST", "ephemeral is for independent sessions (session: new)", close_code=4400))
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
                session.ephemeral = bool(frame.get("ephemeral"))
                session.profile = C.profile_for(frame.get("surface"), (frame.get("client") or {}).get("name"))
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
                   "limits": {"queue_frames": self.config.queue_frames, "max_frame_bytes": self.config.max_frame_bytes},
                   "features": list(protocol.FEATURES)}
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
            out.append(render_view(session, a, a.next_id(), self.jobs, self.hub.data, self.home_tiles))
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
        self.complication_store.drop_connection(a.id)  # hold: connection
        if session.ephemeral and not session.connected:
            self.drop_session(session)  # no resume grace
            return
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
            elif t == "complication.publish":
                self._complication_publish(a, frame)
            elif t == "complication.remove":
                self._complication_remove(a, frame)
            elif t == "complications.configure":
                self._complications_configure(a, frame)
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

    # ------------------------------------------------------------------ complications

    def _provider_check(self, a: Attachment, app_id: str) -> tuple[C.Declaration, dict[str, Any]] | None:
        """Socket, role and namespace rules for complication.publish and .remove (spec 2.4).
        Returns the declaration for a plugin.* id."""
        if a.socket_kind != "local":
            raise InvokeRefused("FORBIDDEN", "complications are published on local.sock only")
        roles = a.principal.roles
        if "provider" not in roles and "admin" not in roles:
            raise InvokeRefused("FORBIDDEN", "publishing complications needs role provider or admin")
        ns, _, rest = app_id.partition(".")
        root = a.principal.uid == 0
        if ns == "wlanpi":
            raise InvokeRefused("FORBIDDEN", "wlanpi.* complications are reserved for the service")
        if ns == "plugin":
            plugin = rest.split(".", 1)[0]
            found = self.complication_store.declared(app_id)
            if found is None:
                owner = self.complication_store.plugin_publisher(plugin)
                if owner is not None and not root and owner != a.principal.name:
                    raise InvokeRefused("FORBIDDEN", f"plugin.{plugin}.* is published by {owner}")
                raise InvokeRefused("BAD_REQUEST", f"{app_id} is not declared in plugins.d/{plugin}/complications.json")
            if not root and found[0].user != a.principal.name:
                raise InvokeRefused("FORBIDDEN", f"plugin.{plugin}.* is published by {found[0].user}")
            return found
        # local.<user>.*
        user = rest.split(".", 1)[0]
        if not root and user != C.map_username(a.principal.name):
            raise InvokeRefused("FORBIDDEN", f"local.{user}.* belongs to another user; "
                                             f"use local.{C.map_username(a.principal.name)}.*")
        return None

    def _complication_publish(self, a: Attachment, frame: dict[str, Any]) -> None:
        app_id = frame["app_id"]
        found = self._provider_check(a, app_id)
        decl = found[1] if found else {}
        label = frame.get("label") or decl.get("label")
        if not label:
            raise InvokeRefused("BAD_REQUEST", "label is required for local.* complications")
        fields: dict[str, Any] = {
            "label": C.sanitize(label, C.LABEL_MAX) or decl.get("label") or "?",
            "value": C.sanitize(frame["value"], C.VALUE_MAX) or C.UNKNOWN_VALUE,
            "status": C.normalize_status(frame.get("status", "unknown")),
        }
        for k, limit in (("short", C.SHORT_MAX), ("detail", C.DETAIL_MAX)):
            if frame.get(k):
                v = C.sanitize(frame[k], limit)
                if v:
                    fields[k] = v
        icon = frame.get("icon") or decl.get("icon")
        if icon:
            fields["icon"] = icon
        if decl.get("node"):
            fields["node"] = decl["node"]
        ttl = frame.get("ttl_sec", C.DEFAULT_TTL)
        try:
            self.complication_store.put(app_id, fields, ttl=ttl, uid=a.principal.uid,
                                        conn=a.id if frame.get("hold") == "connection" else None)
        except C.Refused as exc:
            raise InvokeRefused(exc.code, exc.message) from None
        a.send({"v": 1, "type": "complication.ack", "id": frame["id"], "app_id": app_id, "state": "live",
                "ttl_sec": ttl})

    def _complication_remove(self, a: Attachment, frame: dict[str, Any]) -> None:
        app_id = frame["app_id"]
        self._provider_check(a, app_id)
        if not self.complication_store.remove(app_id):
            raise InvokeRefused("NOT_FOUND", f"{app_id} is not live")
        a.send({"v": 1, "type": "complication.ack", "id": frame["id"], "app_id": app_id, "state": "removed"})

    def _complications_configure(self, a: Attachment, frame: dict[str, Any]) -> None:
        if role_rank(a.principal.roles) < 1:
            raise InvokeRefused("FORBIDDEN", "changing complication layouts needs role operator or admin")
        surface = frame["surface"]
        try:
            if frame.get("reset"):
                self.display.reset(surface)
            else:
                self.display.set(surface, frame["show"])
        except C.ConfigWriteError as exc:
            raise InvokeRefused("INTERNAL", f"cannot save the layout: {exc}") from None
        log.info("complications: %s %s profile %s", a.principal.name, "reset" if frame.get("reset") else "set",
                 surface)
        self._reply_skip = a
        try:
            self.hub.publish("complications.config", self.display.data())
        finally:
            self._reply_skip = None
        a.send(self.hub.event(frame["id"], "complications.config", snapshot=True))

    def home_tiles(self, session: Session, a: Attachment) -> list[dict[str, Any]]:
        """The live records a thin home view shows: the session's profile, resolved, cut to capacity."""
        profile = "panel" if session.default else session.profile
        live = self.complication_store.live()
        ids = C.resolve(self.display.show_for(profile), live)
        alerts = self.hub.data.get("alerts") or []
        cap = C.home_capacity(profile, (a.capabilities or {}).get("screen"), len(alerts))
        return [live[i] for i in ids[:cap]]

    def publish_complication(self, app_id: str, label: str, value: str, status: str = "unknown",
                             ttl_seconds: float = 30) -> None:
        """In-process publish (no namespace checks). An id outside the new
        namespaces reaches only the legacy ``complications`` topic."""
        fields = {"label": C.sanitize(label, C.LABEL_MAX), "value": C.sanitize(value, C.VALUE_MAX) or C.UNKNOWN_VALUE,
                  "status": C.normalize_status(status) if C.ID_RE.match(app_id) else status}
        self.complication_store.put(app_id, fields, ttl=ttl_seconds)

    def publish_complications(self) -> None:
        """Housekeeping: expire records and publish whatever changed."""
        if self.complication_store.expire():
            self.complication_store.changed()

    # ------------------------------------------------------------------ lifecycle

    async def aclose(self) -> None:
        self.complication_store.close()
        for s in list(self.sessions.values()):
            s.close(1001, "service stopping")
        await self.jobs.shutdown()
        await self.core.aclose()

    def new_queue(self, a: Attachment) -> OutQueue:
        return OutQueue(self.config.queue_frames, self.config.queue_bytes,
                        resnapshot=lambda topics: self.resnapshot(a, topics))
