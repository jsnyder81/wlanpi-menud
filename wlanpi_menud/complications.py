"""Complications: small status tiles (a label, a value and a status).

Three parts, all pure or in-memory so they test without sockets:

* **Namespaces and text rules** (``ID_RE``, ``sanitize``, ``map_username``).
* **Built-in providers**: ``builtin_complications()`` derives the ``wlanpi.*``
  tiles from the same poll that builds ``device.summary``, plus the Wi-Fi
  link (``wifi_record``). Thresholds live here, as constants.
* **The store** (``ComplicationStore``): provider records with TTL expiry on
  a timer for the nearest deadline, quotas, and fan-out of the legacy
  ``complications`` topic and the new ``complications.live`` and
  ``complications.catalog`` topics at most once per fan-out window.
* **Display config** (``DisplayConfig``): per surface profile ordered show
  lists in three layers (user, site, shipped) and ``resolve()``, the
  normative selection algorithm every surface implements.

The legacy ``complications`` topic carries provider records only, in its
original four-key shape. Built-ins appear only in ``complications.live``,
so a surface written before this module never shows a value twice.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .manifest import ManifestError, load_schema, schema_errors

log = logging.getLogger(__name__)

ID_RE = re.compile(r"^(wlanpi|plugin|local)(\.[a-z][a-z0-9_]*){1,3}$")
PATTERN_RE = re.compile(r"^(wlanpi|plugin|local)(\.[a-z][a-z0-9_]*){0,2}\.\*$")
PROFILE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)?$")
SEGMENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
ID_MAX = 64
PROFILE_MAX = 32
SHOW_MAX = 24

STATUSES = ("ok", "info", "warn", "error", "off", "unknown")
ICONS = ("battery", "bolt", "bluetooth", "globe", "wifi", "ethernet", "thermometer", "cpu", "users", "radio",
         "shield", "dot")
# status -> status/kv block level (section 2.6)
LEVEL = {"ok": "ok", "info": "info", "warn": "warn", "error": "error", "off": "info", "unknown": "info"}

LABEL_MAX, VALUE_MAX, SHORT_MAX, DETAIL_MAX, TITLE_MAX, DESCRIPTION_MAX = 8, 20, 4, 64, 24, 120
DEFAULT_TTL = 30
UNKNOWN_VALUE = "--"

VISIBLE = ("app_id", "label", "value", "status", "short", "detail", "icon", "node")
LEGACY = ("app_id", "label", "value", "status")

PROFILES = ("panel.strip", "panel", "tui", "webui")
DEFAULT_PROFILE = "default"
CLIENT_PROFILES = {"wlanpi-fpms": "panel", "wlanpi-menu-tui": "tui", "wlanpi-webui": "webui"}
DEFAULT_SHOW: dict[str, list[str]] = {
    "panel.strip": ["wlanpi.battery", "wlanpi.bluetooth", "wlanpi.internet"],
    "panel": ["wlanpi.mode", "wlanpi.ip", "wlanpi.wifi", "wlanpi.cpu_temp", "wlanpi.hotspot_clients",
              "plugin.*", "local.*"],
    "tui": ["wlanpi.mode", "wlanpi.eth0", "wlanpi.ip", "wlanpi.cpu_temp", "plugin.*", "local.*"],
    "webui": ["wlanpi.mode", "wlanpi.ip", "wlanpi.internet", "wlanpi.wifi", "wlanpi.battery", "wlanpi.cpu_temp",
              "wlanpi.profiler", "plugin.*", "local.*"],
    "default": ["wlanpi.mode", "wlanpi.ip", "plugin.*", "local.*"],
}

# ---------------------------------------------------------------------------- text

# CSI and OSC escape sequences, then any remaining C0/C1 control or
# bidirectional formatting character.
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-_]?")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def sanitize(s: str, limit: int | None = None) -> str:
    """Strip ANSI escapes, control and bidi-override characters, then apply ``limit``."""
    s = _CONTROL.sub("", _ANSI.sub("", s))
    return s[:limit] if limit is not None else s


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def map_username(name: str) -> str:
    """A unix username as an id segment: lowercased, other characters become ``_``.
    A name that would not start with a letter gets a ``u`` prefix."""
    seg = re.sub(r"[^a-z0-9_]", "_", name.lower())
    if not seg or not seg[0].isalpha():
        seg = "u" + seg
    return seg


def is_entry(e: str) -> bool:
    """A show-list entry: an exact id or a family pattern."""
    return len(e) <= ID_MAX and bool(ID_RE.match(e) or PATTERN_RE.match(e))


def normalize_status(status: Any) -> str:
    if status == "warning":
        return "warn"
    return status if status in STATUSES else "unknown"


# ---------------------------------------------------------------------------- resolution

def resolve(show: Iterable[str], live_ids: Iterable[str]) -> list[str]:
    """Section 4.3: walk ``show`` in order; an exact id yields itself if live;
    a pattern yields every live id it matches that no exact entry names and
    that nothing earlier yielded, sorted. The caller cuts to capacity."""
    show = list(show)
    live = sorted(set(live_ids))
    exact = {e for e in show if not e.endswith(".*")}
    out: list[str] = []
    seen: set[str] = set()
    for e in show:
        if e.endswith(".*"):
            prefix = e[:-1]
            for i in live:
                if i.startswith(prefix) and i not in exact and i not in seen:
                    out.append(i)
                    seen.add(i)
        elif e in live and e not in seen:
            out.append(e)
            seen.add(e)
    return out


def body_rows(screen: dict[str, Any] | None) -> int:
    """Panel renderer body rows: ``(height - 15 - 12) // 12`` (8 at 128 px, 3 at 64 px)."""
    screen = screen or {}
    if screen.get("height_px"):
        return max(0, (int(screen["height_px"]) - 15 - 12) // 12)
    if screen.get("rows"):
        return max(0, int(screen["rows"]) - 2)  # title and hints
    return 8


def home_capacity(profile: str, screen: dict[str, Any] | None, alert_rows: int) -> int:
    """Tile rows on a thin home view: body rows, minus the strip row on the
    panel, minus the alert rows shown (at most 2)."""
    if profile == "panel.strip":
        cols = (screen or {}).get("cols", 21)
        return max(0, (cols - 5) // 5)
    strip = 1 if profile == "panel" else 0
    return max(0, min(SHOW_MAX, body_rows(screen) - strip - min(alert_rows, 2)))


# ---------------------------------------------------------------------------- built-ins

@dataclass(frozen=True)
class Builtin:
    label: str
    title: str
    icon: str


BUILTINS: dict[str, Builtin] = {
    "wlanpi.mode": Builtin("Mode", "Device mode", "dot"),
    "wlanpi.ip": Builtin("IP", "IP address", "ethernet"),
    "wlanpi.eth0": Builtin("eth0", "eth0 address", "ethernet"),
    "wlanpi.wlan0": Builtin("wlan0", "wlan0 address", "wifi"),
    "wlanpi.wifi": Builtin("Wi-Fi", "Wi-Fi link", "wifi"),
    "wlanpi.internet": Builtin("Internet", "Internet reachability", "globe"),
    "wlanpi.bluetooth": Builtin("BT", "Bluetooth", "bluetooth"),
    "wlanpi.battery": Builtin("Batt", "Battery", "battery"),
    "wlanpi.cpu_temp": Builtin("Temp", "CPU temperature", "thermometer"),
    "wlanpi.cpu": Builtin("CPU", "CPU load", "cpu"),
    "wlanpi.profiler": Builtin("Profiler", "Profiler", "radio"),
    "wlanpi.hotspot_clients": Builtin("Clients", "Hotspot clients", "users"),
}

# Draft thresholds (spec decision log Q6: ship these, tune during the device trial).
BATTERY_WARN_AT = 20   # warn from 11 to 20 %
BATTERY_ERROR_AT = 10  # error at 10 % and below
TEMP_WARN_AT = 70      # warn from 70 to 79 C
TEMP_ERROR_AT = 80     # error at 80 C and above
RSSI_OK_AT = -67       # ok at -67 dBm or better
RSSI_WARN_AT = -75     # warn from -68 to -75, error below -75
RSSI_HYSTERESIS_DB = 3

MODE_SHORT = {"classic": "CLS", "hotspot": "HOT", "server": "SRV", "bridge": "BRG", "wiperf": "WPF",
              "lanwatch": "LAN"}

WLAN_LINK_ROUTE = "GET /network/interfaces/{iface}/wlan-link"
PROFILER_ROUTE = "GET /profiler/status"
WIFI_IFACE = "wlan0"
WIFI_UNKNOWN: dict[str, Any] = {"_unknown": True}  # the read failed: the tile shows unknown


def _number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d+(?:\.\d+)?", v)
        if m:
            return float(m.group())
    return None


def _rec(app_id: str, value: str, status: str, short: str | None = None, detail: str | None = None,
         icon: str | None = None) -> dict[str, Any]:
    meta = BUILTINS[app_id]
    r: dict[str, Any] = {"app_id": app_id, "label": meta.label, "value": clip(sanitize(value) or UNKNOWN_VALUE, VALUE_MAX),
                         "status": status, "icon": icon or meta.icon}
    if short:
        r["short"] = sanitize(short, SHORT_MAX)
    if detail:
        r["detail"] = clip(sanitize(detail), DETAIL_MAX)
    return r


def channel_of(freq: float | None) -> int | None:
    """802.11 channel from a centre frequency (2.4, 5 and 6 GHz)."""
    if freq is None:
        return None
    f = round(freq)
    if f == 2484:
        return 14
    if 2412 <= f <= 2472:
        return (f - 2407) // 5
    if f == 5935:
        return 2
    if 5955 <= f <= 7115:
        return (f - 5950) // 5
    if 5000 <= f <= 5900:
        return (f - 5000) // 5
    return None


def signal_status(dbm: float | None) -> str:
    if dbm is None:
        return "unknown"
    if dbm >= RSSI_OK_AT:
        return "ok"
    if dbm >= RSSI_WARN_AT:
        return "warn"
    return "error"


def wifi_record(link: dict[str, Any] | None) -> dict[str, Any]:
    """Section 3.3: one ``WlanLink`` answer as the ``wlanpi.wifi`` tile.

    Carries ``_signal`` and ``_ssid`` (not published) for the hysteresis check."""
    if not isinstance(link, dict) or link.get("_unknown"):
        return _rec("wlanpi.wifi", UNKNOWN_VALUE, "unknown")
    if not link.get("connected"):
        return _rec("wlanpi.wifi", "not connected", "off")
    ssid = sanitize(str(link.get("ssid") or ""))
    sig = _number(link.get("signal_dbm"))
    freq = _number(link.get("freq_mhz"))
    parts = []
    ch = channel_of(freq)
    if freq is not None:
        if ch is not None:
            parts.append(f"ch {ch}")
        parts.append(f"{round(freq)} MHz")
    else:
        freqs = [round(f) for f in (_number(x.get("freq_mhz")) for x in link.get("links") or []
                                          if isinstance(x, dict)) if f is not None]
        if freqs:
            parts.append("links " + "/".join(str(f) for f in freqs) + " MHz")
    if link.get("bssid"):
        parts.append(str(link["bssid"]))
    detail = ", ".join(parts) or None
    if sig is None:
        r = _rec("wlanpi.wifi", ssid[:VALUE_MAX] or UNKNOWN_VALUE, "unknown", detail=detail)
    else:
        s = str(round(sig))
        room = VALUE_MAX - len(s) - 1
        name = ssid if len(ssid) <= room else clip(ssid, room)
        r = _rec("wlanpi.wifi", f"{name} {s}" if name else s, signal_status(sig), short=s, detail=detail)
    r["_signal"] = sig
    r["_ssid"] = ssid
    return r


def wifi_hysteresis(prev: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """Keep the published Wi-Fi tile unless the signal moved 3 dB or more, the
    status band changed, or anything else (SSID, detail) changed."""
    if prev is None or prev.get("_signal") is None or new.get("_signal") is None:
        return new
    if prev["status"] != new["status"] or prev.get("_ssid") != new.get("_ssid") \
            or prev.get("detail") != new.get("detail"):
        return new
    if abs(new["_signal"] - prev["_signal"]) >= RSSI_HYSTERESIS_DB:
        return new
    return prev


def builtin_complications(summary: dict[str, Any], wifi: dict[str, Any] | None = None, *,
                          capabilities: Iterable[str] = (), profiler_route: bool = True) -> dict[str, dict[str, Any]]:
    """Section 3.2: the ``wlanpi.*`` tiles as a second view of one poll.

    ``wifi`` is the last ``WlanLink`` answer, ``WIFI_UNKNOWN`` when the read
    failed, or None when the tile does not apply (route not advertised,
    hotspot mode, no wlan0). An absent tile is one the device cannot produce;
    ``unknown`` means it should exist but core did not answer."""
    if not summary or summary.get("core") == "unavailable":
        return {}
    caps = set(capabilities)
    out: dict[str, dict[str, Any]] = {}

    def put(r: dict[str, Any]) -> None:
        out[r["app_id"]] = r

    mode = summary.get("mode")
    if mode:
        m = str(mode)
        put(_rec("wlanpi.mode", m[:1].upper() + m[1:], "info", short=MODE_SHORT.get(m.lower(), m[:3].upper())))
    else:
        put(_rec("wlanpi.mode", UNKNOWN_VALUE, "unknown"))

    ip = summary.get("ip")
    put(_rec("wlanpi.ip", str(ip), "ok") if ip else _rec("wlanpi.ip", "none", "warn"))

    if "ethernet" in caps:
        v = summary.get("eth0")
        put(_rec("wlanpi.eth0", str(v), "ok") if v else _rec("wlanpi.eth0", "no IP", "off"))
    if summary.get("wlan_adapters"):
        v = summary.get("wlan0")
        put(_rec("wlanpi.wlan0", str(v), "ok") if v else _rec("wlanpi.wlan0", "no IP", "off"))

    if wifi is not None:
        put(wifi_record(wifi))

    reach = summary.get("reachable")
    if reach is None:
        put(_rec("wlanpi.internet", UNKNOWN_VALUE, "unknown", short="NET"))
    else:
        put(_rec("wlanpi.internet", "up" if reach else "down", "ok" if reach else "error", short="NET"))

    if "bluetooth" in caps:
        on = summary.get("bluetooth_on")
        if on is None:
            put(_rec("wlanpi.bluetooth", UNKNOWN_VALUE, "unknown", short="BT"))
        else:
            put(_rec("wlanpi.bluetooth", "on" if on else "off", "ok" if on else "off", short="BT"))

    batt = summary.get("battery")
    if isinstance(batt, dict) and batt.get("present"):
        pct = _number(batt.get("percent"))
        state = str(batt.get("status") or "").lower()
        charging = "charg" in state and "discharg" not in state and "not charg" not in state
        icon = "bolt" if charging else "battery"
        if pct is None:
            put(_rec("wlanpi.battery", UNKNOWN_VALUE, "unknown", icon=icon))
        else:
            p = round(pct)
            status = "ok" if p > BATTERY_WARN_AT else ("warn" if p > BATTERY_ERROR_AT else "error")
            if charging and status == "error":
                status = "warn"
            put(_rec("wlanpi.battery", f"{p}% charging" if charging else f"{p}%", status, short=f"{p}%", icon=icon))

    temp = _number(summary.get("cpu_temp"))
    if temp is None:
        put(_rec("wlanpi.cpu_temp", UNKNOWN_VALUE, "unknown"))
    else:
        t = round(temp)
        status = "ok" if t < TEMP_WARN_AT else ("warn" if t < TEMP_ERROR_AT else "error")
        put(_rec("wlanpi.cpu_temp", f"{t}C", status, short=f"{t}C"))

    cpu = _number(summary.get("cpu"))
    if cpu is None:
        put(_rec("wlanpi.cpu", UNKNOWN_VALUE, "unknown"))
    else:
        put(_rec("wlanpi.cpu", f"{round(cpu)}%", "info", short=f"{round(cpu)}%"))

    if profiler_route:
        running = summary.get("profiler_running")
        if running is None:
            put(_rec("wlanpi.profiler", UNKNOWN_VALUE, "unknown", short="PRF"))
        else:
            put(_rec("wlanpi.profiler", "running" if running else "stopped", "info" if running else "off",
                     short="PRF"))

    if mode == "hotspot":
        n = summary.get("hotspot_clients")
        if n is None:
            put(_rec("wlanpi.hotspot_clients", UNKNOWN_VALUE, "unknown"))
        else:
            put(_rec("wlanpi.hotspot_clients", str(n), "info" if n else "off", short=str(n)[:SHORT_MAX]))
    return out


# ---------------------------------------------------------------------------- declarations

DECLARATION_FILE = "complications.json"


@dataclass(frozen=True)
class Declaration:
    plugin: str
    user: str
    path: str
    entries: dict[str, dict[str, Any]]


def load_declarations(plugins_dir: Path | None) -> tuple[dict[str, Declaration], list[ManifestError]]:
    """Read ``plugins.d/<name>/complications.json``. A bad file is rejected
    whole, with errors naming the file and the field; nothing here raises."""
    decls: dict[str, Declaration] = {}
    errors: list[ManifestError] = []
    if plugins_dir is None:
        return decls, errors
    try:
        schema = load_schema("complications.schema.json")
    except Exception as exc:  # noqa: BLE001 - a broken install must still not crash the service
        errors.append(ManifestError("complications.schema.json", "/", str(exc)))
        return decls, errors
    try:
        dirs = sorted(p for p in plugins_dir.iterdir() if p.is_dir())
    except OSError:
        return decls, errors
    for pdir in dirs:
        path = pdir / DECLARATION_FILE
        if not path.is_file():
            continue
        f = str(path)
        try:
            doc = json.loads(path.read_text())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(ManifestError(f, "/", f"unreadable: {exc}"))
            continue
        errs = schema_errors(doc, schema, f)
        name = pdir.name
        if not errs:
            if not SEGMENT_RE.match(name):
                errs.append(ManifestError(f, "/", f"plugin directory name {name!r} is not a valid id segment"))
            prefix = f"plugin.{name}."
            seen: set[str] = set()
            for i, c in enumerate(doc["complications"]):
                if not c["id"].startswith(prefix):
                    errs.append(ManifestError(f, f"/complications/{i}/id", f"{c['id']} must start with {prefix}"))
                if c["id"] in seen:
                    errs.append(ManifestError(f, f"/complications/{i}/id", f"duplicate id {c['id']}"))
                seen.add(c["id"])
                node = c.get("node")
                if node is not None and not node.startswith(prefix):
                    errs.append(ManifestError(f, f"/complications/{i}/node", f"{node} must start with {prefix}"))
        if errs:
            for e in errs:
                log.warning("complication declaration rejected: %s", e)
            errors.extend(errs)
            continue
        decls[name] = Declaration(name, doc["publisher"]["user"], f,
                                  {c["id"]: dict(c) for c in doc["complications"]})
    return decls, errors


# ---------------------------------------------------------------------------- display config

class ConfigWriteError(Exception):
    pass


def _read_layer(path: Path | None, *, user: bool = False) -> dict[str, list[str]]:
    """``{"schema": 1, "surfaces": {profile: {"show": [...]}}}``. Invalid entries are dropped
    with a log line. A user file that fails to parse is renamed aside and ignored."""
    if path is None or not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text())
        surfaces = doc["surfaces"]
        if not isinstance(surfaces, dict):
            raise TypeError("surfaces is not an object")
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
        log.error("complications config %s unusable (%s); ignoring it", path, exc)
        if user:
            aside = path.with_name(f"{path.name}.bad-{int(time.time())}")
            try:
                path.rename(aside)
                log.error("renamed %s to %s", path, aside)
            except OSError as exc2:
                log.error("cannot rename %s aside: %s", path, exc2)
        return {}
    out: dict[str, list[str]] = {}
    for profile, body in surfaces.items():
        if not isinstance(profile, str) or len(profile) > PROFILE_MAX or not PROFILE_RE.match(profile):
            log.warning("%s: ignoring bad profile name %r", path, profile)
            continue
        show = body.get("show") if isinstance(body, dict) else None
        if not isinstance(show, list):
            log.warning("%s: profile %s has no show list", path, profile)
            continue
        good = [e for e in show if isinstance(e, str) and is_entry(e)]
        if len(good) != len(show):
            log.warning("%s: profile %s: dropped %d invalid entries", path, profile, len(show) - len(good))
        out[profile] = good[:SHOW_MAX]
    return out


class DisplayConfig:
    """Per surface profile show lists. Precedence per profile: user, site, shipped."""

    def __init__(self, defaults_file: Path | None, site_file: Path | None, user_file: Path | None):
        self.user_file = user_file
        shipped = _read_layer(defaults_file)
        self.shipped: dict[str, list[str]] = {**DEFAULT_SHOW, **shipped}
        self.site = _read_layer(site_file)
        self.user = _read_layer(user_file, user=True)

    def profiles(self) -> list[str]:
        return sorted(set(PROFILES) | {DEFAULT_PROFILE} | set(self.shipped) | set(self.site) | set(self.user))

    def entry(self, profile: str) -> tuple[list[str], str]:
        if profile in self.user:
            return list(self.user[profile]), "user"
        if profile in self.site:
            return list(self.site[profile]), "site"
        if profile in self.shipped:
            return list(self.shipped[profile]), "default"
        return self.entry(DEFAULT_PROFILE) if profile != DEFAULT_PROFILE else (list(DEFAULT_SHOW["default"]), "default")

    def show_for(self, profile: str) -> list[str]:
        return self.entry(profile)[0]

    def data(self) -> dict[str, Any]:
        surfaces = {}
        for p in self.profiles():
            show, source = self.entry(p)
            surfaces[p] = {"show": show, "source": source}
        return {"surfaces": surfaces}

    def _write(self, user: dict[str, list[str]]) -> None:
        if self.user_file is None:
            return
        path = self.user_file
        body = {"schema": 1, "surfaces": {p: {"show": s} for p, s in sorted(user.items())}}
        tmp = path.with_name(f".{path.name}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w") as fh:
                json.dump(body, fh, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            log.error("cannot write %s: %s", path, exc)
            raise ConfigWriteError(str(exc)) from exc

    def set(self, profile: str, show: list[str]) -> None:
        user = dict(self.user)
        user[profile] = list(show)[:SHOW_MAX]
        self._write(user)
        self.user = user

    def reset(self, profile: str) -> None:
        user = {p: s for p, s in self.user.items() if p != profile}
        self._write(user)
        self.user = user


def profile_for(surface: str | None, client_name: str | None) -> str:
    if surface:
        return surface
    return CLIENT_PROFILES.get(client_name or "", DEFAULT_PROFILE)


# ---------------------------------------------------------------------------- store

class Refused(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class _Timers:
    expiry: asyncio.TimerHandle | None = None
    flush: asyncio.TimerHandle | None = None


class ComplicationStore:
    """Live records and the ``complications*`` topics.

    Provider records carry ``expires_at`` (store clock), ``uid`` and ``conn``
    (the attachment for ``hold: connection``) besides their visible fields.
    Built-ins have no TTL; each poll replaces them.
    """

    def __init__(self, publish: Callable[[str, Any], Any], *, fanout_sec: float = 0.5, quota_per_uid: int = 8,
                 quota_total: int = 32, clock: Callable[[], float] = time.monotonic):
        self._publish = publish
        self.fanout_sec = fanout_sec
        self.quota_per_uid = quota_per_uid
        self.quota_total = quota_total
        self.clock = clock
        self.providers: dict[str, dict[str, Any]] = {}
        self.builtins: dict[str, dict[str, Any]] = {}
        self.builtin_ids: set[str] = set()
        self.declarations: dict[str, Declaration] = {}
        self._timers = _Timers()
        self._last_flush = float("-inf")

    # ---------------------------------------------------------------- declarations

    def set_declarations(self, decls: dict[str, Declaration]) -> None:
        self.declarations = dict(decls)
        self.changed()

    def declared(self, app_id: str) -> tuple[Declaration, dict[str, Any]] | None:
        parts = app_id.split(".")
        if len(parts) < 3 or parts[0] != "plugin":
            return None
        d = self.declarations.get(parts[1])
        if d is None or app_id not in d.entries:
            return None
        return d, d.entries[app_id]

    def plugin_publisher(self, plugin: str) -> str | None:
        d = self.declarations.get(plugin)
        return d.user if d else None

    # ---------------------------------------------------------------- writes

    def put(self, app_id: str, fields: dict[str, Any], *, ttl: float, uid: int | None = None,
            conn: str | None = None) -> bool:
        """Create or replace a provider record. Returns True when visible fields changed.
        Raises ``Refused`` when a quota is reached (a replace is not counted)."""
        if app_id not in self.providers:
            if uid is not None and sum(1 for r in self.providers.values() if r.get("uid") == uid) >= self.quota_per_uid:
                raise Refused("FORBIDDEN", f"quota reached: {self.quota_per_uid} live complications per uid")
            if len(self.providers) >= self.quota_total:
                raise Refused("FORBIDDEN", f"quota reached: {self.quota_total} provider complications in total")
        before = self.providers.get(app_id)
        rec = {"app_id": app_id, **fields, "expires_at": self.clock() + ttl, "uid": uid, "conn": conn}
        self.providers[app_id] = rec
        changed = before is None or _visible(before) != _visible(rec)
        self._arm_expiry()
        if changed:
            self.changed()
        return changed

    def remove(self, app_id: str) -> bool:
        if self.providers.pop(app_id, None) is None:
            return False
        self._arm_expiry()
        self.changed()
        return True

    def drop_connection(self, conn: str) -> None:
        gone = [k for k, r in self.providers.items() if r.get("conn") == conn]
        for k in gone:
            del self.providers[k]
        if gone:
            self._arm_expiry()
            self.changed()

    def expire(self) -> bool:
        now = self.clock()
        gone = [k for k, r in self.providers.items() if r["expires_at"] <= now]
        for k in gone:
            del self.providers[k]
        return bool(gone)

    def set_builtins(self, records: dict[str, dict[str, Any]]) -> None:
        records = dict(records)
        if "wlanpi.wifi" in records:
            records["wlanpi.wifi"] = wifi_hysteresis(self.builtins.get("wlanpi.wifi"), records["wlanpi.wifi"])
        ids = set(records)
        if records == self.builtins and ids == self.builtin_ids:
            return
        self.builtins = records
        if ids:
            self.builtin_ids = ids  # the catalog keeps the last known set while core is away
        self.changed()

    def clear_builtins(self) -> None:
        """Core unavailable: every wlanpi.* record leaves the live set."""
        if self.builtins:
            self.builtins = {}
            self.changed()

    # ---------------------------------------------------------------- reads

    def live(self) -> dict[str, dict[str, Any]]:
        out = {k: _visible(r) for k, r in self.builtins.items()}
        for k, r in self.providers.items():
            if ID_RE.match(k):
                out[k] = _visible(r)
        return out

    def live_data(self) -> list[dict[str, Any]]:
        return [v for _, v in sorted(self.live().items())]

    def legacy_data(self) -> list[dict[str, Any]]:
        return [{k: r[k] for k in LEGACY} for _, r in sorted(self.providers.items())]

    def catalog_data(self) -> list[dict[str, Any]]:
        cat: dict[str, dict[str, Any]] = {}
        for i in self.builtin_ids:
            m = BUILTINS[i]
            cat[i] = {"app_id": i, "title": m.title, "label": m.label, "source": "builtin", "icon": m.icon}
        for d in self.declarations.values():
            for i, e in d.entries.items():
                c = {"app_id": i, "title": e["title"], "label": e["label"], "source": "plugin"}
                for k in ("icon", "description", "node"):
                    if e.get(k):
                        c[k] = e[k]
                cat[i] = c
        for i, r in self.providers.items():
            if i.startswith("local.") and ID_RE.match(i):
                c = {"app_id": i, "title": r["label"], "label": r["label"], "source": "local"}
                if r.get("icon"):
                    c["icon"] = r["icon"]
                cat[i] = c
        return [v for _, v in sorted(cat.items())]

    # ---------------------------------------------------------------- fan-out

    def changed(self) -> None:
        """Publish now, or once the fan-out window since the last publish ends."""
        if self._timers.flush is not None:
            return
        wait = self._last_flush + self.fanout_sec - self.clock()
        if self.fanout_sec <= 0 or wait <= 0:
            self.flush()
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.flush()
            return
        self._timers.flush = loop.call_later(wait, self._timer_flush)

    def _timer_flush(self) -> None:
        self._timers.flush = None
        self.flush()

    def flush(self) -> None:
        if self._timers.flush is not None:
            self._timers.flush.cancel()
            self._timers.flush = None
        self.expire()
        self._last_flush = self.clock()
        self._publish("complications", self.legacy_data())
        self._publish("complications.catalog", self.catalog_data())
        self._publish("complications.live", self.live_data())

    def _arm_expiry(self) -> None:
        if self._timers.expiry is not None:
            self._timers.expiry.cancel()
            self._timers.expiry = None
        if not self.providers:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop (sync callers): the poller's housekeeping still expires records
        nearest = min(r["expires_at"] for r in self.providers.values())
        self._timers.expiry = loop.call_later(max(0.0, nearest - self.clock()) + 0.01, self._on_expiry)

    def _on_expiry(self) -> None:
        self._timers.expiry = None
        if self.expire():
            self.changed()
        self._arm_expiry()

    def close(self) -> None:
        for h in (self._timers.expiry, self._timers.flush):
            if h is not None:
                h.cancel()
        self._timers = _Timers()


def _visible(r: dict[str, Any]) -> dict[str, Any]:
    return {k: r[k] for k in VISIBLE if r.get(k) is not None}

