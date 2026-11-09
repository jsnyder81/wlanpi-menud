# Phase 1 Deliverables & Cross-Agent Integration Review (`wlanpi-menud` + `wlanpi-menu-tui`)

**Date:** 2026-11-08  
**Targets:**
- `wlanpi-menud` (`7f34921`, branch `phase1/service`)
- `wlanpi-menu-tui` (`20e25fe`, branch `packaging/debian`)
- Evaluated against `FPMS Re-Architecture Plan.md` (Phase 1 exit criteria & February release readiness), `wlanpi-menud/protocol/PROTOCOL.md`, `wlanpi-menud/protocol/frames.schema.json`, and `wlanpi-menud/protocol/menu.schema.json`

**Verdict:** **REQUEST-CHANGES**

| Severity | Count | Summary |
| --- | --- | --- |
| **Blocker** | **0** | None — wire protocol, framing, auth rules, manifests, core client, and packaging work end-to-end across both repos. |
| **Major** | **2** | Cross-agent integration drift in `wlanpi-menu-tui`: (M1) `job` vs. `view` frame ordering overwrites `display_mode = "view"`, disabling `c`/`Escape` job cancellation against the real service; (M2) `complications` topic payload shape mismatch (`label`/`value` vs. `key`/`text`) prevents complications from rendering in the TUI footer. |
| **Minor** | **4** | (m1) TUI `prompt` confirm defaults to `"confirm"` instead of `"cancel"`; (m2) `wlanpi_menu_tui/qr.py` renders a non-ISO/IEC 18004 pseudo-QR grid; (m3) untyped `event.data` in `frames.schema.json` allowed `stub_server.py` topic payload drift; (m4) packaging deviations in `wlanpi-menud.service` (`RuntimeDirectoryMode=0755` + `SupplementaryGroups=www-data` and implicit `NoNewPrivs=0` reliance under seccomp hardening for the `sudo` token helper). |

---

## 1. Phase 1 Exit Criteria Verification

### Criterion (1): Every menu item whose core endpoint exists is runnable from the TUI
**Status:** **PASS (45/45 runnable action nodes verified live)**

- **Manifest inventory (`wlanpi-menud/share/menu.d/{10-network,20-bluetooth,30-utils,35-modes,40-apps,50-system}.json`):**
  - **76 total nodes**: **18 branches** and **58 action nodes**.
  - **13 action nodes** are gated by `visibility.requires_core` for `wlanpi-core` endpoints not present in `wlanpi-core` `2.4.13` (`apps.scanner.csv`, `apps.scanner.pcap_start`, `apps.scanner.pcap_stop`, `modes.classic`, `modes.hotspot`, `modes.server`, `modes.bridge`, and `utils.cloud.{arista,aruba,extreme,meraki,mist,ruckus}`), matching `wlanpi-menud/docs/parity-list.md`.
  - **45 action nodes** have existing `wlanpi-core` endpoints (or local handlers backed by existing endpoints): **44 visible in `classic` mode** plus **1 visible in non-classic modes** (`utils.ssid_passphrase`, `not_modes: ["classic"]`).
- **End-to-end TUI execution:**
  - Connected the real `MenuTuiApp` (`wlanpi-menu-tui/wlanpi_menu_tui/app.py`) to the real `wlanpi-menud` `Server` + `Service` (`wlanpi-menud/wlanpi_menud/server.py`, `service.py`) over a temporary `local.sock` and navigated/invoked all 44 `classic`-mode action nodes via thin-mode key presses (`up`/`down`/`left`/`enter`) plus `utils.ssid_passphrase` after switching `FakeCore` to `hotspot` mode and running `Poller.poll_once()`.
  - Verified all 45 action nodes complete with valid result/view frames, including:
    - Multi-step parameter prompts: `system.timezone.set` (`Region` select prompt -> `Zone` select prompt -> `POST /system/timezone/set`) and `system.reg_domain.set` (`Country` select prompt -> `RF Domain` confirm prompt -> `POST /system/reg-domain/set`).
    - Destructive action confirmations: `system.reboot`, `system.shutdown`, `apps.profiler.purge`.
    - Multi-page table results: `apps.scanner.scan` (45 networks across 3 pages of 20 rows).
    - QR + secret-masked key/value blocks: `utils.ssid_passphrase` and `apps.profiler.status` (`Passphrase` masked as `••••••••` in initial view; `s3cret-pass` never leaked in `event` topics).

### Criterion (2): Two concurrent sessions navigate independently
**Status:** **PASS**

- **Architecture & unit coverage:**
  - `Session` (`wlanpi-menud/wlanpi_menud/session.py:55-97`) owns an independent `Cursor` (`wlanpi-menud/wlanpi_menud/nav.py:29-42`: `screen`, `stack`, `index`, `job_id`, `page`, `reveal`, `prompt_id`, `prompt_index`), `MenuView`, topic subscription map, and 64-frame bounded outbound queue.
  - Verified by `wlanpi-menud/tests/test_sessions_and_nav.py` and `wlanpi-menud/demo/two_thin_clients.py`.
- **Live two-TUI verification:**
  - Ran two concurrent `MenuTuiApp` instances (`app1`, `app2`) connected simultaneously to one `wlanpi-menud` `Server` over `local.sock`.
  - Confirmed distinct session IDs (`s1 != s2`), independent cursor stacks (`s1` navigated `Home -> Network -> Interfaces` to `screen="result"`, `stack=('network',)` while `s2` remained on `screen="home"`, `stack=()`, then navigated independently to `screen="menu"`, `stack=('utils',)`), and strict prompt isolation (invoking `system.reboot` on `s1` delivered the `confirm` prompt exclusively to `s1` while `s2.active_prompt` remained `None`).

### Criterion (3): Cross-Agent Live Integration & Schema Validation
**Status:** **PARTIAL PASS (100% schema-valid wire frames; 2 Major and 3 Minor behavioral drifts identified)**

- **Live integration setup:**
  - Pointed the real `MenuTuiApp` at the real `wlanpi-menud` `Server` over a temporary `local.sock` with a schema-validation spy wrapping `MenuTuiApp._send_frame` and `MenuTuiApp._handle_server_frame` using `Draft202012Validator(frames.schema.json)`, plus `Config(validate_outbound=True)` on the server.
- **Wire & schema conformance:**
  - **593 Client-to-Server (C2S) frames** and **788 Server-to-Client (S2C) frames** were exchanged across the full-menu run with **0 JSON Schema errors**.
  - Handshake (`hello` -> `welcome` + `menu` + initial `view`), topic subscriptions (`device.summary`, `jobs`, `complications`, `alerts`), thin navigation (`input`), rich invocation (`invoke`), prompts (`prompt` -> `answer`), paging (`page`), secret reveal (`key1`), and session resume (`hello.resume` with matching `epoch` and `seqs` -> `welcome.resumed=True` with delta replay; mismatched `epoch` -> `welcome.resumed=False` with full snapshot reset) all functioned over the real Unix domain socket.
- **Cross-agent protocol drift findings:**
  - Because `wlanpi-menu-tui` was developed against `wlanpi-menu-tui/tests/stub_server.py` rather than the live `wlanpi-menud`, two **Major** behavioral drifts and three **Minor** issues were uncovered during live integration:
    1. **Major M1 (`job` + `view` dual-emission overwrites `display_mode` in `MenuTuiApp`):** The real service emits *both* a `job` frame and a thin-mode `view` frame on job transitions (`wlanpi-menud/wlanpi_menud/jobs.py:229-233`, `service.py:344-363`), whereas `stub_server.py` emits only one or the other. Because `view` arrives immediately after `job`, `MenuTuiApp._handle_server_frame` (`wlanpi-menu-tui/wlanpi_menu_tui/app.py:297-335`) sets `display_mode = "job"` and then immediately overwrites it with `display_mode = "view"`. This renders `c`/`Escape` job cancellation (`app.py:779, 799-802`) unreachable against the real service and splits result paging between thin `input` and `page`.
    2. **Major M2 (`complications` topic item shape drift):** `wlanpi-menud` publishes `{"app_id", "label", "value", "status"}` (`wlanpi-menud/wlanpi_menud/service.py:367-379`), while `MenuTuiApp._render_status_footer` (`wlanpi-menu-tui/wlanpi_menu_tui/app.py:682-693`) only checks `comp.get("text")` or `comp.get("key") and comp.get("value")` (matching `stub_server.py:174-181`). Complications published by the real service never appear in the TUI footer.
    3. See **Section 2 (Findings)** below for full root-cause analysis, `file:line` references, and recommended fixes.

