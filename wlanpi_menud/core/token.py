"""The service's own core token.

Core reserves ``did=wlanpi-menud``. The token comes from a root helper,
``/usr/libexec/wlanpi-menud/get-core-token`` (it runs ``getjwt wlanpi-menud``),
which sudoers lets only the ``wlanpi`` user run. The token lives in memory
only: it is never written to disk or logged. It is refreshed before ``exp``
and re-minted after a 401 or ``AUTH_CLOCK_NOT_SET`` from core.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import Awaitable, Callable, Sequence

log = logging.getLogger(__name__)

Runner = Callable[[Sequence[str]], Awaitable[str]]


class TokenError(Exception):
    pass


def jwt_claims(token: str) -> dict:
    """Decode a JWT payload without verifying it (core verifies; we only read exp)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception as exc:
        raise TokenError(f"not a JWT: {exc}") from None


def parse_helper_output(out: str) -> str:
    """Accept getjwt's JSON, its ``export WLANPI_TOKEN=`` form, or a bare token."""
    out = out.strip()
    token = None
    if out.startswith("{"):
        try:
            token = json.loads(out).get("access_token")
        except json.JSONDecodeError as exc:
            raise TokenError(f"helper printed invalid JSON: {exc}") from None
    elif out.startswith("export WLANPI_TOKEN="):
        token = out.split("=", 1)[1].strip().strip("'\"")
    else:
        token = out.splitlines()[0].strip() if out else None
    if not token or token.count(".") != 2:
        raise TokenError("helper did not print a JWT")
    return token


async def run_helper(command: Sequence[str], timeout: float = 15.0) -> str:
    proc = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, stdin=asyncio.subprocess.DEVNULL
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        raise TokenError(f"{command[-1]} timed out") from None
    if proc.returncode != 0:
        raise TokenError(f"{command[-1]} exited {proc.returncode}: {err.decode(errors='replace').strip()[:200]}")
    return out.decode()


class TokenSource:
    def __init__(
        self,
        command: Sequence[str],
        refresh_margin_sec: float = 300.0,
        runner: Runner | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.command = tuple(command)
        self.margin = refresh_margin_sec
        self._runner = runner or run_helper
        self._clock = clock
        self._token: str | None = None
        self._exp: float | None = None
        self._lock = asyncio.Lock()
        self.mints = 0

    @property
    def expires_at(self) -> float | None:
        return self._exp

    def _stale(self) -> bool:
        return self._token is None or (self._exp is not None and self._clock() >= self._exp - self.margin)

    async def _mint(self) -> str:
        out = await self._runner(self.command)
        token = parse_helper_output(out)
        exp = jwt_claims(token).get("exp")
        self._token = token
        self._exp = float(exp) if isinstance(exp, (int, float)) else None
        self.mints += 1
        log.info("core token minted (exp %s)", self._exp)
        return token

    async def get(self) -> str:
        if not self._stale():
            return self._token  # type: ignore[return-value]
        async with self._lock:
            if self._stale():
                await self._mint()
            return self._token  # type: ignore[return-value]

    async def remint(self, rejected: str | None = None) -> str:
        """Fetch a new token. Concurrent callers that saw the same rejected token share one mint."""
        async with self._lock:
            if rejected is not None and self._token is not None and self._token != rejected:
                return self._token
            return await self._mint()

    def forget(self) -> None:
        self._token = None
        self._exp = None

    async def refresh_loop(self) -> None:
        """Keep the token fresh so requests rarely wait for a mint."""
        while True:
            try:
                if self._exp is None:
                    await asyncio.sleep(60)
                    await self.get()
                    continue
                delay = max(1.0, self._exp - self.margin - self._clock())
                await asyncio.sleep(delay)
                async with self._lock:
                    if self._stale():
                        await self._mint()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("core token refresh failed: %s", exc)
                await asyncio.sleep(30)
