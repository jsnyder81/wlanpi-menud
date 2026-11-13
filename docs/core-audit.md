# Core compatibility audit: wlanpi-fpms2 PoC against wlanpi-core main

Completes the plan's "PoC against current core" table (Phase 0).

| | |
| --- | --- |
| PoC | `wlanpi-fpms2` 0.1.2, `wlanpi_fpms2/core_client/client.py` (the only module that talks to core; actions and `state/periodic.py` call its methods) |
| Core | `WLAN-Pi/wlanpi-core` `main` at `df7ac8a` (6 Nov 2026), OpenAPI `info.version` 2.4.13 |
| Sources | `docs/openapi.json`, `docs/core-worker-api.md`, `docs/api-gap-matrix.csv`, `docs/API-DEPRECATED-ENDPOINTS.md`, `wlanpi_core/services/system_service.py` (service allowlist), `wlanpi_core/core/transmit_policy.py` |
| Method | Every path literal in the PoC client, matched by method and path against `openapi.json` `paths` (prefix `/api/v1`); request parameters and response schemas compared with the PoC's pydantic models in `core_client/models.py` |

## Status key

| Status | Meaning |
| --- | --- |
| exists | Same method and path. Core's response is a superset of what the PoC reads, so the PoC model still parses. |
| shape-changed | Same method and path, but the response or error contract changed in a way the PoC does not handle. |
| moved | Gone or deprecated at this path; a replacement exists. |
| missing | No route in core. |

## Result

**37 endpoints audited: 28 exist, 2 shape-changed, 3 moved, 4 missing.**

That agrees with the plan's "the other 30 core paths still exist" (28 exist + 2 shape-changed). The PoC client has 37 distinct method+path pairs because `get_interfaces()` hits both `/network/interfaces` and `/network/interfaces/{interface}`.

## Endpoint table

"PoC caller" names the client method and where it is used. Three client methods are never called (marked *unused*).

