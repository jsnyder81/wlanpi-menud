# Phase 3 Front Panel (`wlanpi-fpms`) Review

- **Date:** 2026-11-11
- **Reviewer:** Dispatched Reviewer Worker (`task_b858e60ba7de` / `ctx_5720ea61a86f`)
- **Repository Under Review:** `wlanpi-fpms`
- **Branch / Commit:** `phase3/menud-client` at `6aeae3f` (branched from `main` `87b96cc`)
  - `0311c65` — `feat!: make the panel a thin client of wlanpi-menud`
  - `6ace456` — `fix(panel): wake the sleeping screen when eth0 carrier changes`
  - `6569c6b` — `chore(debian): recommend wlanpi-menud instead of hard-depending on it`
  - `6aeae3f` — `style: format with ruff`
- **Reference Specifications:**
  - `FPMS Re-Architecture Plan.md` (Phase 3, Runtime connections, Two client modes, Sessions, View blocks)
  - `wlanpi-menud/protocol/frames.schema.json`
  - `wlanpi-menud/protocol/PROTOCOL.md`
- **Verdict:** **APPROVE**

---

## Executive Summary

`wlanpi-fpms` on branch `phase3/menud-client` (`6aeae3f`) cleanly replaces the legacy in-process menu tree, polling threads, and page renderers with a thin WebSocket client (`fpms/modules/menud_client/`) connected to `/run/wlanpi-menud/local.sock` on the persistent `"default"` session, while preserving all hardware-facing responsibilities (early C framebuffer splash, Python boot splash, systemd `sd_notify`, D-Bus `login1` shutdown inhibitor and final-frame display, Pi 5 RP1 `gpiochip` resolution, model/display detection, and SPI OLED/TFT screen drivers).

All 8 required verification items passed with zero blocking or non-blocking defects:
- **Unit & Integration Suites:**
  - `wlanpi-fpms`: **108 passed** (`pytest`), `ruff check` clean, `ruff format --check` clean (44 files), `mypy fpms` clean (28 files, 0 issues)
  - `wlanpi-menud`: **216 passed** (`pytest`)
  - `wlanpi-menu-tui`: **25 passed** (`pytest`)
  - `wlanpi-webui`: **483 passed** (`pytest`), **230 passed** (`node --test`, 54 suites)
  - Live Container Protocol Verification (`fpms-verify:redesign` against real `wlanpi-menud` + `FakeCore`): **9 scenarios, 212 frames** schema-validated against `frames.schema.json`
  - Clean Container `.deb` Build & `lintian` (`wlanpi-fpms-builder:trixie`): `wlanpi-fpms_1.4.26_arm64.deb` built cleanly via `dpkg-buildpackage -us -uc -b` and inspected with `lintian`

---

## 1. Legacy Menu Logic Removal vs. Hardware/Lifecycle Survival

### 1.1 Deleted Legacy Modules & Assets
Git diff (`git diff --stat 87b96cc..6aeae3f`) confirms complete deletion of all legacy menu logic, system/network/wireless/app command wrappers, TextFSM templates, and legacy page classes:
- **Deleted modules (`fpms/modules/`):**
  - `apps/kismet.py`, `apps/profiler.py`, `apps/scanner.py`
  - `battery.py`, `bluetooth.py`, `cloud_tests.py`, `modes.py`, `network.py`, `reg_domain.py`, `system.py`, `time_zone.py`, `utils.py`
  - `pages/alert.py`, `pages/display.py`, `pages/homepage.py`, `pages/menu.py`, `pages/pagedtable.py`, `pages/simpletable.py`, `pages/textbox.py`
  - `templates/eth0_ipconfig_info.textfsm`, `templates/publicip6.textfsm`, `templates/ufw_status.textfsm`, `templates/wlan_eth0_info.textfsm`
  - `fonts/ionicons.ttf`
- **Deleted legacy tests (`tests/`):**
  - `test_apps_profiler.py`, `test_bluetooth.py`, `test_cloud_tests.py`, `test_menu_B.py`, `test_network.py`, `test_reg_domain.py`, `test_system.py`, `test_time_zone.py`, `test_utils.py`, `tests/modules/pages/test_simpletable.py`

### 1.2 Grep Audit for Residual Legacy Imports, State Machine, and Config Reads
A full grep across `fpms/` and `tests/` for legacy module names (`homepage`, `simpletable`, `pagedtable`, `textbox`, `kismet`, `profiler`, `scanner`, `cloud_tests`, `reg_domain`, `time_zone`, `textfsm`, `ionicons`), legacy `g_vars` menu state machine keys (`current_menu_location`, `option_selected`, `page_Rendered`, `result_cache`, `menu_state`, `alert_mode`, `scan_mode`), and config keys confirmed zero remaining references:
- `g_vars` in `fpms/fpms.py:153-155` now holds only `{"shutdown_in_progress": False}` for the D-Bus `PrepareForShutdown` / `JobNew` shutdown splash handler (`fpms/fpms.py:72-97`).
- Configuration file parsing in `fpms/fpms.py:158-192, 284-286` reads only `display.orientation` (`normal` / `flipped`) from `/etc/wlanpi-fpms.conf`.
  - *Note on `FPMS Re-Architecture Plan.md` lines 397-407:* While the earlier draft plan described a transitional `menu_backend = legacy | wlanpi-menud` toggle in `/etc/wlanpi-fpms.conf`, commit `0311c65` intentionally removed the legacy backend outright in accordance with Phase 3 requirement (1) (*"legacy menu logic fully gone"*).

### 1.3 Surviving Hardware, Boot/Shutdown, and Screen Driver Paths
All hardware and OS lifecycle components remain intact and covered by unit tests:
1. **Early Boot Splash & Python Boot Splash:**
   - C framebuffer splash binary `splash/fpms-splash.c` + `debian/wlanpi-fpms-splash.service` + `debian/rules:11-16`.
   - Python boot splash `fpms/boot_splash.py:1-40` (`tests/modules/test_boot_splash.py`).
   - Failure splash unit `debian/wlanpi-fpms-failed.service`.
