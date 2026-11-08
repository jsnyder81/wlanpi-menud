# Upgrade and rollback plan

Phase 0 deliverable: how wlanpi-menud, wlanpi-menu-tui, the WebUI plugin and the panel opt-in are upgraded and rolled back, on both package upgrades and A/B image upgrades.

## Assumptions about the image

The plan gives one fact about the A/B layout: **`/home` persists across slots and `/var` does not.** This plan also assumes the following; the image maintainers should confirm or correct each one:

| Path | Assumed behaviour on a slot switch |
| --- | --- |
| `/usr`, `/opt` | Per slot. The new slot carries its own packages. |
| `/etc` | Per slot, taken from the new image. Local edits are not carried over unless the image tooling copies them. |
| `/var` (logs, `/var/lib`, `/var/www/html/profiler`) | Per slot, not carried over (the plan states this) |
| `/home` (including `/home/wlanpi`) | Persistent and shared by both slots (the plan states this) |
| `/run` | tmpfs, empty on every boot |
| Rollback | Boot the other slot, by hand or after a failed boot health check |

Package upgrades (`apt upgrade` within the running slot) are the other path, and they behave as normal Debian upgrades.

## Where wlanpi-menud keeps things

The design rule: **nothing the service needs to start lives anywhere that does not come with the slot, and nothing the user would miss lives anywhere that does not persist.**

| What | Path | Persists across slots | Notes |
| --- | --- | --- | --- |
| Code and virtualenv | `/opt/wlanpi-menud` (dh_virtualenv) | No, per slot | |
| Built-in manifests | `/usr/share/wlanpi-menud/menu.d/` | No, per slot | Always match the code in the same slot |
| Plugin manifests | `/usr/share/wlanpi-menud/plugins.d/<name>/` | No, per slot | Shipped by third-party `.deb`s |
| Service config and local overrides | `/etc/wlanpi-menud/` | No (assumed) | Defaults only. See "user state" below. |
| User state: local manifest overrides, panel shortcuts, role assignments, provider allowlist edits | `/home/wlanpi/.config/wlanpi-menud/` | **Yes** | Read after `/etc`, so it wins. Versioned files (see "data format rules"). |
| Core token | Memory only | n/a | Fetched from `/usr/libexec/wlanpi-menud/get-core-token` at start; nothing to migrate or leak |
| Sessions, jobs, cursors, caches | Memory only | n/a | A restart is a new `epoch`, and clients resnapshot |
| Sockets | `/run/wlanpi-menud/{local,proxy}.sock` | n/a | Created by the systemd unit (`RuntimeDirectory=`) |
| Audit log (WebUI user per action) | journald | No | Losing it on a slot switch is acceptable. Persist it only if the team asks. |
| Panel opt-in | `/etc/wlanpi-fpms.conf` `menu_backend` | No (assumed) | See "panel" below |

The service writes nothing under `/var`, and nothing it needs at start lives under `/home`. A missing or unreadable `~/.config/wlanpi-menud/` means "no local changes", never a failed start.

## Data format rules (anything under `/home`)

After a rollback, older code reads data that newer code wrote. So:

1. Every persisted file carries a format version (`"schema": 1` for manifests, `"version": n` for state files).
2. Changes are additive within a version. Unknown fields are ignored on read and kept on write.
3. A breaking change writes a **new file** (`shortcuts.v2.json`) alongside the old one, and keeps writing the old one for one release. It never rewrites the old file in place.
4. A file a version cannot parse is skipped with a logged error naming the file and field (the same rule as manifests). It is never deleted.

## Compatibility between components

Everything that might be at a different version within one slot is negotiated, not assumed:

