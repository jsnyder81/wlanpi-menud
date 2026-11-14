# Complications Service Review (`wlanpi-menud` Phase 1 / Service)

**Date:** 2026-11-14  
**Target:** `wlanpi-menud` (branch `phase1/service`, commits `cbb69c0`, `489149d`, and `35f63ff`)  
**Evaluated against:** `docs/complications-spec.md`, Decision Log §8 (user rulings Nov 13, 2026), `protocol/PROTOCOL.md`, and `protocol/frames.schema.json`

**Verdict:** **APPROVE**

| Severity | Count | Summary |
| --- | --- | --- |
| **Blocker** | **0** | None. Protocol, schemas, daemon store, auth, namespaces, built-ins, profiles, CLI, and thin home customization strictly comply with spec. |
| **Major** | **0** | None. Backwards compatibility for legacy surfaces and topics is byte-for-byte preserved. |
| **Minor** | **0** | None. All unit and contract tests green (412/412 passed). Resource budget and packaging verified. |

---

## 1. Executive Summary

Commits `cbb69c0` (core complications implementation, store, frames, display profiles, CLI), `489149d` (contract test suite, resolve vectors, protocol documentation, debian packaging), and `35f63ff` (thin `system.home_tiles` customize node, SIGHUP reload semantics, resource budget measurements) fulfill the entire service-side specification for complications (Phases C0 through C4 in `docs/complications-spec.md` §7).

All 9 target areas requested for verification have been thoroughly audited and tested live:
1. `frames.schema.json` delta is strictly additive with full backward compatibility.
2. Namespace isolation (`wlanpi.*`, `plugin.<name>.*`, `local.<user>.*`) and role gates (`provider`, `admin`) are strictly enforced via kernel `SO_PEERCRED`.
3. Quotas (8 per UID, 32 total), TTL bounds (5–3600s, default 30s), standalone timer-based expiry (<1s), and `hold: connection` connection-tracking behave per spec.
4. Legacy `complications` topic and `device.summary` payload are frozen and byte-identical to pre-complications shapes.
5. Built-in tiles including `wlanpi.wifi` adhere to the 10s connected / 30s context cadence and 3 dB / status-band hysteresis.
6. Display profile configuration adheres to the 3-layer precedence hierarchy (`user` > `site` > `default`), atomic persistence, corrupted-file recovery, and resolution vectors.
7. Thin session interactive customization (`system.home_tiles`) renders cleanly with pagination and screen capacity limits (`- - fits above - -`).
8. The `wlanpi-complication` CLI interacts seamlessly with a live WebSocket Unix domain socket using ephemeral sessions.
9. Full test suite passes completely (412 passed in ~21s) with plausible resource-budget metrics (mean 0.42% CPU, 41–45 MiB RSS, 0.87 core calls/s).

---

## 2. Detailed Verification Checklist

### (1) Schema Backward Compatibility (`protocol/frames.schema.json`)
- **Inspection (`git diff 008dc59..HEAD protocol/frames.schema.json`):**
  - **Root `oneOf`**: Appended references `#/$defs/complication_publish`, `#/$defs/complication_remove`, `#/$defs/complications_configure`, and `#/$defs/complication_ack`. No existing frame definitions were modified or removed.
  - **`hello`**: Additive optional properties `surface` (`$ref: #/$defs/surface_profile`) and `ephemeral` (`type: boolean`). Existing hello frames from thin/rich legacy clients validate without errors.
  - **`welcome`**: Additive optional property `features` (`type: array` of strings). Advertises `["complications.2"]`.
  - **`event`**: Added conditional branch mappings in `allOf` for topics `complications.live`, `complications.catalog`, and `complications.config`. The existing `complications` topic definition (`topic_complications`) remains untouched.
  - **`block_kv`**: Added optional `level` enum (`["ok", "info", "warn", "error"]`) per item. Existing renderers ignore unexpected or optional keys.
- **Suite Verification:**
  - Ran `protocol/tests/test_schemas.py`: **156 passed in 0.74s**.
  - All existing frame samples in `protocol/samples/frames/` and manifests validate successfully.

### (2) Namespace Rules & Mutation Checks
- **Implementation (`wlanpi_menud/service.py:545-585`):**
  - `wlanpi.*`: Code explicitly rejects publishes with `FORBIDDEN` ("wlanpi.* complications are reserved for the service") even if UID is 0 (`root`).
  - `local.<user>.*`: Uses kernel `SO_PEERCRED` to map UID to system username via `pwd.getpwuid` (or fallback `uid_<id>`) and sanitizes it via `C.map_username`. Rejects attempts by any UID to publish under another user's namespace (`FORBIDDEN`).
  - `plugin.<name>.*`: Requires a root-installed declaration file at `/usr/share/wlanpi-menud/plugins.d/<name>/complications.json` naming the allowed Unix user. Non-matching UIDs get `FORBIDDEN`; undeclared IDs get `BAD_REQUEST`.
  - Socket isolation: Publishes on `proxy.sock` are unconditionally rejected (`FORBIDDEN`), irrespective of authenticated WebUI roles.