2. **D-Bus Shutdown Inhibitor, `PrepareForShutdown` / `JobNew` Signal Handlers, and OLED Final Frame:**
   - `fpms/fpms.py:70-145` acquires the `org.freedesktop.login1.Manager.Inhibit("shutdown", ...)` file descriptor, subscribes to `PrepareForShutdown` and systemd `JobNew` (`reboot.target`, `poweroff.target`, `halt.target`), stops the panel loop, renders `reboot.png` or `shutdown.png`, and puts the display into low-power state before releasing the inhibitor lock (`tests/modules/test_shutdown_inhibitor.py`, `tests/modules/test_wlanpi_oled_final_frame.py`).
3. **systemd Readiness Notification (`sd_notify`):**
   - `fpms/modules/sdnotify.py:7-16` sends `READY=1` to `NOTIFY_SOCKET`, invoked in `fpms/fpms.py:322` immediately after the initial frame is drawn (`panel.draw()`) and before entering the asyncio loop (`tests/modules/test_sd_notify.py`).
4. **Platform & Pi 5 RP1 GPIO Chip Detection:**
   - `fpms/modules/platform.py:1-12` (`PLATFORM_PRO`, `PLATFORM_R4`, `PLATFORM_M4`, `PLATFORM_M4_PLUS`, `PLATFORM_GO`, `PLATFORM_UNKNOWN`, `GPIO_CHIP_LABELS = ("pinctrl-rp1", "pinctrl-bcm2711", "pinctrl-bcm2835")`).
   - `fpms/modules/env_utils.py:18-71` resolves model from `/etc/wlanpi-model` or `/usr/bin/wlanpi-model -b`, maps platform to display driver (`oled` vs `st7735`), and resolves `/dev/gpiochipN` by kernel label via `gpiod.Chip` (`tests/modules/test_env_utils_gpiochip.py`).
5. **GPIO Buttons & Orientation Remapping:**
   - `fpms/modules/nav/buttons.py:19-80` claims GPIO lines with `Bias.PULL_UP`, `Edge.FALLING`, and 10 µs debounce (`request_buttons`), maps falling edges to protocol key names (`up`, `down`, `left`, `right`, `center`, `key1`, `key2`, `key3`), and swaps `up`/`down`, `left`/`right`, and `key1`/`key3` when `DISPLAY_ORIENTATION_FLIPPED` is active (`oriented_key`, `tests/menud_client/test_buttons.py`).
   - Keyboard emulation (`-e`) in `fpms/modules/nav/buttons.py:29-49, 83-125` maps keypad/letter keys (`8`/`w`, `2`/`x`, `4`/`a`, `6`/`d`, `5`/`s`, `*`/`i`, `-`/`o`, `+`/`p`, `g` screenshot, `k` quit) to the same panel callbacks (`tests/menud_client/test_fpms_emulation.py`).
6. **Screen Drivers & Carrier Wakeup:**
   - `fpms/modules/screen/luma.py`, `fpms/modules/screen/st7735.py`, `fpms/modules/screen/virtual.py`, `fpms/modules/wlanpi_oled.py`, `fpms/modules/display.py`, and `fpms/modules/themes.py` all survive intact.
   - `fpms/modules/menud_client/panel.py:36-41, 359-363` polls `/sys/class/net/eth0/carrier` once per second and wakes the sleeping screen when the Ethernet cable is plugged in or unplugged (`test_panel.py:test_eth0_carrier_change_wakes_sleeping_screen`).

---

## 2. Protocol Conformance Against Real `wlanpi-menud` in Container

### 2.1 Schema Parity & Envelope Conformance
- `tests/menud_client/fixtures/frames.schema.json` is byte-for-byte identical to `wlanpi-menud/protocol/frames.schema.json`.
- All C2S frame builders in `fpms/modules/menud_client/protocol.py:20-112` emit `{"v": 1, "type": ..., "id": ...}` envelopes validated against `frames.schema.json` in both unit tests (`tests/menud_client/test_protocol.py`) and the live container run (`212` total C2S + S2C frames validated with `Draft202012Validator`).

### 2.2 Truthful `hello` Capabilities (`fpms/modules/menud_client/protocol.py:24-65`)
- `mode`: `"thin"`
- `session`: `"default"`
- `protocol`: `[1]`
- `client`: `{"name": "wlanpi-fpms", "version": "1.4.26"}`
- `capabilities.screen`: `{"width_px": 128, "height_px": 128, "cols": 21, "rows": 8}` (matches `PAGE_WIDTH = 128`, `PAGE_HEIGHT = 128`, `COLS = 21`, `ROWS = 8` in `render.py:42-48`)
- `capabilities.colour`: `"colour"`
- `capabilities.blocks`: `["text", "kv", "table", "list", "qr", "status", "progress", "confirm"]`
- `capabilities.keys`: Hardware-accurate per platform (`constants.py:47-71`, `fpms.py:293`):
  - **Waveshare HAT (`PLATFORM_R4` / `M4` / `M4+` / `Go`):** `["up", "down", "left", "right", "center", "key1", "key2", "key3"]`
  - **WLAN Pi Pro (`PLATFORM_PRO`):** `["up", "down", "left", "right", "center"]`

### 2.3 Live Container Frame-Level Transcripts (`fpms-verify:redesign` + Real `wlanpi-menud`)
The panel (`Panel` + `MenudClient` + `Virtual` screen) was executed inside `fpms-verify:redesign` against the real `wlanpi_menud.service.Service`, `Server`, `Poller`, and `FakeCore` over a Unix domain socket (`local.sock`) with `validate_outbound=True` and a `TranscriptRecorder` validating every C2S and S2C frame against `wlanpi-menud/protocol/frames.schema.json`.