| # | PoC call | PoC caller | Core main | Status | Notes and action |
| --- | --- | --- | --- | --- | --- |
| 1 | `GET /system/device/info` | `get_device_info`: system.about, periodic | `DeviceInfo` | exists | Core adds required `wlan_management`. |
| 2 | `GET /system/device/stats` | `get_device_stats`: system.summary, periodic | `DeviceStats` | exists | Same fields. |
| 3 | `GET /system/service/status?name=` | `get_service_status` (*unused*) | `ServiceStatus` | exists | Allowlist enforced. |
| 4 | `POST /system/service/start?name=` | `start_service`: kismet, profiler start, bluetooth pair | `ServiceRunning`, 403 | exists | Core allowlist includes `kismet` and `wlanpi-profiler` but **not `bt-timedpair`**, so the PoC's Bluetooth pair gets 403. Rebind pair to `POST /bluetooth/pair`, profiler start to `POST /profiler/start`. |
| 5 | `POST /system/service/stop?name=` | `stop_service`: kismet, profiler stop | `ServiceRunning` | exists | Prefer `POST /profiler/stop` for the profiler. |
| 6 | `GET /bluetooth/status` | `get_bluetooth_status`: bluetooth.status, periodic | `BluetoothStatus` | exists | Core adds optional `blocked`. |
| 7 | `POST /bluetooth/power/{action}` | `set_bluetooth_power`: bluetooth.on/off | `PowerState`, 403 | exists | 403 on the Go and unknown models (transmit policy covers Bluetooth units). Hide "Turn On" with the `transmit` capability. |
| 8 | `GET /network/interfaces` | `get_interfaces()`: network.interfaces, wlan_interfaces, periodic | `{group: IPInterface[]}` | exists | Core adds `qdisc`, `group`, `txqlen`, `namespace`. |
| 9 | `GET /network/interfaces/{interface}` | `get_interfaces("eth0")`: network.eth0_ipconfig | same | exists | |
| 10 | `GET /network/ethernet/{interface}/vlan` | `get_vlans`: network.eth0_vlan | same | exists | Optional `vlan` query added. |
| 11 | `GET /network/wlan/getInterfaces` | `get_wlan_interfaces` (*unused*) | Deprecated | moved | Redirected to `iw dev`. Canonical: `GET /network/config/status`. Drop the method. |
| 12 | `GET /network/info/` | `get_network_info`: lldp, cdp, publicip4 | `NetworkInfo` | exists | Core adds `eth0_ipconfig_info`, which suits "Eth0 IP Config" better than the per-interface call. |
| 13 | `GET /utils/reachability` | `get_reachability`: utils.reachability, periodic | `ReachabilityTest`, 400/503 | exists | Optional `targets`, `namespace` query and `custom` field added. |
| 14 | `GET /utils/usb` | `get_usb` | `Usb` | exists | |
| 15 | `GET /utils/ufw` | `get_ufw` | `Ufw` | exists | |
| 16 | `POST /system/reboot` | `reboot` | `PowerActionResponse` | exists | |
| 17 | `POST /system/shutdown` | `shutdown` | `PowerActionResponse` | exists | |
| 18 | `GET /system/timezone` | `get_timezone` | `TimezoneInfo` | exists | |
| 19 | `GET /system/timezone/list` | `list_timezones` (*unused*) | `TimezoneList` | exists | The PoC builds its timezone list locally. The new "Manual" picker should use this route. |
| 20 | `POST /system/timezone/set` | `set_timezone` | body `TimezoneSetRequest` | exists | Body `{timezone}` matches. |
| 21 | `POST /system/timezone/auto` | `set_timezone_auto` | `NtpAutoInfo` | exists | Response is `{ntp, timezone}`. The PoC parses it as `TimezoneInfo`, which still works. |
| 22 | `GET /system/reg-domain` | `get_reg_domain` | `RegDomainInfo` | exists | |
| 23 | `POST /system/reg-domain/set` | `set_reg_domain` | body `RegDomainSetRequest` | exists | Body `{country}` matches. `GET /system/reg-domain/list` is also available. |
| 24 | `GET /system/updates` | `get_updates`: system.updates.check | none | missing | Gap matrix: P1, defer, "fpms2 only". **Dropped from the new menu.** |
| 25 | `POST /system/updates/install` | `install_updates`: system.updates.install | none | missing | Same. **Dropped.** |
| 26 | `GET /system/battery` | `get_battery` | `BatteryInfo` | exists | `present=false` when there is no battery. |
| 27 | `GET /system/datetime` | `get_datetime` | `DateTimeInfo` | exists | Core adds `source`. |
| 28 | `POST /system/mode/switch` | `switch_mode`: modes.* | none | missing | Gap matrix P0 "Gap / build" (Stream A). Core parity ask. |
| 29 | `GET /system/hotspot/ssid-passphrase` | `get_ssid_passphrase`: utils.ssid_passphrase, periodic | `HotspotCredentials`, 409 | exists | Adds `mode`. **409 outside hotspot mode**, while FPMS also shows this item in server, bridge and wiperf modes. Returns a secret, so never shareable. |
| 30 | `GET /system/hotspot/clients` | `get_connected_clients`: periodic | `HotspotClients`, 409 | shape-changed | Core returns `{mode, interface, count}` and no `clients[]` list, which the PoC model expects. 409 outside hotspot mode, so the periodic loop must call it only in hotspot mode. The per-station list is now `GET /wifi/hotspot/stations`. |
| 31 | `GET /network/info/publicip6` | `get_public_ipv6` | `PublicIpInfo` | exists | |
| 32 | `GET /utils/speedtest` | `run_speedtest` | `SpeedTest`, 503 | exists | Adds `testedAt`. Takes 30 to 90 s, so run it as a job with a long timeout. |
| 33 | `GET /utils/cloud-test/{vendor}` | `run_cloud_test`: utils.cloud.* | none | missing | Gap matrix P0 "Gap / build" (Stream C). Core parity ask. |
| 34 | `GET /utils/wlan/scan` | `scan_wlan`: apps.scanner.scan, scan_nohidden | `WlanScanResponse`, 400/409/503 | shape-changed | Now `{detail, selectedAdapter, networks[], scannedAt, needsSelection, candidates[]}`. Networks use `WlanNetwork` (adds `key_mgmt`, `secondaryChannelOffset`, `bssLoad`, `amendments`, ...). With several monitor adapters, core sets `needsSelection` and lists `candidates`; the PoC ignores this and shows an empty list. Adds `namespace` and `detail` query. New errors: 409 (scan in progress), 503 (no adapter). Handle with a `select` prompt, then re-run with `iface` and `namespace`. |
| 35 | `GET /profiler/status` | `get_profiler_status` | `Status` | exists | |
| 36 | `POST /profiler/purge/reports` | `profiler_purge_reports` | none | moved | Replaced by one `POST /profiler/purge`, which removes clients and reports and returns `Purge {files, bytes}` (the PoC expects `{success, message}`). 409 while the profiler runs. |
| 37 | `POST /profiler/purge/files` | `profiler_purge_files` | none | moved | Same as 36. |

## Core endpoints the new manifest needs that the PoC does not call

All of these exist on `main`. They come from rebinding PoC stubs and service calls (see `parity-list.md`).

