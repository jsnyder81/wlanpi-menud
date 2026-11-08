> **DRAFT — not filed.** File via GitHub when Phase 0 exits. Target repo: `WLAN-Pi/wlanpi-core`.

# Title

Add `POST /system/mode/switch` — device mode switching over the API (Stream A)

## Labels suggestion

`enhancement`, `parity`, `api-gap` — priority: gates the new-panel default (panel parity).

## Context

The FPMS re-architecture (*FPMS Re-Architecture Plan.md*, section **“wlanpi-core
integration”** → “Core changes”, row “`POST /system/mode/switch` — Panel parity
— core-worker-api.md Stream A; not built yet”) moves every menu action behind
core's REST API. Today FPMS runs the mode switcher scripts itself
(`wlanpi-fpms` `fpms/modules/modes.py` → `/opt/wlanpi-hotspot/hotspot_switcher`).
The parity list (`wlanpi-menud/docs/parity-list.md` rows 26–27: Modes > Hotspot,
Mode > Classic) is blocked on this endpoint.

Verified against `main` at `df7ac8a` (OpenAPI 2.4.13): **no such route exists**
— nothing in `wlanpi_core/api/api_v1/endpoints/system_api.py`, nothing under any
path in `docs/openapi.json`. The design is already written down:

- `docs/core-worker-api.md:239` (Stream A): “`POST /system/mode/switch` — Mode
  switcher scripts; 409 if config active”.
- `docs/core-worker-api.md:56`: auth-gated, must work from any current mode;
  **409 with a clear message when a namespace config is active unless
  `force: true`** (which deactivates configs first).
- `docs/api-gap-matrix.csv:18`: P0, Gap, Core, build — “Body `{mode}`. API must
  work in any mode. Namespace activate/deactivate remains classic-only.”

The pieces to wrap already exist in core: `get_mode()` reads
`/etc/wlanpi-state` (`wlanpi_core/services/system_service.py:177`,
`wlanpi_core/constants.py:27`), the valid mode set is
`["classic", "wconsole", "hotspot", "wiperf", "server", "bridge"]`
(`system_service.py:179`), the five switcher scripts are already constants
(`wlanpi_core/constants.py:31-35`, currently unused), and `require_mode()`
shows the 409 house style (`wlanpi_core/core/mode_guard.py:12-25`).

## Proposed API shape

Following the neighboring `POST /system/reboot` / `/shutdown`
(`system_api.py:503-531`, flat `PowerActionResponse {status}`) and the
`{mode}` body the gap matrix already promises:

```
POST /api/v1/system/mode/switch
Authorization: Bearer <jwt>   (or localhost HMAC, same as all /system routes)
```

```json
{ "mode": "hotspot", "force": false }
```

| Field | Type | Notes |
| --- | --- | --- |
| `mode` | enum | One of `classic`, `wconsole`, `hotspot`, `wiperf`, `server`, `bridge` (same set as `get_mode()`). |
| `force` | bool, default `false` | Deactivate any active namespace config(s) first; without it they make the switch a 409. |

**200** — `ModeSwitchResponse`:

```json
{ "status": "switched", "previousMode": "classic", "mode": "hotspot" }
```

Errors, in core's existing `ApiErrorResponse`/`ValidationError` style:

| Code | When |
| --- | --- |
| 400 | Unknown `mode` value. |
| 401 | Missing/invalid token (incl. `AUTH_CLOCK_NOT_SET` 503 branch, unchanged). |
| 403 | Target mode needs a transmit unit (`hostapd`, `wpa_supplicant`) on a model with transmit disabled (Go / unknown) — reuse `transmit_gate` (`core/transmit_policy.py:9-17`). |
| 409 | A namespace config is active and `force` is `false`; message names the config(s). |
| 503 | Switcher script missing or exited non-zero; `{"error": "MODE_SWITCH_FAILED", ...}` with stderr summary. |

Implementation notes: run the matching `*_switcher` script with
`asyncio.to_thread`/`create_subprocess_exec` (no `shell=True`), write nothing to
`/etc/wlanpi-state` directly (the scripts own that), serialize switches with a
single lock, and treat the call as long-running (document a ≥60 s client
timeout, like `/utils/speedtest` at `utils_api.py:92`). Add the route to
`system_api.py`, a schema in `schemas/system/system.py`, and a row flip in
`api-gap-matrix.csv` (Gap → Live).

## Acceptance criteria

- [ ] Switching works from and to every mode on every supported model, including while the API caller is in a different mode than the target.
- [ ] 409 (with config names) when a namespace config is active; `force: true` deactivates and switches.
- [ ] 403 on transmit-disabled models for modes that start hostapd/wpa_supplicant, matching the existing transmit-gate detail strings.
- [ ] `GET /system/device/info` reflects the new `mode` after a 200 response returns.
- [ ] Route appears in `docs/openapi.json`; gap matrix row 18 marked Live; unit + on-device tests for each mode pair on at least R4 and Pro.

## Who needs it

- **menud service**: rebinds `modes.hotspot` / `modes.classic` handlers (parity list rows 26–27) — until built, those nodes are hidden with `requires_core = "/api/v1/system/mode/switch"`.
- **Panel parity**: required for the new stack to become the FPMS default.
- **TUI**: same handlers, no extra work.
