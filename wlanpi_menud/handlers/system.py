"""System handlers: help, the two-step timezone picker, display rotation, home tiles customize."""

from __future__ import annotations

from typing import Any

from .. import blocks as B
from ..complications import SHOW_MAX, home_capacity
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


@handler("system.home_tiles")
async def home_tiles(ctx: HandlerContext) -> HandlerResult:
    """Customize home screen tiles on thin surfaces (panel, TUI) per spec §4."""
    session = ctx.service.sessions.get(ctx.session_id) if ctx.service and ctx.session_id else None
    if session is None or ctx.service is None:
        raise PromptCancelled("no session")

    if session.default:
        profiles = [("panel", "Rows"), ("panel.strip", "Status strip")]
    else:
        prof = session.profile
        label = "Rows" if prof == "panel" else prof.replace(".", " ").title()
        profiles = [(prof, label)]

    while True:
        options = []
        for p_id, p_label in profiles:
            show_list = ctx.service.display.show_for(p_id)
            options.append({
                "value": f"profile:{p_id}",
                "label": p_label,
                "detail": f"{len(show_list)} >",
            })
        options.append({
            "value": "reset",
            "label": "Reset to default",
        })

        try:
            choice = await ctx.ask("select", "Home tiles", options=options)
        except PromptCancelled:
            break

        if choice == "reset":
            for p_id, _ in profiles:
                ctx.service.display.reset(p_id)
            ctx.service.hub.publish("complications.config", ctx.service.display.data())
            continue

        if choice.startswith("profile:"):
            target_prof = choice.split(":", 1)[1]
            target_title = next((l for p, l in profiles if p == target_prof), "Rows")
            await _edit_profile(ctx, session, target_prof, target_title)

    return HandlerResult([B.status("ok", "Home tiles updated", "Home tiles")], "Home tiles")


async def _edit_profile(ctx: HandlerContext, session: Any, profile: str, title: str) -> None:
    current_tile: str | None = None
    while True:
        show = list(ctx.service.display.show_for(profile))
        live = ctx.service.complication_store.live()
        catalog_items = ctx.service.complication_store.catalog_data()
        catalog_by_id = {c["app_id"]: c for c in catalog_items}

        alerts = ctx.service.hub.data.get("alerts") or []
        screen = None
        for a in session.attachments:
            if a.capabilities and a.capabilities.get("screen"):
                screen = a.capabilities["screen"]
                break
        cap = home_capacity(profile, screen, len(alerts))

        options: list[dict[str, Any]] = []
        # 1. Shown tiles
        for idx, item in enumerate(show, start=1):
            if idx == cap + 1:
                options.append({"value": "__fits_above__", "label": "- - fits above - -"})

            if item == "plugin.*":
                name = "All plugin tiles"
            elif item == "local.*":
                name = "All local tiles"
            elif item in catalog_by_id:
                name = catalog_by_id[item]["label"]
            else:
                name = item.split(".")[-1].replace("_", " ").title()

            detail = None
            if not item.endswith(".*") and item not in live:
                detail = "idle"

            options.append({
                "value": f"tile:{item}",
                "label": f"{idx} {name}",
                "detail": detail,
            })

        # 2. Hidden entries
        # Family patterns if not in show
        if "plugin.*" not in show:
            options.append({"value": "tile:plugin.*", "label": "  All plugin tiles", "detail": "hidden"})
        if "local.*" not in show:
            options.append({"value": "tile:local.*", "label": "  All local tiles", "detail": "hidden"})

        # Catalog items not in show
        for c in catalog_items:
            cid = c["app_id"]
            if cid not in show:
                options.append({"value": f"tile:{cid}", "label": f"  {c['label']}", "detail": "hidden"})

        default_val = f"tile:{current_tile}" if current_tile else None
        try:
            choice = await ctx.ask("select", title, options=options, default=default_val)
        except PromptCancelled:
            return

        if choice == "__fits_above__":
            continue

        if not choice.startswith("tile:"):
            continue

        tile_id = choice.split(":", 1)[1]
        current_tile = tile_id

        if tile_id == "plugin.*":
            tile_title = "All plugin tiles"
        elif tile_id == "local.*":
            tile_title = "All local tiles"
        elif tile_id in catalog_by_id:
            tile_title = catalog_by_id[tile_id]["label"]
        else:
            tile_title = tile_id.split(".")[-1].replace("_", " ").title()

        is_shown = tile_id in show
        sub_options: list[dict[str, Any]] = []
        if is_shown:
            sub_options.append({"value": "hide", "label": "Hide"})
            sub_options.append({"value": "up", "label": "Move up"})
            sub_options.append({"value": "down", "label": "Move down"})
            sub_options.append({"value": "back", "label": "Back"})
        else:
            sub_options.append({"value": "show", "label": "Show"})
            sub_options.append({"value": "back", "label": "Back"})

        try:
            sub_choice = await ctx.ask("select", tile_title, options=sub_options)
        except PromptCancelled:
            continue

        if sub_choice == "back":
            continue

        if sub_choice == "hide":
            if tile_id in show:
                show.remove(tile_id)
                ctx.service.display.set(profile, show)
                ctx.service.hub.publish("complications.config", ctx.service.display.data())
        elif sub_choice == "show" and tile_id not in show and len(show) < SHOW_MAX:
            show.append(tile_id)
            ctx.service.display.set(profile, show)
            ctx.service.hub.publish("complications.config", ctx.service.display.data())
        elif sub_choice == "up" and tile_id in show:
            i = show.index(tile_id)
            if i > 0:
                show[i], show[i - 1] = show[i - 1], show[i]
                ctx.service.display.set(profile, show)
                ctx.service.hub.publish("complications.config", ctx.service.display.data())
        elif sub_choice == "down" and tile_id in show:
            i = show.index(tile_id)
            if i < len(show) - 1:
                show[i], show[i + 1] = show[i + 1], show[i]
                ctx.service.display.set(profile, show)
                ctx.service.hub.publish("complications.config", ctx.service.display.data())