### Criterion (4): Auth & Security Rules Hold
**Status:** **PASS**

- **`SO_PEERCRED` on `local.sock` (`wlanpi-menud/wlanpi_menud/auth.py:42-94`, `server.py:87-105`, `service.py:194-205`):**
  - `peercred_from_writer` reads `socket.SO_PEERCRED` (`<iii` -> `pid, uid, gid`) from the kernel Unix domain socket.
  - Mapped to `Principal(user=..., role=..., source="peercred")` via `config.local_uids`/`local_gids` (defaulting `root` and `wlanpi` to `admin`, `wlanpi-menud` group to `operator`). Unmapped UIDs/GIDs are rejected with `AUTH_FAILED` (`close_code=4401`). Any `auth` frame sent on `local.sock` is rejected as `BAD_REQUEST` (`service.py:227-228`).
- **Login-first on `proxy.sock` (`wlanpi-menud/wlanpi_menud/auth.py:97-179`, `service.py:194-243`):**
  - Connections on `proxy.sock` accept either nginx-verified `X-Forwarded-User` / `X-Forwarded-Role` headers (`authenticate_proxy_headers`) or require the very first frame within `auth_timeout_sec` (`5.0s`, `server.py:143-155`) to be `{"type": "auth", "token": "<jwt>"}`.
  - Any frame before `auth` on an unauthenticated proxy connection is rejected immediately with `AUTH_FAILED` (`fatal=True`, `close_code=4401`, `service.py:212-214`).
  - `verify_core_token` verifies the JWT signature against `wlanpi-core` (`GET /system/device/model` with `Authorization: Bearer <token>`), enforces a 5-minute revocation/expiry cache (`TokenVerifier`, `auth.py:132-179`), and re-checks token validity before every `invoke` (`service.py:296-299`).
- **No TCP listener (`grep` verification across both repositories):**
  - Grepping `wlanpi-menud/wlanpi_menud` and `wlanpi-menu-tui/wlanpi_menu_tui` for `bind(`, `listen(`, `start_server`, `start_unix_server`, `0.0.0.0`, `127.0.0.1`, and `AF_INET` confirms:
    - `wlanpi-menud/wlanpi_menud/server.py:87-96` calls only `websockets.unix_serve(..., path=str(path))` on `config.local_sock` and `config.proxy_sock`.
    - `127.0.0.1` appears solely as the outbound `httpx` client destination `core_base_url: str = "https://127.0.0.1:31415"` (`wlanpi-menud/wlanpi_menud/config.py:50`, `core/client.py:3-6`).
    - `wlanpi-menu-tui/wlanpi_menu_tui/client.py:72` calls only `websockets.unix_connect(path=...)`.
- **Tokens never in URLs (`wlanpi-menud/wlanpi_menud/server.py:66-79, 121-125`, `core/client.py:130-145`):**
  - `_path_has_query_token` (`server.py:66-79`) inspects the WebSocket request path/query string on handshake and immediately rejects any URL containing `?token=`, `access_token`, `jwt`, or `bearer` with `AUTH_FAILED` (`close_code=4401`) before reading any frame.
  - Outbound `CoreClient.request` (`core/client.py:130-145`) passes tokens strictly in the HTTP `Authorization: Bearer <token>` header and strips `Authorization` from debug logs (`core/client.py:26-29`).
- **Secrets never shareable, cached, or broadcast (`wlanpi-menud/wlanpi_menud/blocks.py:25-33, 62-75`, `jobs.py:181-189, 254-264`, `views.py:114-129`):**
  - Handler results containing `secret_keys` (`utils.ssid_passphrase` in `handlers/utils.py:18`, `apps.profiler.status` in `handlers/apps.py:19`) set `has_secrets=True` on the `Job` (`jobs.py:181-185`), which forces `shareable = False` and skips `_fresh` caching (`jobs.py:187-189, 254-264`), prevents cross-session attachment (`jobs.py:209`), masks secret values with `••••••••` unless `session.cursor.reveal=True` on that specific session (`views.py:114-129`, auto-cleared on any non-`key1` key in `nav.py:112` or after 10s TTL), and omits result blocks from `event` frames on the `jobs` topic (`jobs.py:212-218`).
- **Confirmations bound to invoking session & principal (`wlanpi-menud/wlanpi_menud/jobs.py:269-271, 336-380`):**
  - Thin-mode sessions cannot bypass confirmation via `invoke(..., confirmed=True)`: `jobs.py:270` enforces `already_confirmed = bool(confirmed and session.mode == "rich")`.
  - Every `Prompt` records `session_id = owner.id` and `principal_key = owner.principal.key` (`jobs.py:345-346`), and `jobs.answer` / `jobs.cancel_prompt` (`jobs.py:354-380`) reject any response where `p.session_id != session.id or p.principal_key != session.principal.key` with `InvokeRefused("FORBIDDEN", "Prompt belongs to another session")`.

### Criterion (5): Packaging & Coexistence with `wlanpi-fpms`
**Status:** **PASS (with 2 scrutinized packaging deviations recorded in Minor m4)**

- **`wlanpi-menud` Debian package (`wlanpi-menud/debian/*`, `wlanpi-menud/install/*`, `wlanpi-menud/dist/wlanpi-menud_0.1.0_arm64.deb`):**
  - Built with `dh-virtualenv` (`debian/rules:19-30`) into `/opt/wlanpi-menud` using Python 3.13 (`Pre-Depends: dpkg (>= 1.16.1), python3 (>= 3.13)`).
  - Runs as `User=wlanpi` and `Group=wlanpi-menud` in `wlanpi-menud/install/lib/systemd/system/wlanpi-menud.service:9-10`, with `RuntimeDirectory=wlanpi-menud`, `MemoryMax=96M`, `PrivateDevices=yes`, `ProtectSystem=strict`, and the root token helper `/usr/libexec/wlanpi-menud/get-core-token` scoped in `install/etc/sudoers.d/wlanpi-menud:5-6` (`Defaults!/usr/libexec/wlanpi-menud/get-core-token !requiretty` and `wlanpi ALL=(root) NOPASSWD: /usr/libexec/wlanpi-menud/get-core-token ""`).
  - Installs alongside `wlanpi-fpms` without conflict: touches no `/lib/systemd/system/wlanpi-fpms.service`, imports no GPIO/SPI/I2C/OLED libraries (`PrivateDevices=yes`), and binds no TCP port.
