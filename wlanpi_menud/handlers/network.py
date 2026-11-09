"""Network handlers: interface tables and the composed adapter summary."""

from __future__ import annotations

from typing import Any

from .. import blocks as B
from .base import HandlerContext, HandlerResult, handler


def _ipv4(iface: dict[str, Any]) -> str | None:
    for a in iface.get("addr_info") or []:
        if a.get("family") == "inet" and a.get("local"):
            return f"{a['local']}/{a.get('prefixlen', '')}".rstrip("/")
    return None


def _iter_ifaces(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        return [i for group in data.values() if isinstance(group, list) for i in group if isinstance(i, dict)]
    if isinstance(data, list):
        return [i for i in data if isinstance(i, dict)]
    return []


@handler("network.interfaces")
async def interfaces(ctx: HandlerContext) -> HandlerResult:
    ifaces_data = await ctx.core.get_network_interfaces()
    all_ifaces = _iter_ifaces(ifaces_data)

    adapter_info: dict[str, dict[str, Any]] = {}
    try:
        status = await ctx.core.get_network_config_status()
        if isinstance(status, dict):
            for ifmap in status.values():
                if isinstance(ifmap, dict) and not isinstance(ifmap.get("error"), str):
                    for name, info in ifmap.items():
                        if isinstance(info, dict):
                            adapter_info[name] = info
    except Exception:
        pass

    eth_ifaces: list[dict[str, Any]] = []
    wlan_ifaces: list[dict[str, Any]] = []
    for i in all_ifaces:
        ifname = str(i.get("ifname", ""))
        if ifname == "lo":
            continue
        if ifname.startswith(("wlan", "wifi")):
            wlan_ifaces.append(i)
        else:
            eth_ifaces.append(i)

    rows: list[list[Any]] = []
    for i in eth_ifaces:
        rows.append([
            "Ethernet",
            i.get("ifname"),
            (i.get("operstate") or "").upper(),
            _ipv4(i) or "-",
        ])

    for i in wlan_ifaces:
        name = str(i.get("ifname", ""))
        rows.append([
            "WLAN",
            name,
            (i.get("operstate") or "").upper(),
            _ipv4(i) or "-",
        ])
        details: list[str] = []
        mac = i.get("address") or (adapter_info.get(name, {}).get("addr"))
        if mac:
            details.append(f"MAC: {mac}")
        ainfo = adapter_info.get(name, {})
        if ainfo.get("type"):
            details.append(f"Type: {ainfo['type']}")
        if ainfo.get("driver"):
            details.append(f"Driver: {ainfo['driver']}")
        if details:
            rows.append(["", "  " + " · ".join(details), "", ""])

    if not rows:
        return HandlerResult([B.text("No interfaces found")], "Interfaces")
    cols = [("section", "Section"), ("ifname", "Interface"), ("state", "State"), ("ipv4", "IPv4")]
    return HandlerResult([B.table(cols, rows)], "Interfaces")


@handler("network.adapter_summary")
async def adapter_summary(ctx: HandlerContext) -> HandlerResult:
    """core-worker-api.md section 2.5: iw view per namespace joined with addresses."""
    status = await ctx.core.get_network_config_status()
    addrs = {i.get("ifname"): _ipv4(i) for i in _iter_ifaces(await ctx.core.get_network_interfaces())}
    rows = []
    errors = []
    for ns, ifaces in (status or {}).items():
        if isinstance(ifaces, dict) and "error" in ifaces and not isinstance(ifaces.get("error"), dict):
            errors.append(f"{ns}: {ifaces['error']}")
            continue
        for name, info in (ifaces or {}).items():
            info = info or {}
            rows.append([name, ns, info.get("type"), info.get("ssid"), info.get("channel"), addrs.get(name) or "-"])
    out: list[dict[str, Any]] = []
    if rows:
        cols = [("iface", "Iface"), ("namespace", "NS"), ("type", "Type"), ("ssid", "SSID"),
                ("channel", "Ch"), ("ipv4", "IPv4")]
        out.append(B.table(cols, rows))
    else:
        out.append(B.text("No Wi-Fi adapters"))
    out.extend(B.status("warn", e) for e in errors)
    return HandlerResult(out, "Adapters")
