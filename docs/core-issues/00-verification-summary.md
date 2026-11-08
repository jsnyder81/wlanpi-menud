# wlanpi-core endpoint verification summary (Phase 0 → issue drafts)

Verification for the core issues in this directory. Everything below was checked
against a fresh clone of <https://github.com/WLAN-Pi/wlanpi-core> at
`/tmp/core-verify`, current `main` = `df7ac8a` (6 Nov 2026), OpenAPI
`info.version` 2.4.13 (80 paths in `docs/openapi.json`). This is the same commit
the Phase 0 audit (`../core-audit.md`) used, so the audit and this verification
share a baseline.

Authoritative context: *FPMS Re-Architecture Plan.md*, section **“wlanpi-core
integration”** (the “PoC against current core” table and the “Core changes”
table).

## Endpoint-by-endpoint table

| # | Item | Exists? | Evidence (file:line at `df7ac8a`) | Draft |
| --- | --- | --- | --- | --- |
| a | `POST /system/mode/switch` | **no — missing, confirmed** | No route in `wlanpi_core/api/api_v1/endpoints/system_api.py` (routes at lines 50–556; grep for `mode/switch` across `wlanpi_core/` returns nothing); absent from `docs/openapi.json` paths. Planned: `docs/core-worker-api.md:239` (Stream A, “Mode switcher scripts; 409 if config active”), `docs/api-gap-matrix.csv:18` (P0, Gap, build). Mode *read* exists: `get_mode()` at `wlanpi_core/services/system_service.py:177` reading `/etc/wlanpi-state` (`wlanpi_core/constants.py:27`); `DeviceInfo.mode` at `wlanpi_core/schemas/system/system.py:41`; `require_mode()` 409 guard at `wlanpi_core/core/mode_guard.py:12`. The five switcher-script constants are already declared but unused: `wlanpi_core/constants.py:31-35`. | `02-mode-switch-endpoint.md` |
| b | `GET /utils/cloud-test/{vendor}` | **no — missing, confirmed** | `utils_api.py` routes are `/reachability` (:24), `/speedtest` (:92), `/blinker/start` (:136), `/blinker/stop` (:154), `/blinker/status` (:168), `/usb` (:182), `/ufw` (:260), `/wlan/scan` (:285, :310); no cloud-test anywhere (grep). Planned: `docs/core-worker-api.md:268` (Stream C), `docs/api-gap-matrix.csv:72` (P0, Gap, Both, build, 60 s timeout). | `03-cloud-test-endpoint.md` — **filed as #481** |
| c | Capture-session API (Stream D) for scanner CSV + pcap | **no — designed, not built, confirmed** | `docs/api/wifi-capture.md:3-8`: “Status: Design, not implemented. No REST capture API exists yet.” Only shipped capture surface is `WS /api/v1/streaming/capture` (`wlanpi_core/api/api_v1/endpoints/streaming_api.py:93`) with first-frame JWT auth and close code 4401 (`streaming_api.py:18,30-88`; `?token=` in URL refused at :37). Session REST table: `docs/core-worker-api.md:274-289`; design shapes: `docs/api/wifi-capture.md` §4–§5.8. | `04-capture-session-api.md` |
| d | `GET /system/updates`, `POST /system/updates/install` | **no — still absent, as expected** | No routes; absent from `docs/openapi.json`. `docs/api-gap-matrix.csv:89-90` (P1, Gap, **defer**, “fpms2 only”). | **No issue needed** — dropped from the new menu per the plan (“wlanpi-core integration” → PoC table row 4). |
| e | Token introspection endpoint | **no — absent, as expected** | Only `/api/v1/auth/token` (POST, DELETE) appears in `docs/openapi.json` auth paths; `GET /auth/signing_keys` and `/auth/debug/db-state` are HMAC-only and `include_in_schema=False` (`auth_api.py:405-418`). Internal machinery for introspection already exists: `TokenManager.verify_token()` (`wlanpi_core/core/token.py:335`), `TokenValidationResult{is_valid, device_id, payload}` (`token.py:64-72`), claims incl. `did`/`jti`/`exp` (`token.py:364`), revocation check (`token.py:353-354`). | `05-token-introspection.md` |
| f | Reserved `did` — does core reserve `wlanpi-menud`? | **no — only `wlanpi-webui` is reserved** | `PAM_CLIENT_DEVICE_ID = "wlanpi-webui"` (`wlanpi_core/core/auth.py:23`) is the only reserved id. `POST /auth/token` refuses non-HMAC minting of *that* id only (`api/api_v1/endpoints/auth_api.py:313-319`); a bearer can mint only its own `did` (`auth_api.py:320-322`). `getjwt` accepts any device-id string with no allowlist (`wlanpi_core/cli/getjwt.py:109,77-90`), so `sudo getjwt wlanpi-menud` already mints a working token today — but nothing declares `wlanpi-menud` reserved or protects it from future mint-path changes. PAM endpoints accept only `did=wlanpi-webui` (`core/auth.py:69`), so a menud token cannot reach PAM (fine: the plan says menud never calls PAM). | `01-reserve-wlanpi-menud-did.md` |
| g | `bt-timedpair` service-start finding | **confirmed absent from allowlist — but status code is 400, not 403 (audit correction)** | `bt-timedpair` is not in `allowed_services` (`wlanpi_core/services/system_service.py:42-68`); `start_systemd_service` then raises `ValidationError(..., status_code=400)` (`system_service.py:565-568`). 403 comes only from the transmit gate (`system_api.py:198-213`), and `bt-timedpair` is not in `TRANSMIT_UNITS` (`wlanpi_core/core/transmit_policy.py:9-17`). Replacement exists: `POST /bluetooth/pair` (`bluetooth_api.py:93,108`) starts `bt-timedpair.service` internally (`wlanpi_core/services/bluetooth_service.py:427`). | **No issue needed** — rebind the menu action to `POST /bluetooth/pair` (already in `../core-audit.md` “needs” table). |

