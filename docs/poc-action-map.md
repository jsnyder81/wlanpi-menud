# PoC action map

Where each of the wlanpi-fpms2 PoC's action ids went. Source: `wlanpi_fpms2/actions/registry.py` (68 action ids; `state/menu_tree.py` has 61 `action_id=` sites because the RF-domain and timezone leaves are built in loops). `tests/test_manifests.py::test_every_poc_action_is_mapped` checks this table against `share/menu.d/`.

| PoC action id | Built-in node | Note |
| --- | --- | --- |
| `network.interfaces` | `network.interfaces` | same id |
| `network.wlan_interfaces` | `network.wlan_interfaces` | same id |
| `network.eth0_ipconfig` | `network.eth0_ipconfig` | same id |
| `network.eth0_vlan` | `network.eth0_vlan` | same id |
| `network.lldp` | `network.lldp` | same id |
| `network.cdp` | `network.cdp` | same id |
| `network.publicip4` | `network.publicip4` | same id |
| `network.publicip6` | `network.publicip6` | same id |
| `bluetooth.status` | `bluetooth.status` | same id |
| `bluetooth.on` | `bluetooth.on` | same id |
| `bluetooth.off` | `bluetooth.off` | same id |
| `bluetooth.pair` | `bluetooth.pair` | same id |
| `utils.reachability` | `utils.reachability` | same id |
| `utils.speedtest` | `utils.speedtest.run` | renamed: the PoC action id was the branch id |
| `utils.cloud.arista` | `utils.cloud.arista` | same id |
| `utils.cloud.aruba` | `utils.cloud.aruba` | same id |
| `utils.cloud.extreme` | `utils.cloud.extreme` | same id |
| `utils.cloud.meraki` | `utils.cloud.meraki` | same id |
| `utils.cloud.mist` | `utils.cloud.mist` | same id |
| `utils.cloud.ruckus` | `utils.cloud.ruckus` | same id |
| `utils.port_blinker.start` | `utils.port_blinker.start` | same id |
| `utils.port_blinker.stop` | `utils.port_blinker.stop` | same id |
| `utils.ssid_passphrase` | `utils.ssid_passphrase` | same id |
| `utils.usb` | `utils.usb` | same id |
| `utils.ufw` | `utils.ufw` | same id |
| `modes.hotspot` | `modes.hotspot` | same id |
| `modes.server` | `modes.server` | same id |
| `modes.bridge` | `modes.bridge` | same id |
| `modes.classic` | `modes.classic` | same id |
| `apps.profiler.status` | `apps.profiler.status` | same id |
| `apps.profiler.stop` | `apps.profiler.stop` | same id |
| `apps.profiler.start` | `apps.profiler.start` | same id |
| `apps.profiler.start_2_4` | `apps.profiler.start_2_4` | same id |
| `apps.profiler.start_5_36` | `apps.profiler.start_5_36` | same id |
| `apps.profiler.start_5_149` | `apps.profiler.start_5_149` | same id |
| `apps.profiler.start_no11r` | `apps.profiler.start_no11r` | same id |
| `apps.profiler.start_no11ax` | `apps.profiler.start_no11ax` | same id |
| `apps.profiler.purge_reports` | `apps.profiler.purge` | merged: core has one POST /profiler/purge |
| `apps.profiler.purge_files` | `apps.profiler.purge` | merged: core has one POST /profiler/purge |
| `apps.kismet.start` | `apps.kismet.start` | same id |
| `apps.kismet.stop` | `apps.kismet.stop` | same id |
| `apps.scanner.scan` | `apps.scanner.scan` | same id |
| `apps.scanner.scan_nohidden` | `apps.scanner.scan_nohidden` | same id |
| `apps.scanner.csv` | `apps.scanner.csv` | same id |
| `apps.scanner.pcap_start` | `apps.scanner.pcap_start` | same id |
| `apps.scanner.pcap_stop` | `apps.scanner.pcap_stop` | same id |
| `system.about` | `system.about` | same id |
| `system.help` | `system.help` | same id |
| `system.summary` | `system.summary` | same id |
| `system.battery` | `system.battery` | same id |
| `system.date` | `system.date` | same id |
| `system.timezone.auto` | `system.timezone.auto` | same id |
| `system.timezone.set` | `system.timezone.set` | same id |
| `system.reg_domain.show` | `system.reg_domain.show` | same id |
| `system.reg_domain.set_us` | `system.reg_domain.set` | merged: country=US is an enum parameter |
| `system.reg_domain.set_br` | `system.reg_domain.set` | merged: country=BR is an enum parameter |
| `system.reg_domain.set_ca` | `system.reg_domain.set` | merged: country=CA is an enum parameter |
| `system.reg_domain.set_cz` | `system.reg_domain.set` | merged: country=CZ is an enum parameter |
| `system.reg_domain.set_de` | `system.reg_domain.set` | merged: country=DE is an enum parameter |
| `system.reg_domain.set_fr` | `system.reg_domain.set` | merged: country=FR is an enum parameter |
| `system.reg_domain.set_gb` | `system.reg_domain.set` | merged: country=GB is an enum parameter |
| `system.reg_domain.set_nl` | `system.reg_domain.set` | merged: country=NL is an enum parameter |
| `system.reg_domain.set_no` | `system.reg_domain.set` | merged: country=NO is an enum parameter |
| `system.rotate_display` | `system.rotate_display` | same id |
| `system.updates.check` | none | dropped per the plan: no core endpoint |
| `system.updates.install` | none | dropped per the plan: no core endpoint |
| `system.reboot` | `system.reboot` | same id |
| `system.shutdown` | `system.shutdown` | same id |

New node with no PoC action: `network.adapter_summary` (Network > Adapters), the composed adapter view from core-worker-api.md section 2.5, as a first-party handler.

PoC-only modes `modes.server` and `modes.bridge` are kept (not in current FPMS; parity list: team decision). Like Hotspot they need `POST /system/mode/switch` (hidden until core has it) and the `transmit` capability.
