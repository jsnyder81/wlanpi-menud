"""WebSocket server on two unix sockets. There is no TCP listener.

``local.sock``: ``SO_PEERCRED`` decides who the caller is; no auth frame.
``proxy.sock`` (mode 0660, nginx's group): peer credentials are ignored;
nginx's ``X-WebUI-User``/``X-WebUI-Session`` headers or a first-frame core
JWT are required, ``?token=`` in the URL is refused and ``Origin`` must
match the device's host.
"""

from __future__ import annotations

import asyncio
import contextlib
import grp
import json
import logging
import os
from pathlib import Path
from typing import Any

from websockets.asyncio.server import Server as WsServer
from websockets.asyncio.server import ServerConnection, unix_serve
from websockets.exceptions import ConnectionClosed

from . import protocol
from .auth import Principal, origin_ok, peer_credentials, token_in_url
from .outqueue import OutQueue, encode
from .service import ProtocolViolation, Service
from .session import Session

log = logging.getLogger(__name__)
audit = logging.getLogger("wlanpi_menud.audit")


class _Closing(Exception):
    pass


class Connection:
    def __init__(self, ws: ServerConnection):
        self.ws = ws
        self.close_req: tuple[int, str] | None = None
        self.queue: OutQueue | None = None

    async def send_now(self, frame: dict[str, Any]) -> None:
        await self.ws.send(encode(frame))

    async def fail(self, frame: dict[str, Any]) -> None:
        """Send a fatal error frame and close with its close code (pre-handshake)."""
        with contextlib.suppress(ConnectionClosed):
            await self.send_now(frame)
            await self.ws.close(frame["close_code"], frame["message"][:120])
        raise _Closing

    def request_close(self, code: int, reason: str) -> None:
        self.close_req = (code, reason[:120])
        if self.queue is not None:
            self.queue.wake()
        else:
            asyncio.get_running_loop().create_task(self.ws.close(code, reason[:120]))

    async def writer(self) -> None:
        q = self.queue
        assert q is not None
        try:
            while True:
                data = q.get_nowait()
                if data is None:
                    if self.close_req:
                        await self.ws.close(*self.close_req)
                        return
                    await q.wait()
                    continue
                await self.ws.send(data)
        except ConnectionClosed:
            pass