#### Scenario 1: `hello`, `welcome`, `subscribe`, `menu`, `view` (`home`), and Topic Snapshots
```json
C->S {"v":1,"type":"hello","id":"p1","mode":"thin","session":"default","protocol":[1],"client":{"name":"wlanpi-fpms","version":"1.4.26"},"capabilities":{"screen":{"width_px":128,"height_px":128,"cols":21,"rows":8},"colour":"colour","blocks":["text","kv","table","list","qr","status","progress","confirm"],"keys":["up","down","left","right","center","key1","key2","key3"]}}
S->C {"v":1,"type":"welcome","id":"p1","session_id":"SZ18lPsBz9C_VNNtu","session":"default","epoch":"orBG3VzmI1nJlPq_","principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"roles":["admin"],"version":1,"mode":"thin","resumed":false,"limits":{"queue_frames":64,"max_frame_bytes":262144}}
C->S {"v":1,"type":"subscribe","id":"p2","topics":["device.summary","complications","alerts","jobs"]}
S->C {"v":1,"type":"menu","id":"s:1","revision":2,"nodes":[{"id":"network","label":"Network","parent":null,"order":10,"kind":"branch"},...],"device_mode":"classic"}
S->C {"v":1,"type":"view","id":"s:2","screen":"home","node":null,"title":"WLAN Pi","blocks":[{"type":"kv","text":"Host: wlanpi-test\nMode: classic\nIP: 192.0.2.10\nModel: R4\nTemp: 48.0C","items":[{"key":"Host","value":"wlanpi-test"},{"key":"Mode","value":"classic"},{"key":"IP","value":"192.0.2.10"},{"key":"Model","value":"R4"},{"key":"Temp","value":"48.0C"}]}],"hints":{"down":"Menu"}}
S->C {"v":1,"type":"event","id":"p2","topic":"device.summary","seq":2,"data":{"hostname":"wlanpi-test","model":"R4","mode":"classic","software_version":"3.4.0","ip":"192.0.2.10","eth0":"192.0.2.10","wlan0":null,"cpu":"3%","cpu_temp":"48.0C","uptime":"1h","battery":{"present":false,"percent":null,"status":null},"bluetooth_on":true,"profiler_running":false,"reachable":true,"wlan_adapters":["wlan0","wlan1"],"hotspot_clients":null},"snapshot":true}
S->C {"v":1,"type":"event","id":"p2","topic":"complications","seq":1,"data":[],"snapshot":true}
S->C {"v":1,"type":"event","id":"p2","topic":"alerts","seq":1,"data":[],"snapshot":true}
S->C {"v":1,"type":"event","id":"p2","topic":"jobs","seq":1,"data":[],"snapshot":true}
```

#### Scenario 2: `input` Navigation & Action Execution (`view` with `table` Block)
```json
C->S {"v":1,"type":"input","id":"p3","key":"down"}
S->C {"v":1,"type":"view","id":"p3","screen":"menu","node":"network","title":"Menu","breadcrumb":[],"blocks":[{"type":"list","text":"> Network >\n  Bluetooth >\n  Utils >\n  Apps >\n  System >","items":[{"label":"Network","node":"network","branch":true},{"label":"Bluetooth","node":"bluetooth","branch":true},{"label":"Utils","node":"utils","branch":true},{"label":"Apps","node":"apps","branch":true},{"label":"System","node":"system","branch":true}],"selected":0}],"hints":{"left":"Back","center":"Select"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":true,"surface":"wlanpi-fpms"}}
C->S {"v":1,"type":"input","id":"p4","key":"center"}
S->C {"v":1,"type":"view","id":"p4","screen":"menu","node":"network.interfaces","title":"Network","breadcrumb":["Network"],"blocks":[{"type":"list","text":"> Interfaces\n  Eth0 IP Config\n  Eth0 VLAN\n  LLDP Neighbour\n  CDP Neighbour\n  Public IPv4\n  Public IPv6","items":[{"label":"Interfaces","node":"network.interfaces","branch":false},...],"selected":0}],"hints":{"left":"Back","center":"Select"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":true,"surface":"wlanpi-fpms"}}
C->S {"v":1,"type":"input","id":"p5","key":"center"}
S->C {"v":1,"type":"job","id":"s:3","job_id":"j5UVqyVW1gkDc","node":"network.interfaces","state":"queued"}
S->C {"v":1,"type":"event","id":"s:4","topic":"jobs","seq":2,"data":[{"job_id":"j5UVqyVW1gkDc","node":"network.interfaces","state":"queued","sessions":1}]}
S->C {"v":1,"type":"view","id":"p5","screen":"busy","node":"network.interfaces","title":"Interfaces","job_id":"j5UVqyVW1gkDc","blocks":[{"type":"progress","text":"Working\u2026","pct":null,"label":"Working\u2026"}],"hints":{"left":"Back"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":true,"surface":"wlanpi-fpms"},"breadcrumb":["Network"]}
S->C {"v":1,"type":"job","id":"s:5","job_id":"j5UVqyVW1gkDc","node":"network.interfaces","state":"progress","progress":{"pct":null,"message":"Running"}}
S->C {"v":1,"type":"event","id":"s:7","topic":"jobs","seq":3,"data":[{"job_id":"j5UVqyVW1gkDc","node":"network.interfaces","state":"progress","sessions":1}]}
S->C {"v":1,"type":"job","id":"s:8","job_id":"j5UVqyVW1gkDc","node":"network.interfaces","state":"result","result":{"blocks":[{"type":"table","text":"Section | Interface | State | IPv4\nEthernet | eth0 | UP | 192.0.2.10/24\nWLAN | wlan0 | DOWN | -\n |   MAC: 02:00:00:00:00:00 \u00b7 Type: managed |  | ","columns":[{"key":"section","label":"Section","priority":0},{"key":"ifname","label":"Interface","priority":1},{"key":"state","label":"State","priority":2},{"key":"ipv4","label":"IPv4","priority":3}],"rows":[["Ethernet","eth0","UP","192.0.2.10/24"],["WLAN","wlan0","DOWN","-"],["","  MAC: 02:00:00:00:00:00 \u00b7 Type: managed","",""]]}],"total":3,"cursor":null,"title":"Interfaces"}}
S->C {"v":1,"type":"view","id":"s:9","screen":"result","node":"network.interfaces","title":"Interfaces","job_id":"j5UVqyVW1gkDc","blocks":[{"type":"table","text":"Section | Interface | State | IPv4\nEthernet | eth0 | UP | 192.0.2.10/24\nWLAN | wlan0 | DOWN | -\n |   MAC: 02:00:00:00:00:00 \u00b7 Type: managed |  | ","columns":[{"key":"section","label":"Section","priority":0},{"key":"ifname","label":"Interface","priority":1},{"key":"state","label":"State","priority":2},{"key":"ipv4","label":"IPv4","priority":3}],"rows":[["Ethernet","eth0","UP","192.0.2.10/24"],["WLAN","wlan0","DOWN","-"],["","  MAC: 02:00:00:00:00:00 \u00b7 Type: managed","",""]]}],"page":{"index":0,"count":1,"more":false},"hints":{"left":"Back","center":"Again"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":true,"surface":"wlanpi-fpms"},"breadcrumb":["Network"]}
S->C {"v":1,"type":"event","id":"s:10","topic":"jobs","seq":4,"data":[]}
```

