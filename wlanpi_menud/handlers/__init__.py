"""First-party Python action handlers, registered by dotted name.

A manifest references one with ``"action": {"handler": "network.adapter_summary"}``.
Handlers live in the wlanpi-menud tree only; third parties use providers.
"""

from __future__ import annotations

from .base import HANDLERS, HandlerContext, HandlerResult, PromptCancelled, get_handler, handler, handler_exists

# Importing the modules registers their handlers.
from . import apps, network, system, utils  # noqa: E402,F401

__all__ = ["HANDLERS", "HandlerContext", "HandlerResult", "PromptCancelled", "get_handler", "handler", "handler_exists"]
