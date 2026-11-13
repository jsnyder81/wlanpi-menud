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
| `wlanpi_menud/nav.py` | Pure thin-mode navigator; one `Cursor` per session (the default session's is shared by every attached connection) |
| `wlanpi_menud/jobs.py` | Jobs: sharing and caching, prompts, confirmations, cancel, paging, core error mapping |
| `wlanpi_menud/handlers/` | First-party Python handlers, registered by dotted name |
| `wlanpi_menud/core/` | Core client: `generated.py` (from core's `docs/openapi.json`), HTTPS transport with a pinned cert, service token |
| `wlanpi_menud/server.py`, `service.py`, `session.py`, `outqueue.py`, `topics.py`, `views.py` | Sockets, auth, the default session and independent sessions (attachments, fan-out), bounded queues, topics, thin views |
| `wlanpi_menud/poller.py` | The one polling loop (publishes only on change) |
| `install/` | Root token helper, sudoers entry, systemd unit, example config (installed by `debian/`) |
| `debian/`, `build-package-native.sh`, `Dockerfile.build` | Debian packaging (dh-virtualenv) and the container build |
| `tests/` | pytest suite with a fake core (`tests/fakecore.py`, respx) |
| `demo/` | Two concurrent thin clients navigating independently, with captured output |
| `tools/gen_core_client.py` | Regenerates `wlanpi_menud/core/generated.py` from a wlanpi-core checkout |
| `docs/` | Core audit, parity list, PoC action map, resource budget, upgrade and rollback |

## Runtime

- `local.sock` (`/run/wlanpi-menud/local.sock`): `SO_PEERCRED` uid/gid
  allowlist (root is the panel, the `wlanpi` user, the `wlanpi-menud` group,
  provider uids). No auth frame. Any local admin UID on `local.sock`
  (including `wlanpi`) may revoke WebUI menu connections for a session id;
  this is DoS-only with no privilege gain on a single-user device, and the
  default session is never ended by revoke.
- `proxy.sock` (mode 0660, nginx's group): nginx `auth_request` headers
  (`X-WebUI-User`, `X-WebUI-Session`) or a core JWT in the first frame
  (checked with core introspection, cached at most 60 s or until `exp`).
  `?token=` is refused; `Origin` must match the host.
- No TCP listener.
- Core: `https://127.0.0.1:31415/api/v1`, device certificate pinned, reserved
  `did=wlanpi-menud` token from `sudo -n /usr/libexec/wlanpi-menud/get-core-token`,
  held in memory, refreshed before `exp`, re-minted on 401 and
  `AUTH_CLOCK_NOT_SET`.
- `systemctl reload wlanpi-menud` (SIGHUP) reloads manifests and complication declarations in `plugins.d/` (no dpkg trigger is configured today);
  `wlanpi-menud --check` validates them and exits.

## Installation

`wlanpi-menud` ships as a Debian package built with dh-virtualenv:

```sh
sudo apt install ./wlanpi-menud_<version>_arm64.deb
systemctl status wlanpi-menud
```

| Path | What |
| --- | --- |
| `/opt/wlanpi-menud/` | Private Python 3.13 virtualenv |
| `/usr/bin/wlanpi-menud` | Link to the venv entry point (`--check` validates manifests) |
| `/etc/wlanpi-menud/config.toml` | Config (conffile; every key optional) |
| `/etc/wlanpi-menud/menu.d/` | Local manifest overrides |
| `/usr/share/wlanpi-menud/menu.d/`, `plugins.d/`, `protocol/` | Built-in manifests, plugin manifests, schemas |
| `/usr/lib/systemd/system/wlanpi-menud.service` | Runs as `wlanpi`, group `wlanpi-menud`, sandboxed |
| `/run/wlanpi-menud/` | 0750 `wlanpi:wlanpi-menud`, plus a traverse-only ACL for `www-data`; holds `local.sock` (0660, group `wlanpi-menud`) |
| `/run/wlanpi-menud/proxy/` | 2750 `wlanpi:www-data` (setgid, made by `tmpfiles.d` as root); holds `proxy.sock` (0660, group `www-data` by inheritance). nginx proxies `/ui/ws` here. |
| `/usr/libexec/wlanpi-menud/get-core-token`, `/etc/sudoers.d/wlanpi-menud` | Root token helper; only `wlanpi` may run it, only without arguments |

The package creates the `wlanpi-menud` group. Add SSH users who run the TUI
to it (`sudo usermod -aG wlanpi-menud <user>`); `wlanpi` itself connects as the
socket's owner. Purge removes the group unless users were added to it. It binds no TCP port, touches
no hardware and shares no files with `wlanpi-fpms`, so both run side by side.
`postinst` runs `wlanpi-menud --check` and will not restart the service on
manifests that fail validation.

## Building the Package

```sh
./build-package-native.sh        # Debian trixie container (podman or docker), runs lintian
ls dist/                         # wlanpi-menud_<version>_<arch>.deb, lintian.txt
```

Or natively on trixie with `build-essential debhelper dh-virtualenv python3-venv`
installed: `dpkg-buildpackage -us -uc -b`.

## Releases

Versions live in `debian/changelog` and match `pyproject.toml`. Add an entry with:

```sh
export DEBFULLNAME="Your Name" DEBEMAIL="you@example.com"
dch -i            # or: dch -v <version>
```

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
