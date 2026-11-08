# wlanpi-menud

Headless menu service for the WLAN Pi. It owns the menu, sessions and jobs;
surfaces (TUI, WebUI, front panel, mobile) connect over one authenticated
WebSocket and render render-neutral view blocks. Every device operation goes
to wlanpi-core. See `FPMS Re-Architecture Plan.md` for the design and
`protocol/` for the contract.

## Layout

| Path | What |
| --- | --- |
| `protocol/` | Contract: `menu.schema.json`, `frames.schema.json`, `PROTOCOL.md`, samples and their tests |
| `share/menu.d/` | Built-in menu manifests (installed to `/usr/share/wlanpi-menud/menu.d/`) |
| `wlanpi_menud/loader.py`, `manifest.py`, `menu.py` | Manifest validation and loading, per-session visibility |
| `wlanpi_menud/nav.py` | Pure thin-mode navigator; one `Cursor` per session |
| `wlanpi_menud/jobs.py` | Jobs: sharing and caching, prompts, confirmations, cancel, paging, core error mapping |
| `wlanpi_menud/handlers/` | First-party Python handlers, registered by dotted name |
| `wlanpi_menud/core/` | Core client: `generated.py` (from core's `docs/openapi.json`), HTTPS transport with a pinned cert, service token |
| `wlanpi_menud/server.py`, `service.py`, `session.py`, `outqueue.py`, `topics.py`, `views.py` | Sockets, auth, sessions, bounded queues, topics, thin views |
| `wlanpi_menud/poller.py` | The one polling loop (publishes only on change) |
| `install/` | Root token helper, sudoers entry, systemd unit, example config |
| `tests/` | pytest suite with a fake core (`tests/fakecore.py`, respx) |
| `demo/` | Two concurrent thin clients navigating independently, with captured output |
| `tools/gen_core_client.py` | Regenerates `wlanpi_menud/core/generated.py` from a wlanpi-core checkout |
| `docs/` | Core audit, parity list, PoC action map, resource budget, upgrade and rollback |

## Runtime

- `local.sock` (`/run/wlanpi-menud/local.sock`): `SO_PEERCRED` uid/gid
  allowlist (root is the panel, the `wlanpi` user, the `wlanpi-menud` group,
  provider uids). No auth frame.
- `proxy.sock` (mode 0660, nginx's group): nginx `auth_request` headers
  (`X-WebUI-User`, `X-WebUI-Session`) or a core JWT in the first frame
  (checked with core introspection, cached at most 60 s or until `exp`).
  `?token=` is refused; `Origin` must match the host.
- No TCP listener.
- Core: `https://127.0.0.1:31415/api/v1`, device certificate pinned, reserved
  `did=wlanpi-menud` token from `sudo -n /usr/libexec/wlanpi-menud/get-core-token`,
  held in memory, refreshed before `exp`, re-minted on 401 and
  `AUTH_CLOCK_NOT_SET`.
- `systemctl reload wlanpi-menud` (SIGHUP) reloads manifests;
  `wlanpi-menud --check` validates them and exits.

## Develop

```sh
uv venv --python 3.13
uv pip install -e '.[test]'
.venv/bin/pytest
.venv/bin/python demo/two_thin_clients.py
```

Regenerate the core client after moving the pinned core version:

```sh
git clone https://github.com/WLAN-Pi/wlanpi-core /tmp/wlanpi-core
.venv/bin/python tools/gen_core_client.py /tmp/wlanpi-core
```