- **Mutation Verification:**
  - Simulated a second UID (`uid = os.getuid() + 1000`) connecting over `local.sock` with `SO_PEERCRED` monkeypatching:
    - Attempt to publish `local.jakesnyder.test`: Rejected with `FORBIDDEN: local.jakesnyder.* belongs to another user; use local.uid_2000.*`.
    - Attempt to publish `wlanpi.mode`: Rejected with `FORBIDDEN: wlanpi.* complications are reserved for the service`.
  - Tests in `tests/test_complications.py` (`test_local_namespace_is_bound_to_the_callers_username`, `test_wlanpi_namespace_is_reserved_even_for_root`, `test_plugin_namespace_only_for_the_declared_user`, `test_proxy_sock_may_never_publish`) pass.

### (3) Quotas, TTL & Connection-Tracking (`hold: connection`)
- **Implementation (`wlanpi_menud/complications.py:584-660`, `wlanpi_menud/service.py:325`):**
  - **Quotas**: `quota_per_uid` (default 8) and `quota_total` (default 32) are checked on insert. Updates to existing `app_id` keys do not increment quota counts. Reaching quotas raises `FORBIDDEN`.
  - **TTL Bounds**: Configurable 5–3600 seconds; defaults to 30s. Values outside bounds are rejected by schema validation.
  - **Timer-based Expiry**: Expiry runs off dedicated `asyncio.TimerHandle` targeting the nearest `expires_at` timestamp rather than waiting for the 5-second poller tick.
  - **`hold: "connection"`**: Attachments track client IDs. On socket disconnect, `service._on_close()` triggers `complication_store.drop_connection(a.id)`, instantly pruning ephemeral complications while retaining TTL-held entries.
  - **Heartbeats**: Publishes with identical visible fields only update `expires_at` and reset the timer without emitting an `event` on `complications.live`.
- **Suite Verification:**
  - Verified with `test_quotas`, `test_ttl_default_and_bounds`, `test_expiry_runs_on_a_timer_not_the_poller`, `test_hold_connection_removes_on_close`, and `test_no_change_publish_extends_ttl_without_an_event`.

### (4) Legacy `complications` Topic & `device.summary` Immutability
- **Implementation (`wlanpi_menud/complications.py:688-695`, `wlanpi_menud/poller.py:180-205`):**
  - The legacy `complications` topic payload is derived via `legacy_data()`, which filters records to only include provider entries containing `{"app_id", "label", "value", "status"}`. No `wlanpi.*` built-ins are ever injected into `complications`.
  - `device.summary` retains its exact 17-key shape (`hostname`, `model`, `mode`, `software_version`, `ip`, `eth0`, `wlan0`, `cpu`, `cpu_temp`, `uptime`, `battery`, `bluetooth_on`, `profiler_running`, `reachable`, `wlan_adapters`, `hotspot_clients`). `wlanpi.wifi` is intentionally not added to `device.summary`.
- **Suite Verification:**
  - `protocol/tests/test_schemas.py::test_legacy_complications_and_summary_are_frozen`: Schema fields pinned.
  - `tests/test_complications.py::test_legacy_topic_holds_provider_records_only_in_the_old_shape`: Verifies legacy topic excludes `wlanpi.*`.
  - `tests/test_complications.py::test_device_summary_unchanged_for_the_same_fake_core`: Asserts byte-identical payload for `device.summary`.

### (5) Built-in Tiles & `wlanpi.wifi` Cadence / Hysteresis
- **Implementation (`wlanpi_menud/complications.py:250-395`, `wlanpi_menud/poller.py:185-200`):**
  - `wlanpi.wifi` queries `GET /network/interfaces/{iface}/wlan-link`.
  - **Cadence**: When `connected: True`, queried every 2nd fast tick (every 10s); when disconnected or failed, queried only during context ticks (every 30s). Immediate re-poll is triggered if a mutating job invalidates context.
  - **Hysteresis (`wifi_hysteresis`)**: RSSI fluctuations under 3.0 dB (`RSSI_HYSTERESIS_DB`) are suppressed unless the status band changes (`ok` >= -67 dBm, `warn` -68 to -75 dBm, `error` <= -76 dBm) or SSID/detail changes.
  - When core is unreachable (`core: unavailable`), `svc.complication_store.clear_builtins()` immediately purges all `wlanpi.*` records.
- **Suite Verification:**
  - `test_wifi_mapping`, `test_wifi_hysteresis`, `test_wifi_tile_and_cadence`, `test_wifi_jitter_does_not_republish`, and `test_all_builtins_removed_while_core_is_unavailable` pass.

