# Phase 0 Deliverables Review (`wlanpi-menud`)

**Date:** 2026-11-08  
**Target:** `wlanpi-menud` (`4e3c450`) against `FPMS Re-Architecture Plan.md`, `wlanpi-fpms` (`87b96cc`), `wlanpi-fpms2` (`0.1.2`), and `wlanpi-core` `main` (`df7ac8a`, OpenAPI `2.4.13`)  
**Verdict:** **APPROVE**

| Severity | Count |
| --- | --- |
| Blocker | 0 |
| Major | 0 |
| Minor | 5 |

---

## Checklist Verification

### (a) Protocol and Manifest Schemas (`protocol/menu.schema.json`, `protocol/frames.schema.json`, `protocol/PROTOCOL.md`)

- **Message types (16/16 covered):**
  - Client-to-Server (9): `auth`, `hello`, `subscribe`, `unsubscribe`, `input`, `invoke`, `answer`, `page`, `cancel`.
  - Server-to-Client (7): `welcome`, `menu`, `view`, `prompt`, `job`, `event`, `error`.
  - Envelope (`{"v": 1, "type": "...", "id": "..."}`), thin vs. rich mode (`hello.mode`), reconnect state (`hello.resume`, `welcome.resumed`, `epoch`, per-topic `seq`), and bounded queue limits (`welcome.limits`) match the plan.
- **View blocks (8/8 covered):**
  - `text`, `kv`, `table`, `list`, `qr`, `status`, `progress`, `confirm`.
  - Every block variant in `frames.schema.json` requires the mandatory `text` fallback (`#/$defs/fallback`), verified by schema rules, negative samples (`block-without-text.json`), and unit tests.
- **Error and close codes:**
  - All four plan-named error codes (`AUTH_FAILED`, `FORBIDDEN`, `CONFLICT`, `VERSION`) are present and supplemented by `BAD_REQUEST`, `NOT_FOUND`, `CORE_UNAVAILABLE`, `TIMEOUT`, and `INTERNAL`.
  - Conditional schema constraints enforce `fatal: true` + `close_code: 4401` on `AUTH_FAILED` and `fatal: true` + `close_code: 4426` on `VERSION`. Close codes (`1000`, `1001`, `1008`, `1013`, `4400`, `4401`, `4426`) are documented in `x-close-codes` and `PROTOCOL.md`.
- **Visibility rules:**
  - `menu.schema.json` covers device mode (`modes`, `not_modes` over `classic`, `hotspot`, `server`, `bridge`, `wiperf`, `wconsole`), hardware/platform capabilities (`transmit`, `bluetooth`, `battery`, `monitor_adapter`, `multiple_adapters`, `ethernet`, `display`, `gpio_buttons`), role (`min_role`: `viewer`, `operator`, `admin`), and core route availability (`requires_core` as a single route or list of routes).
- **Action shapes & parameters:**
  - `action` is a strict `oneOf` between `core_action` (declarative `METHOD /path` binding with `bind`, `query`, `body`, RFC 6901 `select` pointer, `view`, `shareable`, `fresh_sec`, `timeout_sec`, `cancellable`, `confirm`) and `handler_action` (`handler`, `view`, `timeout_sec`, `cancellable`, `confirm`).
  - Schema `allOf` rules enforce that mutations (`POST|PUT|PATCH|DELETE`) cannot set `shareable: true` or non-zero `fresh_sec`, and `fresh_sec > 0` requires `shareable: true`.
  - All four parameter types (`enum`, `integer`, `string` with `secret`, `adapter` with `kind` and `monitor`) match the plan.

### (b) Manifest Format Minimalism

- **Pass.** `menu.schema.json` sets `additionalProperties: false` on the top-level document, `node`, `visibility`, every `param_*` definition, `confirm`, `core_action`, and `handler_action`.
- No conditionals, loops, variables, or chained calls exist in the schema. `select` is strictly a single RFC 6901 JSON Pointer projection into core's response.
- Negative samples in `protocol/samples/invalid/manifests/` (`conditional.json`, `variable-in-body.json`, `core-and-handler.json`, `post-shareable.json`, `fresh-without-shareable.json`, `params-on-branch.json`) verify rejection.

