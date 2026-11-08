"""System handlers: help, the two-step timezone picker, display rotation."""

from __future__ import annotations

from .. import blocks as B
from .base import HandlerContext, HandlerResult, PromptCancelled, handler

HELP_URL = "https://www.wlanpi.com/docs"


@handler("system.help")
async def help_(ctx: HandlerContext) -> HandlerResult:
    return HandlerResult(
        [
            B.text("Docs, guides and support:"),
            B.qr(HELP_URL, caption="wlanpi.com/docs", text_fallback=HELP_URL),
        ],
        "Help",
    )


@handler("system.timezone.pick")
async def timezone_pick(ctx: HandlerContext) -> HandlerResult:
    """Region, then zone, from core's list; then set it. Replaces the PoC's
    hundreds of generated timezone leaf nodes with two select prompts."""
    zones = (await ctx.core.get_system_timezone_list()).get("timezones", [])
    regions: dict[str, list[str]] = {}
    for z in zones:
        region = z.split("/", 1)[0] if "/" in z else "Other"
        regions.setdefault(region, []).append(z)
    if not regions:
        return HandlerResult([B.status("error", "Core returned no timezones")], "Timezone")
    names = sorted(regions)
    region = await ctx.ask("select", "Region", options=[{"value": r, "label": r} for r in names])
    if region not in regions:
        raise PromptCancelled("unknown region")
    zone = await ctx.ask(
        "select", region[:64],
        options=[{"value": z, "label": z.split("/", 1)[-1].replace("_", " ")[:64]} for z in sorted(regions[region])],
    )
    if zone not in regions[region]:
        raise PromptCancelled("unknown zone")
    await ctx.core.post_system_timezone_set(body={"timezone": zone})
    return HandlerResult([B.status("ok", f"Timezone set to {zone}", "Timezone")], "Timezone")


@handler("system.rotate_display")
async def rotate_display(ctx: HandlerContext) -> HandlerResult:
    # Panel-local (parity list row 63, drop-candidate): the panel surface
    # rotates itself on key2. The node stays behind the display capability.
    return HandlerResult([B.status("info", "Rotation is handled by the panel (key2)", "Display")], "Display")
