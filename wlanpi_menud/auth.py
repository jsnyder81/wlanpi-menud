"""Who is connecting.

* ``local.sock``: the kernel's ``SO_PEERCRED`` uid and gid, checked against an
  allowlist (root is the panel, the ``wlanpi`` user, members of the
  ``wlanpi-menud`` group, provider uids). No auth frame.
* ``proxy.sock``: peer credentials are ignored (everything arrives as
  nginx). Either nginx attached ``X-WebUI-User`` and ``X-WebUI-Session``
  after its ``auth_request`` check (it overwrites any client values), or the
  first frame is ``auth`` with a core JWT, checked through core's
  introspection endpoint. ``Origin``, when present, must match the device's
  own host.
"""

from __future__ import annotations

import grp
import logging
import os
import pwd
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .core import CoreError, CoreUnavailable

log = logging.getLogger(__name__)
audit = logging.getLogger("wlanpi_menud.audit")


@dataclass(frozen=True)
class PeerCred:
    pid: int
    uid: int
    gid: int


@dataclass(frozen=True)
class Principal:
    kind: str  # local | webui | jwt
    name: str
    roles: tuple[str, ...]
    uid: int | None = None
    did: str | None = None
    webui_session: str | None = None
    token: str | None = field(default=None, repr=False)  # jwt only, never logged
    token_exp: float | None = None

    @property
    def key(self) -> str:
        """Identity a prompt or session is bound to."""
        if self.kind == "local":
            return f"local:{self.uid}"
        if self.kind == "webui":
            return f"webui:{self.name}:{self.webui_session}"
        return f"jwt:{self.did}:{self.name}"

    def frame(self) -> dict[str, Any]:
        f: dict[str, Any] = {"kind": self.kind, "name": self.name[:128]}
        if self.uid is not None:
            f["uid"] = self.uid
        f["did"] = (self.did or "wlanpi-menud")[:128]
        return f


def peer_credentials(sock: socket.socket) -> PeerCred:
    raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    pid, uid, gid = struct.unpack("3i", raw)
    return PeerCred(pid, uid, gid)


class LocalPolicy:
    """uid/gid allowlist for local.sock."""

    def __init__(
        self,
        uids: dict[int, tuple[str, ...]],
        gids: dict[int, tuple[str, ...]],
        provider_uids: tuple[int, ...] = (),
        user_names: dict[str, tuple[str, ...]] | None = None,
        group_names: dict[str, tuple[str, ...]] | None = None,
    ):
        self.uids = dict(uids)
        self.gids = dict(gids)
        for name, roles in (user_names or {}).items():
            try:
                self.uids.setdefault(pwd.getpwnam(name).pw_uid, roles)
            except KeyError:
                log.info("local.sock allowlist: no user %s on this system", name)
        for name, roles in (group_names or {}).items():
            try:
                self.gids.setdefault(grp.getgrnam(name).gr_gid, roles)
            except KeyError:
                log.info("local.sock allowlist: no group %s on this system", name)
        for uid in provider_uids:
            self.uids.setdefault(uid, ("provider",))
        self.declared_uids: dict[int, tuple[str, ...]] = {}

    def set_declared_providers(self, user_names: set[str] | list[str]) -> None:
        """A complication declaration (plugins.d/<name>/complications.json, root-owned)
        grants the provider role on local.sock to the unix user it names."""
        declared: dict[int, tuple[str, ...]] = {}
        for name in sorted(set(user_names)):
            try:
                declared[pwd.getpwnam(name).pw_uid] = ("provider",)
            except KeyError:
                log.info("complication declaration: no user %s on this system", name)
        self.declared_uids = declared

    def _groups(self, cred: PeerCred) -> set[int]:
        groups = {cred.gid}
        try:
            groups.update(os.getgrouplist(pwd.getpwuid(cred.uid).pw_name, cred.gid))
        except (KeyError, OSError):
            pass
        return groups

    def principal(self, cred: PeerCred) -> Principal | None:
        roles: list[str] = list(self.uids.get(cred.uid, ()))
        roles.extend(self.declared_uids.get(cred.uid, ()))
        for g in sorted(self._groups(cred)):
            roles.extend(self.gids.get(g, ()))
        if not roles:
            return None
        try:
            name = pwd.getpwuid(cred.uid).pw_name
        except KeyError:
            name = f"uid {cred.uid}"
        uniq = tuple(dict.fromkeys(roles))
        return Principal("local", name, uniq, uid=cred.uid)


def token_in_url(path: str) -> bool:
    return "token" in parse_qs(urlsplit(path).query)


def origin_ok(origin: str | None, host: str | None, allowed: tuple[str, ...]) -> bool:
    """Blocks cross-site WebSocket hijacking with the WebUI cookie."""
    if origin is None:
        return True  # not a browser (mobile app, CLI); those authenticate with a JWT
    if allowed:
        return origin.rstrip("/") in allowed
    try:
        o = urlsplit(origin)
    except ValueError:
        return False
    if not o.netloc or not host:
        return False
    return o.netloc.lower() == host.lower()


@dataclass
class Introspection:
    valid: bool
    did: str | None = None
    exp: float | None = None
    jti: str | None = None
    reason: str | None = None


class Introspector:
    """Checks remote JWTs with core, caching each answer for at most ``cache_sec`` or until exp.

    Core does not have an introspection endpoint yet (plan: core changes,
    "Token introspection"). Until it does, every JWT is refused, which is
    the safe outcome.
    """

    def __init__(self, core: Any, path: str = "/auth/introspect", cache_sec: float = 60.0, clock=time.time):
        self.core = core
        self.path = path
        self.cache_sec = cache_sec
        self.clock = clock
        self._cache: dict[str, tuple[Introspection, float]] = {}

    def cached_until(self, token: str) -> float | None:
        hit = self._cache.get(token)
        return hit[1] if hit else None

    async def check(self, token: str) -> Introspection:
        now = self.clock()
        hit = self._cache.get(token)
        if hit and now < hit[1]:
            return hit[0]
        try:
            body = await self.core.request("POST", self.path, body={"token": token})
            res = Introspection(
                valid=bool(body.get("valid")), did=body.get("did"), exp=body.get("exp"), jti=body.get("jti"),
                reason=None if body.get("valid") else "token not valid",
            )
        except CoreUnavailable as exc:
            return Introspection(False, reason=f"cannot check token: {exc.message}")  # not cached
        except CoreError as exc:
            reason = "core has no token introspection endpoint" if exc.status in (404, 405) else exc.message
            res = Introspection(False, reason=reason)
        if res.valid and res.exp is not None and now >= res.exp:
            res = Introspection(False, did=res.did, exp=res.exp, jti=res.jti, reason="token expired")
        until = now + self.cache_sec
        if res.exp is not None:
            until = min(until, res.exp)
        self._cache[token] = (res, until)
        if len(self._cache) > 1024:
            for k, (_, u) in list(self._cache.items()):
                if u <= now:
                    del self._cache[k]
        return res