| Pair | Mechanism | On mismatch |
| --- | --- | --- |
| Surface and service | `hello.protocol` / `welcome.version` | `error VERSION`, close 4426. The surface shows "update needed". The service supports protocol N and N-1 for at least one release. |
| Manifest and service | Manifest `"schema"` | A file with an unknown schema version is rejected, with an error naming the file. Other files still load. |
| Service and core | Client generated from core's `openapi.json`; `requires_core` per node; the package declares `Depends: wlanpi-core (>= <pinned>)` | Nodes whose endpoint is missing are hidden. Contract tests in CI catch a changed shape before release. |
| Service and core token | Reserved `did=wlanpi-menud`; re-mint on 401 or `AUTH_CLOCK_NOT_SET` | A slot that boots offline with a stale clock recovers after NTP, not on a reboot |

## Package upgrade (within a slot)

1. `apt` installs the new `.deb`. `postinst` validates the shipped manifests (`wlanpi-menud --check`) **before** restarting the unit. If they fail, the old service keeps running and the postinst fails loudly.
2. `systemctl try-restart wlanpi-menud`. Clients see close 1001 and reconnect. The new epoch triggers a full snapshot, so no client state survives that should not.
3. Plugin packages call `wlanpi-menud reload` in their own `postinst`. A bad plugin manifest is rejected by itself and named in the log; the built-in menu is unaffected.

**Rollback:** `apt install wlanpi-menud=<previous>` (packagecloud keeps old versions). The `/home` data rules make that safe. Removing the package entirely is also a rollback for Phases 1 and 2: FPMS never depended on it.

## Image upgrade (A/B)

1. The new slot boots with its own wlanpi-core, wlanpi-menud, TUI, WebUI and FPMS, all released together and contract-tested together.
2. `~/.config/wlanpi-menud/` is read as-is. Older or newer state files follow the data format rules above.
3. Third-party plugin `.deb`s installed in the old slot **are not present** in the new slot, since `/usr` and `/var/lib/dpkg` are per slot. Their nodes simply do not appear. Open question for the image owners: does the upgrade tool replay a package list saved under `/home`? Until it does, the release notes must say so.
4. Local edits in `/etc/wlanpi-menud/` are lost unless the image tooling carries `/etc` over. That is why user state goes under `/home/wlanpi/.config/`.

**Rollback:** boot the other slot. Everything in the old slot is unchanged, and `/home` state is readable by the older code under the data format rules.

### Boot health check

- Phases 1 and 2: wlanpi-menud is **not** part of the image's boot health check. Nothing depends on it, so a failing service must not roll back the whole image.
- Phase 3 (panel opt-in): still not part of it. The panel uses `Wants=wlanpi-menud.service`, and if the service is down it shows a local "service unavailable" screen with the IP address and keeps reconnecting.
- Only when the panel default flips to `wlanpi-menud` should the team decide whether "panel shows a menu within N seconds of boot" joins the health check. If it does, the legacy backend must still be present in that image, so the fallback is a setting flip, not a slot rollback.

## Panel opt-in (Phase 3)

- `menu_backend = legacy | wlanpi-menud` in `/etc/wlanpi-fpms.conf`. Rollback is flipping it back and restarting `wlanpi-fpms`.
- Because `/etc` is assumed per slot, an opt-in does not follow an image upgrade, and a new image reverts to its own default. That is the safe direction while the panel is opt-in. Once the default flips, the new image's default is `wlanpi-menud`, and an owner who wants legacy sets it again. Persist the choice under `/home` only if the team wants opt-ins to survive image upgrades.
- The legacy menu is frozen while both exist, so rolling back to it never loses a fix.

## Release checklist

- [ ] Manifests validate in CI and in `postinst` before restart.
- [ ] Contract tests pass against the pinned wlanpi-core.
- [ ] Protocol N-1 surfaces still connect (TUI and WebUI from the previous release).
- [ ] Downgrade test: install the release, write `~/.config/wlanpi-menud/` state, reinstall the previous release, and confirm it starts and reads that state.
- [ ] A/B test: upgrade the image, roll back by slot, and confirm FPMS, the service and the TUI start in both slots.
- [ ] Offline boot with a stale clock: the service re-mints its token after NTP and recovers without a restart.
- [ ] Removing the `wlanpi-menud` package leaves FPMS working and removes the sudoers entry and the root helper.
