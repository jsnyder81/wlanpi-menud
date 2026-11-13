"""Render-neutral view blocks, rendering core JSON into them, and paging.

Every block carries ``text``, a plain-text rendering surfaces fall back to.
"""

from __future__ import annotations

import json
from typing import Any

MAX_TEXT = 16384
Scalar = str | int | float | bool | None


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _scalar(v: Any) -> Scalar:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v):
        return ", ".join(str(x) for x in v)
    return json.dumps(v, separators=(",", ":"), default=str)


def text(s: str, style: str | None = None) -> dict[str, Any]:
    b: dict[str, Any] = {"type": "text", "text": _clip(s, MAX_TEXT)}
    if style:
        b["style"] = style
    return b


def kv(items: list[tuple[str, Any]] | dict[str, Any], secret_keys: set[str] | frozenset[str] = frozenset()) -> dict[str, Any]:
    pairs = list(items.items()) if isinstance(items, dict) else list(items)
    out = []
    lines = []
    for k, v in pairs:
        key = _clip(str(k), 64)
        item: dict[str, Any] = {"key": key, "value": _scalar(v)}
        if k in secret_keys:
            item["secret"] = True
        out.append(item)
        lines.append(f"{key}: {'' if item['value'] is None else item['value']}")
    return {"type": "kv", "text": _clip("\n".join(lines), MAX_TEXT), "items": out}


def kv_levels(rows: list[tuple[str, Any, str | None]]) -> dict[str, Any]:
    """A ``kv`` block whose rows carry a status ``level`` (ok, info, warn, error)."""
    b = kv([(k, v) for k, v, _ in rows])
    for item, (_, _, level) in zip(b["items"], rows):
        if level:
            item["level"] = level
    return b


def table(columns: list[tuple[str, str]], rows: list[list[Any]]) -> dict[str, Any]:
    cols = [{"key": k, "label": _clip(label, 32), "priority": i} for i, (k, label) in enumerate(columns)]
    rows = [[_scalar(c) for c in r] for r in rows]
    head = " | ".join(label for _, label in columns)
    body = ["" if not r else " | ".join("" if c is None else str(c) for c in r) for r in rows]
    return {"type": "table", "text": _clip("\n".join([head, *body]), MAX_TEXT), "columns": cols, "rows": rows}


def list_block(items: list[dict[str, Any]], selected: int | None = None) -> dict[str, Any]:
    clean = []
    for it in items:
        c = {"label": _clip(str(it["label"]), 64)}
        if it.get("detail") is not None:
            c["detail"] = _clip(str(it["detail"]), 64)
        if it.get("node"):
            c["node"] = it["node"]
        if "branch" in it:
            c["branch"] = bool(it["branch"])
        clean.append(c)
    lines = []
    for i, it in enumerate(clean):
        mark = "> " if selected is not None and i == selected else "  "
        lines.append(mark + it["label"] + (f" ({it['detail']})" if "detail" in it else "") + (" >" if it.get("branch") else ""))
    b: dict[str, Any] = {"type": "list", "text": _clip("\n".join(lines), MAX_TEXT), "items": clean}
    if selected is not None:
        b["selected"] = selected
    return b


def status(level: str, message: str, label: str | None = None) -> dict[str, Any]:
    b: dict[str, Any] = {"type": "status", "text": _clip(message, MAX_TEXT), "level": level}
    if label:
        b["label"] = _clip(label, 64)
    return b


def qr(data: str, caption: str | None = None, text_fallback: str | None = None) -> dict[str, Any]:
    b: dict[str, Any] = {"type": "qr", "text": _clip(text_fallback or caption or "QR code", MAX_TEXT), "data": data[:2048]}
    if caption:
        b["caption"] = _clip(caption, 64)
    return b


def progress(pct: float | None, label: str | None = None) -> dict[str, Any]:
    t = label or ("Working…" if pct is None else f"{pct:.0f}%")
    b: dict[str, Any] = {"type": "progress", "text": t, "pct": pct}
    if label:
        b["label"] = _clip(label, 64)
    return b


def confirm(prompt_id: str, message: str, confirm_label: str = "Confirm", cancel_label: str = "Cancel",
            selected: str = "cancel") -> dict[str, Any]:
    return {
        "type": "confirm",
        "text": f"{message}\n[{confirm_label}] [{cancel_label}]",
        "prompt_id": prompt_id,
        "message": message[:256],
        "confirm_label": confirm_label[:16],
        "cancel_label": cancel_label[:16],
        "selected": selected,
    }


def wifi_qr_payload(ssid: str, passphrase: str) -> str:
    def esc(s: str) -> str:
        return "".join("\\" + c if c in '\\;,:"' else c for c in s)
    return f"WIFI:S:{esc(ssid)};T:WPA;P:{esc(passphrase)};;"


def has_secret(blocks: list[dict[str, Any]]) -> bool:
    return any(b.get("type") == "kv" and any(i.get("secret") for i in b["items"]) for b in blocks)


# ---------------------------------------------------------------------------
# Core JSON -> blocks
# ---------------------------------------------------------------------------