#### Scenario 3: Hardware Shortcuts (`key1`, `key3`) & `confirm` Prompt (`input` Toggle & Cancel)
```json
C->S {"v":1,"type":"input","id":"p6","key":"key1"}
S->C {"v":1,"type":"job","id":"s:11","job_id":"jcJyMLQDaKG3Z","node":"utils.reachability","state":"queued"}
...
S->C {"v":1,"type":"view","id":"s:17","screen":"result","node":"utils.reachability","title":"Reachability","job_id":"jcJyMLQDaKG3Z","blocks":[{"type":"kv","text":"ping_google: OK\nbrowse_google: OK\ndns_google: OK","items":[{"key":"ping_google","value":"OK"},{"key":"browse_google","value":"OK"},{"key":"dns_google","value":"OK"}]}],"page":{"index":0,"count":1,"more":false},"hints":{"left":"Back","center":"Again"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":true,"surface":"wlanpi-fpms"},"breadcrumb":["Utils"]}
C->S {"v":1,"type":"input","id":"p7","key":"key3"}
S->C {"v":1,"type":"job","id":"s:19","job_id":"j0fuuGnvblxk6","node":"system.shutdown","state":"queued"}
S->C {"v":1,"type":"prompt","id":"s:21","prompt_id":"pwdIoPAEKfgaY","job_id":"j0fuuGnvblxk6","node":"system.shutdown","kind":"confirm","title":"Shutdown","expires_sec":120,"message":"Shut down the WLAN Pi now?","invoked_by":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"confirm_label":"Confirm","cancel_label":"Cancel"}
S->C {"v":1,"type":"view","id":"s:22","screen":"prompt","node":"system.shutdown","title":"Shutdown","blocks":[{"type":"confirm","text":"Shut down the WLAN Pi now?\n[Confirm] [Cancel]","prompt_id":"pwdIoPAEKfgaY","message":"Shut down the WLAN Pi now?","confirm_label":"Confirm","cancel_label":"Cancel","selected":"cancel"}],"job_id":"j0fuuGnvblxk6","answerable":true,"hints":{"left":"Cancel","center":"OK"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":true,"surface":"wlanpi-fpms"},"breadcrumb":["System"]}
C->S {"v":1,"type":"input","id":"p8","key":"down"}
S->C {"v":1,"type":"view","id":"p8","screen":"prompt","node":"system.shutdown","title":"Shutdown","blocks":[{"type":"confirm","text":"Shut down the WLAN Pi now?\n[Confirm] [Cancel]","prompt_id":"pwdIoPAEKfgaY","message":"Shut down the WLAN Pi now?","confirm_label":"Confirm","cancel_label":"Cancel","selected":"confirm"}],"job_id":"j0fuuGnvblxk6","answerable":true,"hints":{"left":"Cancel","center":"OK"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":true,"surface":"wlanpi-fpms"},"breadcrumb":["System"]}
C->S {"v":1,"type":"input","id":"p9","key":"left"}
S->C {"v":1,"type":"job","id":"s:24","job_id":"j0fuuGnvblxk6","node":"system.shutdown","state":"cancelled"}
S->C {"v":1,"type":"view","id":"s:25","screen":"result","node":"system.shutdown","title":"Shutdown","job_id":"j0fuuGnvblxk6","blocks":[{"type":"status","text":"Cancelled","level":"info"}],"hints":{"left":"Back","center":"Again"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":true,"surface":"wlanpi-fpms"},"breadcrumb":["System"]}
```

#### Scenario 4: `select` Prompt (`system.reg_domain.set`) -> `confirm` Prompt
```json
C->S {"v":1,"type":"input","id":"p27","key":"center"}
S->C {"v":1,"type":"job","id":"s:27","job_id":"jq0dcrJqeOHTI","node":"system.reg_domain.set","state":"queued"}
S->C {"v":1,"type":"prompt","id":"s:29","prompt_id":"pXlx43iol3Uhg","job_id":"jq0dcrJqeOHTI","node":"system.reg_domain.set","kind":"select","title":"Country","expires_sec":120,"invoked_by":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"options":[{"value":"US","label":"US"},{"value":"AU","label":"AU"},...]}
S->C {"v":1,"type":"view","id":"s:30","screen":"prompt","node":"system.reg_domain.set","title":"Country","blocks":[{"type":"list","text":"> US\n  AU\n  BR\n  CA\n  CZ\n  DE\n  FR\n  GB\n  NL\n  NO","items":[{"label":"US"},{"label":"AU"},...],"selected":0}],"job_id":"jq0dcrJqeOHTI","answerable":true,"hints":{"left":"Cancel","center":"OK"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":true,"surface":"wlanpi-fpms"},"breadcrumb":["System","Settings","RF Domain"]}
C->S {"v":1,"type":"input","id":"p28","key":"down"}
S->C {"v":1,"type":"view","id":"p28","screen":"prompt","node":"system.reg_domain.set","title":"Country","blocks":[{"type":"list","text":"  US\n> AU\n...","items":[{"label":"US"},{"label":"AU"},...],"selected":1}],"job_id":"jq0dcrJqeOHTI","answerable":true,"hints":{"left":"Cancel","center":"OK"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":true,"surface":"wlanpi-fpms"},"breadcrumb":["System","Settings","RF Domain"]}
C->S {"v":1,"type":"input","id":"p29","key":"center"}
S->C {"v":1,"type":"prompt","id":"s:32","prompt_id":"pSKnHqtYhAuxW","job_id":"jq0dcrJqeOHTI","node":"system.reg_domain.set","kind":"confirm","title":"RF Domain","expires_sec":120,"message":"Change the regulatory domain? A reboot is needed to apply it.","invoked_by":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"confirm_label":"Confirm","cancel_label":"Cancel"}
```