- **Scrutiny of the two flagged packaging deviations against the Plan's Auth & Authorization section (`FPMS Re-Architecture Plan.md:223-260`):**
  1. **Deviation 1 — `RuntimeDirectoryMode=0755` (widened from `0750`) + `SupplementaryGroups=www-data` (`wlanpi-menud/install/lib/systemd/system/wlanpi-menud.service:11-18`, `wlanpi-menud/debian/wlanpi-menud.tmpfiles:1`, `wlanpi-menud/wlanpi_menud/server.py:98-105`):**
     - *Why it was introduced:* The plan (`FPMS Re-Architecture Plan.md:237-247`) places both `local.sock` (`0660`, group `wlanpi-menud`) and `proxy.sock` (`0660`, group `www-data`) under `/run/wlanpi-menud/`. Because `/run/wlanpi-menud` is owned by `wlanpi:wlanpi-menud`, mode `0750` prevents `nginx` (`www-data`, not in `wlanpi-menud`) from traversing `/run/wlanpi-menud` (`EACCES`). Moreover, because `Server._listen` (`server.py:98-105`) calls `os.chown(path, -1, gid)` as the unprivileged `wlanpi` user, `wlanpi` must hold `www-data` in its supplementary groups (`SupplementaryGroups=www-data`) or `chown(2)` fails with `EPERM`.
     - *Security impact:* On Linux, `connect(2)` to an `AF_UNIX` stream socket enforces write permission on the socket inode itself (`0660`), so `RuntimeDirectoryMode=0755` does **not** allow unprivileged users outside `wlanpi-menud`/`www-data` to connect to either socket (it only permits listing the two filenames in `/run/wlanpi-menud`). However, `SupplementaryGroups=www-data` grants the long-running `wlanpi-menud` daemon permanent membership in `www-data` (flagged by `systemd-analyze security`, exposure `+0.1`), giving `wlanpi-menud` group access to any `www-data`-group-writable resources or sockets under `/run`.
     - *Tighter options (no `0755` parent dir and/or no `SupplementaryGroups=www-data`):*
       - **Option A (Setgid subdirectory via `tmpfiles.d` or `+ExecStartPre` — recommended if paths can be nested):** Place `proxy.sock` in `/run/wlanpi-menud/proxy/proxy.sock` where `/run/wlanpi-menud/proxy` is created by root (`tmpfiles.d` or `ExecStartPre=+/usr/bin/install -d -m 2750 -o wlanpi -g www-data /run/wlanpi-menud/proxy`). The `2750` (`setgid`) bit causes the kernel to assign group `www-data` to `proxy.sock` automatically upon `bind()` without `wlanpi-menud` calling `chown()` or holding `SupplementaryGroups=www-data`.
       - **Option B (Keep flat `/run/wlanpi-menud/{local,proxy}.sock` paths, drop `0755` via POSIX ACL):** Keep `/run/wlanpi-menud` at mode `0750` (`wlanpi:wlanpi-menud`) and grant traverse-only access to `www-data` via `tmpfiles.d` (`a+ /run/wlanpi-menud - - - - g:www-data:--x`).
  2. **Deviation 2 — Hardened unit (`SystemCallFilter=@system-service`, `RestrictSUIDSGID=yes`, etc.) with `NoNewPrivileges=` left unset for the `sudo` token helper (`wlanpi-menud/install/lib/systemd/system/wlanpi-menud.service:24-54`):**
     - *Plan requirement (`FPMS Re-Architecture Plan.md:229-231`):* `wlanpi-menud` runs as `wlanpi` and mints its `did=wlanpi-menud` JWT by running `/usr/libexec/wlanpi-menud/get-core-token` via `sudo` at startup, before `exp`, and after any `401`.
     - *How the unit interacts with `NoNewPrivs` and `sudo`:* `sudo` is `setuid-root` (`-rwsr-xr-x`), so it fails immediately if `PR_SET_NO_NEW_PRIVS` (`NoNewPrivs: 1`) is set or if `/` is mounted `MS_NOSUID` inside the service's mount namespace. Although `systemd.exec(5)` states that seccomp/namespace directives (`SystemCallFilter=`, `RestrictAddressFamilies=`, `RestrictSUIDSGID=`, `RestrictNamespaces=`, `PrivateDevices=`, `ProtectKernel*=`, etc.) imply `NoNewPrivileges=yes` when unprivileged, binary inspection of Debian Trixie's `systemd-executor` and `libsystemd-core` (`unit_patch_contexts`) confirms that `PID 1` only forces `context->no_new_privileges = true` when `DynamicUser=yes` is enabled, and installs the seccomp filters as root (`CAP_SYS_ADMIN`) *before* calling `fully_set_uid_gid()`. Additionally, `SystemCallFilter=@system-service` includes `@setuid`, and `ReadWritePaths=-/run/sudo` allows `sudo` to write its timestamp file under `ProtectSystem=strict`.
     - *Caveats / Risks (recorded in Minor m4):*
       1. Relying on `SystemCallFilter=` / `RestrictSUIDSGID=` not setting `NoNewPrivs: 1` is an implementation detail of `PID 1` executing as `CAP_SYS_ADMIN` with `DynamicUser=no`. If `NoNewPrivileges=yes` is ever implied by a future systemd version (or if `DynamicUser=` is toggled), `sudo -n /usr/libexec/wlanpi-menud/get-core-token` will fail at startup and on token refresh.
       2. Leaving `NoNewPrivileges` unset on the daemon (`systemd-analyze --offline=true security` score `3.6 OK`, `+0.2` penalty for `NoNewPrivileges=no`) means the long-running service process can still elevate privileges via `setuid` binaries (`sudo`). A future hardening improvement (requiring no change to the plan's token model) would be a tiny root-owned Unix socket / `systemd` credential generator or `ExecStartPre=+` + periodic root timer, which would allow setting `NoNewPrivileges=yes` on `wlanpi-menud.service`.
- **`wlanpi-menu-tui` Debian package (`wlanpi-menu-tui/debian/*`, `wlanpi-menu-tui/dist/wlanpi-menu-tui_0.1.0_arm64.deb`):**
  - Built with `dh-virtualenv` (`debian/rules:12-20`) into `/opt/wlanpi-menu-tui` and symlinks `/usr/bin/wlanpi-menu-tui` (`debian/wlanpi-menu-tui.links`).
  - Ships no systemd unit (`override_dh_installsystemd:` no-op in `debian/rules:22-24`), binds no sockets, and touches no hardware.
- **Resource budget verification:**
  - Standalone `VmRSS` measured on arm64 Python 3.13.14:
    - `wlanpi-menud`: **35,472 kB (~34.6 MiB)** vs. `<= 64 MiB` target (`MemoryMax=96M`).
    - `wlanpi-menu-tui`: **39,532 kB (~38.6 MiB)** vs. `<= 48 MiB` target.
    - Combined: **~73.2 MiB** vs. `<= 112 MiB` combined budget in `docs/resource-budget.md`.

### Criterion (6): Full Pytest Suite Execution
**Status:** **PASS (163 passed, 0 failed across both repos)**

| Repository | Command | Result | Duration |
| --- | --- | --- | --- |
| `wlanpi-menud` | `cd wlanpi-menud && .venv/bin/pytest -v` | **153 passed, 0 failed** (65 protocol/schema + 88 service/auth/jobs/nav/poller/reconnect/packaging) | 9.22s |
| `wlanpi-menu-tui` | `cd wlanpi-menu-tui && .venv/bin/pytest -v` | **10 passed, 0 failed** (pilot navigation, view blocks, prompts, paging, reconnect, errors) | 3.66s |
| **Total** | | **163 passed, 0 failed** | **12.88s** |

---

## 2. Classified Findings (`Blocker` / `Major` / `Minor`)

### Blocker (0)

None.

---

### Major (2)

#### M1. Dual `job` + `view` emission from `wlanpi-menud` overwrites `MenuTuiApp.display_mode` to `"view"`, disabling `c`/`Escape` job cancellation and bypassing client `JobState` paging
- **Severity:** Major
- **Locations:**
  - `wlanpi-menu-tui/wlanpi_menu_tui/app.py:297-335` (`_handle_server_frame`)
  - `wlanpi-menu-tui/wlanpi_menu_tui/app.py:778-803` (`on_key` job paging & cancellation branch)
  - `wlanpi-menud/wlanpi_menud/jobs.py:228-235` (`JobManager._broadcast`)
  - `wlanpi-menud/wlanpi_menud/service.py:161-163` (`job_changed_for`), `304-312` (`_invoke`), `343-345, 363` (`_input`)
- **Details:**
  - In `wlanpi-menu-tui/tests/stub_server.py`, the stub server responds to `input` with *either* a `view` frame *or* a `job` frame.
  - However, the real `wlanpi-menud` emits **both** a `job` frame (`state: "queued" | "progress" | "result" | ...`) **and** a thin-mode `view` frame (`screen: "busy"` or `screen: "result"`) on every job lifecycle transition for any attached thin session:
    - In `JobManager._broadcast` (`wlanpi_menud/jobs.py:232-233`), `s.send(self.job_frame(job, s.next_id()))` is queued immediately *before* `self.service.job_changed_for(s, job)`, which calls `self.push_view(session)` (`service.py:163`).
    - In `Service._input` (`wlanpi_menud/service.py:344-363`), `self.jobs.invoke(...)` queues `job(state="queued")` before `self.push_view(session, mid)` queues `view(screen="busy")`.
  - When `MenuTuiApp._handle_server_frame` processes this pair of frames:
    1. The `job` frame sets `self.display_mode = "job"` (`app.py:334`).
    2. The immediately following `view` frame sets `self.display_mode = "view"` (`app.py:306`).
  - **Impact 1 (Unreachable job cancellation via `c` / `Escape`):** In `MenuTuiApp.on_key` (`app.py:779-802`), pressing `c` or `escape` to send a `cancel` frame (`build_cancel`) is guarded by `if self.display_mode == "job" and self.active_job_id and self.active_job_id in self.jobs: ... elif job.state in ("queued", "progress") and key in ("escape", "c"):`. Because `self.display_mode` is `"view"` while a job is running against the real `wlanpi-menud`, pressing `c` or `Escape` in the TUI falls through to `key_map` (`app.py:805-822`), where neither `c` nor `escape` is mapped — so **no `cancel` frame is ever sent**. (Only pressing `left` detaches/cancels via thin-mode `input` in `wlanpi_menud/nav.py:186-190`.)
  - **Impact 2 (Split paging state between `view` and `job`):** When a multi-page job completes (e.g., `apps.scanner.scan`), `self.display_mode` ends up as `"view"`. Pressing `down` sends thin-mode `input(key="down")` (`app.py:806-822`), which advances `session.cursor.page` on the server (`nav.py:194-199`) and pushes a new `view` frame, while `self.jobs[self.active_job_id].current_idx` stays at `0` (`app.py:782-793` is skipped). Conversely, if `page` is ever triggered while `display_mode == "job"`, the server replies with a `job` frame (`service.py:328-334`) *without* updating `session.cursor.page`, desynchronizing client `JobState` from server `Cursor.page`.
- **Recommendation:**
  - In `wlanpi-menu-tui/wlanpi_menu_tui/app.py`:
    1. Allow `c` / `escape` to cancel an in-flight job (`self.active_job_id` in `("queued", "progress")`) regardless of whether `self.display_mode` is `"view"` or `"job"` (or check `self.current_view.get("screen") == "busy"`).
    2. Choose a single consistent thin-mode rendering/paging path when `hello.mode == "thin"`: either drive the center pane and `up`/`down` paging exclusively from the server's `view` frames (`input` `up`/`down`) while using `job` frames only for metadata/cancellation, or keep `display_mode = "job"` when an active job is attached and synchronize `left` to exit `display_mode = "job"` back to the menu `view`.

#### M2. `complications` event topic payload drift between `wlanpi-menud` and `wlanpi-menu-tui` prevents complications from rendering in the TUI status footer
- **Severity:** Major
- **Locations:**
  - `wlanpi-menud/wlanpi_menud/service.py:367-379` (`publish_complication`, `publish_complications`)
  - `wlanpi-menud/wlanpi_menud/views.py:36-37` (`render_home`)
  - `wlanpi-menu-tui/wlanpi_menu_tui/app.py:682-693` (`_render_status_footer`)
  - `wlanpi-menu-tui/tests/stub_server.py:174-181`
- **Details:**
  - `wlanpi-menud` publishes `complications` topic items with keys `{"app_id": app_id, "label": label[:8], "value": value, "status": status}` (`service.py:369`, consumed by `views.py:36-37` as `c['label']: c['value']`).
  - `wlanpi-menu-tui` (`app.py:686-692`) inspects each complication dict only for `comp.get("text")` or `comp.get("key") and comp.get("value")` (matching `stub_server.py:175-180`, which used `{"provider": "acme", "key": "uplink", "text": "Uplink OK"}`).
  - When connected to the real `wlanpi-menud`, any complication published to the `complications` topic (e.g., `{"app_id": "gps", "label": "GPS", "value": "8 sats", "status": "ok"}`) has neither `"text"` nor `"key"`, so `MenuTuiApp._render_status_footer` silently drops it and renders nothing in `#status-footer`.
- **Recommendation:**
  - Update `MenuTuiApp._render_status_footer` (`wlanpi-menu-tui/wlanpi_menu_tui/app.py:685-692`) to check `label = comp.get("label") or comp.get("key")` together with `comp.get("value")` (and optional `comp.get("text")`), and update `wlanpi-menu-tui/tests/stub_server.py:174-181` to emit `{"app_id": ..., "label": ..., "value": ..., "status": ...}`.

---

### Minor (4)

#### m1. Destructive `confirm` prompt defaults to `"confirm"` instead of `"cancel"` in `MenuTuiApp`
- **Severity:** Minor
- **Locations:**
  - `wlanpi-menu-tui/wlanpi_menu_tui/app.py:362, 860-881` (`_init_prompt_state`, `_handle_prompt_key`)
  - `wlanpi-menud/protocol/frames.schema.json:753` (`confirm_block.selected` default `"cancel"`)
  - `wlanpi-menud/wlanpi_menud/views.py:52`, `wlanpi-menud/wlanpi_menud/nav.py:39, 97, 218-222`
- **Details:**
  - In `wlanpi-menud` and `frames.schema.json`, confirmation prompts default to `"cancel"` (`cursor.prompt_index = 0` -> `"cancel"`, `1` -> `"confirm"`) so an accidental `Enter`/`Center` key press on `System > Reboot`, `System > Shutdown`, or `Apps > Profiler > Purge Data` cancels rather than executing the destructive operation.
  - While `MenuTuiApp` initializes inline `confirm` view blocks to `str(block.get("selected") or "cancel")` (`app.py:304, 331`), `wlanpi-menud` also sends a `prompt` frame (`kind: "confirm"`) when opening a confirmation (`jobs.py:349`). `MenuTuiApp._init_prompt_state` (`app.py:362`) sets `self.prompt_confirm_choice = "confirm"`, and `on_key` (`app.py:745`) routes keys to `_handle_prompt_key` whenever `self.active_prompt is not None`. Pressing `Enter` immediately upon opening `Reboot` or `Shutdown` therefore sends `answer(confirmed=True)`.
- **Recommendation:**
  - Change `self.prompt_confirm_choice = "confirm"` to `self.prompt_confirm_choice = "cancel"` in `wlanpi-menu-tui/wlanpi_menu_tui/app.py:362`.

#### m2. `wlanpi_menu_tui/qr.py` generates a decorative 21x21 pseudo-QR pattern rather than an ISO/IEC 18004 scannable QR matrix
- **Severity:** Minor
- **Locations:**
  - `wlanpi-menu-tui/wlanpi_menu_tui/qr.py:8-57` (`_build_qr_matrix`)
- **Details:**
  - `_build_qr_matrix` draws three 7x7 finder patterns, timing patterns, and a dark module on a fixed 21x21 grid, and fills the remaining cells with raw UTF-8 payload bits concatenated with a SHA-256 digest (`raw = data.encode("utf-8"); digest = hashlib.sha256(raw).digest(); stream = raw + digest`, lines 42-55).
  - Because it omits ISO/IEC 18004 mode indicators, character count indicators, Reed-Solomon error correction codewords, format information bits, and mask patterns (and because a 21x21 Version 1 QR code cannot hold a `WIFI:T:WPA;S:...;P:...;;` payload at all), a phone camera cannot decode the rendered Unicode block art.
  - Severity is classified as **Minor** because `render_qr_text_art` (`qr.py:60-90`) always renders the mandatory `caption` and `fallback_text` (`SSID: ...`) alongside the `kv` block beneath it, preserving usability in SSH sessions.
- **Recommendation:**
  - Use a lightweight pure-Python QR encoder (e.g., `segno` or `qrcode`, or a compact self-contained Reed-Solomon + QR Version 1-4 encoder) in `wlanpi_menu_tui/qr.py` so phone cameras can scan Wi-Fi and Profiler QR blocks directly from an SSH terminal.

#### m3. `event.data` in `protocol/frames.schema.json` is untyped, allowing `stub_server.py` topic drift to pass schema validation
- **Severity:** Minor
- **Locations:**
  - `wlanpi-menud/protocol/frames.schema.json:633-647` (`#/$defs/event`)
  - `wlanpi-menu-tui/tests/stub_server.py:168-181`
- **Details:**
  - In `frames.schema.json`, `#/$defs/event` constrains `topic` to `["device.summary", "jobs", "complications", "alerts"]`, but defines `data` only as `{"type": ["object", "array"]}`.
  - This allowed `wlanpi-menu-tui/tests/stub_server.py` to pass schema validation while emitting a different `complications` item schema (`{"provider", "key", "text"}` vs. `wlanpi-menud`'s `{"app_id", "label", "value", "status"}`) and using `"eth0"` instead of `"ip"` in `device.summary`.
- **Recommendation:**
  - Add per-topic `allOf` / `if-then` subschemas on `#/$defs/event` in `protocol/frames.schema.json` for the four built-in topics (`device.summary`, `jobs`, `complications`, `alerts`) so schema validation catches topic payload drift between `wlanpi-menud` and clients (`wlanpi-menu-tui`, Phase 2 WebUI, Phase 3 OLED client).

#### m4. Packaging deviations in `wlanpi-menud.service`: `RuntimeDirectoryMode=0755` + `SupplementaryGroups=www-data` and implicit `NoNewPrivs=0` reliance under seccomp hardening
- **Severity:** Minor
- **Locations:**
  - `wlanpi-menud/install/lib/systemd/system/wlanpi-menud.service:9-18, 24-54`
  - `wlanpi-menud/debian/wlanpi-menud.tmpfiles:1`
  - `wlanpi-menud/wlanpi_menud/server.py:98-105`
  - `wlanpi-menud/install/etc/sudoers.d/wlanpi-menud:5-6`
- **Details:**
  1. **`RuntimeDirectoryMode=0755` and `SupplementaryGroups=www-data`:**
     - Because `/run/wlanpi-menud` is owned by `wlanpi:wlanpi-menud`, the coder widened `RuntimeDirectoryMode` from `0750` to `0755` so `nginx` (`www-data`) can traverse `/run/wlanpi-menud` to reach `proxy.sock`, and added `SupplementaryGroups=www-data` so the unprivileged `wlanpi` daemon can `os.chown(proxy.sock, -1, gid_of("www-data"))` (`server.py:98-105`).
     - While `0755` on `/run/wlanpi-menud` does not weaken `connect(2)` access control on the `0660` sockets themselves, `SupplementaryGroups=www-data` grants the long-running `wlanpi-menud` process full `www-data` group privileges throughout its lifetime.
     - **Tighter alternatives:** Either (a) keep `/run/wlanpi-menud` at `0750` and add a traverse-only POSIX ACL for `www-data` in `debian/wlanpi-menud.tmpfiles` (`a+ /run/wlanpi-menud - - - - g:www-data:--x`), or (b) place `proxy.sock` in a root-created `2750` (`setgid`) subdirectory `/run/wlanpi-menud/proxy` owned by `wlanpi:www-data` (`d /run/wlanpi-menud/proxy 2750 wlanpi www-data -` in `tmpfiles.d`), which causes `proxy.sock` to inherit group `www-data` automatically without `chown()` or `SupplementaryGroups=www-data`.
  2. **Seccomp/namespace hardening (`SystemCallFilter=@system-service`, `RestrictSUIDSGID=yes`, etc.) paired with `sudo` setuid token helper:**
     - Per the plan (`FPMS Re-Architecture Plan.md:229-231`), `wlanpi-menud` runs `sudo -n /usr/libexec/wlanpi-menud/get-core-token` at startup, before `exp`, and after any `401`. Because `sudo` is `setuid-root`, `NoNewPrivileges=` and `CapabilityBoundingSet=` are intentionally omitted from `wlanpi-menud.service:24-28`.
     - On Debian Trixie (`systemd 257+`), binary inspection of `systemd-executor` and `libsystemd-core` (`unit_patch_contexts`) confirms `NoNewPrivs` stays `0` because `PID 1` only forces `context->no_new_privileges = true` when `DynamicUser=yes` is enabled and installs seccomp filters as `CAP_SYS_ADMIN` before `fully_set_uid_gid()`.
     - However, `systemd.exec(5)` documents `SystemCallFilter=`, `RestrictAddressFamilies=`, `RestrictSUIDSGID=`, `RestrictNamespaces=`, `PrivateDevices=`, and `ProtectKernel*=` as implying `NoNewPrivileges=yes` in unprivileged contexts, and leaving `NoNewPrivileges` off allows the daemon to invoke `setuid` binaries. Keep an eye on `NoNewPrivs: 0` across `systemd` upgrades (or consider moving token minting to an `ExecStartPre=+` + root-owned socket/credential helper in a later phase so `NoNewPrivileges=yes` can be enabled).

---

## 3. Ready for February Release? Assessment Against Exit Criteria

| Phase 1 Exit Criterion | Status | Release Readiness Assessment |
| --- | --- | --- |
| **(1) Every menu item whose core endpoint exists is runnable from the TUI** | **PASS** | **Ready.** All 45 runnable action nodes (44 in `classic` + `utils.ssid_passphrase` in `hotspot`/`server`/`wconsole`/`bridge`) execute end-to-end from the TUI through `wlanpi-menud` against `wlanpi-core` routes, and the 13 routes awaiting `wlanpi-core` Streams A/C/D are cleanly hidden via `visibility.requires_core`. |
| **(2) Two concurrent sessions navigate independently** | **PASS** | **Ready.** Per-session `Cursor`, `MenuView`, bounded 64-frame queues, and prompt ownership are isolated and verified across concurrent `MenuTuiApp` instances. |
| **(3) Live TUI <-> Service integration & `frames.schema.json` validation** | **NEEDS 2 FIXES** | **Fix M1 & M2 before release.** All 1,381 live integration frames (593 C2S + 788 S2C) validate against `frames.schema.json`, and navigation/invocation/prompts/resume work end-to-end, but **M1** (`display_mode` overwrite breaking `c`/`Escape` job cancel in TUI) and **M2** (`complications` footer rendering in TUI) should be resolved before tagging `0.1.0`. |
| **(4) Auth & security rules hold** | **PASS** | **Ready.** `SO_PEERCRED` on `local.sock`, login-first / header auth + 5-min JWT re-verification on `proxy.sock`, zero TCP listeners, URL query-token rejection, non-shareable/masked secrets, and session/principal-bound confirmations are all enforced and covered by unit + integration tests. |
| **(5) Packaging (`wlanpi` user, `/opt` venv, coexists with `wlanpi-fpms`)** | **PASS** | **Ready.** Both `.deb` packages (`wlanpi-menud_0.1.0_arm64.deb` and `wlanpi-menu-tui_0.1.0_arm64.deb`) build cleanly into `/opt` venvs, `wlanpi-menud.service` runs as `User=wlanpi` with `PrivateDevices=yes` and `MemoryMax=96M`, and combined RSS (~73.2 MiB) is well inside the 112 MiB budget. |
| **(6) Pytest suites pass** | **PASS** | **Ready.** 153/153 pass in `wlanpi-menud` and 10/10 pass in `wlanpi-menu-tui` (163/163 total). |

### Bottom-Line February Release Assessment
- **`wlanpi-menud` (`7f34921`) is release-ready** for the February opt-in ship alongside `wlanpi-fpms`.
- **`wlanpi-menu-tui` (`20e25fe`) requires targeted fixes for M1 and M2 (plus 1-line m1)** — roughly 25 lines of change in `wlanpi_menu_tui/app.py` and `tests/stub_server.py` — to align its job cancellation/paging state machine and `complications` footer renderer with the real `wlanpi-menud` frame sequence. Once M1, M2, and m1 are patched, Phase 1 is ready to ship for the February release.

---

## Re-review

**Date:** 2026-11-08  
**Targets:**
- `wlanpi-menud` (`da4eac2`, branch `phase1/service`)
- `wlanpi-menu-tui` (`4e9a5e9` on `main`, `4e97593` on `packaging/debian`)

**Verdict:** **APPROVE**

| Finding / Gap | Origin | Status | Resolution Summary |
| --- | --- | --- | --- |
| **M1** (`job` + `view` dual-emission overwrites `display_mode`, disabling `c`/`Escape` cancel & splitting paging) | Phase 1 Review | **RESOLVED** | `MenuTuiApp` (`app.py:158, 322-330, 356-357, 768-784, 829-858`) tracks view-driven jobs in `_view_driven_jobs`, keeps `active_job_id` set, pages view-driven jobs exclusively via thin `input`, and allows `c`/`Escape` to cancel queued/running jobs on screen in either `"view"` or `"job"` mode. |
| **M2** (`complications` item shape mismatch `label`/`value` vs. `key`/`text`) | Phase 1 Review | **RESOLVED** | `MenuTuiApp._render_status_footer` (`app.py:713-725`) renders `{app_id, label, value, status}` items as `label: value` (with `key`/`text` fallback); `stub_server.py:175-182` emits the real service shape. |
| **m1** (destructive `confirm` prompt defaults to `"confirm"`) | Phase 1 Review | **RESOLVED** | `MenuTuiApp` (`app.py:143, 387`) initializes `prompt_confirm_choice = "cancel"`, matching `frames.schema.json` and `wlanpi-menud`. |
| **m2** (`wlanpi_menu_tui/qr.py` pseudo-QR matrix) | Phase 1 Review | **DEFERRED (Non-blocking Minor)** | Mandatory `caption` + `text` + `kv` block fallback remains intact for SSH sessions; scannable ISO/IEC 18004 encoder remains an optional post-Phase 1 enhancement. |
| **m3** (`event.data` untyped in `frames.schema.json`) | Phase 1 Review | **RESOLVED** | `protocol/frames.schema.json:540-644` types `event.data` per built-in topic (`topic_device_summary`, `topic_alerts`, `topic_jobs`, `topic_complications`, all `additionalProperties: false`) while keeping plugin topics open; 4 negative samples added. |
| **m4** (`RuntimeDirectoryMode=0755` + `SupplementaryGroups=www-data` in `wlanpi-menud.service`) | Phase 1 Review | **RESOLVED** | `/run/wlanpi-menud` restored to `0750` (`wlanpi:wlanpi-menud`) with a traverse-only POSIX ACL (`group:www-data:--x`), `proxy.sock` moved to setgid `/run/wlanpi-menud/proxy/proxy.sock` (`2750 wlanpi:www-data`) via `tmpfiles.d` + `ExecStartPre=+`, and `SupplementaryGroups=www-data` removed. |
| **32-row terminal layout bug** (`ScrollableContainer` focus consumed `up`/`down` & scrolled results off-screen at `80x32`) | On-Device Report | **RESOLVED** | `MainScroll(ScrollableContainer, can_focus=False)` (`app.py:42-50, 167`) prevents arrow-key scroll capture and `_refresh_ui` (`app.py:428-433`) calls `scroll_home(animate=False)` when the main screen changes. |
| **D2** (Idle CPU `1.9–2.2%` on M4+ vs. `<= 1%` budget) | On-Device Report | **RESOLVED (Dev-host verified; board re-measure noted)** | `core_keepalive_sec = 55.0` eliminates per-tick TLS handshakes (`120 -> 0` over 90s), `Poller` splits into 5s fast tier + 30s context tier with immediate wake on mutating jobs (`1.34 -> 0.78` calls/s), and `httpx`/`httpcore` log at `WARNING` (`169 -> 22` log lines). Dev-host idle CPU dropped **2.5x** (`0.75% -> 0.30%`). |
| **D4** (`wlanpi-menud.service` vs. `docs/resource-budget.md` & `cgroup_disable=memory`) | On-Device Report | **RESOLVED** | Unit (`wlanpi-menud.service:8-10, 59-67`) reconciled to `Type=notify`, `NotifyAccess=main` (`sdnotify.py`), `MemoryHigh=80M`, `MemoryMax=112M`, and `CPUWeight=50`; `docs/resource-budget.md:85-106` documents `cgroup_disable=memory` on 26.10 and `/proc` `VmRSS`/`VmHWM` measurement. |
| **D5** (`apt purge` left `wlanpi-menud` group behind) | On-Device Report | **RESOLVED** | `debian/wlanpi-menud.postrm:18-26` removes `wlanpi-menud` via `groupdel` on `purge` when it has no supplementary members and is no user's primary GID, guarded so `purge` never fails. |
| **D7** (`needsSelection` `prompt -> answer -> rerun` coverage) | On-Device Report | **RESOLVED** | 4 fake-core tests in `wlanpi-menud/tests/test_jobs.py:163-226` (rich, thin/front-panel keys, cancel, and 2-prompt cap) plus live TUI <-> service verification. |

---

### 1. Verification of TUI Fixes & Regression Tests (`wlanpi-menu-tui` on `main` `4e9a5e9`)
**Status:** **PASS (14/14 passed on `main`; 5/5 regression tests confirmed to fail against pre-fix `84ecc7f:app.py`)**

- **Pytest execution on `main` (`4e9a5e9`):**
  - `cd wlanpi-menu-tui && .venv/bin/pytest -v` -> **14 passed, 0 failed in 5.78s**.
  - Verified that `packaging/debian` (`4e97593`) is directly on top of `4e9a5e9` with zero diff in `wlanpi_menu_tui/` or `tests/`.
- **Negative verification (regression tests run against pre-fix `84ecc7f:wlanpi_menu_tui/app.py`):**
  - Swapped in `84ecc7f:wlanpi_menu_tui/app.py` in an isolated scratch path and ran the 5 regression tests: **5 failed, 0 passed**:
    1. `test_m1_dual_job_view_emission_keeps_cancel_reachable` — **FAILED** on pre-fix `app.py` (`TimeoutError` waiting for `cancel` frame after `send_job_transition(job, busy_view)` overwrote `display_mode = "view"`).
    2. `test_m1_dual_job_view_result_pages_via_thin_input_only` — **FAILED** on pre-fix `app.py` (late `job` frame overwrote `display_mode = "job"` and stole the screen from the paged `view`).
    3. `test_m2_real_complications_payload_renders_in_footer` — **FAILED** on pre-fix `app.py` (`AssertionError` waiting for `"Uplink: OK"` / `"GPS: 8 sats"` from `{app_id, label, value, status}` items).
    4. `test_prompt_flows_select_confirm_and_form` (`m1`) — **FAILED** on pre-fix `app.py` (`assert app.prompt_confirm_choice == "cancel"` failed because pre-fix defaulted to `"confirm"`).
    5. `test_layout_80x32_result_visible_after_invoke_with_tall_menu` — **FAILED** on pre-fix `app.py` (`AssertionError: assert 10 == 0` on `scroll.scroll_y` because `ScrollableContainer` focused and scrolled 10 lines down during 10 `"down"` key presses).

### 2. Verification of Service Fixes (`wlanpi-menud` on `phase1/service` `da4eac2`)
**Status:** **PASS (181/181 passed on `phase1/service`; idle-CPU benchmark re-verified live)**

- **Pytest execution on `phase1/service` (`da4eac2`):**
  - `cd wlanpi-menud && .venv/bin/pytest -v` -> **181 passed, 0 failed in 10.00s** (+28 new tests over the initial 153).
- **Idle-CPU measurement methodology & budget verification (`tools/idle_bench/{bench.py,fakecore_https.py}`, `docs/resource-budget.md:59-84`):**
  - **Methodology audit:** Sound. `bench.py` spawns a real `python -m wlanpi_menud` process (`INFO` logging) against `fakecore_https.py` serving real TLS (`RSA-2048` pinned via `core_cert`) and HTTP/1.1 keep-alive with M4+-shaped responses where `cpu` and `cpu_temp` vary on every call (forcing `device.summary` publication on every fast tick), while two idle clients (one `thin` on home screen, one `rich` subscribed to all 4 topics) remain connected. After a 12s warm-up, CPU is computed directly from `/proc/<pid>/stat` `(utime + stime) / SC_CLK_TCK` over the measurement window.
  - **Before vs. After results:**
    - Recorded 90s runs (`docs/resource-budget.md:75-82`): mean CPU dropped **2.5x** from **0.75%** (`0.71%, 0.68%, 0.86%` at `7f34921`) to **0.30%** (`0.21%, 0.40%, 0.20%, 0.39%` at `da4eac2`), core calls/s dropped from **1.34/s to 0.78/s**, new TLS connections dropped from **120 to 0**, and journal lines dropped from **169 to 22** (RSS **44–45 MiB**).
    - Live 20s re-review benchmark run (`tools/idle_bench/bench.py . 20 rereview`): measured **`cpu_pct: 0.2%`** (`0.04` CPU sec over `20.0s`), **`core_calls_per_s: 0.85`**, **`new_tls_conns: 0`**, **`rss: 44,760 kB (~43.7 MiB)`**, and **`menud log lines: 22`**.
    - Applied to the M4+ board's pre-fix `1.9–2.2%` idle CPU, eliminating 100% of per-tick TLS handshakes and 42% of HTTP polls brings projected board idle CPU to **~0.7–0.9%** (within the `<= 1%` budget).
  - **Poller unit coverage (`tests/test_poller.py`):** 9 tests verify fast-tier-only idle ticks, publish-on-change only, 30s context re-read, immediate wake + context refresh on mutating job completion (`test_mutating_job_refreshes_context_now`, `test_wake_runs_a_tick_without_waiting_the_interval`), core-down detection between context reads, pool keep-alive (`55s`), and slow retry (`60s`) on unusable `openapi.json`.
- **`systemd` unit reconciliation & Minor m4 (`install/lib/systemd/system/wlanpi-menud.service`, `debian/wlanpi-menud.tmpfiles`, `wlanpi_menud/sdnotify.py`, `tests/test_install.py`, `tests/test_sdnotify.py`):**
  - Reconciled with `docs/resource-budget.md`: `Type=notify`, `NotifyAccess=main`, `MemoryHigh=80M`, `MemoryMax=112M`, `CPUWeight=50`.
  - `sdnotify.py` tested across 4 unit tests (`test_sdnotify.py`: no-op without `$NOTIFY_SOCKET`, filesystem datagram socket, abstract namespace `@...`, and dead socket resilience).
  - **Socket permissions & access-control matrix against the Plan's Auth section (`FPMS Re-Architecture Plan.md:223-260`, resolving m4):**
    - `RuntimeDirectoryMode=0750`, `SupplementaryGroups=www-data` removed from `wlanpi-menud.service`, and `ExecStartPre=+/usr/bin/systemd-tmpfiles --create wlanpi-menud.conf` creates `/run/wlanpi-menud` (`0750 wlanpi:wlanpi-menud` + POSIX ACL `a+ group:www-data:--x`) and `/run/wlanpi-menud/proxy` (`2750 wlanpi:www-data`) for `/run/wlanpi-menud/proxy/proxy.sock`.
    - **Who can connect where (verified):**
      - *Unprivileged users outside `wlanpi-menud` and `www-data`:* Match `other::---` on `/run/wlanpi-menud` (`0750`); cannot traverse `/run/wlanpi-menud` or reach either socket (`EACCES`).
      - *`nginx` (`www-data`):* Holds traverse-only (`group:www-data:--x`) on `/run/wlanpi-menud` (cannot list `/run/wlanpi-menud`), cannot connect to `/run/wlanpi-menud/local.sock` (`0660 wlanpi:wlanpi-menud` -> `www-data` matches `other` `---` on the socket inode -> `EACCES`, and is also refused by `SO_PEERCRED` in `Server._local_principal`), and traverses `/run/wlanpi-menud/proxy` (`2750 wlanpi:www-data`) to connect to `proxy.sock` (`0660 wlanpi:www-data`).
      - *SSH operator users in `wlanpi-menud`:* Traverse `/run/wlanpi-menud` (`0750`) and connect to `local.sock` (`0660 wlanpi:wlanpi-menud`), but match `other` (`---`) on `/run/wlanpi-menud/proxy` (`2750 wlanpi:www-data`) and on `proxy.sock` (`0660 wlanpi:www-data`) -> cannot traverse or connect to `proxy.sock` (`EACCES`).
      - *`wlanpi-menud` service (`wlanpi:wlanpi-menud`):* Because `/run/wlanpi-menud/proxy` is `2750` (`setgid`, group `www-data`), the kernel assigns group `www-data` to `proxy.sock` upon `bind(2)` without `chown(2)` or `SupplementaryGroups=www-data`, so the daemon holds zero `www-data` privileges.
- **`postrm purge` group removal (`debian/wlanpi-menud.postrm`, `tests/test_install.py:75-115`):**
  - Verified by 4 tests with stubbed `getent`/`groupdel`: removes `wlanpi-menud` when unused, preserves it when supplementary members exist or when it is a user's primary GID, and no-ops cleanly on `remove` or missing group/tools.
- **Rebuilt `.deb` artifacts inspection (`/tmp/tui-finisher-debs/wlanpi-menud_0.1.0_arm64.deb` & `wlanpi-menu-tui_0.1.0_arm64.deb`):**
  - Verified via `dpkg-deb -I` / `dpkg-deb -c` / `dpkg-deb --fsys-tarfile` that both rebuilt arm64 `.deb` packages carry all `da4eac2` (`wlanpi-menud`) and `4e9a5e9`/`4e97593` (`wlanpi-menu-tui`) changes (`wlanpi-menud.service` with `Type=notify` and `RuntimeDirectoryMode=0750`, `/usr/lib/tmpfiles.d/wlanpi-menud.conf`, `/etc/wlanpi-menud/config.toml` with `/run/wlanpi-menud/proxy/proxy.sock`, `DEBIAN/postrm` group cleanup, and updated `wlanpi_menu_tui/app.py`), with **0 lintian findings** in both `lintian-menud.txt` and `lintian-tui.txt`.
- **`needsSelection` coverage (`tests/test_jobs.py:163-226`):**
  - Verified by 4 fake-core tests: `test_needs_selection_prompts_then_reruns` (rich), `test_needs_selection_thin_panel_select_then_rerun` (thin/panel keys), `test_needs_selection_cancelled_does_not_rerun`, and `test_needs_selection_twice_then_gives_up`.

### 3. Live Cross-Agent Re-Integration (`MenuTuiApp` <-> `wlanpi-menud` over `local.sock`)
**Status:** **PASS (413/413 live wire frames schema-valid; all 6 live scenarios verified)**

Connected the real `MenuTuiApp` (`4e9a5e9`) to the real `wlanpi-menud` `Server` + `Service` + `Poller` (`da4eac2`) over a temporary Unix socket (`local.sock`) with `validate_outbound=True` and a `Draft202012Validator(frames.schema.json)` spy on both C2S and S2C frames:

1. **80x32 terminal layout check against the full 76-node manifest tree:**
   - Ran `MenuTuiApp` at `size=(80, 32)`. Confirmed `MainScroll.can_focus is False` and `scroll.max_scroll_y == 41` (the real menu tree overflows a 32-row pane).
   - Navigated with `down`/`up`/`enter` into `Network -> Interfaces` and subsequently walked all **45 runnable action nodes** (44 in `classic` + `utils.ssid_passphrase` in `hotspot`).
   - Confirmed `scroll.scroll_y == 0.0` after every navigation and action result, `screen_view.region.y` remained at the top of `scroll.content_region`, and `app.export_screenshot()` verified the result blocks were visibly painted on the 80x32 screen.
2. **Job cancellation (`c` and `Escape`) during dual `job` + `view` emission (M1):**
   - Invoked `utils.speedtest.run` twice against the real service. On each run, the service emitted `job(state="queued"|"progress")` followed by `view(screen="busy", job_id=...)`, setting `app.display_mode == "view"` and adding `job_id` to `app._view_driven_jobs`.
   - Pressing `"c"` on the first run and `"escape"` on the second run each emitted exactly one C2S `cancel` frame (`{"type": "cancel", "job_id": ...}`), cancelling the job on the real service and returning to the menu view.
3. **Result paging during dual `job` + `view` emission (M1):**
   - Invoked `apps.scanner.scan` (45 networks across 3 pages of 20/20/5 rows). The service emitted `job(state="result")` followed by `view(screen="result", page={"index": 0, "count": 3, "more": True})`.
   - Pressing `down`, `down`, `up` in `MenuTuiApp` sent only thin `input` frames (`["down", "down", "up"]`) and **0 `page` frames**, transitioning `#screen-view` cleanly across `(page 1/3+)` (`net000`) -> `(page 2/3+)` (`net020`) -> `(page 3/3)` (`net040`) -> `(page 2/3+)` (`net020`).
4. **Complications rendering from the real service (M2):**
   - Published `gps` (`"GPS"`, `"8 sats"`, `"ok"`) and `kismet` (`"Kismet"`, `"running"`, `"ok"`) via `service.publish_complication(...)`.
   - Confirmed `#status-footer` rendered `"GPS: 8 sats"` and `"Kismet: running"`.
5. **Destructive `confirm` prompt default (m1) & `needsSelection` prompt -> answer -> rerun:**
   - Invoked `system.reboot`: `app.prompt_confirm_choice` defaulted to `"cancel"`, and pressing bare `enter` sent `answer(confirmed=False)` with `fake.reboots == 0`.
   - Invoked `apps.scanner.scan_nohidden` with `fake.scan_candidates` populated (`wlan1` in `root`, `wlan2` in `ns1`): `MenuTuiApp` displayed the 2-option `select` prompt, pressing `down` + `enter` sent `answer(value={"iface": "wlan2", "namespace": "ns1"})`, and the service re-ran `GET /utils/wlan/scan?hidden=false&iface=wlan2&namespace=ns1` and rendered the resulting table in `#screen-view`.
6. **Two concurrent sessions (`app1`, `app2`) at `80x32`:**
   - Connected two `MenuTuiApp` instances concurrently over `local.sock`. Verified distinct session IDs, independent cursor/view state (`app1` on `Network -> Interfaces` result while `app2` stayed on `Home` and then navigated to `Bluetooth`), and strict prompt isolation (`system.shutdown` confirmation delivered only to `app1`).

### 4. Protocol & Schema Drift Verification (`protocol/`)
**Status:** **PASS (0 schema drift across both repositories)**

- `wlanpi-menud/protocol/frames.schema.json` and `menu.schema.json` validate cleanly as Draft 2020-12 schemas.
- **Typed topic contracts (`frames.schema.json:540-644`):**
  - `device.summary` (`#/$defs/topic_device_summary`): typed object (`additionalProperties: false`) using `"ip"` (rejecting legacy `"eth0"` key via `invalid/frames/event-summary-unknown-key.json`).
  - `alerts` (`#/$defs/topic_alerts`): array of `{level: "info"|"warn"|"error", message: string}` objects (`additionalProperties: false`, rejecting unknown levels or non-object items via `invalid/frames/event-alert-bad-level.json`), aligned across `wlanpi_menud/poller.py:98, 164-171`, `wlanpi-menu-tui/tests/stub_server.py:183`, and `wlanpi-menu-tui/wlanpi_menu_tui/app.py:726-733`.
  - `jobs` (`#/$defs/topic_jobs`): array of `{job_id, node, state: "queued"|"progress", sessions}` objects (`additionalProperties: false`, rejecting terminal states via `invalid/frames/event-jobs-finished-state.json`).
  - `complications` (`#/$defs/topic_complications`): array of `{app_id, label, value, status}` objects (`additionalProperties: false`, `label` `maxLength: 8`, rejecting `{provider, key, text}` drift via `invalid/frames/event-complications-drift.json`).
- All **73 protocol schema & manifest tests** in `wlanpi-menud/protocol/tests/test_schemas.py` pass (including the 4 new typed-topic positive samples and 4 negative drift samples).
- All **14 pilot tests** in `wlanpi-menu-tui/tests/test_tui.py` validate every C2S and S2C frame against `wlanpi-menud/protocol/frames.schema.json` and `menu.schema.json` (`PARAM_VALIDATOR` and `CONFIRM_VALIDATOR`).
- All **413 frames (90 C2S + 323 S2C)** captured during the live `MenuTuiApp` <-> `wlanpi-menud` re-review passed `frames.schema.json` validation with **0 errors**.

---

### 5. February-Release Readiness Statement & Remaining Device-Only Gaps

**Release Readiness:** **APPROVED — Ready for the February opt-in release alongside `wlanpi-fpms`.**

All Phase 1 exit criteria, all blocking/major/minor review items (`M1`, `M2`, `m1`, `m3`, `m4`), and the on-device validation fixes (`D2`, `D4`, `D5`, `D7`, and the `80x32` terminal layout fix) are implemented, regression-tested (**195/195 total pytest tests passing**: **181** in `wlanpi-menud` + **14** in `wlanpi-menu-tui`), and verified end-to-end over a live Unix socket. Note for Phase 2 (`wlanpi-webui` integration): the nginx `/ui/ws` upstream configuration must point at the new setgid socket path `/run/wlanpi-menud/proxy/proxy.sock`.

**Remaining Device-Only Validation Gaps (to execute on physical hardware before closing the on-device checklist):**
1. **`needsSelection` on a multi-radio board:** Exercise `apps.scanner.scan` / `apps.scanner.scan_nohidden` on a board with multiple Wi-Fi radios attached (e.g., internal `wlan0` + USB `wlan1`) against the real `wlanpi-core` to confirm the live `needsSelection -> prompt -> answer -> rerun` flow on hardware.
2. **Offline-clock recovery test (`AUTH_CLOCK_NOT_SET`):** Boot a board offline with a stale clock so `wlanpi-core` returns `503 AUTH_CLOCK_NOT_SET`, then restore NTP sync and confirm `wlanpi-menud` automatically re-mints its service JWT via `/usr/libexec/wlanpi-menud/get-core-token` and recovers without restarting the service.
3. **Real `nginx` -> `/run/wlanpi-menud/proxy/proxy.sock` integration:** Verify on-device that `nginx` (`www-data`) traverses `/run/wlanpi-menud` via the POSIX ACL (`group:www-data:--x`), connects to `/run/wlanpi-menud/proxy/proxy.sock` (`0660 wlanpi:www-data`), and forwards authenticated WebSocket sessions (`X-WebUI-User` / `X-WebUI-Session` or first-frame `auth` JWT).
4. **On-board idle-CPU confirmation (`wlanpi-9be`):** Re-measure 60s idle CPU on the M4+ board with `pidstat -p $(systemctl show -p MainPID --value wlanpi-menud) 60 1` after installing the updated `.deb` to confirm the dev-host 2.5x reduction (`0.75% -> 0.30%`) translates to `<= 1.0%` on Cortex-A72 hardware.