def _flatten(d: dict[str, Any], prefix: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and v:
            out.extend(_flatten(v, key + "."))
        else:
            out.append((key, v))
    return out


def _first_record_list(data: Any) -> list[dict[str, Any]] | None:
    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
        return data
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                return v
        # {group: [records]} as in GET /network/interfaces
        merged = [x for v in data.values() if isinstance(v, list) for x in v if isinstance(x, dict)]
        if merged:
            return merged
    return None


def _records_table(records: list[dict[str, Any]], max_cols: int = 8) -> dict[str, Any]:
    keys: list[str] = []
    for r in records:
        for k, v in r.items():
            if k not in keys and not isinstance(v, (dict, list)):
                keys.append(k)
    keys = keys[:max_cols] or list(records[0])[:max_cols]
    return table([(k, k.replace("_", " ").title()) for k in keys], [[r.get(k) for k in keys] for r in records])


def _info_lines(data: Any) -> list[str] | None:
    """Core's {"info": [...]} / {"error": ...} pattern (LLDP, CDP, public IP)."""
    if isinstance(data, dict) and set(data) <= {"info", "error"} and data:
        if data.get("error"):
            return None
        info = data.get("info", [])
        if isinstance(info, list) and all(not isinstance(x, (dict, list)) for x in info):
            return [str(x) for x in info if str(x).strip()]
        if isinstance(info, dict):
            return [f"{k}: {v}" for k, v in info.items()]
    return None


def render(data: Any, view: str | None, title: str | None = None) -> list[dict[str, Any]]:
    """Turn a core response into blocks for the node's preferred view."""
    if isinstance(data, dict) and data.get("error") and set(data) <= {"error", "info", "message"}:
        return [status("error", str(data["error"]))]
    lines = _info_lines(data)
    if lines is not None:
        if not lines:
            return [text("No data")]
        if all(":" in ln for ln in lines) and view == "kv":
            return [kv([tuple(s.strip() for s in ln.split(":", 1)) for ln in lines])]
        return [list_block([{"label": ln} for ln in lines])]
    if data is None or data == {} or data == []:
        return [text("No data")]
    if view == "table" or (view is None and _first_record_list(data)):
        records = _first_record_list(data)
        if records is not None:
            return [_records_table(records)]
        if isinstance(data, list):
            return [list_block([{"label": str(x)} for x in data])] if data else [text("No results")]
        if isinstance(data, dict):
            empty_list = any(isinstance(v, list) and not v for v in data.values())
            if empty_list:
                return [text("No results")]
    if view == "list":
        if isinstance(data, list):
            return [list_block([{"label": _label_of(x), "detail": _detail_of(x)} for x in data])]
        records = _first_record_list(data)
        if records is not None:
            return [list_block([{"label": _label_of(x), "detail": _detail_of(x)} for x in records])]
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    return [list_block([{"label": _label_of(x), "detail": _detail_of(x)} for x in v])] if v else [text("None")]
    if view == "status" and isinstance(data, dict):
        ok = data.get("success", data.get("running", data.get("ok", True)))
        message = data.get("message") or data.get("detail") or data.get("status") or data.get("reason")
        level = "ok" if ok not in (False, "error", "failed") else "error"
        if not message:
            message = ", ".join(f"{k}: {_scalar(v)}" for k, v in data.items()) or ("Done" if level == "ok" else "Failed")
        return [status(level, str(message), title)]
    if isinstance(data, dict):
        return [kv(_flatten(data))]
    if isinstance(data, list):
        return [list_block([{"label": _label_of(x)} for x in data])]
    return [text(str(data))]


def _label_of(x: Any) -> str:
    if isinstance(x, dict):
        for k in ("name", "label", "ssid", "ifname", "description", "id"):
            if x.get(k):
                return str(x[k])
        return json.dumps(x, default=str)[:64]
    return str(x)


def _detail_of(x: Any) -> str | None:
    if isinstance(x, dict):
        for k in ("id", "vendor", "address", "state", "value"):
            if x.get(k) and str(x[k]) != _label_of(x):
                return str(x[k])
    return None


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------

def _rows_of(b: dict[str, Any]) -> int:
    t = b["type"]
    if t == "table":
        return len(b["rows"])
    if t in ("kv", "list"):
        return len(b["items"])
    return 1


def _slice(b: dict[str, Any], start: int, stop: int) -> dict[str, Any]:
    t = b["type"]
    if t == "table":
        cols = [(c["key"], c["label"]) for c in b["columns"]]
        return table(cols, b["rows"][start:stop])
    if t == "kv":
        items = b["items"][start:stop]
        return {"type": "kv", "items": items,
                "text": "\n".join(f"{i['key']}: {'' if i['value'] is None else i['value']}" for i in items)}
    if t == "list":
        return list_block(b["items"][start:stop])
    return b


def paginate(blocks: list[dict[str, Any]], page_size: int) -> tuple[list[list[dict[str, Any]]], int]:
    """Split blocks into pages of at most ``page_size`` rows. Returns (pages, total rows)."""
    pages: list[list[dict[str, Any]]] = [[]]
    used = 0
    total = 0
    for b in blocks:
        n = _rows_of(b)
        total += n
        start = 0
        while start < n or (n == 0 and start == 0):
            room = page_size - used
            if room <= 0:
                pages.append([])
                used, room = 0, page_size
            take = min(room, n - start) if n else 0
            if n <= 1 or (start == 0 and take == n):
                pages[-1].append(b)
                used += max(n, 1)
                break
            pages[-1].append(_slice(b, start, start + take))
            used += take
            start += take
    if not pages[-1] and len(pages) > 1:
        pages.pop()
    return pages, total