#### Scenario 5: Multi-Page Result (`apps.scanner.scan`, 45 rows / 3 pages) — Local Scroll + Server-Side `input` Page Turn + C2S `page` Cursor Frame
```json
S->C {"v":1,"type":"job","id":"s:43","job_id":"jbQvYehy1l9tL","node":"apps.scanner.scan","state":"result","result":{"blocks":[{"type":"table","text":"...","columns":[{"key":"ssid","label":"Ssid","priority":0},{"key":"bssid","label":"Bssid","priority":1},{"key":"channel","label":"Channel","priority":2},{"key":"signal","label":"Signal","priority":3}],"rows":[["net000","02:00:00:00:00:00",1,-40],...,["net019","02:00:00:00:00:13",9,-59]]}],"total":45,"cursor":"camJRdlllaHkxbDl0TDox","title":"Scan"}}
S->C {"v":1,"type":"view","id":"s:44","screen":"result","node":"apps.scanner.scan","title":"Scan","job_id":"jbQvYehy1l9tL","blocks":[...],"page":{"index":0,"count":3,"more":true},"hints":{"left":"Back","center":"Again"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":true,"surface":"wlanpi-fpms"},"breadcrumb":["Apps","Scanner"]}
C->S {"v":1,"type":"input","id":"p45","key":"down"}
S->C {"v":1,"type":"view","id":"p45","screen":"result","node":"apps.scanner.scan","title":"Scan","job_id":"jbQvYehy1l9tL","blocks":[...],"page":{"index":1,"count":3,"more":true},"hints":{"left":"Back","center":"Again"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":true,"surface":"wlanpi-fpms"},"breadcrumb":["Apps","Scanner"]}
C->S {"v":1,"type":"page","id":"p46","job_id":"jbQvYehy1l9tL","cursor":"camJRdlllaHkxbDl0TDox"}
S->C {"v":1,"type":"job","id":"p46","job_id":"jbQvYehy1l9tL","node":"apps.scanner.scan","state":"result","result":{"blocks":[...],"total":45,"cursor":"camJRdlllaHkxbDl0TDoy","title":"Scan"}}
C->S {"v":1,"type":"page","id":"p47","job_id":"jbQvYehy1l9tL","cursor":"camJRdlllaHkxbDl0TDoy"}
S->C {"v":1,"type":"job","id":"p47","job_id":"jbQvYehy1l9tL","node":"apps.scanner.scan","state":"result","result":{"blocks":[...],"total":45,"cursor":null,"title":"Scan"}}
```

#### Scenario 6: Client-Side `PromptEditor` (`string` / `form`) -> C2S `answer` Frame
```json
C->S {"v":1,"type":"input","id":"p61","key":"center"}
S->C {"v":1,"type":"job","id":"s:46","job_id":"jVS7mOS_PMFWk","node":"utils.ping_host","state":"queued"}
S->C {"v":1,"type":"prompt","id":"s:48","prompt_id":"pV_Lv5gqcyW6T","job_id":"jVS7mOS_PMFWk","node":"utils.ping_host","kind":"string","title":"Hostname","expires_sec":120,"invoked_by":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"default":"ab"}
S->C {"v":1,"type":"view","id":"s:49","screen":"prompt","node":"utils.ping_host","title":"Hostname","blocks":[{"type":"text","text":"Hostname: answer on the client keyboard"}],"job_id":"jVS7mOS_PMFWk","answerable":true,"hints":{"left":"Cancel","center":"OK"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":true,"surface":"wlanpi-fpms"},"breadcrumb":["Utils"]}
C->S {"v":1,"type":"answer","id":"p62","prompt_id":"pV_Lv5gqcyW6T","value":"abaaa"}
S->C {"v":1,"type":"job","id":"s:51","job_id":"jVS7mOS_PMFWk","node":"utils.ping_host","state":"progress","progress":{"pct":null,"message":"Running"}}
S->C {"v":1,"type":"job","id":"s:54","job_id":"jVS7mOS_PMFWk","node":"utils.ping_host","state":"result","result":{"blocks":[{"type":"kv","text":"ping_google: OK\nbrowse_google: OK\ndns_google: OK","items":[{"key":"ping_google","value":"OK"},{"key":"browse_google","value":"OK"},{"key":"dns_google","value":"OK"}]}],"total":3,"cursor":null,"title":"Ping Host"}}
S->C {"v":1,"type":"view","id":"s:55","screen":"result","node":"utils.ping_host","title":"Ping Host","job_id":"jVS7mOS_PMFWk","blocks":[{"type":"kv","text":"ping_google: OK\nbrowse_google: OK\ndns_google: OK","items":[{"key":"ping_google","value":"OK"},{"key":"browse_google","value":"OK"},{"key":"dns_google","value":"OK"}]}],"page":{"index":0,"count":1,"more":false},"hints":{"left":"Back","center":"Again"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":true,"surface":"wlanpi-fpms"},"breadcrumb":["Utils"]}
```

