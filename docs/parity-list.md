# Parity list: current FPMS menu against wlanpi-core

The tracker for making the panel default (plan: wlanpi-core integration > Parity list). One row per menu item in current FPMS's menu, which lives in `wlanpi-fpms/fpms/fpms.py` (the `menu` list in `main()`, plus `modules/modes.py` `place_hotspot_menu`). FPMS 1.4.26, commit `87b96cc`. Endpoint status comes from [`core-audit.md`](core-audit.md); `#n` is the row in its endpoint table.

Items that only open a confirm screen (FPMS's `Confirm` sub-items) are folded into their parent row. The manifest node column refers to [`protocol/samples/manifests/builtin.json`](../protocol/samples/manifests/builtin.json).

## Status key

| Status | Meaning |
| --- | --- |
| works | The core endpoint exists with a usable shape; the manifest binds it directly or through a handler. |
| needs-rebind | Core can do it, but the PoC binding is wrong (wrong route, stub, or a changed shape). Fixed in wlanpi-menud, not in core. |
| missing-in-core | Core has no endpoint yet. The node ships hidden via `requires_core`. |
| drop-candidate | Proposed for removal from the shared menu; team to confirm. |
| dropped | Decided: not in the new menu. |

## Summary

**65 FPMS rows: 40 works, 13 needs-rebind, 11 missing-in-core, 1 drop-candidate.** Plus 1 PoC-only row (system updates), dropped per the plan.

| Status | Rows |
| --- | --- |
| works | 40 |
| needs-rebind | 13 |
| missing-in-core | 11 |
| drop-candidate | 1 |
| dropped (PoC only) | 1 |

The 11 missing-in-core rows come from 3 core asks: mode switch (2 rows), cloud tests (6), and the Stream D capture-session API for scanner CSV and PCAP (3).

Mode visibility follows FPMS: Apps only in classic; SSID/Passphrase everywhere except classic; Modes > Hotspot in classic and Mode > Classic Mode in hotspot.

The **Team** column is for the parity review: mark each row `required`, `deferred` or `dropped`. The panel stays opt-in until every `required` row works on every supported model.

## Rows

| # | FPMS menu item | FPMS action | Manifest node | Core endpoint | Endpoint status | Parity status | Team | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Network > Interfaces | `show_interfaces` | `network.interfaces` | `GET /network/interfaces` | exists (#8) | works |  | Merged with network.wlan_interfaces; grouped Ethernet/WLAN table with adapter sub-rows. |
| 2 | Network > WLAN Interfaces | `show_wlan_interfaces` | (reserved) `network.wlan_interfaces` | `GET /network/interfaces` | exists (#8) | merged |  | Merged into network.interfaces (id reserved). |
| 3 | Network > Eth0 IP Config | `show_eth0_ipconfig` | `network.eth0_ipconfig` | `GET /network/info/ (`/eth0_ipconfig_info`)` | exists (#12) | works |  | PoC uses `/network/interfaces/eth0` (#9); `eth0_ipconfig_info` matches FPMS's DHCP-server view. |
| 4 | Network > Eth0 VLAN | `show_vlan` | `network.eth0_vlan` | `GET /network/ethernet/eth0/vlan` | exists (#10) | works |  |  |
| 5 | Network > LLDP Neighbour | `show_lldp_neighbour` | `network.lldp` | `GET /network/info/ (`/lldp_neighbour_info`)` | exists (#12) | works |  | Also FPMS key1 shortcut. |
| 6 | Network > CDP Neighbour | `show_cdp_neighbour` | `network.cdp` | `GET /network/info/ (`/cdp_neighbour_info`)` | exists (#12) | works |  |  |
| 7 | Network > Public IPv4 | `show_publicip` | `network.publicip4` | `GET /network/info/ (`/public_ip`)` | exists (#12) | works |  |  |
| 8 | Network > Public IPv6 | `show_publicip6` | `network.publicip6` | `GET /network/info/publicip6` | exists (#31) | works |  |  |
| 9 | Bluetooth > Status | `bluetooth_status` | `bluetooth.status` | `GET /bluetooth/status` | exists (#6) | works |  |  |
| 10 | Bluetooth > Turn On | `bluetooth_on` | `bluetooth.on` | `POST /bluetooth/power/on` | exists (#7) | works |  | 403 on the Go; hidden by the `transmit` capability. |
| 11 | Bluetooth > Turn Off | `bluetooth_off` | `bluetooth.off` | `POST /bluetooth/power/off` | exists (#7) | works |  |  |
| 12 | Bluetooth > Pair Device | `bluetooth_pair` | `bluetooth.pair` | `POST /bluetooth/pair` | PoC: `service/start?name=bt-timedpair` gets 403 | needs-rebind |  | Core has `POST /bluetooth/pair`. |
| 13 | Utils > Reachability | `show_reachability` | `utils.reachability` | `GET /utils/reachability` | exists (#13) | works |  | Also FPMS key1 shortcut. |
| 14 | Utils > Speedtest > Run Test | `show_speedtest` | `utils.speedtest.run` | `GET /utils/speedtest` | exists (#32) | works |  | 30 to 90 s; job with 120 s timeout. |
| 15 | Utils > Cloud Tests > Arista CV-CUE | `show_arista_test` | `utils.cloud.arista` | `GET /utils/cloud-test/arista` | missing (#33) | missing-in-core |  | Stream C. Hidden via `requires_core` until built. |
| 16 | Utils > Cloud Tests > Aruba Central | `show_aruba_test` | `utils.cloud.aruba` | `GET /utils/cloud-test/aruba` | missing (#33) | missing-in-core |  |  |
| 17 | Utils > Cloud Tests > ExtremeCloud IQ | `show_extreme_test` | `utils.cloud.extreme` | `GET /utils/cloud-test/extreme` | missing (#33) | missing-in-core |  |  |
| 18 | Utils > Cloud Tests > Meraki Cloud | `show_meraki_test` | `utils.cloud.meraki` | `GET /utils/cloud-test/meraki` | missing (#33) | missing-in-core |  |  |
| 19 | Utils > Cloud Tests > Mist Cloud | `show_mist_test` | `utils.cloud.mist` | `GET /utils/cloud-test/mist` | missing (#33) | missing-in-core |  |  |
| 20 | Utils > Cloud Tests > RUCKUS Cloud | `show_ruckus_test` | `utils.cloud.ruckus` | `GET /utils/cloud-test/ruckus` | missing (#33) | missing-in-core |  |  |
| 21 | Utils > Port Blinker > Start | `show_blinker` | `utils.port_blinker.start` | `POST /utils/blinker/start?interface=eth0` | PoC stub; core route live | needs-rebind |  | Core has had blinker routes; PoC never bound them. |
| 22 | Utils > Port Blinker > Stop | `stop_blinker` | `utils.port_blinker.stop` | `POST /utils/blinker/stop` | PoC stub; core route live | needs-rebind |  |  |
| 23 | Utils > SSID/Passphrase | `show_ssid_passphrase` | `utils.ssid_passphrase` | `GET /system/hotspot/ssid-passphrase` | exists (#29) | works |  | Hidden in classic (as FPMS). Core returns 409 outside hotspot mode, so it is empty in server, bridge and wiperf, where FPMS shows it today. Team: accept or ask core. Secret, never shareable. |
| 24 | Utils > USB Devices | `show_usb` | `utils.usb` | `GET /utils/usb` | exists (#14) | works |  |  |
| 25 | Utils > UFW Ports | `show_ufw` | `utils.ufw` | `GET /utils/ufw` | exists (#15) | works |  |  |
| 26 | Modes > Hotspot > Confirm (classic) | `hotspot_confirm` | `modes.hotspot` | `POST /system/mode/switch {mode: hotspot}` | missing (#28) | missing-in-core |  | FPMS runs the hotspot switcher script itself. Stream A. |
| 27 | Mode > Classic Mode > Confirm (hotspot) | `hotspot_confirm` | `modes.classic` | `POST /system/mode/switch {mode: classic}` | missing (#28) | missing-in-core |  |  |
| 28 | Apps > Kismet > Start | `kismet_start` | `apps.kismet.start` | `POST /system/service/start?name=kismet` | exists (#4) | works |  | `kismet` is in core's allowlist. |
| 29 | Apps > Kismet > Stop | `kismet_stop` | `apps.kismet.stop` | `POST /system/service/stop?name=kismet` | exists (#5) | works |  |  |
| 30 | Apps > Profiler > Status | `profiler_status` | `apps.profiler.status` | `GET /profiler/status` | exists (#35) | works |  | Handler builds the QR block. |
| 31 | Apps > Profiler > Stop | `profiler_stop` | `apps.profiler.stop` | `POST /profiler/stop` | exists | works |  | PoC uses service stop (#5), which also works; manifest uses the canonical route. |
| 32 | Apps > Profiler > Start | `profiler_start` | `apps.profiler.start` | `POST /profiler/start {}` | PoC: service start (#4) | needs-rebind |  | Canonical route returns `reason` (for example `country_code_detection`). |
| 33 | Apps > Profiler > Start Other > Start 2.4 GHz | `profiler_start_2dot4ghz` | `apps.profiler.start_2_4` | `POST /profiler/start {channel: 6}` | PoC: service start, no args | needs-rebind |  | PoC variants all start the same unit with no arguments. |
| 34 | Apps > Profiler > Start Other > Start 5 GHz 36 | `profiler_start_5ghz_unii1` | `apps.profiler.start_5_36` | `POST /profiler/start {channel: 36}` | PoC: service start, no args | needs-rebind |  |  |
| 35 | Apps > Profiler > Start Other > Start 5 GHz 149 | `profiler_start_5ghz_unii3` | `apps.profiler.start_5_149` | `POST /profiler/start {channel: 149}` | PoC: service start, no args | needs-rebind |  |  |
| 36 | Apps > Profiler > Start Other > Start (no 11r) | `profiler_start_no11r` | `apps.profiler.start_no11r` | `POST /profiler/start {no11r: true}` | PoC: service start, no args | needs-rebind |  |  |
| 37 | Apps > Profiler > Start Other > Start (no 11ax) | `profiler_start_no11ax` | `apps.profiler.start_no11ax` | `POST /profiler/start {no11ax: true}` | PoC: service start, no args | needs-rebind |  |  |
| 38 | Apps > Profiler > Purge Reports > Confirm | `profiler_purge_reports` | `apps.profiler.purge` | `POST /profiler/purge` | moved (#36) | needs-rebind |  | Core purges clients and reports together. FPMS's two items become one "Purge Data" node; team to confirm. |
| 39 | Apps > Profiler > Purge Files > Confirm | `profiler_purge_files` | `apps.profiler.purge` | `POST /profiler/purge` | moved (#37) | needs-rebind |  | Same node as the row above. |
| 40 | Apps > Scanner > Scan | `scanner_scan` | `apps.scanner.scan` | `GET /utils/wlan/scan?hidden=true` | shape-changed (#34) | needs-rebind |  | Handle `needsSelection` with an adapter prompt; render `networks[]`. |
| 41 | Apps > Scanner > Scan (no hidden) | `scanner_scan_nohidden` | `apps.scanner.scan_nohidden` | `GET /utils/wlan/scan?hidden=false` | shape-changed (#34) | needs-rebind |  |  |
| 42 | Apps > Scanner > Scan to CSV | `scanner_scan_tofile_csv` | `apps.scanner.csv` | `Stream D capture-session files (gap matrix: `POST /scanner/scan/csv`)` | missing | missing-in-core |  | Designed in wifi-capture.md, not built. |
| 43 | Apps > Scanner > Scan to PCAP > Start | `scanner_scan_tofile_pcap_start` | `apps.scanner.pcap_start` | `Stream D capture session start` | missing | missing-in-core |  | `WS /streaming/capture` exists, but the menu needs the REST capture-session API. |
| 44 | Apps > Scanner > Scan to PCAP > Stop | `scanner_scan_tofile_pcap_stop` | `apps.scanner.pcap_stop` | `Stream D capture session stop` | missing | missing-in-core |  |  |
| 45 | System > About | `show_about` | `system.about` | `GET /system/device/info` | exists (#1) | works |  |  |
| 46 | System > Help | `show_help` | `system.help` | `none (static)` | n/a | works |  | Static text and QR; no core call. |
| 47 | System > Summary | `show_summary` | `system.summary` | `GET /system/device/stats` | exists (#2) | works |  |  |
| 48 | System > Battery | `show_battery` | `system.battery` | `GET /system/battery` | exists (#26) | works |  | `present=false` when there is no battery. |
| 49 | System > Settings > Date & Time > Show Time & Zone | `show_date` | `system.date` | `GET /system/datetime` | exists (#27) | works |  |  |
| 50 | System > Settings > Date & Time > Set Timezone > Auto | `set_time_zone_auto` | `system.timezone.auto` | `POST /system/timezone/auto` | exists (#21) | works |  |  |
| 51 | System > Settings > Date & Time > Set Timezone > Manual | `timezones_available (country > zone list)` | `system.timezone.set` | `GET /system/timezone/list + POST /system/timezone/set` | exists (#19, #20) | works |  | Handler: two-level select prompt from core's list. |
| 52 | System > Settings > RF Domain > Show Domain | `show_reg_domain` | `system.reg_domain.show` | `GET /system/reg-domain` | exists (#22) | works |  | FPMS jumps here after a successful Set Domain. |
| 53 | System > Settings > RF Domain > Set Domain US > Confirm | `set_reg_domain("US")` | `system.reg_domain.set (country param)` | `POST /system/reg-domain/set {country: US}` | exists (#23) | works |  | Ten FPMS items become one node with an enum parameter. |
| 54 | System > Settings > RF Domain > Set Domain AU > Confirm | `set_reg_domain("AU")` | `system.reg_domain.set (country param)` | `POST /system/reg-domain/set {country: AU}` | exists (#23) | works |  | Missing from the PoC menu; present in FPMS. |
| 55 | System > Settings > RF Domain > Set Domain BR > Confirm | `set_reg_domain("BR")` | `system.reg_domain.set (country param)` | `POST /system/reg-domain/set {country: BR}` | exists (#23) | works |  |  |
| 56 | System > Settings > RF Domain > Set Domain CA > Confirm | `set_reg_domain("CA")` | `system.reg_domain.set (country param)` | `POST /system/reg-domain/set {country: CA}` | exists (#23) | works |  |  |
| 57 | System > Settings > RF Domain > Set Domain CZ > Confirm | `set_reg_domain("CZ")` | `system.reg_domain.set (country param)` | `POST /system/reg-domain/set {country: CZ}` | exists (#23) | works |  |  |
| 58 | System > Settings > RF Domain > Set Domain DE > Confirm | `set_reg_domain("DE")` | `system.reg_domain.set (country param)` | `POST /system/reg-domain/set {country: DE}` | exists (#23) | works |  |  |
| 59 | System > Settings > RF Domain > Set Domain FR > Confirm | `set_reg_domain("FR")` | `system.reg_domain.set (country param)` | `POST /system/reg-domain/set {country: FR}` | exists (#23) | works |  |  |
| 60 | System > Settings > RF Domain > Set Domain GB > Confirm | `set_reg_domain("GB")` | `system.reg_domain.set (country param)` | `POST /system/reg-domain/set {country: GB}` | exists (#23) | works |  |  |
| 61 | System > Settings > RF Domain > Set Domain NL > Confirm | `set_reg_domain("NL")` | `system.reg_domain.set (country param)` | `POST /system/reg-domain/set {country: NL}` | exists (#23) | works |  |  |
| 62 | System > Settings > RF Domain > Set Domain NO > Confirm | `set_reg_domain("NO")` | `system.reg_domain.set (country param)` | `POST /system/reg-domain/set {country: NO}` | exists (#23) | works |  |  |
| 63 | System > Settings > Rotate Display | `rotate_display` | `system.rotate_display` | `none (panel-local)` | n/a | drop-candidate |  | Panel-only setting (FPMS key2 does the same). Better owned by the panel surface in wlanpi-fpms than a shared menu node; the manifest keeps it behind the `display` capability until the team decides. |
| 64 | System > Reboot > Confirm | `reboot` | `system.reboot` | `POST /system/reboot` | exists (#16) | works |  | Also FPMS key3 shortcut. |
| 65 | System > Shutdown > Confirm | `shutdown` | `system.shutdown` | `POST /system/shutdown` | exists (#17) | works |  | Also FPMS key3 shortcut. |
| P1 | System > Updates (PoC only, not in FPMS) | n/a (fpms2 `system.updates.check`, `system.updates.install`) | none | `GET /system/updates, POST /system/updates/install` | missing (#24, #25) | **dropped** | dropped | Dropped per the plan. Not in the new manifest. |

## FPMS behaviour outside the menu tree

These are not menu rows, but the panel must keep them (Phase 3):

- key1 cycles shortcuts: Utils > Reachability, Network > LLDP Neighbour, Network > Eth0 IP Config. In the new stack these become `invoke` on `utils.reachability`, `network.lldp` and `network.eth0_ipconfig`.
- key2 toggles display orientation (the same thing as System > Settings > Rotate Display) and redraws the page; key3 toggles between the Reboot and Shutdown confirm screens.
- After a successful Set Domain, FPMS jumps to RF Domain > Show Domain.
- Home page variants per mode (Classic, Hotspot, Wiperf, Server, Bridge); in the new stack these are `view` frames with `screen: home` fed by the polling loop.
