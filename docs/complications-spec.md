# Complications: creation and display integration (spec)

Status: **design, not implemented**. Written 13 Nov 2026 against `wlanpi-menud` branch `phase1/service`, `wlanpi-menu-tui` `main`, `wlanpi-fpms` `phase3/menud-client`, `wlanpi-webui` `redesign/webui-menu-panel`.

A complication is one small status tile: a label, a value and a status, for example `Batt 85%` or `Uplink OK`. This spec defines the two integrations that are missing today:

1. **Creation**: how anything outside the service publishes, updates and removes a complication.
2. **Display**: how each screen chooses which complications it shows, and in what order.

Everything here is additive to protocol v1. A client or provider written before this spec keeps working unchanged.

## Headline decisions

| # | Decision | Why |
| --- | --- | --- |
| D1 | The one write path is a pair of `local.sock` frames, `complication.publish` and `complication.remove`. `/usr/bin/wlanpi-complication` is a thin wrapper around them. A JSON declaration file in `plugins.d/<name>/` supplies catalog metadata and ownership, never values. | One path to authorize, test and rate-limit. Scripts get a one-line CLI; packages get stable, owned ids. |
| D2 | `app_id` is namespaced: `wlanpi.*` (built-in, service only), `plugin.<name>.*` (declared by a root-installed file that names the publishing unix user), `local.<user>.*` (ad hoc, no declaration, bound to the caller's own username). | Ownership comes from who can write a root-owned file or from `SO_PEERCRED`, so an id cannot be squatted. |
| D3 | The legacy `complications` topic and `device.summary` stay exactly as they are. Three new topics carry the new model: `complications.live` (built-in plus provider, full fields), `complications.catalog` (what can exist) and `complications.config` (what each surface shows). | Old surfaces render every entry of `complications` next to `device.summary`. Putting built-ins there would show every value twice on a surface that has not been updated. |
| D4 | Display config is stored in the service, keyed by **surface profile** (`panel`, `panel.strip`, `tui`, `webui`), and changed with one frame, `complications.configure`. It is not per principal and not client-local. | The thin home screen is rendered by the service, so the service has to know the selection anyway. It also lets the WebUI edit the panel's layout, which is hard to do with five buttons. |
| D5 | The user's config decides **which tiles and in what order**. The surface decides **how many fit and how they are drawn**. Overflow is cut from the end of the list and never reordered. Alerts take rows before tiles do. | Capacity is a property of the screen, so a config never fails on a smaller screen; it just shows a shorter prefix. |

## 1. Where things stand

| Area | Today | Reference |
| --- | --- | --- |
| Store | `Service.complications`, a dict keyed by `app_id`; `publish_complication(app_id, label, value, status="unknown", ttl_seconds=30)` truncates the label to 8 characters | `wlanpi_menud/service.py:518` |
| Front door | **None.** No frame, CLI or file reaches `publish_complication`; only in-process code and tests call it | `protocol/PROTOCOL.md:281` ("Not yet in the protocol") |
| Expiry | Checked only when the poller's housekeeping runs, so an entry can outlive its TTL by up to `poll_interval_sec` (5 s) | `wlanpi_menud/poller.py:190` |
| Wire shape | `topic_complications`: array of `{app_id, label (max 8), value, status}`, `additionalProperties: false`; `app_id`, `value` and `status` are unconstrained strings | `protocol/frames.schema.json:695` |
| Status values | Free text. The service's home view maps `ok`, `warning` and `error`; anything else becomes `info` | `wlanpi_menud/views.py:41` |
| Built-in status | In `device.summary`, not in complications | `wlanpi_menud/poller.py:161` |
| Thin home | `home_blocks()` shows a fixed `kv` (Host, Mode, IP, Model, Temp), then alerts, then every complication as a `status` block | `wlanpi_menud/views.py:30` |
| TUI | Footer shows five fixed `device.summary` keys, then every complication as `label: value`; status is ignored | `wlanpi_menu_tui/app.py:827` |
| Panel | Subscribes to `complications` but draws home from `view` blocks only | `fpms/modules/menud_client/protocol.py:12` |
| WebUI | Home device card from `device.summary`, then every complication as `label value [status]` | `wlanpi_webui/static/js/menu/pages.js:534` |
| Legacy FPMS | Status line `HH:MM ⚡85% BT ☀` (clock, battery, Bluetooth, reachability), then interface address, link state, alert, hostname | `wlanpi-fpms` `main`, `fpms/modules/pages/homepage.py:372` |

No surface can choose what it shows, and nothing outside the service can create a complication.

## 2. Creation integration

### 2.1 Options compared

| | (a) `local.sock` frames | (b) CLI helper | (c) Manifest-declared |
| --- | --- | --- | --- |
| Carries live values | Yes | Yes, by sending (a) | No. A static file cannot change every few seconds |
| Works from a shell script or cron | Needs a WebSocket client | One line | n/a |
| Works from any language, long-lived daemon | Yes | Costs a process per update | n/a |
| Identity | `SO_PEERCRED` uid | The caller's uid, same check | File ownership (root, through dpkg) |
| Catalog entry before the first publish | No | No | Yes |

**Recommendation: (a) is the primary and only write path.** (b) ships in the same package as a wrapper, so there is still one code path in the service. (c) is used for what it is good at: declaring that a complication exists, what it is called and which unix user may publish it. Values never come from files.

### 2.2 Frames

A provider connects to `local.sock` like any client and completes the normal handshake. It should send `hello` with `mode: "rich"`, `session: "new"` and the new `ephemeral: true` (section 5.1), then:

```json
{"v":1, "type":"complication.publish", "id":"c2",
 "app_id":"plugin.acme.uplink", "value":"412 Mb/s", "short":"412",
 "status":"ok", "detail":"WAN2, 14 ms", "ttl_sec":60}
```

```json
{"v":1, "type":"complication.ack", "id":"c2",
 "app_id":"plugin.acme.uplink", "state":"live", "ttl_sec":60}
```

```json
{"v":1, "type":"complication.remove", "id":"c3", "app_id":"plugin.acme.uplink"}
```

```json
{"v":1, "type":"complication.ack", "id":"c3", "app_id":"plugin.acme.uplink", "state":"removed"}
```

`complication.publish` fields:

| Field | Required | Constraint | Meaning |
| --- | --- | --- | --- |
| `app_id` | Yes | Section 2.4 | Identity. One live record per `app_id` |
| `value` | Yes | 1 to 20 characters | The reading |
| `label` | For `local.*` ids | 1 to 8 characters | Short name. A declared id defaults to its declared label; a published label overrides it |
| `short` | No | 1 to 4 characters | Compact form for strips (`85%`, `-52`, `ON`). Without it a strip shows the first 4 characters of `value` |
| `status` | No, default `unknown` | Section 2.6 | Health of the reading |
| `detail` | No | Up to 64 characters | Longer text for surfaces with room (WebUI tooltip, TUI) |
| `icon` | No | Section 2.7 | Named glyph; overrides the declared icon |
| `ttl_sec` | No, default 30 | Integer 5 to 3600 | Lifetime from this publish |
| `hold` | No, default `ttl` | `ttl` or `connection` | `connection` also removes the record when this connection closes |

Refusals use the existing `error` frame and existing codes, so no error-code enum changes:

| Case | Code |
| --- | --- |
| Sent on `proxy.sock`; role is not `provider` or `admin`; `app_id` is outside the caller's namespace; `wlanpi.*`; quota reached | `FORBIDDEN` |
| Fails the schema; `plugin.*` id with no declaration; `label` missing on a `local.*` id | `BAD_REQUEST` |
| `complication.remove` for an id that is not live | `NOT_FOUND` |

### 2.3 Update semantics

- **Replace by `app_id`.** A publish replaces the whole record. Omitted optional fields return to their defaults; nothing is merged from the previous publish.
- **TTL.** Every publish restarts the lifetime. Values outside 5 to 3600 fail the schema; they are not clamped, so a provider learns about the mistake. There is no "never expires": a provider that stops reporting must disappear. 3600 covers hourly cron jobs.
- **Expiry.** The service removes a record within 1 s of its deadline using a timer for the nearest deadline, not the 5 s poller tick. Removal publishes the changed topics. The catalog entry of a declared id remains.
- **`hold: "connection"`.** For daemons: a crash removes the tile at once instead of after the TTL. The TTL still applies.
- **No-change publishes.** A publish whose visible fields equal the live record only restarts the TTL and causes no `event`. This is the cheap heartbeat.
- **Fan-out.** The `complications*` topics publish at most twice a second; changes inside that window collapse into one event. Every publish is still acknowledged.
- **Quotas.** 8 live records per publishing uid, 32 provider records in total. Both are config keys.
- **Restart.** Records are not persisted. A new `epoch` starts with only built-ins; providers publish again when they reconnect.
- **Sanitizing.** The service strips control characters, ANSI escapes and bidirectional-override characters from every text field, then applies the length limits. Surfaces still treat tile text as untrusted and escape it.
- **No secrets.** Complications reach every subscriber, including `viewer` sessions. Passphrases and keys must never be published. The provider docs state this.

### 2.4 Auth and namespaces

Who may publish: a connection on **`local.sock`** whose principal has role **`provider` or `admin`**. `operator` and `viewer` may not. `proxy.sock` never may, whatever the role: browsers and remote clients are surfaces, not providers.

`app_id` matches `^(wlanpi|plugin|local)(\.[a-z][a-z0-9_]*){1,3}$`, at most 64 characters.

| Namespace | Who may publish | How ownership is established |
| --- | --- | --- |
| `wlanpi.*` | Only the service itself. Refused for every connection, including root | Reserved in code. Section 3 lists the ids |
| `plugin.<name>.*` | The unix user named in that plugin's declaration, and root | `/usr/share/wlanpi-menud/plugins.d/<name>/complications.json` is root-owned and installed by a package. `<name>` must equal the directory name, the same rule plugin menu nodes follow |
| `local.<user>.*` | The principal whose unix username is `<user>`; root may publish under any `local.*` | `SO_PEERCRED`. No declaration needed. This is the path for a personal script |

Usernames that do not fit the segment pattern are mapped by lowercasing and replacing other characters with `_`.

A declaration **grants the `provider` role** on `local.sock` to its `publisher.user`. Today that needs a manual `provider_uids` edit in `config.toml`. Installing a root-owned file already takes root, so this adds no privilege. `provider_uids` keeps working.

Declaration file, validated by a new `protocol/complications.schema.json` with the same reject-and-name-the-field behaviour as manifests:

```json
{
  "schema": 1,
  "publisher": {"user": "acme"},
  "complications": [
    {
      "id": "plugin.acme.uplink",
      "title": "Acme uplink speed",
      "label": "Uplink",
      "icon": "globe",
      "description": "Measured WAN throughput from the Acme agent.",
      "node": "plugin.acme.status"
    }
  ]
}
```

| Field | Constraint | Use |
| --- | --- | --- |
| `id` | `plugin.<name>.*` | The `app_id` |
| `title` | 1 to 24 characters | Name shown in customize screens |
| `label` | 1 to 8 characters | Default label |
| `icon` | Section 2.7 | Default icon |
| `description` | Up to 120 characters | WebUI customize page |
| `node` | A node id under `plugin.<name>.` | Menu entry a rich surface opens when the tile is activated. Thin surfaces ignore it in this version |

A bad declaration is rejected as a whole and logged; the plugin's menu manifests still load. The existing dpkg trigger on `plugins.d` reloads declarations.

### 2.5 CLI helper

`/usr/bin/wlanpi-complication`, shipped in the `wlanpi-menud` package as a link into its virtualenv. One invocation is one connection: `hello`, one request, wait for the reply, close.

```sh
wlanpi-complication publish local.wlanpi.vpn --label VPN --value up --status ok --ttl 120
wlanpi-complication publish plugin.acme.uplink --value "412 Mb/s" --short 412 --status ok
wlanpi-complication remove  local.wlanpi.vpn
wlanpi-complication list            # live set, one line each; --json for the raw topic
wlanpi-complication catalog         # catalog; --json
wlanpi-complication show panel      # a surface profile's list
wlanpi-complication show panel --set wlanpi.mode,wlanpi.ip,plugin.*   # operator or admin
wlanpi-complication show panel --reset
```

Exit codes: `0` done, `2` usage error, `3` refused by the service (the message goes to stderr), `4` service unreachable. A cron job reporting every minute uses `--ttl 120`, so one missed run does not blank the tile.

### 2.6 Status vocabulary

Closed set. `status` on the new frames and the new topic is an enum.

| Status | Meaning | Panel colour and marker | TUI style | WebUI | `status` block level |
| --- | --- | --- | --- | --- | --- |
| `ok` | Working as intended | Green square | green | success tag, check icon | `ok` |
| `info` | Neutral fact, nothing to judge | Text colour, no marker | default | no tag | `info` |
| `warn` | Degraded, look soon | Amber square | yellow | warning tag, warning icon | `warn` |
| `error` | Broken, act now | Red square | bold red | danger tag, close icon | `error` |
| `off` | Deliberately disabled or idle | Dimmed text, hollow square | dim | muted tag | `info` |
| `unknown` | No reading yet | Dimmed text, value drawn as `--` | dim | muted, value `—` | `info` |

Rules for surfaces:

- Colour is never the only signal. The marker shape or the tag text changes with it; mono and grey screens rely on that.
- An unrecognized status is drawn as `unknown`.
- `warning` is accepted on `complication.publish` as a deprecated alias and stored as `warn`, because the in-process API has used it.

### 2.7 Label, value and icon budgets

The limits come from the 128 px panel: 21 columns of the 10 px font (`COLS` in `fpms/modules/menud_client/render.py`).

| Field | Hard limit (schema) | Fits the panel untruncated when |
| --- | --- | --- |
| `label` | 8 | Always |
| `value` | 20 | `len(label) + 1 + len(value) <= 21`, so 12 characters beside an 8-character label |
| `short` | 4 | Always. A strip slot is 4 characters plus a separator |
| `detail` | 64 | Never shown on the panel |
| `title` (catalog) | 24 | Customize list rows, where it has the full width |

A surface that runs out of width cuts the value and ends it with `…`. It never cuts the label. `short` should be ASCII: the panel replaces a glyph it cannot draw with `?`.

`icon` is one name from a fixed list, mapped by each surface to its own glyphs: `battery`, `bolt`, `bluetooth`, `globe`, `wifi`, `ethernet`, `thermometer`, `cpu`, `users`, `radio`, `shield`, `dot`. A surface without that glyph draws none. Adding a name later is additive.

### 2.8 Catalog and live set

Two different questions get two topics, both typed in the schema.

| Topic | Answers | Changes when |
| --- | --- | --- |
| `complications.catalog` | What could be shown? Every built-in id the device can produce, every declared plugin id, every `local.*` id that is live now | A plugin is installed or removed, a capability appears, a `local.*` id appears or expires |
| `complications.live` | What has a value now? | A value, status or liveness changes |
| `complications` (legacy) | Unchanged: provider-published records as `{app_id, label, value, status}` | As today |

Catalog entry: `{app_id, title, label, source: "builtin"|"plugin"|"local", icon?, description?, node?}`, sorted by `app_id`. A `local.*` entry is synthesized with `title` equal to its label and leaves the catalog when it expires.

Live entry: `{app_id, label, value, status, short?, detail?, icon?, node?}`, sorted by `app_id`. Order is a display concern and comes from the config.

A customize screen lists the catalog and marks each entry "reporting" when it is also in the live set, so a user can select a tile whose provider is not running at the moment.

`complications` keeps provider records only. Built-ins appear only in `complications.live`. That is what keeps an old surface from showing `Mode` twice.

## 3. Built-in providers

### 3.1 `device.summary` stays; complications are derived

`device.summary` is unchanged in shape and timing. The TUI footer, the WebUI device card and status bar, and the service's own unavailable screen read it. Built-in complications are a **second view of the same poll**: a pure function `builtin_complications(summary, wifi)` runs after the summary is assembled and feeds the store. Apart from the Wi-Fi link in 3.3 it makes no core calls.

- Built-ins have no TTL. Each tick replaces them.
- A built-in is absent from the live set when the device cannot produce it: no battery, no Bluetooth capability, not in hotspot mode. It is `unknown` when it should exist but core did not answer.
- While core is unavailable every `wlanpi.*` record is removed. The `unavailable` screen and the `alerts` topic already cover that state.
- `alerts` is untouched. Alerts are sentences that demand attention; complications are readings. `NO INTERNET` stays an alert and `wlanpi.internet` is the reading behind it.

### 3.2 Reserved ids

| `app_id` | Label | Value (example) | `short` | Status rule | Icon | Source field | Present when |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `wlanpi.mode` | `Mode` | `Classic` | `CLS` | `info` | `dot` | `mode` | Always |
| `wlanpi.ip` | `IP` | `192.0.2.10` | none | `ok`; `warn` with value `none` when null | `ethernet` | `ip` | Always |
| `wlanpi.eth0` | `eth0` | `192.0.2.10` | none | `ok`; `off` with value `no IP` | `ethernet` | `eth0` | `ethernet` capability |
| `wlanpi.wlan0` | `wlan0` | `198.51.100.7` | none | `ok`; `off` with value `no IP` | `wifi` | `wlan0` | A wlan adapter exists |
| `wlanpi.wifi` | `Wi-Fi` | `HomeNet -52` | `-52` | Section 3.3 | `wifi` | wlan-link | Section 3.3 |
| `wlanpi.internet` | `Internet` | `up` / `down` | `NET` | `ok` / `error`; `unknown` when null | `globe` | `reachable` | Always |
| `wlanpi.bluetooth` | `BT` | `on` / `off` | `BT` | `ok` / `off` | `bluetooth` | `bluetooth_on` | `bluetooth` capability |
| `wlanpi.battery` | `Batt` | `85% charging` | `85%` | `ok` above 20, `warn` 11 to 20, `error` 10 and below; charging is never worse than `warn` | `battery`, `bolt` while charging | `battery` | `battery.present` |
| `wlanpi.cpu_temp` | `Temp` | `48C` | `48C` | `ok` below 70, `warn` 70 to 79, `error` 80 and above | `thermometer` | `cpu_temp` | Always |
| `wlanpi.cpu` | `CPU` | `3%` | `3%` | `info` | `cpu` | `cpu` | Always |
| `wlanpi.profiler` | `Profiler` | `running` / `stopped` | `PRF` | `info` / `off` | `radio` | `profiler_running` | Route `/profiler/status` advertised |
| `wlanpi.hotspot_clients` | `Clients` | `3` | `3` | `info`; `off` at 0 | `users` | `hotspot_clients` | Hotspot mode |

Thresholds are constants in one module, covered by tests, and not user-configurable in this version. Temperature and CPU are whole numbers, so a tile does not publish on every tenth of a degree.

### 3.3 The Wi-Fi depth gap

`device.summary` knows only `wlan0`'s IP address. SSID, signal and channel are missing from every surface.

- **Endpoint**: `GET /network/interfaces/{iface}/wlan-link`. It is in `wlanpi-core/docs/openapi.json` and returns `WlanLink`: `connected`, `ssid`, `bssid`, `freq_mhz`, `signal_dbm`, `links[]` for MLO, bitrates. No core change is needed. `docs/core-audit.md` does not list it, because the PoC never called it; add a row when this lands.
- **Interface**: `wlan0` in this version. Skipped in hotspot mode (the radio is an AP; `wlanpi.hotspot_clients` covers it) and when no wlan adapter exists. Per-adapter tiles (`wlanpi.wifi.<iface>`) are a later, additive step.
- **Gating**: the tile is in the catalog only when the route table advertises the path, the same mechanism as `requires_core`.
- **Cadence**: every second fast tick (10 s) while the last answer was `connected: true`; otherwise on the context tick (30 s). A mutating job's context invalidation forces an early read, as it does for the rest of the context.
- **Mapping**: value `<ssid> <signal>` with the SSID cut to fit 20 characters, `short` is the signal (`-52`), `detail` is `ch <n>, <freq> MHz, <bssid>`. The channel is computed from `freq_mhz` in the service. For MLO with no single active link, `freq_mhz` is null and `detail` lists the links' frequencies.
- **Status**: `ok` at -67 dBm or better, `warn` from -68 to -75, `error` below -75, `off` with value `not connected` when `connected` is false, `unknown` when `signal_dbm` is null.
- **Jitter**: a new signal value is published only when it differs from the published one by 3 dB or more, or when the status band changes. Otherwise RSSI noise would repaint every surface every 10 s.
- **Not added to `device.summary`.** The summary is frozen; new readings are complications. That is the point of having one surface for small status tiles.

## 4. Per-surface display customization

### 4.1 Options compared

| | Client-local | Server-side, per principal | Server-side, per surface profile |
| --- | --- | --- | --- |
| Thin home (rendered by the service) | Cannot work: the service draws the screen | Works | Works |
| Panel configurable from the WebUI | No | Only if the WebUI can address the panel's principal (root) | Yes |
| Panel storage | Needs a writable file owned by the fpms package | In the service | In the service |
| Survives a browser change or cleared storage | No | Yes | Yes |
| Two people on one device want different layouts | Yes | Yes | No |
| Mirror panes show what the panel shows | No | No | Yes |

**Recommendation: server-side, keyed by surface profile.** The first row decides it: `view` frames with `screen: home` are composed in `views.home_blocks()`, so the service must know the selection for thin surfaces. Per-principal config is deferred. The device is single-user in practice, and the plan already says roles are not a security boundary. If it is wanted later, a `principal` key can be added beside `surface` without changing anything defined here.

Client-local storage stays the right place for pure presentation state, such as whether the WebUI strip is collapsed.

### 4.2 Profiles

A profile is a named ordered list. The name matches `^[a-z][a-z0-9_]*(\.[a-z0-9_]+)?$`. Four are predefined; a new surface may use its own name and gets the `default` list until someone configures it.

| Profile | Used by | Drawn as |
| --- | --- | --- |
| `panel.strip` | Front panel status line; the home view of the default session | Compact: icon or `short`, coloured by status |
| `panel` | Front panel home rows; the home view of the default session | One row each, `label value` |
| `tui` | TUI footer; home view of an independent thin session from the TUI | `label: value` segments on one line |
| `webui` | WebUI device status bar and the Home page status card | Chips; all live tiles on Home |

**The default session always uses `panel` and `panel.strip`**, whichever surface is attached, because it is the panel's screen and a mirror must show the same thing. An independent thin session uses the profile named in `hello.surface`. Without that field the service maps `hello.client.name` (`wlanpi-fpms` to `panel`, `wlanpi-menu-tui` to `tui`, `wlanpi-webui` to `webui`) and falls back to `default`.

### 4.3 Config model

A profile's `show` list has up to 24 entries. Each entry is an exact `app_id` or a family pattern ending in `.*`, the same wildcard rule as topic subscriptions.

```json
{"v":1, "type":"complications.configure", "id":"c9",
 "surface":"panel", "show":["wlanpi.mode","wlanpi.ip","wlanpi.wifi","plugin.acme.uplink","local.*"]}
```

```json
{"v":1, "type":"complications.configure", "id":"c10", "surface":"panel", "reset":true}
```

The reply is an `event` on `complications.config` carrying the request id and `snapshot: true`, sent whether or not the caller is subscribed. Every subscriber gets the same change as a normal event, so a panel repaints when the WebUI edits its layout.

`complications.config` data:

```json
{
  "surfaces": {
    "panel.strip": {"show": ["wlanpi.battery","wlanpi.bluetooth","wlanpi.internet"], "source": "default"},
    "panel":       {"show": ["wlanpi.mode","wlanpi.ip","wlanpi.wifi","plugin.acme.uplink","local.*"], "source": "user"},
    "tui":         {"show": ["wlanpi.mode","wlanpi.eth0","wlanpi.ip","wlanpi.cpu_temp","plugin.*","local.*"], "source": "default"},
    "webui":       {"show": ["wlanpi.mode","wlanpi.ip","wlanpi.internet","wlanpi.wifi","wlanpi.battery","wlanpi.cpu_temp","wlanpi.profiler","plugin.*","local.*"], "source": "default"}
  }
}
```

Who may configure: role `operator` or higher, on either socket. `viewer` and `provider` get `FORBIDDEN`. An entry that is neither a valid id nor a valid pattern is `BAD_REQUEST`. An id that is not in the catalog is **accepted**, so a layout survives its plugin being reinstalled.

**Resolution** (normative; every surface and the service's home view use it):

1. Walk `show` in order.
2. An exact id yields that live record, if it is live.
3. A pattern yields every live record it matches that no exact entry anywhere in the list names and that no earlier entry has yielded, sorted by `app_id`.
4. Drop duplicates, keeping the first.
5. Cut the result to the surface's capacity (4.5).

A selected tile that is not live takes no slot; the rest close up. `protocol/samples/complications/resolve-vectors.json` holds input and expected-output cases that the service and all three surfaces test against, so four implementations cannot drift.

**Storage and precedence**, highest first:

| Layer | Path | Written by |
| --- | --- | --- |
| User | `/var/lib/wlanpi-menud/complications.json` | `complications.configure`. Needs `StateDirectory=wlanpi-menud` in the unit, which it does not have today. Written atomically (temp file, rename) |
| Site | `/etc/wlanpi-menud/complications.json` | An administrator or an image build |
| Shipped | `/usr/share/wlanpi-menud/complications.d/defaults.json` | The package |

Layers apply per profile: a user list for `panel` does not hide the site list for `tui`. `reset` deletes the user layer for that profile. `source` in the topic says which layer won (`user`, `site` or `default`). A user file that fails to parse is logged, renamed aside and ignored; the service never refuses to start over it.

### 4.4 Defaults

| Profile | Shipped `show` | Reasoning |
| --- | --- | --- |
| `panel.strip` | `wlanpi.battery`, `wlanpi.bluetooth`, `wlanpi.internet` | Exactly legacy FPMS's status line after the clock (`⚡85% BT ☀`) |
| `panel` | `wlanpi.mode`, `wlanpi.ip`, `wlanpi.wifi`, `wlanpi.cpu_temp`, `wlanpi.hotspot_clients`, `plugin.*`, `local.*` | Today's service home rows (Mode, IP, Temp) plus the Wi-Fi reading. Hostname stays the view title. `Model` leaves the home rows; it is on the About screen |
| `tui` | `wlanpi.mode`, `wlanpi.eth0`, `wlanpi.ip`, `wlanpi.cpu_temp`, `plugin.*`, `local.*` | Today's footer keys in today's order. Hostname stays fixed chrome |
| `webui` | `wlanpi.mode`, `wlanpi.ip`, `wlanpi.internet`, `wlanpi.wifi`, `wlanpi.battery`, `wlanpi.cpu_temp`, `wlanpi.profiler`, `plugin.*`, `local.*` | Today's primary device fields |
| `default` | `wlanpi.mode`, `wlanpi.ip`, `plugin.*`, `local.*` | Unknown surfaces |

Every default ends with `plugin.*` and `local.*`. That keeps today's behaviour, where a newly published complication shows up with no configuration, and lets a user remove it by saving a list without the pattern. The clock is not a complication: the panel draws it from its own time, as it does today.

### 4.5 Capacity

Capacity is computed by the surface from its own geometry, and by the service for thin home views from `hello.capabilities.screen`. It is never stored in the config.

| Surface | Rule | 128x128 panel | 128x64 panel | Typical |
| --- | --- | --- | --- | --- |
| Panel strip | `floor((cols - 5) / 5)`: clock takes 5 columns, each slot 4 plus a separator | 3 | 3 | |
| Panel rows | Body rows minus the strip row minus alert rows shown (at most 2) | 7, or 5 with two alerts | 2, or 1 with an alert | |
| TUI footer | In order until the line is full; at most 8 | | | 5 or 6 at 80 columns |
| WebUI status bar | In order until the bar is full, then a `+N` chip opening the rest; at most 12 before the chip | | | 6 to 8 on a laptop |
| WebUI Home card | All resolved tiles | | | Unlimited |

Body rows come from the panel renderer: `(height - 15 - 12) // 12`, which is 8 at 128 px and 3 at 64 px. Every shipping model has a 128x128 display (plan, "How the screen is drawn today"); the 128x64 column is the floor the layout must survive, not a product.

### 4.6 Who wins

| Conflict | Winner |
| --- | --- |
| The list is longer than the surface's capacity | The surface. It shows the prefix that fits and drops the tail |
| Order | The user's list, always. A surface never reorders by status or source |
| Alerts against tile rows | Alerts. Up to two alert rows take space from the end of the tile rows |
| A tile is `error` but not selected | The config. It stays hidden. A provider that needs attention regardless of layout should not rely on a tile; a provider alert channel is future work (section 8) |
| User, site and shipped lists for one profile | User, then site, then shipped |
| Two surfaces save the same profile at once | Last write wins; both get the resulting `complications.config` event |
| A surface kind cannot draw a form (no `short` for a strip, no glyph for an icon) | The surface falls back: first 4 characters of `value`, no icon |

### 4.7 Sketches

Panel home, 128x128, at the defaults, with one provider tile and one alert:

```
+---------------------+
| wlanpi-a1b2         |  title: hostname (unchanged)
+---------------------+
|12:41  85%  BT  NET  |  panel.strip: clock + 3 slots, coloured by status
|Mode Classic         |  panel rows, in configured order
|IP 192.0.2.10        |
|Wi-Fi HomeNet -52    |
|Temp 48C             |
|Uplink 412 Mb/s      |  plugin.acme.uplink via "plugin.*"
|                     |
|! NO WI-FI ADAPTER   |  alerts take rows from the bottom
+---------------------+
| v Menu              |  hints (unchanged)
+---------------------+
```

The same list on a 128x64 screen: the prefix that fits, nothing reordered.

```
+---------------------+
| wlanpi-a1b2         |
|12:41  85%  BT  NET  |
|Mode Classic         |
|IP 192.0.2.10        |
+---------------------+
```

Customize on a thin surface (panel or TUI). It is a built-in menu node, `system.home_tiles`, run by a Python handler in the service that uses only the existing `select` prompt, so no new prompt kind or view block is needed. `min_role` is `operator`.

```
 Menu > System > Home tiles         pick a tile                pick what to do

+---------------------+     +---------------------+     +---------------------+
| Home tiles          |     | Rows                |     | Wi-Fi               |
+---------------------+     +---------------------+     +---------------------+
|>Rows             5 >|     | 1 Mode              |     |>Hide                |
| Status strip     3 >| --> | 2 IP                | --> | Move up             |
| Reset to default    |     |>3 Wi-Fi             |     | Move down           |
|                     |     | 4 Temp              |     | Back                |
|                     |     | 5 Clients (idle)    |     |                     |
|                     |     | - - fits above - -  |     |                     |
|                     |     | 6 All plugin tiles  |     |                     |
|                     |     |   CPU      (hidden) |     |                     |
|                     |     |   Profiler (hidden) |     |                     |
+---------------------+     +---------------------+     +---------------------+
| < Back     o Open   |     | < Done     o Edit   |     | < Back     o OK     |
+---------------------+     +---------------------+     +---------------------+
```

- Shown tiles come first, numbered in order; hidden catalog entries follow, marked `(hidden)`. A selected tile that is not reporting is marked `(idle)`.
- The `fits above` rule marks the capacity of the surface being edited, so the user sees what a long list will lose.
- Each action writes the profile at once and returns to the list with the cursor on the same tile. There is no separate save step and nothing to lose by walking away.
- Family patterns appear as one row (`All plugin tiles`, `All local tiles`) and can be hidden or moved like a tile.
- The node edits `panel` and `panel.strip` from the default session, and the session's own profile from an independent session.

Customize in the WebUI (rich), a page under System:

```
 Home tiles                                   Editing: [ Panel rows      v ]

 Shown (drag to reorder)                      Available
 +----------------------------------------+   +-------------------------------+
 | =  Mode          Classic               |   | +  CPU            3%          |
 | =  IP            192.0.2.10            |   | +  Profiler       stopped     |
 | =  Wi-Fi         HomeNet -52           |   | +  eth0           192.0.2.10  |
 | =  Temp          48C                   |   | +  Acme uplink    not         |
 | =  Clients       not reporting         |   |                   reporting   |
 | - - - - fits on the panel above - - - -|   +-------------------------------+
 | =  All plugin tiles                    |
 | =  All local tiles                     |    [ Reset to default ]
 +----------------------------------------+
```

- The profile picker lists all four profiles, so the panel is configured from a browser.
- Reordering works without a pointer: each row has Move up, Move down and Remove buttons, and drag is an extra.
- Each change sends `complications.configure`; the page redraws from the `complications.config` event, not from its own optimistic state.

## 5. Protocol delta

All of it is additive under the `PROTOCOL.md` versioning rule: `v` stays 1. The existing `complications` topic, `topic_complications` and `topic_device_summary` are not touched.

### 5.1 `frames.schema.json`

| Change | Kind | Detail |
| --- | --- | --- |
| `$defs.complication_id` | New | `^(wlanpi|plugin|local)(\.[a-z][a-z0-9_]*){1,3}$`, max 64 |
| `$defs.complication_status` | New | enum `ok`, `info`, `warn`, `error`, `off`, `unknown` |
| `$defs.complication_icon` | New | enum of the names in 2.7 |
| `$defs.surface_profile` | New | `^[a-z][a-z0-9_]*(\.[a-z0-9_]+)?$`, max 32 |
| `$defs.complication_publish` | New C to S frame | 2.2. `status` here also accepts `warning` |
| `$defs.complication_remove` | New C to S frame | `{app_id}` |
| `$defs.complications_configure` | New C to S frame | `{surface, show}` or `{surface, reset: true}`, exactly one; `show` max 24 items of id or id-pattern |
| `$defs.complication_ack` | New S to C frame | `{app_id, state: "live"|"removed", ttl_sec?}` |
| `$defs.topic_complications_live` | New | Array of `{app_id, label, value, status, short?, detail?, icon?, node?}`, `additionalProperties: false` |
| `$defs.topic_complications_catalog` | New | Array of `{app_id, title, label, source, icon?, description?, node?}` |
| `$defs.topic_complications_config` | New | `{surfaces: {<profile>: {show, source}}}` |
| `event.allOf` | Three more `if/then` | Bind the three new topic names to their defs |
| Root `oneOf` | Four more refs | The three C to S frames and `complication_ack` |
| `hello.surface` | New optional field | `surface_profile`. Omitted: derived from `client.name` |
| `hello.ephemeral` | New optional boolean | `true`: the independent session is dropped at disconnect with no resume grace. Without it, a script publishing every few seconds would leave one detached session per run for `resume_grace_sec` (120 s) and reach `sessions_per_socket` (16). Refused `BAD_REQUEST` with `session: "default"` |
| `welcome.features` | New optional array of strings | The service sends `["complications.2"]`. A client uses the new frames and topics only when it sees this |
| `block_kv` item `level` | New optional field | `ok`, `info`, `warn`, `error`. Lets the home rows carry a status in one row each. A renderer that ignores it shows the row uncoloured |

Why old peers are unaffected:

- **Old client, new service.** It never sends the new frames or subscribes to the new topics. `complications` and `device.summary` reach it byte-for-byte as before. `welcome.features` and `kv` `level` are optional fields a receiver ignores. The home `view` it receives is still `kv` and `status` blocks.
- **New client, old service.** `welcome.features` is absent, so the client stays on `complications` and `device.summary`. This check matters: an old service answers a `subscribe` to an unknown topic with a `data: null` snapshot and never an error, so a client cannot detect support by subscribing.
- **Old provider.** There is none; no front door existed.
- **Family subscriptions.** A client subscribed to `complications.*` would start receiving the three new topics. No shipped client does (all subscribe to `complications`), and the `.*` rule already means "and future members".
- **Vendored schema copies** (`wlanpi-webui/protocol/`, `wlanpi-fpms/tests/menud_client/fixtures/`) are stricter than the wire rule because of `additionalProperties: false`. They are re-synced in the same change as each surface, as was done for `welcome.session` and the `eth0`/`wlan0` fields.

One visible behaviour change that is not a protocol change: the thin home view's content moves from the fixed Host/Mode/IP/Model/Temp list to the `panel` profile. The shipped default is close to today's rows, and the frame shape is the same.

### 5.2 Sample additions

```
protocol/samples/frames/c2s/
  complication.publish.json
  complication.publish.minimal.json          app_id, label, value only
  complication.publish.hold-connection.json
  complication.remove.json
  complications.configure.json
  complications.configure.reset.json
  hello.provider-ephemeral.json
  hello.thin-surface.json
protocol/samples/frames/s2c/
  complication.ack.live.json
  complication.ack.removed.json
  event.complications-live.json
  event.complications-catalog.json
  event.complications-config.json
  welcome.features.json
  view.home-tiles.json                       kv items with level
protocol/samples/invalid/frames/
  complication-publish-bad-namespace.json    app_id "acme"
  complication-publish-uppercase-id.json
  complication-publish-label-9-chars.json
  complication-publish-value-21-chars.json
  complication-publish-short-5-chars.json
  complication-publish-bad-status.json       "critical"
  complication-publish-ttl-too-low.json      4
  complication-publish-ttl-too-high.json     3601
  complication-publish-unknown-key.json
  complication-remove-no-app-id.json
  complications-configure-show-and-reset.json
  complications-configure-neither.json
  complications-configure-25-entries.json
  complications-configure-bad-pattern.json   "plugin.*.x"
  event-complications-live-bad-status.json
  event-complications-live-drift.json        legacy keys in the new topic
  event-complications-config-bad-source.json
protocol/samples/complications/
  declaration.json                           valid plugins.d file
  resolve-vectors.json                       show + live -> expected order
protocol/samples/invalid/complications/
  declaration-wrong-namespace.json           id outside plugin.<dir>
  declaration-no-publisher.json
  declaration-title-25-chars.json
protocol/complications.schema.json           declaration file schema
```

`event-complications-drift.json` (existing, legacy topic) stays and keeps failing.

### 5.3 `PROTOCOL.md` sections

| Section | Change |
| --- | --- |
| Transport | `local.sock` row: providers publish complications here; `proxy.sock` row: never |
| Handshake | `hello.surface`, `hello.ephemeral`, `welcome.features` |
| Events, epoch and reconnect (line 159) | Seven typed topics; the legacy `complications` topic is provider records only and frozen |
| **Complications** (new) | Namespaces and ownership; publish, replace and remove; TTL bounds and expiry; `hold`; quotas and the fan-out limit; status vocabulary with the surface mapping table; text budgets; catalog against live; refusal codes |
| **Complication display** (new) | Profiles; which profile a session uses; `complications.configure`; the resolution algorithm; capacity belongs to the surface; storage layers and precedence |
| Versioning | Add this delta to "Additive changes so far" |
| Manifest notes | `plugins.d/<name>/complications.json`: validation, whole-file rejection, role grant |
| Not yet in the protocol (line 281) | Remove the complications sentence. Provider menu subtrees and provider `invoke` remain there |

## 6. Test plan

### 6.1 Service unit tests (`tests/test_complications.py`, plus additions to `test_poller.py` and `test_auth.py`)

| Area | Cases |
| --- | --- |
| Authorization | `provider` and `admin` on `local.sock` accepted; `operator` and `viewer` refused; any role on `proxy.sock` refused; `wlanpi.*` refused for root |
| Namespaces | `local.<own user>` accepted; `local.<other user>` refused, accepted for root; `plugin.x.*` accepted only for the declared user; undeclared `plugin.*` is `BAD_REQUEST`; username mapping |
| Declarations | Valid file loads and grants `provider`; id outside its directory's namespace rejects the whole file and names the field; a bad file does not stop menu manifests; reload on trigger; removal drops catalog entries and the grant |
| Replace | Second publish replaces; omitted optional fields reset; declared label is the default and a published label overrides it |
| TTL | Default 30; bounds; removal within 1 s of the deadline with the poller stopped (fake clock); a no-change publish extends without an event; `hold: connection` removes on close |
| Quotas | Ninth record for a uid refused; thirty-third overall refused; a replace is not counted |
| Sanitizing | Control characters, ANSI and bidi overrides stripped; limits applied after stripping; `warning` stored as `warn` |
| Topics | `complications` holds provider records only, in the old shape; `complications.live` holds both; a `local.*` id enters and leaves the catalog; two changes inside the window give one event |
| Built-ins | One table-driven test per row of 3.2, including absent and `unknown`; all removed when core is unavailable; `device.summary` output identical to before for the same fake-core input |
| Wi-Fi | Connected, disconnected, null signal, MLO with null frequency; 10 s and 30 s cadence; skipped in hotspot mode and without an adapter; absent when the route is not advertised; 3 dB hysteresis; channel from frequency for 2.4, 5 and 6 GHz |
| Config | Layer precedence per profile; `reset`; unknown ids kept; `viewer` refused; corrupt user file renamed aside; atomic write; event to all subscribers and a reply to the caller |
| Resolution | Every case in `resolve-vectors.json` |
| Home view | Default session uses `panel` whatever attaches; independent session uses `hello.surface`; capacity from `capabilities.screen` at 128 and 64 px; alerts take rows from the end |
| Sessions | An `ephemeral` session is gone at disconnect; 100 sequential one-shot publishes never reach the socket session limit |
| Customize node | Hide, show, move up, move down and reset through `select` prompts; `viewer` cannot see the node |

### 6.2 Contract tests (`protocol/tests/test_schemas.py`)

- Every new valid sample passes; every new invalid sample fails for the intended reason.
- All **existing** samples still pass and all existing invalid samples still fail. This is the additivity check.
- `validate_outbound` is on for the whole service suite, so each new frame and topic the service emits is checked against the schema.
- Generated Python models and TypeScript types are regenerated and committed.

### 6.3 Per-surface acceptance

| Surface | Must show |
| --- | --- |
| TUI | Against an old service (no `features`): footer unchanged. Against a new one: footer follows the `tui` profile in order, cut to width; status styles from 2.6; tile text escaped (a value of `[red]x` is drawn literally); an edit made elsewhere repaints without reconnecting; `Home tiles` usable by keyboard |
| Panel | Strip of clock plus 3 slots; rows in order; alert rows displace the last tiles; `warn` and `error` distinguishable on a mono render; a glyph the font lacks becomes `?`; an edit from the WebUI appears within one fan-out window; `Home tiles` works with five buttons. Golden-image tests at 128x128 and 128x64 |
| WebUI | Status bar follows `webui`; `+N` overflow; customize page edits all four profiles; reorder by keyboard; "not reporting" marking; text inserted as text nodes, never HTML; old-service fallback to today's rendering. Playwright, real Chromium |
| All | `resolve-vectors.json` passes in the surface's own language |

### 6.4 Container end to end (`verify-container/run_verification.py`)

1. Install a sample provider package: `plugins.d/demo/complications.json` and a `demo` user.
2. As `demo`: `wlanpi-complication publish plugin.demo.ping --value "12 ms" --status ok --ttl 5` exits 0.
3. As `demo`: publishing `plugin.other.x` and `wlanpi.mode` exits 3.
4. A thin client, a rich client and a legacy-style client (subscribes only to `complications`) are connected. The first two see the tile in `complications.live`; the legacy client sees it in `complications` in the old four-key shape and sees no built-ins there.
5. After 6 s without a publish the tile is gone from all three.
6. Through the browser, set the `panel` profile to put `plugin.demo.ping` first. The thin client's next home `view` has it as the first row, and `/var/lib/wlanpi-menud/complications.json` contains the list.
7. Restart the service. The config survives, the live tile does not, and a republish brings it back.
8. Fake core returns a `wlan-link` answer: `wlanpi.wifi` appears with the expected value and status band.

## 7. Rollout

Service first; each surface follows on its own schedule and falls back cleanly until then.

| Phase | Repo | Lands | Exit |
| --- | --- | --- | --- |
| C0 Contract | `wlanpi-menud` | Schema delta, `complications.schema.json`, samples, resolve vectors, `PROTOCOL.md` | Contract tests green; existing samples untouched |
| C1 Service core | `wlanpi-menud` | Store with timer expiry, the three frames, namespaces, quotas, new topics, `welcome.features`, `hello.ephemeral` | 6.1 green; legacy topic output unchanged |
| C2 Built-ins | `wlanpi-menud` | `builtin_complications()`, Wi-Fi link poll | `device.summary` unchanged for the same input; resource budget re-measured (one more core call per 10 s) |
| C3 Display config | `wlanpi-menud` | Profiles, storage layers, `complications.configure`, home view from the `panel` profile, `system.home_tiles`, `StateDirectory=` | Thin home at defaults reviewed against today's screen on a device |
| C4 Front door for people | `wlanpi-menud` | `wlanpi-complication`, declaration loader and role grant, example provider, provider docs | Container steps 1 to 5 |
| C5 WebUI | `wlanpi-webui` | Status bar from the `webui` profile, customize page, schema re-sync | 6.3; container steps 6 and 7 |
| C6 TUI | `wlanpi-menu-tui` | Footer from the `tui` profile, status styles | 6.3 |
| C7 Panel | `wlanpi-fpms` | Strip, `kv` `level` colours, fixture re-sync | 6.3 on a real device |

C1 to C4 can ship in one service release. C5 comes before C6 and C7 because the WebUI is where the panel's layout gets edited. Rollback at any phase is downgrading that one package: a newer surface sees no `features` entry and uses the old topics, and a newer service is unaffected by an older surface.

The legacy `complications` topic is not removed in protocol v1.

## 8. Open questions

| # | Question | Proposed answer |
| --- | --- | --- |
| Q1 | The brief names a 128x64 screen; the plan and both renderers say every model is 128x128 | Design for 128x128 and require the 64 px layout to degrade correctly (4.5). Confirm no 128x64 hardware is planned |
| Q2 | May `operator` publish under `local.<user>.*`? | No for now: `provider` and `admin` only. The `wlanpi` user is `admin`, which covers personal scripts. Revisit if operators ask |
| Q3 | Should a provider be able to raise an alert that ignores the display config? | Not in this spec. `alerts` stays service-owned. If needed, add a namespaced provider alert frame later |
| Q4 | Per-principal layouts | Deferred (4.1). Additive when wanted |
| Q5 | Should activating a tile on a thin surface open its `node`? | Deferred. Home's key map is `down: Menu` only, and tile focus is a navigation change that deserves its own review |
| Q6 | Health thresholds (battery, temperature, RSSI) | Constants in 3.2 and 3.3. Needs a pass by someone who knows the hardware's thermal limits per model |

### Decision log (Nov 13, 2026 — user rulings)

- **Q1 RESOLVED:** 128x128 is the target; 64 px is a degradation floor, not hardware. (The brief's "128x64" was a coordinator error.)
- **Q2 RESOLVED:** `provider` and `admin` roles only for `local.<user>.*`; revisit if operators ask.
- **Q6 RESOLVED:** ship the draft constants from §3.2/3.3; tune during the device trial.
- **Q3/Q4/Q5:** accepted as proposed (out of scope / deferred).
- **STATUS: SPEC APPROVED, IMPLEMENTATION ON HOLD** by user decision Nov 13 — no code written; the §7 rollout plan stands whenever the go comes.