#### Scenario 7: `job` Progress & C2S `cancel` Frame on `busy` Screen (`utils.speedtest.run`)
```json
C->S {"v":1,"type":"input","id":"p69","key":"center"}
S->C {"v":1,"type":"job","id":"s:57","job_id":"j8rVe0swuBepK","node":"utils.speedtest.run","state":"queued"}
S->C {"v":1,"type":"event","id":"s:58","topic":"jobs","seq":18,"data":[{"job_id":"j8rVe0swuBepK","node":"utils.speedtest.run","state":"queued","sessions":1}]}
S->C {"v":1,"type":"view","id":"p69","screen":"busy","node":"utils.speedtest.run","title":"Run Test","job_id":"j8rVe0swuBepK","blocks":[{"type":"progress","text":"Working\u2026","pct":null,"label":"Working\u2026"}],"hints":{"left":"Back"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":true,"surface":"wlanpi-fpms"},"breadcrumb":["Utils","Speedtest"]}
S->C {"v":1,"type":"job","id":"s:59","job_id":"j8rVe0swuBepK","node":"utils.speedtest.run","state":"progress","progress":{"pct":null,"message":"Running"}}
S->C {"v":1,"type":"view","id":"s:60","screen":"busy","node":"utils.speedtest.run","title":"Run Test","job_id":"j8rVe0swuBepK","blocks":[{"type":"progress","text":"Running","pct":null,"label":"Running"}],"hints":{"left":"Back"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":true,"surface":"wlanpi-fpms"},"breadcrumb":["Utils","Speedtest"]}
S->C {"v":1,"type":"event","id":"s:61","topic":"jobs","seq":19,"data":[{"job_id":"j8rVe0swuBepK","node":"utils.speedtest.run","state":"progress","sessions":1}]}
C->S {"v":1,"type":"cancel","id":"p70","job_id":"j8rVe0swuBepK"}
S->C {"v":1,"type":"job","id":"p70","job_id":"j8rVe0swuBepK","node":"utils.speedtest.run","state":"cancelled"}
S->C {"v":1,"type":"event","id":"s:62","topic":"jobs","seq":20,"data":[{"job_id":"j8rVe0swuBepK","node":"utils.speedtest.run","state":"progress","sessions":0}]}
S->C {"v":1,"type":"view","id":"s:63","screen":"menu","node":"utils.speedtest.run","title":"Speedtest","breadcrumb":["Utils","Speedtest"],"blocks":[{"type":"list","text":"> Run Test","items":[{"label":"Run Test","node":"utils.speedtest.run","branch":false}],"selected":0}],"hints":{"left":"Back","center":"Select"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":true,"surface":"wlanpi-fpms"}}
S->C {"v":1,"type":"event","id":"s:64","topic":"jobs","seq":21,"data":[]}
```

#### Scenario 8: Same-Epoch Reconnect & Resume (`welcome.resumed: true`)
```json
C->S {"v":1,"type":"hello","id":"p71","mode":"thin","session":"default","protocol":[1],"client":{"name":"wlanpi-fpms","version":"1.4.26"},"capabilities":{"screen":{"width_px":128,"height_px":128,"cols":21,"rows":8},"colour":"colour","blocks":["text","kv","table","list","qr","status","progress","confirm"],"keys":["up","down","left","right","center","key1","key2","key3"]},"resume":{"epoch":"orBG3VzmI1nJlPq_","session_id":"SZ18lPsBz9C_VNNtu","seq":{"device.summary":2,"complications":1,"alerts":1,"jobs":21},"menu_revision":2}}
S->C {"v":1,"type":"welcome","id":"p71","session_id":"SZ18lPsBz9C_VNNtu","session":"default","epoch":"orBG3VzmI1nJlPq_","principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"roles":["admin"],"version":1,"mode":"thin","resumed":true,"limits":{"queue_frames":64,"max_frame_bytes":262144}}
S->C {"v":1,"type":"view","id":"s:65","screen":"menu","node":"utils.speedtest.run","title":"Speedtest","breadcrumb":["Utils","Speedtest"],"blocks":[{"type":"list","text":"> Run Test","items":[{"label":"Run Test","node":"utils.speedtest.run","branch":false}],"selected":0}],"hints":{"left":"Back","center":"Select"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":false,"surface":"wlanpi-fpms"}}
```

---

## 3. All 8 Block Types on Virtual Screen & Geometry Analysis

### 3.1 Virtual Screen Rendering (`128x128`)
All 8 protocol block types (`text`, `kv`, `table`, `list`, `qr`, `status`, `progress`, `confirm`) in `fpms/modules/menud_client/render.py:139-473` (plus `qr.py:1-54`) were rendered onto `fpms.modules.screen.virtual.Virtual` (`128x128` RGB image + plain-text `Frame.lines`). Every block produced a non-empty, multi-colour image and pairwise-distinct `image.tobytes()` payload:

| Block Type | Implementation (`render.py`) | Image Size | Unique RGB Colours | Rendered `Frame.lines` |
| --- | --- | --- | --- | --- |
| `text` | `_text` (`L139-160`) | `128x128` | 221 | `["Hello WLAN Pi"]` |
| `kv` | `_kv` (`L163-197`) | `128x128` | 310 | `["IP: 192.168.1.10", "Mode: Classic"]` |
| `table` | `_table` + `table_layout` (`L200-270`) | `128x128` | 278 | `["If    IP", "eth0  10.0.0.2", "wlan0 down"]` |
| `list` | `_list` (`L273-299`) | `128x128` | 291 | `["> Network", "  System"]` |
| `qr` | `_qr` (`L423-441`) + `qr.py:18-54` | `128x128` | 213 | `["[qr 58x58] wlanpi.com"]` (29x29 modules @ 2px/module; module centres verified) |
| `status` | `_status` (`L301-331`) | `128x128` | 279 | `["[ok] Connected: 1 Gbps Full"]` |
| `progress` | `_progress` (`L333-360`) | `128x128` | 242 | `["[progress 45%] Running speedtest"]` |
| `confirm` | `_confirm` (`L362-421`) | `128x128` | 346 | `["Reboot now?", "[Cancel]  Reboot "]` |

