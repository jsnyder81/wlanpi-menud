"""Apps handlers: profiler status and the scanner file actions."""

from __future__ import annotations

from .. import blocks as B
from ..core import CoreUnavailable
from .base import HandlerContext, HandlerResult, handler


@handler("apps.profiler.status")
async def profiler_status(ctx: HandlerContext) -> HandlerResult:
    st = await ctx.core.get_profiler_status()
    if not st.get("running"):
        return HandlerResult([B.status("info", "Profiler stopped", "Profiler")], "Profiler")
    out = [B.status("ok", "Profiler running", "Profiler")]
    ssid, pw = st.get("ssid"), st.get("passphrase")
    if ssid:
        out.append(B.qr(B.wifi_qr_payload(ssid, pw or ""), caption=ssid, text_fallback=f"SSID: {ssid}"))
        out.append(B.kv([("SSID", ssid), ("Passphrase", pw or "")], secret_keys={"Passphrase"}))
    return HandlerResult(out, "Profiler")


def _not_in_core(what: str) -> None:
    # These nodes are hidden by requires_core until core's capture-session
    # API (Stream D) exists, so reaching here means core advertised a route
    # this build does not know how to drive yet.
    raise CoreUnavailable(f"{what} needs the wlanpi-core capture-session API, which this core does not provide")


@handler("apps.scanner.csv")
async def scanner_csv(ctx: HandlerContext) -> HandlerResult:
    _not_in_core("Scan to CSV")
    raise AssertionError  # pragma: no cover


@handler("apps.scanner.pcap_start")
async def scanner_pcap_start(ctx: HandlerContext) -> HandlerResult:
    _not_in_core("Scan to PCAP")
    raise AssertionError  # pragma: no cover


@handler("apps.scanner.pcap_stop")
async def scanner_pcap_stop(ctx: HandlerContext) -> HandlerResult:
    _not_in_core("Scan to PCAP")
    raise AssertionError  # pragma: no cover
