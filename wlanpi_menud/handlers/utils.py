"""Utils handlers."""

from __future__ import annotations

from .. import blocks as B
from .base import HandlerContext, HandlerResult, handler


@handler("utils.ssid_passphrase")
async def ssid_passphrase(ctx: HandlerContext) -> HandlerResult:
    """Hotspot SSID and passphrase as a join QR. A secret: never shared or cached.

    Core answers 409 outside hotspot mode; the job layer passes that through
    as CONFLICT with core's message.
    """
    creds = await ctx.core.get_system_hotspot_ssid_passphrase()
    ssid, pw = creds.get("ssid", ""), creds.get("passphrase", "")
    return HandlerResult(
        [
            B.qr(B.wifi_qr_payload(ssid, pw), caption=ssid, text_fallback=f"SSID: {ssid}"),
            B.kv([("SSID", ssid), ("Passphrase", pw)], secret_keys={"Passphrase"}),
        ],
        "SSID/Passphrase",
    )