### 3.2 Display Geometry Support (`128x128`, `128x64`, `172x32`)
- **Supported Hardware Display Paths in `wlanpi-fpms`:**
  - `wlanpi-fpms` supports the `128x128` SSD1351 SPI OLED (`PLATFORM_PRO`, via `fpms/modules/wlanpi_oled.py` / `luma.py:42-47`), `128x128` ST7735 SPI TFT (`PLATFORM_R4` / `M4` / `M4+` / `Go`, via `fpms/modules/screen/st7735.py`), and `240x240` ST7789 SPI TFT (`luma.py:49-58`, which scales the `128x128` canvas to `240x240`).
  - `128x64` (legacy NEO2 NanoHat I2C OLED from the older `fpms` repo, noted in `README.md:7`) and `172x32` are **not** hardware display driver paths in `wlanpi-fpms` (`constants.py:31-32` sets `PAGE_WIDTH = 128`, `PAGE_HEIGHT = 128`).
- **Parameterized Geometry Verification (`128x64` and `172x32`):**
  - Because `render.py` computes layout from `PAGE_WIDTH`, `PAGE_HEIGHT`, `BODY_H`, `ROWS`, and `COLS`, monkeypatching `render` to `128x64` (`rows=2, cols=21`) and `172x32` (`rows=1, cols=28`) in the container verification script confirmed that all 8 block types render cleanly at both dimensions without error, and that `_qr` (`render.py:426-431`) automatically detects when a QR matrix cannot fit at `>= 1 px/module` in `128x64` / `172x32` and falls back to `block["text"]` (`"https://wlanpi.com"`) as required by `PROTOCOL.md`.

---

## 4. Service-Down Screen & systemd Unit Ordering

### 4.1 Service-Down Screen (`render.unavailable_view`, `netinfo.py`, `panel.py`)
- When `not client.connected or client.view is None`, `Panel.compose()` (`fpms/modules/menud_client/panel.py:297-305`) renders `render.unavailable_view(netinfo.ipv4_addresses(), netinfo.hostname(), failures=client.failures, reason=client.last_failure)` (`render.py:591-628`).
- Before the first connection attempt fails (`failures == 0`), it shows `[info] Starting menu: Connecting…`.
- Once a connection attempt has failed or dropped (`failures >= 1`), it shows:
  - `[error] Menu unavailable: Retrying…`
  - Every non-loopback IPv4 interface and address from `ip -4 -o addr show scope global` (`fpms/modules/menud_client/netinfo.py:13-35`), or `[warn] No IP address` if none is assigned
  - Device hostname (`netinfo.hostname()`) and failure reason (`client.last_failure`)
- While disconnected, `MenudClient.run()` (`fpms/modules/menud_client/client.py:175-253`) retries with jittered exponential backoff (`0.5s` to `8.0s`), and pressing any front-panel button calls `client.retry_now()` (`panel.py:141-143`, `client.py:160-162`) to skip the remaining backoff wait immediately.

### 4.2 systemd Unit (`debian/wlanpi-fpms.service`)
Inspection of `debian/wlanpi-fpms.service:1-25` confirms:
- Line 7 uses `Wants=wlanpi-menud.service` (not `Requires=`, and no blocking `After=wlanpi-menud.service`), so `wlanpi-fpms` starts early in boot even if `wlanpi-menud` is still starting, restarting, or stopped, and displays the local service-down/IP screen until `/run/wlanpi-menud/local.sock` becomes available.
- `debian/control:20` uses `Recommends: wlanpi-menud` (not a hard `Depends:`).

---

## 5. Mid-Session `wlanpi-menud` Restart & Epoch-Resume Recovery

In Scenario 9 of the live container verification (`fpms-verify:redesign`), the real `wlanpi-menud` service (`epoch = "orBG3VzmI1nJlPq_"`) was stopped mid-session and replaced by a new `Service` + `Server` instance (`epoch = "ijFJYEOaDoWYQJAg"`) on the same `local.sock`:
1. While `wlanpi-menud` was stopped, `Panel.compose()` rendered the local service-down view:
   ```text
   Title: WLAN Pi
   [error] Menu unavailable: Retrying…
   eth0: 192.168.42.1
   wlan0: 10.0.0.99
   wlanpi-test
   connection closed
   ```
2. When the new `wlanpi-menud` instance started on `local.sock`, the panel reconnected and sent `hello` carrying its previous `resume` state (`epoch: "orBG3VzmI1nJlPq_"`).
3. The new `wlanpi-menud` replied with `welcome` (`epoch: "ijFJYEOaDoWYQJAg"`, `resumed: false`).
4. `MenudClient._welcome` (`fpms/modules/menud_client/client.py:289-311`) detected `frame.get("epoch") != self.epoch`, cleared `topic_seq`, `topic_data`, `stale_topics`, `jobs`, `prompts`, and `menu_revision`, discarded `resume`, sent a fresh `subscribe` for `["device.summary", "complications", "alerts", "jobs"]`, received the new `menu`, `view` (`home`), and 4 `event` (`snapshot: true`) frames, and resumed normal button navigation:

```json
C->S {"v":1,"type":"hello","id":"p72","mode":"thin","session":"default","protocol":[1],"client":{"name":"wlanpi-fpms","version":"1.4.26"},"capabilities":{"screen":{"width_px":128,"height_px":128,"cols":21,"rows":8},"colour":"colour","blocks":["text","kv","table","list","qr","status","progress","confirm"],"keys":["up","down","left","right","center","key1","key2","key3"]},"resume":{"epoch":"orBG3VzmI1nJlPq_","session_id":"SZ18lPsBz9C_VNNtu","seq":{"device.summary":2,"complications":1,"alerts":1,"jobs":21},"menu_revision":2}}
S->C {"v":1,"type":"welcome","id":"p72","session_id":"SYmWbu8ekTefV9Q_F","session":"default","epoch":"ijFJYEOaDoWYQJAg","principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"roles":["admin"],"version":1,"mode":"thin","resumed":false,"limits":{"queue_frames":64,"max_frame_bytes":262144}}
C->S {"v":1,"type":"subscribe","id":"p73","topics":["device.summary","complications","alerts","jobs"]}
S->C {"v":1,"type":"menu","id":"s:1","revision":2,"nodes":[...],"device_mode":"classic"}
S->C {"v":1,"type":"view","id":"s:2","screen":"home","node":null,"title":"WLAN Pi","blocks":[...],"hints":{"down":"Menu"}}
S->C {"v":1,"type":"event","id":"p73","topic":"device.summary","seq":2,"data":{...},"snapshot":true}
S->C {"v":1,"type":"event","id":"p73","topic":"complications","seq":1,"data":[],"snapshot":true}
S->C {"v":1,"type":"event","id":"p73","topic":"alerts","seq":1,"data":[],"snapshot":true}
S->C {"v":1,"type":"event","id":"p73","topic":"jobs","seq":1,"data":[],"snapshot":true}
C->S {"v":1,"type":"input","id":"p74","key":"down"}
S->C {"v":1,"type":"view","id":"p74","screen":"menu","node":"network","title":"Menu","breadcrumb":[],"blocks":[...],"hints":{"left":"Back","center":"Select"},"driven_by":{"principal":{"kind":"local","name":"root","uid":0,"did":"wlanpi-menud"},"self":true,"surface":"wlanpi-fpms"}}
```

