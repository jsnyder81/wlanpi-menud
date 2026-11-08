"""HTTPS client for wlanpi-core.

Core is reached through nginx at ``https://127.0.0.1:31415`` with the
device's self-signed certificate pinned: the certificate file is the only
trust anchor, and hostname checking is off because the certificate names the
device, not 127.0.0.1. Requests carry the service's reserved token, or a
remote user's own JWT when a job runs for a JWT session.
"""

from __future__ import annotations

import logging
import ssl
from pathlib import Path
from typing import Any

import httpx

from .generated import GeneratedCoreAPI
from .token import TokenError, TokenSource

log = logging.getLogger(__name__)

AUTH_CLOCK_NOT_SET = "AUTH_CLOCK_NOT_SET"


class CoreError(Exception):
    """Core answered with an error status."""

    def __init__(self, status: int, message: str, error: str | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.error = error
        self.body = body


class CoreUnavailable(CoreError):
    """Core unreachable, 5xx, or the service could not get a working token."""

    def __init__(self, message: str, status: int = 0, error: str | None = None):
        super().__init__(status, message, error)


class CoreAuthRejected(CoreError):
    """A caller-supplied (remote user) token was refused by core."""


def pinned_ssl_context(cert: Path) -> ssl.SSLContext:
    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(cert))
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def _message(body: Any, fallback: str) -> tuple[str, str | None]:
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), str) else None
        for key in ("message", "detail", "error"):
            v = body.get(key)
            if isinstance(v, str) and v:
                return v, err
            if isinstance(v, list) and v:  # FastAPI validation errors
                first = v[0]
                return (first.get("msg") if isinstance(first, dict) else str(first)) or fallback, err
    return fallback, None


class CoreClient(GeneratedCoreAPI):
    def __init__(
        self,
        base_url: str,
        tokens: TokenSource,
        *,
        prefix: str = "/api/v1",
        cert: Path | None = None,
        timeout: float = 10.0,
        keepalive: float = 55.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.prefix = prefix
        self.tokens = tokens
        self.timeout = timeout
        verify: ssl.SSLContext | bool = True
        self._cert_error: str | None = None
        if cert is not None:
            try:
                verify = pinned_ssl_context(cert)
            except (OSError, ssl.SSLError) as exc:
                # Without the pinned cert every request fails; never fall back to other trust.
                log.error("cannot load pinned core certificate %s: %s", cert, exc)
                self._cert_error = str(exc)
        # One small pool kept warm across poll ticks: TLS handshakes are the
        # most expensive part of a tick on a Pi.
        limits = httpx.Limits(max_connections=8, max_keepalive_connections=8, keepalive_expiry=keepalive)
        self._http = httpx.AsyncClient(base_url=self.base_url, verify=verify, timeout=timeout, limits=limits,
                                       transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    def _url(self, path: str) -> str:
        return f"{self.prefix}{path}"

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any = None,
        timeout: float | None = None,
        token: str | None = None,
    ) -> Any:
        if self._cert_error:
            raise CoreUnavailable(f"pinned core certificate unusable: {self._cert_error}")
        params = {k: _qv(v) for k, v in (query or {}).items() if v is not None}
        own = token is None
        for attempt in (1, 2):
            try:
                bearer = token if not own else await self.tokens.get()
            except TokenError as exc:
                raise CoreUnavailable(f"no core token: {exc}") from None
            try:
                resp = await self._http.request(
                    method,
                    self._url(path),
                    params=params or None,
                    json=body if body is not None else None,
                    headers={"Authorization": f"Bearer {bearer}"},
                    timeout=timeout or self.timeout,
                )
            except httpx.TimeoutException:
                raise
            except httpx.HTTPError as exc:
                raise CoreUnavailable(f"wlanpi-core unreachable: {exc.__class__.__name__}") from None
            try:
                data = resp.json() if resp.content else None
            except ValueError:
                data = resp.text
            clock = resp.status_code == 503 and isinstance(data, dict) and data.get("error") == AUTH_CLOCK_NOT_SET
            if resp.status_code == 401 or clock:
                if not own:
                    raise CoreAuthRejected(resp.status_code, "core refused the session token",
                                           AUTH_CLOCK_NOT_SET if clock else None, data)
                if attempt == 1:
                    log.info("core refused the service token (%s); re-minting",
                             AUTH_CLOCK_NOT_SET if clock else 401)
                    try:
                        await self.tokens.remint(rejected=bearer)
                    except TokenError as exc:
                        raise CoreUnavailable(f"token re-mint failed: {exc}",
                                              resp.status_code, AUTH_CLOCK_NOT_SET if clock else None) from None
                    continue
                msg, _ = _message(data, "core refused the service token")
                raise CoreUnavailable(msg, resp.status_code, AUTH_CLOCK_NOT_SET if clock else None)
            if resp.status_code >= 500:
                msg, err = _message(data, f"wlanpi-core returned {resp.status_code}")
                raise CoreUnavailable(msg, resp.status_code, err)
            if resp.status_code >= 400:
                msg, err = _message(data, f"wlanpi-core returned {resp.status_code}")
                raise CoreError(resp.status_code, msg, err, data)
            return data
        raise CoreUnavailable("unreachable")  # pragma: no cover

    async def openapi(self) -> dict[str, Any]:
        return await self.request("GET", "/openapi.json")


def _qv(v: Any) -> Any:
    if isinstance(v, bool):
        return "true" if v else "false"
    return v
