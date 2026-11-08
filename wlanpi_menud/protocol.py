"""Frame validation and builders for protocol v1 (protocol/frames.schema.json)."""

from __future__ import annotations

import copy
import re
from typing import Any

from jsonschema import Draft202012Validator

from .manifest import load_schema

VERSION = 1
SUPPORTED = (1,)
C2S = ("auth", "hello", "subscribe", "unsubscribe", "input", "invoke", "answer", "page", "cancel", "revoke")
S2C = ("welcome", "menu", "view", "prompt", "job", "event", "error")

FATAL_CLOSE = {"AUTH_FAILED": 4401, "VERSION": 4426}

_MSG_ID = re.compile(r"^[A-Za-z0-9:_\-.]{1,64}$")


def reply_id(frame: Any, default: str = "s:0") -> str:
    """The id to answer a (possibly malformed) frame with."""
    mid = frame.get("id") if isinstance(frame, dict) else None
    return mid if isinstance(mid, str) and _MSG_ID.match(mid) else default


_validators: dict[str, Draft202012Validator] = {}
_any: Draft202012Validator | None = None


def _validator(type_: str) -> Draft202012Validator:
    if type_ not in _validators:
        schema = copy.deepcopy(load_schema("frames.schema.json"))
        sub = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": f"#/$defs/{type_}"}
        _validators[type_] = Draft202012Validator(sub)
    return _validators[type_]


def frame_errors(frame: Any, allowed: tuple[str, ...]) -> list[str]:
    """Validate a frame against the $def for its type. Returns readable errors."""
    if not isinstance(frame, dict):
        return ["frame must be a JSON object"]
    t = frame.get("type")
    if t not in allowed:
        return [f"unknown frame type {t!r}"]
    errs = sorted(_validator(t).iter_errors(frame), key=lambda e: list(e.absolute_path))
    return [f"/{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errs]


def validate_any(frame: dict[str, Any]) -> list[str]:
    """Validate against the schema's top-level oneOf (what a surface would do)."""
    global _any
    if _any is None:
        _any = Draft202012Validator(load_schema("frames.schema.json"))
    return [e.message for e in _any.iter_errors(frame)]


def error(msg_id: str, code: str, message: str, *, close_code: int | None = None, **extra: Any) -> dict[str, Any]:
    f: dict[str, Any] = {"v": VERSION, "type": "error", "id": msg_id, "code": code, "message": message[:512]}
    if code in FATAL_CLOSE:
        close_code = FATAL_CLOSE[code]
    if close_code is not None:
        f["fatal"] = True
        f["close_code"] = close_code
    f.update(extra)
    return f
