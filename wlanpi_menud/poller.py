"""The one polling loop.

Until core can push (plan phase 5), the service polls core in one loop for
homepage and status data and publishes a topic only when its value changes.
Surfaces never poll. The same loop learns the device context (mode, model
capabilities, the routes core advertises) that drives menu visibility, and
does housekeeping (complication TTLs, finished jobs, detached sessions).

Ticks are tiered so an idle tick stays cheap: the values on the homepage that
move (stats, battery, profiler state, hotspot clients) are read every
``poll_interval_sec``; the device context (identity, mode, Bluetooth,
adapters, interfaces) only every ``context_interval_sec`` or right after a
mutating job finishes, since that is how mode, Bluetooth and adapters change
from the menu.

Secrets never go into topics: no hotspot or profiler passphrases here.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from .core import CoreError
from .menu import DeviceContext, RouteTable

if TYPE_CHECKING:
    from .service import Service

log = logging.getLogger(__name__)

# core/transmit_policy.py: hostapd, wpa_supplicant and Bluetooth units are refused elsewhere.
TRANSMIT_ALLOWED_MODELS = frozenset({"M4", "M4+", "R4", "R5", "Pro"})
ROUTES_REFRESH_SEC = 600.0
ROUTES_RETRY_SEC = 60.0


class Poller:
    def __init__(self, service: "Service"):
        self.service = service
        self.core = service.core
        self._reach_at = 0.0
        self._reach: bool | None = None
        self._routes_at = 0.0
        self._context_at = 0.0
        self._context: tuple[Any, ...] = (None,) * 5  # info, model, bt, adapters, ifaces
        self._wake = asyncio.Event()
        service.poller = self

    def invalidate_context(self) -> None:
        """Re-read the device context on the next tick, and run that tick now."""
        self._context_at = 0.0
        self._wake.set()

    async def _get(self, method: str, path: str, **kw: Any) -> Any:
        try:
            return await self.core.request(method, path, **kw)
        except CoreError as exc:
            log.debug("poll %s %s failed: %s", method, path, exc)
            return None
        except Exception as exc:  # timeouts and the like
            log.debug("poll %s %s failed: %s", method, path, exc)
            return None

    async def poll_once(self) -> None:
        svc = self.service
        now = time.time()
        if now - self._routes_at > ROUTES_REFRESH_SEC:
            doc = await self._get("GET", "/openapi.json")
            if isinstance(doc, dict) and doc.get("paths"):
                svc.set_routes(RouteTable.from_openapi(doc, svc.config.core_api_prefix))
                self._routes_at = now
            else:
                # Keep the vendored table and retry in a minute, not every tick.
                self._routes_at = now - ROUTES_REFRESH_SEC + ROUTES_RETRY_SEC

        stats, battery, prof = await asyncio.gather(
            self._get("GET", "/system/device/stats"),
            self._get("GET", "/system/battery"),
            self._get("GET", "/profiler/status"),
        )
        # A failed stats read also re-reads the context: it tells "core is
        # down" apart from one endpoint failing.
        if stats is None or now - self._context_at >= svc.config.context_interval_sec:
            self._context = tuple(await asyncio.gather(
                self._get("GET", "/system/device/info"),
                self._get("GET", "/system/device/model"),
                self._get("GET", "/bluetooth/status"),
                self._get("GET", "/network/config/status"),
                self._get("GET", "/network/interfaces"),
            ))
            self._context_at = now if self._context[0] is not None else 0.0
        info, model, bt, adapters, ifaces = self._context
        if info is None and stats is None:
            svc.hub.publish("device.summary", {"core": "unavailable"})
            svc.hub.publish("alerts", [{"level": "error", "message": "wlanpi-core unavailable"}])
            self._housekeeping()
            return

        mode = (info or {}).get("mode")
        model_name = (model or {}).get("model") or (info or {}).get("model")
        if now - self._reach_at > svc.config.reachability_interval_sec:
            reach = await self._get("GET", "/utils/reachability", timeout=30)
            if isinstance(reach, dict):
                self._reach = all(str(v).upper() == "OK" for k, v in reach.items()
                                  if k.startswith(("ping_", "dns_", "browse_", "arping_")) and v not in (None, ""))
            else:
                self._reach = None
            self._reach_at = now
        clients = None
        if mode == "hotspot":  # core answers 409 outside hotspot mode
            hc = await self._get("GET", "/system/hotspot/clients")
            clients = (hc or {}).get("count")

        wlan = []
        for entries in (adapters or {}).values():
            if isinstance(entries, dict) and not isinstance(entries.get("error"), str):
                wlan.extend(name for name in entries if name.startswith("wlan") or entries[name].get("wiphy"))
        names = {i.get("ifname") for group in (ifaces or {}).values() if isinstance(group, list) for i in group
                 if isinstance(i, dict)} if isinstance(ifaces, dict) else set()

        caps = set()
        transmit = model_name in TRANSMIT_ALLOWED_MODELS
        if transmit:
            caps.add("transmit")
        if transmit and isinstance(bt, dict):
            caps.add("bluetooth")
        if isinstance(battery, dict) and battery.get("present"):
            caps.add("battery")
        if wlan:
            caps.add("monitor_adapter")
        if len(wlan) > 1:
            caps.add("multiple_adapters")
        if "eth0" in names:
            caps.add("ethernet")
        if svc.config.has_display:
            caps.add("display")
        if svc.config.has_gpio_buttons:
            caps.add("gpio_buttons")
        svc.set_context(DeviceContext(mode, frozenset(caps), svc.routes))

        bt_power = str((bt or {}).get("power", "")).lower() in ("on", "yes", "true", "powered")

        eth0_ip = None
        wlan0_ip = None
        ifaces_list = [i for group in (ifaces or {}).values() if isinstance(group, list) for i in group if isinstance(i, dict)] if isinstance(ifaces, dict) else (ifaces if isinstance(ifaces, list) else [])
        for i in ifaces_list:
            ifname = i.get("ifname")
            if ifname in ("eth0", "wlan0"):
                for a in (i.get("addr_info") or []):
                    if isinstance(a, dict) and a.get("family") == "inet" and a.get("local"):
                        if ifname == "eth0" and eth0_ip is None:
                            eth0_ip = str(a.get("local"))
                        elif ifname == "wlan0" and wlan0_ip is None:
                            wlan0_ip = str(a.get("local"))

        summary = {
            "hostname": (info or {}).get("hostname"),
            "model": model_name,
            "mode": mode,
            "software_version": (info or {}).get("software_version"),
            "ip": (stats or {}).get("ip"),
            "eth0": eth0_ip,
            "wlan0": wlan0_ip,
            "cpu": (stats or {}).get("cpu"),
            "cpu_temp": (stats or {}).get("cpu_temp"),
            "uptime": (stats or {}).get("uptime"),
            "battery": {"present": battery.get("present"), "percent": battery.get("capacity_percent"),
                        "status": battery.get("status")} if isinstance(battery, dict) else None,
            "bluetooth_on": bt_power if bt is not None else None,
            "profiler_running": bool((prof or {}).get("running")) if prof is not None else None,
            "reachable": self._reach,
            "wlan_adapters": sorted(wlan),
            "hotspot_clients": clients,
        }
        svc.hub.publish("device.summary", summary)

        alerts = []
        if adapters is not None and not wlan:
            alerts.append({"level": "warn", "message": "NO WI-FI ADAPTER"})
        if summary["profiler_running"]:
            alerts.append({"level": "info", "message": "PROFILER ACTIVE"})
        if self._reach is False:
            alerts.append({"level": "warn", "message": "NO INTERNET"})
        svc.hub.publish("alerts", alerts)
        self._housekeeping()

    def _housekeeping(self) -> None:
        svc = self.service
        svc.publish_complications()  # also expires them by TTL
        svc.jobs.prune(svc.config.job_retention_sec)
        svc.hub.publish("jobs", svc.jobs.summary())
        svc.expire_sessions()

    async def run(self) -> None:
        while True:
            self._wake.clear()
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("poll failed")
            try:
                await asyncio.wait_for(self._wake.wait(), self.service.config.poll_interval_sec)
            except TimeoutError:
                pass