---

## 6. Mutation Checks (Resume Epoch Handling & Block Renderers)

Mutations were executed against an isolated `/tmp/fpms-mutation` copy of `wlanpi-fpms` (leaving the workspace repository untouched):

1. **Mutation 1 — Break Resume Epoch Handling (`fpms/modules/menud_client/client.py:289`):**
   - **Change:** Replaced `if self.epoch and frame.get("epoch") != self.epoch:` with `if False:`.
   - **Result:** Test suite **FAILED** as expected:
     - `FAILED tests/menud_client/test_panel.py::test_new_epoch_drops_old_state_and_subscribes_again` (`AssertionError: assert {'jab12': ...} == {}` at `tests/menud_client/test_panel.py:848`).
2. **Mutation 2A — Break `qr` Block Renderer (`fpms/modules/menud_client/render.py:434`):**
   - **Change:** Replaced `img.paste(code, ((PAGE_WIDTH - code.width) // 2, y))` with `pass`.
   - **Result:** Test suite **FAILED** as expected:
     - `FAILED tests/menud_client/test_render.py::test_qr_block_draws_the_real_code` (`AssertionError: (0, 0) assert (0, 0, 0) == (255, 255, 255)` at `tests/menud_client/test_render.py:301`).
3. **Mutation 2B — Break `confirm` Block Renderer (`fpms/modules/menud_client/render.py:461`):**
   - **Change:** Replaced `_confirm(body, b)` with `pass`.
   - **Result:** Test suite **FAILED** as expected (3 failures):
     - `FAILED tests/menud_client/test_render.py::test_confirm_block_highlights_the_selected_button`
     - `FAILED tests/menud_client/test_render.py::test_confirm_block_stacks_long_labels`
     - `FAILED tests/menud_client/test_panel.py::test_confirm_prompt_toggle_and_confirm_with_buttons`

---

## 7. Test Suites, Clean Container `.deb` Build, and `lintian`

### 7.1 Test Suite & Static Analysis Summary
| Repository | Suite / Tool | Result |
| --- | --- | --- |
| `wlanpi-fpms` | `pytest -v` (incl. `test_live_menud.py`) | **108 passed** in 9.97s |
| `wlanpi-fpms` | `ruff check fpms tests` | **All checks passed** |
| `wlanpi-fpms` | `ruff format --check fpms tests` | **44 files already formatted** |
| `wlanpi-fpms` | `mypy fpms` | **Success: no issues found in 28 source files** |
| `wlanpi-menud` | `pytest -q` | **216 passed** in 13.81s |
| `wlanpi-menu-tui` | `pytest -q` | **25 passed** in 11.38s |
| `wlanpi-webui` | `pytest -q` | **483 passed** in 25.43s |
| `wlanpi-webui` | `node --test tests/js/*.test.mjs` | **230 passed** (54 suites) in 4.86s |

### 7.2 Clean Container `.deb` Build & `lintian` (`wlanpi-fpms-builder:trixie`)
- Built in an isolated container (`/tmp/pkg/wlanpi-fpms` copied from read-only `/src`) using `mk-build-deps` + `dpkg-buildpackage -us -uc -b`:
  - **Artifact:** `wlanpi-fpms_1.4.26_arm64.deb`
  - **Control Metadata:** `Recommends: wlanpi-menud`, `Depends: libopenjp2-7, wlanpi-common, libc6 (>= 2.38), ...`
  - **Installed Contents Verified:**
    - `/opt/wlanpi-fpms/bin/fpms`
    - `/opt/wlanpi-fpms/lib/python3.13/site-packages/fpms/modules/menud_client/{__init__,client,editor,netinfo,panel,protocol,qr,render}.py`
    - `/usr/libexec/wlanpi-fpms/fpms-splash` (compiled from `splash/fpms-splash.c`)
    - `/usr/lib/systemd/system/wlanpi-fpms.service` (`Wants=wlanpi-menud.service`)
    - `/usr/lib/systemd/system/wlanpi-fpms-splash.service`
    - `/usr/lib/systemd/system/wlanpi-fpms-failed.service`
    - Zero deleted legacy modules exist in the package payload.
  - **`lintian` Summary:** Ran `lintian -i -I --show-overrides` on `wlanpi-fpms_1.4.26_arm64.deb`. Only expected `dh_virtualenv` `/opt/wlanpi-fpms` layout tags (`dir-or-file-in-opt`, `package-installs-python-pycache-dir`, `unusual-interpreter`, wheel `.so` `unstripped-binary-or-object`) and pre-existing `debian/control` / `debian/changelog` formatting tags from `main` were emitted; zero packaging regressions or broken unit/dependency tags.

---

## 8. Device `10.254.102.60` Untouched

Audited `/home/jakesnyder/.gemini/antigravity-cli/brain/0e7e954b-b776-46bf-bff7-0c05a5e40e5f/.system_generated/logs/transcript.jsonl`:
- Zero `ssh`, `scp`, `rsync`, or network connections to `10.254.102.60` were executed during this session (the IP appears only in the prompt text).

---

## Verdict

**APPROVE** — `wlanpi-fpms` branch `phase3/menud-client` (`6aeae3f`) is approved for merge.
