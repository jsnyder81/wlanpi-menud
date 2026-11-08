"""Service configuration.

Defaults match the installed layout. Every path can be overridden, from
``/etc/wlanpi-menud/config.toml`` in production or directly in tests.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_FILE = Path("/etc/wlanpi-menud/config.toml")


@dataclass(frozen=True)
class Config:
    # Manifests: built-ins, plugin sets (one directory per plugin), local overrides.
    builtin_dir: Path = Path("/usr/share/wlanpi-menud/menu.d")
    plugins_dir: Path = Path("/usr/share/wlanpi-menud/plugins.d")
    override_dir: Path = Path("/etc/wlanpi-menud/menu.d")

    # Sockets. There is deliberately no TCP listener.
    local_sock: Path = Path("/run/wlanpi-menud/local.sock")
    proxy_sock: Path = Path("/run/wlanpi-menud/proxy.sock")
    local_sock_mode: int = 0o660
    proxy_sock_mode: int = 0o660
    local_sock_group: str | None = "wlanpi-menud"
    proxy_sock_group: str | None = "www-data"  # nginx's group on Debian

    # SO_PEERCRED allowlist for local.sock: uid or gid -> roles.
    # Root is the panel (admin). The wlanpi user and the wlanpi-menud group
    # are resolved by name at start-up when the numeric maps are empty.
    local_uids: dict[int, tuple[str, ...]] = field(default_factory=lambda: {0: ("admin",)})
    local_gids: dict[int, tuple[str, ...]] = field(default_factory=dict)
    local_user_names: dict[str, tuple[str, ...]] = field(default_factory=lambda: {"wlanpi": ("admin",)})
    local_group_names: dict[str, tuple[str, ...]] = field(default_factory=lambda: {"wlanpi-menud": ("operator",)})
    provider_uids: tuple[int, ...] = ()

    # proxy.sock: roles for WebUI sessions (nginx auth_request) and JWT clients.
    webui_roles: tuple[str, ...] = ("admin",)
    jwt_roles: tuple[str, ...] = ("admin",)
    allowed_origins: tuple[str, ...] = ()  # empty: Origin host must equal Host header
    auth_timeout_sec: float = 10.0
    introspect_path: str = "/auth/introspect"
    introspect_cache_sec: float = 60.0

    # wlanpi-core.
    core_base_url: str = "https://127.0.0.1:31415"
    core_api_prefix: str = "/api/v1"
    core_cert: Path | None = Path("/etc/nginx/ssl/self-signed-wlanpi.cert")
    token_command: tuple[str, ...] = ("sudo", "-n", "/usr/libexec/wlanpi-menud/get-core-token")
    token_refresh_margin_sec: float = 300.0
    core_timeout_sec: float = 10.0
    openapi_snapshot: Path | None = None  # None: the vendored route table

    # Sessions, queues and paging.
    sessions_per_socket: int = 16
    queue_frames: int = 64
    queue_bytes: int = 1 << 20
    max_frame_bytes: int = 256 * 1024
    page_size: int = 20
    resume_grace_sec: float = 120.0
    prompt_expires_sec: int = 120

    # Polling loop.
    poll_interval_sec: float = 5.0
    reachability_interval_sec: float = 60.0
    job_retention_sec: float = 600.0

    # Static device facts the service cannot learn from core.
    has_display: bool = False
    has_gpio_buttons: bool = False

    # Development aid: validate every outbound frame against frames.schema.json.
    validate_outbound: bool = False

    def with_(self, **kwargs: Any) -> "Config":
        return replace(self, **kwargs)


_PATHS = {f.name for f in fields(Config) if "Path" in str(f.type)}
_TUPLES = {f.name for f in fields(Config) if str(f.type).startswith("tuple")}


def load_config(path: Path = DEFAULT_CONFIG_FILE) -> Config:
    """Read ``config.toml``; unknown keys are an error, a missing file means defaults."""
    if not path.exists():
        return Config()
    raw = tomllib.loads(path.read_text())
    known = {f.name for f in fields(Config)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"{path}: unknown keys {sorted(unknown)}")
    kwargs: dict[str, Any] = {}
    for key, value in raw.items():
        if key in _PATHS:
            value = Path(value) if value else None
        elif key in _TUPLES:
            value = tuple(value)
        elif key in ("local_uids", "local_gids"):
            value = {int(k): tuple(v) for k, v in value.items()}
        elif key in ("local_user_names", "local_group_names"):
            value = {k: tuple(v) for k, v in value.items()}
        kwargs[key] = value
    return Config(**kwargs)