class Server:
    def __init__(self, service: Service):
        self.service = service
        self.config = service.config
        self._servers: list[WsServer] = []
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        self._servers.append(await self._listen(self.config.local_sock, "local", self.config.local_sock_mode,
                                                self.config.local_sock_group))
        self._servers.append(await self._listen(self.config.proxy_sock, "proxy", self.config.proxy_sock_mode,
                                                self.config.proxy_sock_group))

    async def _listen(self, path: Path, kind: str, mode: int, group: str | None) -> WsServer:
        path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        server = await unix_serve(
            lambda ws: self._handle(ws, kind), str(path),
            max_size=self.config.max_frame_bytes, ping_interval=20, ping_timeout=20,
            server_header="wlanpi-menud",
        )
        os.chmod(path, mode)
        if group:
            # proxy.sock sits in a setgid www-data directory that root creates
            # (tmpfiles.d), so the kernel already gave it the right group and
            # the service needs no membership in www-data. chown only if not.
            try:
                gid = grp.getgrnam(group).gr_gid
                if os.stat(path).st_gid != gid:
                    os.chown(path, -1, gid)
            except (KeyError, PermissionError) as exc:
                log.warning("%s: cannot set group %s (%s)", path, group, exc)
        log.info("listening on %s (%s, mode %o)", path, kind, mode)
        return server

    async def close(self) -> None:
        for s in self._servers:
            s.close()
        for s in self._servers:
            await s.wait_closed()
        for p in (self.config.local_sock, self.config.proxy_sock):
            with contextlib.suppress(FileNotFoundError):
                p.unlink()

    # ------------------------------------------------------------------ connection

    async def _handle(self, ws: ServerConnection, kind: str) -> None:
        conn = Connection(ws)
        session: Session | None = None
        writer: asyncio.Task | None = None
        recheck: asyncio.Task | None = None
        try:
            principal = await (self._local_principal(conn) if kind == "local" else self._proxy_principal(conn))
            if self.service.sessions_on(kind) >= self.config.sessions_per_socket:
                await conn.fail(protocol.error("s:0", "INTERNAL", "session limit reached; retry later", close_code=1013))
            hello = await self._first_frame(conn, expect="hello")
            try:
                session, frames = self.service.hello(
                    principal, hello, kind, self.service.new_queue, conn.request_close)
            except ProtocolViolation as exc:
                await conn.fail(exc.frame)
            conn.queue = session.queue
            for f in frames:
                session.send(f)
            writer = asyncio.create_task(conn.writer(), name=f"writer {session.id}")
            if principal.kind == "jwt":
                recheck = asyncio.create_task(self._recheck_jwt(session), name=f"jwt {session.id}")
            async for raw in ws:
                if conn.close_req:
                    break
                self._dispatch(session, raw)
        except (_Closing, ConnectionClosed):
            pass
        except Exception:
            log.exception("connection handler failed")
            with contextlib.suppress(Exception):
                await ws.close(1011, "internal error")
        finally:
            if recheck:
                recheck.cancel()
            if session is not None and conn.queue is not None:
                self.service.disconnected(session, conn.queue)
            if writer:
                if conn.close_req and not writer.done():
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(writer, 2)
                writer.cancel()

    def _dispatch(self, session: Session, raw: str | bytes) -> None:
        try:
            frame = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            session.send(protocol.error("s:bad", "BAD_REQUEST", "frame is not JSON"))
            return
        errs = protocol.frame_errors(frame, protocol.C2S)
        if errs:
            mid = protocol.reply_id(frame, "s:bad")
            session.send(protocol.error(mid, "BAD_REQUEST", "; ".join(errs)[:512]))
            return
        try:
            self.service.handle_frame(session, frame)
        except Exception:
            log.exception("frame handler failed for %s", frame.get("type"))
            session.send(protocol.error(frame["id"], "INTERNAL", "internal error"))

    async def _first_frame(self, conn: Connection, expect: str) -> dict[str, Any]:
        try:
            raw = await asyncio.wait_for(conn.ws.recv(), self.config.auth_timeout_sec)
        except TimeoutError:
            code = "AUTH_FAILED" if expect == "auth" else "VERSION"
            await conn.fail(protocol.error("s:0", code, f"no {expect} frame in time"))
        try:
            frame = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            await conn.fail(protocol.error("s:0", "BAD_REQUEST", "frame is not JSON", close_code=4400))
        if not isinstance(frame, dict) or frame.get("v") != 1 or not isinstance(frame.get("id"), str):
            await conn.fail(protocol.error("s:0", "BAD_REQUEST", "not a v1 frame", close_code=4400))
        mid = protocol.reply_id(frame)
        if frame.get("type") != expect:
            if expect == "auth":
                await conn.fail(protocol.error(mid, "AUTH_FAILED", "first frame on proxy.sock must be auth"))
            await conn.fail(protocol.error(mid, "VERSION", f"expected {expect} before {frame.get('type')}",
                                           supported=list(protocol.SUPPORTED)))
        errs = protocol.frame_errors(frame, (expect,))
        if errs:
            if expect == "auth":
                await conn.fail(protocol.error(mid, "AUTH_FAILED", "malformed auth frame"))
            await conn.fail(protocol.error(mid, "BAD_REQUEST", "; ".join(errs)[:512], close_code=4400))
        return frame

    async def _local_principal(self, conn: Connection) -> Principal:
        sock = conn.ws.transport.get_extra_info("socket")
        try:
            cred = peer_credentials(sock)
        except OSError as exc:
            await conn.fail(protocol.error("s:0", "AUTH_FAILED", f"no peer credentials: {exc}"))
        principal = self.service.local_policy.principal(cred)
        if principal is None:
            audit.warning("local.sock refused uid %d gid %d pid %d", cred.uid, cred.gid, cred.pid)
            await conn.fail(protocol.error("s:0", "AUTH_FAILED", f"uid {cred.uid} is not allowed on local.sock"))
        return principal

    async def _proxy_principal(self, conn: Connection) -> Principal:
        req = conn.ws.request
        headers = req.headers
        if token_in_url(req.path):
            await conn.fail(protocol.error("s:0", "AUTH_FAILED", "tokens in the URL are refused"))
        if not origin_ok(headers.get("Origin"), headers.get("Host"), self.config.allowed_origins):
            audit.warning("proxy.sock refused Origin %s for Host %s", headers.get("Origin"), headers.get("Host"))
            await conn.fail(protocol.error("s:0", "FORBIDDEN", "Origin does not match this device", close_code=1008))
        user, wsess = headers.get("X-WebUI-User"), headers.get("X-WebUI-Session")
        if user or wsess:
            if not (user and wsess):
                await conn.fail(protocol.error("s:0", "AUTH_FAILED", "incomplete WebUI session headers"))
            audit.info("webui user %s connected (session %s…)", user, wsess[:6])
            return Principal("webui", user[:128], tuple(self.config.webui_roles), webui_session=wsess)
        frame = await self._first_frame(conn, expect="auth")
        res = await self.service.introspector.check(frame["token"])
        if not res.valid:
            audit.warning("proxy.sock JWT refused: %s", res.reason)
            await conn.fail(protocol.error(frame["id"], "AUTH_FAILED", res.reason or "invalid token"))
        audit.info("jwt client did=%s jti=%s connected", res.did, res.jti)
        return Principal("jwt", res.did or "jwt", tuple(self.config.jwt_roles), did=res.did,
                         token=frame["token"], token_exp=res.exp)

    async def _recheck_jwt(self, session: Session) -> None:
        """Re-check a remote JWT once its cached answer runs out (at most 60 s, or at exp)."""
        intro = self.service.introspector
        token = session.principal.token or ""
        try:
            while True:
                until = intro.cached_until(token) or (intro.clock() + intro.cache_sec)
                await asyncio.sleep(max(0.05, until - intro.clock()))
                res = await intro.check(token)
                if not res.valid:
                    audit.warning("session %s JWT no longer valid: %s", session.id, res.reason)
                    self.service._revoke(session, res.reason or "token revoked")
                    return
        except asyncio.CancelledError:
            pass