| Endpoint | Used for | Notes |
| --- | --- | --- |
| `POST /bluetooth/pair` | Bluetooth > Pair Device | Serialized, time-bounded `bt-timedpair` window. Replaces the 403-ing service start. |
| `POST /utils/blinker/start`, `POST /utils/blinker/stop`, `GET /utils/blinker/status` | Utils > Port Blinker | The PoC has stubs that say "not yet available". Core has had these for a while. |
| `POST /profiler/start` | Profiler start and every "Start Other" variant | Body `Start-Input` (`channel`, `frequency`, `interface`, `ssid`, `no11r`, `no11ax`, ...). The PoC starts the `wlanpi-profiler` unit with no arguments, so all its variants behave the same. Returns `Start-Output {success, reason, message}`. |
| `POST /profiler/stop` | Profiler > Stop | |
| `POST /profiler/purge` | Profiler purge | Replaces rows 36 and 37. |
| `GET /network/config/status` | `adapter` parameter choices | Namespace-aware `iw dev` view (core-worker-api.md section 2.5). |
| `GET /system/device/model`, `GET /wifi/capabilities` | Capability visibility (`transmit`, `monitor_adapter`) | The model decides the `transmit` capability, matching `TRANSMIT_ALLOWED_MODELS = {M4, M4+, R4, R5, Pro}`. |
| `GET /system/timezone/list` | Timezone > Manual | Already in core, unused by the PoC. |
| `GET /wifi/hotspot/stations` | Hotspot home page client list | Replaces the `clients[]` the PoC expected from row 30. |
| `GET /network/interfaces/{iface}/wlan-link` | Built-in Wi-Fi link complication (`wlanpi.wifi`) | Returns `WlanLink` (`connected`, `ssid`, `bssid`, `freq_mhz`, `signal_dbm`, `links[]`). Polled every 10 s when connected, 30 s otherwise. |

## Endpoints still missing in core for parity

| Endpoint | Blocks | Source |
| --- | --- | --- |
| `POST /system/mode/switch` | Modes > Hotspot, Mode > Classic | Gap matrix P0, Stream A |
| `GET /utils/cloud-test/{vendor}` | 6 cloud tests | Gap matrix P0, Stream C |
| `POST /scanner/scan/csv` (or a capture-session equivalent) | Scanner > Scan to CSV | Gap matrix P1; Stream D, capture-session API designed in wifi-capture.md, not built |
| `POST /scanner/capture/start`, `.../stop` (or capture sessions) | Scanner > Scan to PCAP | Same |
| Token introspection | Remote JWT clients (Phase 4) | Not in core |

## Cross-cutting (the plan's table, completed)

| PoC assumes | Core now | Plan |
| --- | --- | --- |
| `http://localhost:31415/api/v1` | 31415 is HTTPS only (nginx, self-signed device cert); gunicorn on `/run/wlanpi_core.sock` | Call `https://127.0.0.1:31415/api/v1` and pin the device certificate |
| HMAC with `shared_secret.bin` (`core_client/hmac_auth.py`) | Secret directory is root-only (0700); HMAC is localhost bootstrap only | Reserved `did=wlanpi-menud` JWT from `/usr/libexec/wlanpi-menud/get-core-token` |
| `POST /profiler/purge/files` and `/profiler/purge/reports` | One `POST /profiler/purge` returning `{files, bytes}` | Rebind (moved, rows 36 and 37) |
| `/system/updates`, `/system/updates/install` | Not present | Dropped (rows 24 and 25) |
| `/system/mode/switch`, `/utils/cloud-test/{vendor}` | Not present | Core parity asks (rows 28 and 33) |
| Any token works whenever it is presented | 503 `{"error":"AUTH_CLOCK_NOT_SET"}` when the restored clock is more than 30 s behind `iat`; boot-relative expiry checked | Re-mint on `AUTH_CLOCK_NOT_SET` and on 401; branch on the `error` field, not the 503 alone |
| Hotspot, server and Bluetooth actions always offered | Transmit units (`hostapd`, `wpa_supplicant`, `wpa_supplicant@wlan0`, `bt-agent`, `bt-network`) refused on the Go and unknown models (403) | `transmit` capability visibility rule |
| Bluetooth pair via `start_service("bt-timedpair")` | `bt-timedpair` is not in the service allowlist (403) | Rebind to `POST /bluetooth/pair` |
| Profiler variants via `start_service("wlanpi-profiler")` | `POST /profiler/start` takes the variant in its body | Rebind; manifest carries the constant body |
| Port blinker "not yet available" | `/utils/blinker/start|stop|status` live | Rebind |
| Hotspot clients and SSID readable in any mode | 409 outside hotspot mode | Mode visibility; periodic loop polls only in hotspot mode |
| Scan returns a plain list | `needsSelection` + `candidates` with several monitor adapters | `select` prompt, then re-run with `iface` + `namespace` |
| PAM endpoints usable by any local caller | Only the `wlanpi-webui` token or localhost HMAC | wlanpi-menud never calls PAM |
| Capture WebSocket open | First-frame JWT, close 4401, no token in URL | Same pattern for wlanpi-menud remote clients |

## Follow-ups

- Filed: [WLAN-Pi/wlanpi-core#481](https://github.com/WLAN-Pi/wlanpi-core/issues/481) — vendor cloud-test endpoint (drafts/03). Remaining drafts (01, 02, 04, 05) to be filed as narrow issues per the #130 closing guidance.
- Generate the core client from `docs/openapi.json` (pinned to the audited commit) instead of carrying over `core_client/client.py`.
- Add contract tests in CI against a pinned core version, starting with the 37 rows above plus the "needs" table.
- Re-run this audit whenever the pinned core version moves.