### (c) PoC Node ID Continuity

- **Pass.** `protocol/samples/manifests/builtin.json` preserves the PoC's leaf IDs across all menu sections (`network.lldp`, `network.interfaces`, `network.wlan_interfaces`, `network.eth0_ipconfig`, `network.eth0_vlan`, `network.cdp`, `network.publicip4`, `network.publicip6`, `bluetooth.*`, `utils.*`, `modes.*`, `apps.kismet.*`, `apps.profiler.*`, `apps.scanner.*`, `system.*`).
- Where `wlanpi-fpms2`'s `menu_tree.py` had distinct `node_id` and `action_id` strings on a leaf (e.g., `utils.blinker.start` vs. `utils.port_blinker.start`, `apps.scanner.nohidden` vs. `apps.scanner.scan_nohidden`, `system.settings.datetime.show` vs. `system.date`), `builtin.json` consistently standardized on the PoC's `action_id` (and `utils.speedtest.run` for Speedtest), collapsed the two profiler purge nodes into `apps.profiler.purge` (matching core's single `POST /profiler/purge`), collapsed the per-country regulatory domain nodes into `system.reg_domain.set` with a `country` enum parameter (including `AU` from `wlanpi-fpms`), and dropped `system.updates.*` per the plan.

### (d) Core Compatibility Audit (`docs/core-audit.md`)

- **Pass.** All 37 distinct method+path pairs in `wlanpi-fpms2/wlanpi_fpms2/core_client/client.py` are audited against `WLAN-Pi/wlanpi-core` `main` at `df7ac8a` (OpenAPI `2.4.13`):
  - **28 `exists` + 2 `shape-changed` = 30 existing paths**, matching the plan's statement ("The other 30 core paths the PoC calls still exist").
  - **3 `moved`:** `GET /network/wlan/getInterfaces` (deprecated, canonical `GET /network/config/status`), `POST /profiler/purge/reports`, and `POST /profiler/purge/files` (replaced by `POST /profiler/purge`).
  - **4 `missing`:** `GET /system/updates` and `POST /system/updates/install` (dropped), `POST /system/mode/switch` (Stream A parity ask), and `GET /utils/cloud-test/{vendor}` (Stream C parity ask).
- **Specific PoC audit discoveries verified in `core-audit.md`, `parity-list.md`, and `builtin.json`:**
  1. **Bluetooth pair (`bt-timedpair` 403):** In `wlanpi_core/services/system_service.py`, `allowed_services` includes `kismet`, `wlanpi-profiler`, `bt-agent`, and `bt-network`, but **not** `bt-timedpair`. Consequently, the PoC's `start_service("bt-timedpair")` fails with HTTP 403. Both `docs/core-audit.md` (row #4, Needs table, Cross-cutting table) and `docs/parity-list.md` (row #12, `needs-rebind`) document this and rebind `bluetooth.pair` in `builtin.json` to `POST /bluetooth/pair` (gated by the `transmit` capability).
  2. **Profiler start variants passing no arguments:** In the PoC (`actions/apps/profiler.py`), all six profiler start actions call `start_service("wlanpi-profiler")` with no arguments, making every "Start Other" variant (`2.4 GHz`, `5 GHz 36`, `5 GHz 149`, `no 11r`, `no 11ax`) behave identically to plain `Start`. Both `docs/core-audit.md` (row #4, Needs table, Cross-cutting table) and `docs/parity-list.md` (rows #32–37, `needs-rebind`) document this and rebind `apps.profiler.start*` in `builtin.json` to `POST /profiler/start` with the corresponding JSON request bodies (`{}`, `{"channel": 6}`, `{"channel": 36}`, `{"channel": 149}`, `{"no11r": true}`, `{"no11ax": true}`).
  3. **Other cross-cutting items:** `TRANSMIT_ALLOWED_MODELS = {M4, M4+, R4, R5, Pro}` and `TRANSMIT_UNITS = {hostapd, wpa_supplicant, wpa_supplicant@wlan0, bt-agent, bt-network}` in `wlanpi_core/core/transmit_policy.py`; `AUTH_CLOCK_NOT_SET` (503 when wall clock is >30s behind `iat`); `GET /system/hotspot/clients` shape change (`{mode, interface, count}`, with stations at `GET /wifi/hotspot/stations`); and `GET /utils/wlan/scan` (`needsSelection`, `candidates[]`, `networks[]`) all match `wlanpi-core` `main` at `df7ac8a`.

### (e) Parity List (`docs/parity-list.md` vs. `wlanpi-fpms/fpms/fpms.py`)

- **Pass.** In `wlanpi-fpms`, the menu tree is defined in `fpms/fpms.py` (`menu` in `main()`, lines 689–962) and `fpms/modules/modes.py` (`place_hotspot_menu`, lines 54–77), not in a separate `fpms/menu.py` (`docs/parity-list.md` line 3 explicitly notes this).
- AST extraction of `fpms/fpms.py` confirms **63 leaf items** in `menu`, plus **2 mode-dependent leaf items** in `fpms/modules/modes.py` (`Modes > Hotspot > Confirm` in classic mode and `Mode > Classic Mode > Confirm` in hotspot mode) = **65 FPMS menu items**.
- All 65 items are accounted for 1-to-1 in `docs/parity-list.md` (40 `works`, 13 `needs-rebind`, 11 `missing-in-core`, 1 `drop-candidate`), plus row `P1` for the PoC-only `System > Updates` (`dropped`).
- Every non-dropped row maps to a valid node ID in `protocol/samples/manifests/builtin.json`.
- Non-tree FPMS behaviors (`key1` cycling, `key2` display rotation, `key3` reboot/shutdown toggle, post-`set_reg_domain` jump to `Show Domain`, and mode-specific home pages) are documented for Phase 3.

### (f) Resource Budget, Upgrade/Rollback Plan, and Pytest + Invalid-Sample Verification

- **`docs/resource-budget.md` & `docs/upgrade-rollback.md`:** Complete and faithful to the plan (see also **Assumptions Requiring Owner Confirmation** below).
- **Pytest execution (`wlanpi-menud/protocol/tests`):**
  - Ran `.venv/bin/pytest -v protocol/tests`: **65 passed in 0.42s** on Python 3.13.14.
- **Direct verification of invalid samples (24/24 fail for the intended constraint):**
  - Checked all 11 files in `protocol/samples/invalid/manifests/` against `menu.schema.json` and all 13 files in `protocol/samples/invalid/frames/` against both the top-level `frames.schema.json` and the specific `#/$defs/<type>` subschema to confirm each fails strictly for its targeted violation (and not an accidental typo):
    - `bad-core-ref.json`: `$.nodes[0].action.core` missing HTTP method prefix.
    - `bad-id.json`: `$.nodes[0].id` `'Network.LLDP'` rejected by lowercase `node_id` pattern.
    - `conditional.json`: `$.nodes[0]` `'if'` rejected by `additionalProperties: false`.
    - `core-and-handler.json`: `$.nodes[0].action` rejected by `oneOf` (`core_action` vs. `handler_action`).
    - `enum-without-choices.json`: `$.nodes[0].params[0]` missing required `'choices'`.
    - `fresh-without-shareable.json`: `$.nodes[0].action` `fresh_sec: 60` without `shareable: true` rejected by `allOf` conditional.
    - `params-on-branch.json`: `$.nodes[0]` `params` without `action` rejected by `dependentRequired`.
    - `post-shareable.json`: `$.nodes[0].action` `POST` with `shareable: true` rejected by mutation `allOf` conditional.
    - `schema-v2.json`: `$.schema` `2` rejected by `const: 1`.
    - `unknown-mode.json`: `$.nodes[0].visibility.modes[0]` `'turbo'` rejected by `device_mode` enum.
    - `variable-in-body.json`: `$.nodes[0].action` `'let'` rejected by `additionalProperties: false`.
    - `answer-two-kinds.json`: `answer` with both `confirmed` and `cancel` rejected by `oneOf`.
    - `auth-failed-no-close.json`: `error` with `code: "AUTH_FAILED"` missing `fatal` and `close_code` rejected by `allOf`.
    - `auth-failed-wrong-close.json`: `$.close_code` `1008` on `AUTH_FAILED` rejected (`4401` expected).
    - `auth-token-not-jwt.json`: `$.token` `'hunter2'` rejected by 3-part JWT regex.
    - `bad-key.json`: `$.key` `'enter'` rejected by `key` enum.
    - `block-without-text.json`: `$.result.blocks[0]` `kv` block without `text` fallback rejected.
    - `hello-no-mode.json`: `hello` missing required `'mode'`.
    - `job-result-without-result.json`: `job` with `state: "result"` missing required `'result'` object.
    - `missing-id.json`: `input` missing required envelope `'id'`.
    - `select-prompt-without-options.json`: `prompt` with `kind: "select"` missing required `'options'`.
    - `unknown-type.json`: `type: "poll"` rejected by top-level `oneOf`.
    - `untyped-error-code.json`: `$.code` `'OOPS'` rejected by `error_code` enum.
    - `wrong-version.json`: `$.v` `2` rejected by envelope `const: 1`.

---

## Assumptions Requiring Owner Confirmation

Two architectural assumptions in the Phase 0 documents are explicitly flagged as unconfirmed and require sign-off from the hardware and image owners before Phase 1/Phase 3 lock them in:

1. **Smallest supported board = 512 MiB RAM and four Cortex-A53 cores (`docs/resource-budget.md` lines 7–10)**
   - **Why it matters:** `wlanpi-core` enumerates `M4`, `M4+`, `R4`, `R5`, `Pro`, and `Go` (`transmit_policy.py`), while `wlanpi-fpms` still carries the `pinctrl-bcm2835` GPIO label alongside `bcm2711` and `rp1`. The 112 MiB combined steady RSS ceiling (`<= 64 MiB` service + `<= 48 MiB` TUI, ~22% of 512 MiB) and the recommendation to drop FastAPI/uvicorn in favor of plain `websockets` (~14 MiB savings) are sized for a 512 MiB board.
   - **Action needed:** Hardware owners to confirm whether any 512 MiB BCM2837-class board is still supported for Trixie, or whether 1 GiB is the actual floor across `M4`/`M4+`/`R4`/`R5`/`Pro`/`Go`.

2. **Per-slot `/etc` on A/B image switches (`docs/upgrade-rollback.md` lines 6–18, 29–35, 84–85)**
   - **Why it matters:** The plan specifies only that `/home` persists across A/B slots and `/var` does not. `docs/upgrade-rollback.md` assumes `/etc` is per-slot (replaced by the new slot's image unless copied by image tooling). This assumption drives two design choices:
     1. Storing user-editable manifest overrides, shortcuts, role assignments, and provider allowlists in `/home/wlanpi/.config/wlanpi-menud/` so they survive slot switches.
     2. Treating `/etc/wlanpi-fpms.conf` (`menu_backend = legacy | wlanpi-menud`) as per-slot so an opt-in reverts to the new image's default on an A/B upgrade unless persisted under `/home`.
   - **Action needed:** Image maintainers to confirm (a) whether `/etc` (or an overlay/copy list within `/etc`) is carried across A/B slot upgrades, and (b) whether the A/B upgrade tool replays third-party `.deb` packages installed in the old slot.

---

## Findings

### Blocker (0)

None.

### Major (0)

None.

### Minor (5)

1. **`handler_action` lacks `mutates` (and `shareable`/`fresh_sec`) in `menu.schema.json`**
   - **Location:** `protocol/menu.schema.json` lines 115–118, 500–530; `protocol/frames.schema.json` line 392.
   - **Details:** `menu.schema.json` states that `visibility.min_role` defaults to *"viewer for reads, operator for mutations (decided by the loader from the action)"*, and `frames.schema.json` exposes `mutates: boolean` on `menu_node`. For `core_action`, the HTTP method (`GET` vs. `POST|PUT|PATCH|DELETE`) determines whether the action mutates. However, `handler_action` has no `mutates` (or `shareable`/`fresh_sec`) property in `menu.schema.json`, so the loader cannot determine from the manifest alone whether a handler node (e.g., `network.interfaces` or `system.help` vs. `system.timezone.set` or `apps.scanner.pcap_start`) is a read or a mutation without consulting hardcoded handler metadata.
   - **Recommendation:** Add optional `"mutates": {"type": "boolean", "default": false}` (and optional `shareable`/`fresh_sec` for read handlers) to `handler_action` in `menu.schema.json`.

2. **Local manifest override collision rule in `wlanpi_menud/manifest.py` and 4th manifest path**
   - **Location:** `wlanpi_menud/manifest.py` lines 60–73; `protocol/menu.schema.json` line 5; `docs/upgrade-rollback.md` lines 29–30.
   - **Details:** The plan (line 80) and `docs/upgrade-rollback.md` (line 30) describe local manifest overrides in `/etc/wlanpi-menud/menu.d/` and `/home/wlanpi/.config/wlanpi-menud/`. However, `semantic_errors()` in `wlanpi_menud/manifest.py` unconditionally rejects any node ID that already exists in `builtin_files` (`"{nid} collides with a built-in id"`). While plugins/providers must never collide with built-in IDs (plan line 175), local override files either need defined merge/override semantics in `manifest.py` and `PROTOCOL.md` or clarification that local files in `menu.d` may only add new nodes or replace built-ins before cross-file duplicate checking. In addition, update `menu.schema.json`'s `description` to mention `/home/wlanpi/.config/wlanpi-menud/` alongside `/etc/wlanpi-menud/menu.d/`.

3. **Path placeholder regex mismatch between `menu.schema.json` and `wlanpi_menud/manifest.py`**
   - **Location:** `protocol/menu.schema.json` lines 45–49; `wlanpi_menud/manifest.py` lines 20, 82–86.
   - **Details:** `core_ref` in `menu.schema.json` matches `^(GET|POST|PUT|PATCH|DELETE) /[A-Za-z0-9_\-./{}]*$`, which allows uppercase characters, hyphens, or empty braces inside `{...}` (e.g., `GET /utils/cloud-test/{Vendor}` or `GET /foo/{}`). Meanwhile, `_PLACEHOLDER` in `wlanpi_menud/manifest.py` only extracts `\{([a-z][a-z0-9_]*)\}`. A malformed placeholder like `{Vendor}` passes JSON Schema validation and is silently skipped by `_PLACEHOLDER.findall()`, bypassing the unbound path parameter check.
   - **Recommendation:** Match `r"\{([^}]*)\}"` in `manifest.py` (flagging any placeholder that is not a valid `segment` or not bound to `"path"`), or tighten the `core_ref` pattern in `menu.schema.json`.

4. **Loose `params`, `fields`, and `confirm` subschemas in `frames.schema.json` for codegen**
   - **Location:** `protocol/frames.schema.json` lines 375–389, 474–478.
   - **Details:** The plan notes that `wlanpi-menud/protocol/` will generate Python models and TypeScript types. In `frames.schema.json`, `menu_node.params.items` and `prompt.fields.items` are typed loosely as `{"type": "object", "required": ["name", "label", "type"]}` (and `menu_node.confirm` omits `"additionalProperties": false`), which will generate untyped `dict[str, Any]` / `Record<string, unknown>` definitions instead of typed parameter unions matching `menu.schema.json`.
   - **Recommendation:** Share or duplicate the `param` union (`param_enum`, `param_integer`, `param_string`, `param_adapter`) and `confirm` definition (`additionalProperties: false`) in `frames.schema.json`.

5. **Phase 0 exit tracking items outside `wlanpi-menud/docs/`**
   - **Location:** `FPMS Re-Architecture Plan.md` lines 323, 327–329; `docs/core-audit.md` lines 118–120.
   - **Details:** The plan's Phase 0 section lists generating the core client from `docs/openapi.json` (which `docs/core-audit.md` tracks under `Follow-ups` for Phase 1 service implementation) and filing the `wlanpi-core` issues (`did=wlanpi-menud` reservation, `POST /system/mode/switch`, `GET /utils/cloud-test/{vendor}`, and Stream D capture sessions). Recording the `wlanpi-core` issue URLs/numbers in `docs/core-audit.md` and `docs/parity-list.md` once filed will close out the non-doc exit items.
