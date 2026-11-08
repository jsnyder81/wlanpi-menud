> **DRAFT — not filed.** File via GitHub when Phase 0 exits. Target repo: `WLAN-Pi/wlanpi-core`.

# Title

Add `GET /utils/cloud-test/{vendor}` — per-vendor cloud reachability tests (Stream C)

## Labels suggestion

`enhancement`, `parity`, `api-gap` — priority: gates the new-panel default (panel parity).

## Context

The FPMS re-architecture (*FPMS Re-Architecture Plan.md*, section **“wlanpi-core
integration”** → “Core changes”, row “`GET /utils/cloud-test/{vendor}` — Panel
parity — Stream C; not built yet”) requires every menu action to run through
core. Today the six cloud tests are hard-coded shell/socket checks inside FPMS
(`wlanpi-fpms` `fpms/modules/cloud_tests.py`: `test_arista_cloud`,
`test_aruba_cloud`, `test_extreme_cloud`, `test_meraki_cloud`,
`test_mist_cloud`, `test_ruckus_cloud`). The parity list (`wlanpi-menud/docs/
parity-list.md` rows 15–20) is blocked on this endpoint for all six vendors.

Verified against `main` at `df7ac8a` (OpenAPI 2.4.13): **no such route exists**
— `utils_api.py` ships `/reachability`, `/speedtest`, `/blinker/*`, `/usb`,
`/ufw`, `/wlan/scan` only; nothing matching `cloud` in `docs/openapi.json` or
`wlanpi_core/`. Planned in core's own docs:

- `docs/core-worker-api.md:268` (Stream C): “`GET /utils/cloud-test/{vendor}` —
  Per-vendor script”.
- `docs/api-gap-matrix.csv:72`: P0, Gap, Both, build, 60 s — “Per-vendor
  reachability. UI→Core job.” Vendors listed: `cloud.arista; cloud.aruba;
  cloud.extreme; cloud.meraki; cloud.mist; cloud.ruckus`.

## Proposed API shape

Modeled on the neighboring `GET /utils/reachability` (`utils_api.py:24-83`) —
same “run checks, return rows, never fail the whole call because one row
failed” contract — with the structured, camelCase-aliased style of
`schemas/utils/utils.py:25-51`:

```
GET /api/v1/utils/cloud-test/{vendor}
Authorization: Bearer <jwt>   (or localhost HMAC)
```

| Path param | Enum | `arista` \| `aruba` \| `extreme` \| `meraki` \| `mist` \| `ruckus` (422 otherwise, FastAPI enum — same pattern as `/bluetooth/power/{action}`, `utils_api.py` blinker routes). |
| Query | `namespace` (optional, like `/utils/reachability`) | Run the checks inside a network namespace; omit or `root` for the default namespace. |

**200** — `CloudTestResult`:

```json
{
  "vendor": "arista",
  "success": false,
  "checks": [
    { "name": "Eth0 Port Up",  "result": "PASS", "detail": "yes" },
    { "name": "DHCP Address",  "result": "PASS", "detail": "192.0.2.7" },
    { "name": "Redirector 443", "result": "FAIL", "detail": "timeout" },
    { "name": "Cloud Endpoint", "result": "SKIP", "detail": "not attempted after failure" }
  ],
  "testedAt": "2026-11-08T10:15:00+00:00"
}
```

| Field | Notes |
| --- | --- |
| `success` | `true` only when every check is `PASS`. Surfaces render one tick/cross line per `checks[]` entry, like FPMS's table today. |
| `checks[].result` | `PASS` \| `FAIL` \| `SKIP`. A timing-out row is `FAIL`, not a 5xx — mirrors `/utils/reachability` semantics (`utils_api.py:60-83`). |
| `testedAt` | Same field style as `SpeedTest.testedAt` (`schemas/utils/utils.py:61-77`). |

Errors: **401** auth; **422** unknown vendor (path enum); **503**
`{"error": "CLOUD_TEST_UNAVAILABLE", "message": ...}` when the vendor's check
script is missing or the environment can't be inspected at all (e.g. eth0
absent on a model without one → prefer returning rows with `FAIL` over 503).
Document a client timeout ≥60 s (gap matrix column) and recommend UIs run it as
a job, as the speedtest docstring already does (`utils_api.py:92-120`).

Implementation notes: port each `test_*_cloud` body from
`wlanpi-fpms/fpms/modules/cloud_tests.py` into a per-vendor script under
`/opt/wlanpi-common/cloudtests/` (or a Python module in core) emitting the JSON
above; keep the existing per-check 2 s socket timeouts so the whole call stays
under ~30 s.

## Acceptance criteria

- [ ] All six vendors return structured `checks[]` with the same check names FPMS shows today (panel parity means people recognize the output).
- [ ] One failing check yields a `FAIL` row and `success: false`, not an HTTP error.
- [ ] Unknown vendor → 422; route appears in `docs/openapi.json`; gap matrix row 72 flipped to Live.
- [ ] Runs in ≤60 s on an offline board (every row `FAIL`, no hang) — DNS/connect timeouts enforced server-side.
- [ ] Unit tests per vendor with mocked sockets; on-device smoke test for at least aruba and meraki.

## Who needs it

- **menud service**: rebinds the six `utils.cloud.*` handlers; until built the nodes are hidden with `requires_core = "/api/v1/utils/cloud-test/{vendor}"`.
- **Panel parity**: all six Utils > Cloud Tests rows (parity list 15–20) must work before the new stack becomes the FPMS default.
- **TUI**: same handler, rendered as a simple table.