### (6) Display Profile Layering & Resolution
- **Implementation (`wlanpi_menud/complications.py:460-570`):**
  - Profiles: Pre-seeded with `panel.strip`, `panel`, `tui`, `webui`, `default`.
  - Layer precedence: `user` (`/var/lib/wlanpi-menud/complications.json`) overrides `site` (`/etc/wlanpi-menud/complications.json`), which overrides `default` (`/usr/share/wlanpi-menud/complications.d/defaults.json`).
  - Atomic write: Written to a dot-prefixed temporary file, flushed with `os.fsync`, and replaced via `os.replace`.
  - Resiliency: Malformed or unparseable JSON in the user configuration file is moved aside to `.bad-<timestamp>` and logged without service failure.
  - Resolution (`resolve()`): Walks `show` list, matches exact IDs first, expands wildcard families (`.*`) alphabetically for non-exact live entries, deduplicates, and caps to capacity.
- **Suite Verification:**
  - Tested against `protocol/samples/complications/resolve-vectors.json` test cases via `test_resolve_vectors`.
  - `test_layer_precedence_per_profile`, `test_corrupt_user_file_is_renamed_aside`, `test_corrupt_user_file_does_not_stop_the_service`, and `test_configure_reply_event_and_persistence` pass.

### (7) Thin Session Customize Node (`system.home_tiles`)
- **Implementation (`wlanpi_menud/handlers/system.py:56-222`, `share/menu.d/50-system.json`):**
  - Implements the §4.7 UI sketch using existing `select` prompt primitives.
  - Allows editing `Rows` (`panel`) and `Status strip` (`panel.strip`) from the default session, or session-specific profile from an independent session.
  - Visual layout displays ordered shown tiles, capacity threshold marker (`- - fits above - -`), idle indicators for unreporting selected tiles, and hidden catalog entries.
  - Actions (Show, Hide, Move up, Move down) apply immediately and broadcast updated `complications.config` events.
  - Restricted to role `operator` or higher; hidden from `viewer`.
- **Live Verification:**
  - Connected a real thin client (`mode: thin`, `session: default`) to `Server` over Unix domain socket.
  - Navigated through `system.home_tiles` -> `Rows` -> inspect/reorder -> `Reset to default` -> complete. Cleanly emitted prompt, view, and result job frames.
  - `tests/test_complications.py::test_customize_node_hide_show_move_reset_and_viewer_hidden` passes.

### (8) Live Socket CLI (`wlanpi-complication`)
- **Implementation (`wlanpi_menud/cli.py`):**
  - Installed entrypoint `wlanpi-complication` symlinked from `/opt/wlanpi-menud/bin/wlanpi-complication` to `/usr/bin/wlanpi-complication`.
  - Operates via synchronous WebSocket client over Unix socket (`/run/wlanpi-menud/local.sock`).
  - Connects with `mode: rich`, `session: new`, `ephemeral: true`.
  - Commands implemented: `publish`, `remove`, `list`, `catalog`, `show` (`--set`, `--reset`, `--json`).
  - Exit codes: `0` (Success), `2` (Usage error), `3` (Service Refused/Error), `4` (Unreachable socket).
- **Live Verification:**
  - Executed `wlanpi-complication` subprocess against an active `wlanpi-menud` server:
    - `publish local.<user>.testcli`: exit 0 (`live, ttl 30s`).
    - `list`: exit 0, lists active tile.
    - `show panel --set ...`: exit 0, applies user configuration.
    - `remove local.<user>.testcli`: exit 0 (`removed`).
  - `tests/test_complications.py::test_cli_publish_list_remove`, `test_cli_refusals_and_usage`, `test_cli_show_set_reset`, and `test_cli_service_unreachable` pass.

### (9) Test Suite & Resource Budget
- **Full Test Suite:**
  - Command: `.venv/bin/pytest`
  - Result: **412 passed in 20.91s**.
  - Code hygiene: `ruff check` passes clean across all complications modules (`wlanpi_menud/complications.py`, `wlanpi_menud/cli.py`, `tests/test_complications.py`).
- **Resource Budget Plausibility (`docs/resource-budget.md`):**
  - Polling call rate increases modestly from 0.78 to 0.87 calls/s (+1 call every 10s for `wlan-link`).
  - Idle CPU remains at ~0.42% of a single core (well below the 3.0% hardware ceiling).
  - Memory consumption (RSS) measured at 41–45 MiB (far within the 112 MiB steady limit on 512 MiB hardware).
  - Handshake connection pooling (`core_keepalive_sec = 55s`) keeps new TLS handshakes at 0–1 during steady state.

---

## 3. Final Verdict

**APPROVE** — The complications service changes across commits `cbb69c0`, `489149d`, and `35f63ff` fulfill all architectural, protocol, security, and performance requirements specified in `docs/complications-spec.md` and Decision Log §8. The service is ready for downstream surface integration (`wlanpi-webui`, `wlanpi-menu-tui`, and `wlanpi-fpms`).
