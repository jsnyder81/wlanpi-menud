> **DRAFT — not filed.** File via GitHub when Phase 0 exits. Target repo: `WLAN-Pi/wlanpi-core`.

# Title

Build the Stream D capture-session REST API (`/wifi/capture/*`) — what scanner CSV and pcap actions move to

## Labels suggestion

`enhancement`, `parity`, `api-gap` — priority: gates the new-panel default (panel parity); larger build (session hub).

## Context

The FPMS re-architecture (*FPMS Re-Architecture Plan.md*, section **“wlanpi-core
integration”** → “Core changes”, row “Scanner CSV and pcap file actions — Panel
parity — Port to core's capture-session API (core-worker-api.md Stream D:
capture sessions and their files). Gap: designed in wifi-capture.md, not
implemented yet”) rebinds three parity-list actions
(`wlanpi-menud/docs/parity-list.md` rows 42–44): Apps > Scanner > **Scan to
CSV**, **Scan to PCAP > Start**, **Scan to PCAP > Stop**. FPMS implements these
today by driving `dumpcap` and a scan loop itself (`wlanpi-fpms`
`fpms/modules/apps/scanner.py`).

Verified against `main` at `df7ac8a` (OpenAPI 2.4.13):

- **No REST capture API exists.** `docs/api/wifi-capture.md:3-8` says so in its
  own header: “Status: Design, not implemented. No REST capture API exists
  yet.” Nothing matching `capture_session`/`capture-session` in `wlanpi_core/`.
- The only shipped capture surface is `WS /api/v1/streaming/capture`
  (`wlanpi_core/api/api_v1/endpoints/streaming_api.py:93`), now authenticated
  with the first-frame `{"command":"auth","token":...}` pattern and close code
  4401 (`streaming_api.py:18,30-88`; `?token=` in the URL is refused, :37).
  The menu actions need REST **sessions with files**, not a live byte stream:
  a menu item must start a bounded capture, stop it, and leave a file on disk.
- Design exists and is recent: `docs/core-worker-api.md:274-289` (Stream D
  table) and `docs/api/wifi-capture.md` §4–§5.8 (request/response shapes).
- **Doc inconsistency to fix in passing:** `docs/api-gap-matrix.csv:59-62`
  still lists the pre-session shape (`/wifi/capture/start|stop|status|files`),
  and `:83-86` lists `/scanner/scan/csv`, `/scanner/capture/start|stop` as
  separate P1 “defer” rows. The session design in wifi-capture.md supersedes
  all of these; the matrix rows should point at the session API.

## Proposed API shape

Implement Stream D exactly as designed in `docs/api/wifi-capture.md` (that doc
is the contract; this draft only scopes the subset the menu needs and one
CSV addition):

| Endpoint | Purpose (menu need) |
| --- | --- |
| `GET /api/v1/wifi/capture/sources` | Adapter choice for the `adapter` parameter; namespace-aware, `usable`/`risk`/`alternatives` flags (wifi-capture.md §4). |
| `POST /api/v1/wifi/capture/sessions` | “Scan to PCAP > Start” → `{"mode": "file", "sources": [{"sourceId": "root/wlanpi0"}], "filter": {"frameFilter": "all", "stripPayload": false, "bands": [...]}, "autoStart": true}`; “Scan to CSV” → `mode: "summary"` with `outputFormats: ["csv"]` (below). 201 returns `sessionId`, `state`, `controlToken` (§5.1). |
| `GET /api/v1/wifi/capture/sessions/{id}` | Job progress while a capture runs (§5.4). |
| `POST /api/v1/wifi/capture/sessions/{id}/stop` | “Scan to PCAP > Stop”; idempotent, needs `X-Capture-Control-Token` (§5.3, §5.6). |
| `GET /api/v1/wifi/capture/sessions/{id}/files` | Lists written pcapng **and CSV** outputs with paths and sizes (§5.8) — this is how the menu confirms “saved to /home/wlanpi/captures/…”. |

`PATCH .../sessions/{id}` and `GET .../frames` (§5.5, §5.7) can land in the
same effort or a follow-up; the menu needs only the five rows above plus stop.

**One addition beyond the current design — CSV output.** The scanner's “Scan to
CSV” is a beacon-derived table, not a pcap. Proposed: accept
`"outputFormats": ["csv"]` (default `["pcapng"]` for `mode: "file"`, ignored for
`summary`) on session create; on stop, core writes one CSV per source beside
the capture files, with FPMS's current scanner columns —
`SSID, BSS, RSSI, Channel, Time` (`wlanpi-fpms` `fpms/modules/apps/scanner.py:155`,
header written at scan start, one row per network as seen) — so panel output
parity holds. Files land
under `/home/wlanpi/captures/<sessionId>/` (0750, `wlanpi` owner) and are
listed by `GET .../files` as `{"files": [{"path", "format", "sizeBytes",
"modifiedAt"}]}`.

Security defaults per the design doc: JWT/HMAC on all REST routes, control
token for stop/PATCH, `subscriberAccess: "token"` default.

## Acceptance criteria

- [ ] Session lifecycle works end-to-end: create (file mode) → running → stop → `GET .../files` lists a readable pcapng; stop is idempotent.
- [ ] `sources` discovery flags monitor siblings and refuses connected managed interfaces without `allowDisruptive` (§4.4, §5.2).
- [ ] CSV output: `outputFormats: ["csv"]` produces the FPMS-equivalent columns from beacons captured during the session.
- [ ] Multi-monitor-adapter boards get `needsSelection: true` from `/sources`, matching the scan API pattern.
- [ ] Namespace-aware (captures in a profile namespace work from a root-namespace caller).
- [ ] Routes appear in `docs/openapi.json`; `api-gap-matrix.csv` rows 59–62 and 83–86 are collapsed onto the session API; wifi-capture.md status flips from “Design” to implemented.
- [ ] No regression to `WS /streaming/capture`'s existing auth contract.

## Who needs it

- **menud service**: rebinds `apps.scanner.csv`, `apps.scanner.pcap_start`, `apps.scanner.pcap_stop` as jobs; until built the nodes are hidden with `requires_core = "/api/v1/wifi/capture/sessions"`.
- **Panel parity**: the three Scanner rows must work before the new stack becomes the FPMS default.
- **Later**: the WebUI scanner view and MCP tools use the same sessions (`summary` mode) — one build serves all three consumers, which is why the design doc already exists.