## Corrections to the Phase 0 audit (`../core-audit.md`)

1. **Row 4 / cross-cutting table: `bt-timedpair` start returns 400, not 403.**
   The audit says the PoC's Bluetooth pair “gets 403”. Actual behavior at
   `df7ac8a`: services outside the allowlist fail with **400** “starting
   bt-timedpair is restricted or does not exist”
   (`wlanpi_core/services/system_service.py:565-568`); 403 is reserved for
   transmit-capable units on models with transmit disabled
   (`transmit_policy.py:9-17` — `bt-timedpair` is not a transmit unit). The
   conclusion is unchanged (rebind to `POST /bluetooth/pair`), but the error
   contract the menud client should branch on is 400.
2. **Minor, internal to core docs (worth a note in draft 04):** the gap matrix
   rows for capture (`api-gap-matrix.csv:59-62`: `/wifi/capture/start|stop|status|files`)
   predate the session-based design in `docs/api/wifi-capture.md`
   (`/wifi/capture/sessions` CRUD). The design doc is the newer contract; the
   matrix rows are stale.
3. All other “missing” findings (a, b, c, d, e) are **confirmed correct** at
   `df7ac8a`. Item (f) refines the plan’s wording: no core *code* change is
   strictly required to mint a `wlanpi-menud` token via `getjwt` today, but the
   id is not reserved, which is what draft 01 asks for.

## Draft index

| Draft | Blocks | Phase |
| --- | --- | --- |
| `01-reserve-wlanpi-menud-did.md` | menud's own core calls | First release (Phase 1) |
| `02-mode-switch-endpoint.md` | Modes > Hotspot / Classic panel parity rows (parity list #26, #27) | Gates panel default |
| `03-cloud-test-endpoint.md` | Utils > Cloud Tests (parity list #15–#20) | Gates panel default — **FILED as [#481](https://github.com/WLAN-Pi/wlanpi-core/issues/481)** |
| `04-capture-session-api.md` | Scanner > Scan to CSV / PCAP (parity list #42–#44) | Gates panel default |
| `05-token-introspection.md` | Remote JWT clients | Later (Phase 4) |

These are **drafts only** — except 03, filed as #481 (Nov 8 2026); nothing else has been filed to GitHub as of this commit.
