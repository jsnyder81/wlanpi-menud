# wlanpi-menud protocol v1

`frames.schema.json` defines the shape of every frame. This file covers the
rules a schema cannot express. `menu.schema.json` defines manifests. Where this
file and the schemas disagree, fix one of them; never let a surface paper over
the gap.

## Transport

| Socket | Who | Auth |
| --- | --- | --- |
| `/run/wlanpi-menud/local.sock` | TUI, panel, local providers, WebUI backend (logout). Providers publish complications here, and only here | Kernel `SO_PEERCRED` uid and gid. No `auth` frame. |
| `/run/wlanpi-menud/proxy/proxy.sock` (mode 0660, nginx group, in a setgid `www-data` directory) | nginx `/ui/ws`: WebUI browsers, later the mobile app. Never publishes complications, whatever the role (it may configure them) | `X-WebUI-User` and `X-WebUI-Session` from nginx `auth_request`, or a core JWT in the first frame. Peer credentials ignored. `Origin` must match the device host (else close 1008). |

The service has no TCP listener. Each WebSocket message is one JSON frame,
`{"v":1, "type":..., "id":..., ...}`.

## Ids

- The client picks `id` for each request (unique per connection). Every reply
  to that request carries the same `id`; a request can have more than one
  reply (for example `subscribe` gets one `event` snapshot per topic).
- Frames the server sends unprompted carry a server id starting with `s:`.
- `job_id` starts with `j` and `prompt_id` with `p`; both are server-issued.

## Handshake

```
local.sock:                      proxy.sock, JWT client:
C hello                          C auth {token}        (within 10 s)
S welcome                        C hello
S menu                           S welcome
S view        (thin only)        S menu
```

1. On `proxy.sock` without WebUI headers the first frame must be `auth`
   within 10 s. A token in the URL (`?token=`) is refused. Failure, timeout
   or a revoked token: `error AUTH_FAILED` (fatal) then **close 4401**. The
   client must not retry with the same token.
2. A JWT is checked through core's introspection endpoint (it accepts the
   `wlanpi-menud` service token) and the result is cached for at most 60 s or
   until `exp`, then checked again. A token that fails a re-check closes the
   session with 4401 at that point.
3. `hello` lists protocol versions. The server picks the highest common one
   and reports it in `welcome.version`. No overlap: `error VERSION` (fatal)
   then **close 4426**. Any frame other than `auth`/`hello` before the
   handshake completes is a `VERSION` error.
4. `hello.session` picks the session: `default` attaches to the persistent
   default session, `new` opens an independent one (see Sessions). Omitted,
   it is `default` for thin clients and `new` for rich ones. A rich hello
   with `session: "default"` is `BAD_REQUEST` (fatal, close 4400): rich
   clients navigate locally, and a surface that mirrors the shared screen is
   a thin attach.
5. `welcome` carries `session_id`, the session kind (`session`: `default` or
   `new`), `epoch`, the principal, roles, the negotiated version and whether
   the session `resumed`.
6. Additive (complications): `hello.surface` names the display profile an
   independent thin session's home view uses (omitted: derived from
   `client.name`, see Complication display). `hello.ephemeral: true` drops the
   independent session at disconnect with no resume grace, so a script that
   connects every few seconds never accumulates detached sessions; with
   `session: "default"` it is `BAD_REQUEST` (fatal, close 4400).
   `welcome.features` lists optional features; wlanpi-menud sends
   `["complications.2"]`. A client uses the complication frames and topics
   only when it sees that name: an older service answers a `subscribe` to an
   unknown topic with a `data: null` snapshot, never an error, so subscribing
   cannot detect support.

## Thin and rich mode

| | Thin (panel, TUI) | Rich (WebUI, mobile) |
| --- | --- | --- |
| Cursor | Held by the server, one per session (the default session's is shared) | Held by the client (server cursor updated on `nav`) |
| Client sends | `input` (up, down, left, right, center, key1 to key3), `invoke` for shortcuts, `nav` | `invoke` on a node id, `nav` to navigate / fetch landing views |
| Server sends | `view` after every input and when shown data changes | `view` in reply to `nav` |
| Parameters | One `prompt` per parameter | Client renders a form from `menu` node params; missing ones are still prompted |
| Paging | up/down on a result page; the server fetches pages on demand | `page` with the cursor |

Both modes share the same `menu`, jobs, prompts and results. `input` from a
rich session is `BAD_REQUEST`. Two independent sessions never share a
cursor; connections attached to the default session always do.

## Direct navigation (`nav`)

The additive `nav` frame (`{"v": 1, "type": "nav", "id": "<id>", "node": "<node_id>"}`)
allows clients (thin or rich) to move the session cursor directly to a node:
- **Branch node**: The cursor moves to the branch with all ancestors auto-expanded in its stack, pushing the resulting menu `view` frame.
- **View-action node**: The action is invoked, the session cursor transitions to the result screen with ancestors auto-expanded, and the resulting `view` frame is pushed.
- **Errors**: `NOT_FOUND` if the node does not exist in the menu; `FORBIDDEN` if the node is hidden for the session or if the connection is detached.

## Sessions

| | Default session | Independent session |
| --- | --- | --- |
| Joined with | `hello.session: "default"` (thin only; the default for thin) | `hello.session: "new"` (the default for rich) |
| Lifetime | Exists from service start, never ends | Ends when its last connection detaches and the resume grace runs out |
| Connections | Any number attach (panel, TUIs, WebUI mirror) | One (a resume replaces the old connection) |
| `session_id` | The same for every connection in an epoch | Its own |
| Cursor and view | Live in the service, shared by every attached connection | Its own |
| Prompts | Sent to every attached connection | Private to the session |

- **Fan-out.** Input from any attached connection moves the one cursor, and
  the resulting `view` goes to every attached connection: the sender gets it
  as the reply (its own `id`), the others an unprompted copy (`s:` id). Job
  progress, results and prompts reach all of them the same way.
- **Last write wins.** Concurrent input is applied in arrival order; nobody
  holds a lock. Each default-session `view` carries `driven_by`: the
  principal and surface (`hello.client.name`) of the connection whose input,
  `invoke`, `answer` or `cancel` produced it, plus `self` (true for that
  connection), so a surface can show "driven from the WebUI".
- **Visibility is per connection.** Each connection gets the `menu` its own
  roles allow and its `view` is rendered through that tree; input is
  navigated in the sender's tree and `invoke` is checked against it.
- **Prompts.** A prompt on the default session goes to every attached
  connection and the first `answer` wins; a later one gets `NOT_FOUND` and
  every surface sees the prompt resolved (the shared view moves on). Thin
  surfaces answer with input on the shared prompt screen.
- **Confirmations stay with the invoking principal**, not the connection:
  any connection of that principal may answer or cancel it. Every other
  attached connection sees it (`prompt.invoked_by`, and its `view` has
  `answerable: false` and no hints) but its `answer`, `input` and `invoke`
  are refused `FORBIDDEN` while the confirmation is on screen. If the last
  connection of the invoking principal detaches, the confirmation is
  cancelled so the shared screen is not stranded.
- **Persistence.** The default session's cursor, view and jobs survive
  connections coming and going; a job keeps running when its invoker
  detaches. A connection that attaches (or reattaches) gets the current
  `view` right after `welcome`.
- **Independent sessions** behave as before: their own cursor, prompts bound
  to the session and its principal, and teardown (jobs detached, prompts
  cancelled) once the last connection has been gone for the resume grace.

## Jobs

- `invoke` replies with `job` `queued` (same `id`). Later `job` frames for that
  job go to every attached session: `progress`, then exactly one of `result`,
  `error` or `cancelled`.
- Shareable reads already running, or finished within `fresh_sec`, attach the
  caller to the existing job (`shared: true`, `cached_at`). Nothing else is
  shared, deduplicated or cached. Results that contain a secret are never
  shareable.
- `cancel` detaches the caller only. The job stops when no session is attached
  and the action is cancellable.
- A core 409 becomes `CONFLICT` with core's message (in `job.error` for a
  running job, or an `error` frame when the request itself is refused).
- `result` holds the first page of blocks and a `cursor`; `page` fetches the
  next. A `null` or missing cursor marks the last page.

## Prompts

- Sent for a node's parameters, its `confirm` text, or mid-job (a scan that
  returns `needsSelection` becomes a `select` prompt listing core's
  `candidates`, then re-runs with the chosen `{iface, namespace}`).
- A prompt is bound to the session that invoked the job. On an independent
  session it is also bound to that session's principal; on the default
  session any attached connection may answer it (first answer wins) except a
  `confirm`, which only the invoking principal may answer or cancel (see
  Sessions). An `answer` from anyone else is `FORBIDDEN`. An answer carries
  exactly one of `value`, `values`, `confirmed` or `cancel`.
- An unanswered prompt expires after `expires_sec`; the job then ends
  `cancelled`.

## Events, epoch and reconnect

- The service picks a new random `epoch` each time it starts.
- `event.seq` starts at 1 per topic per epoch and increments by one for each
  change the service publishes. The service publishes only on change.
- The seven built-in topics have typed `data` in `frames.schema.json`:
  `device.summary` (`topic_device_summary`), `alerts` (`topic_alerts`), `jobs`
  (`topic_jobs`), `complications` (`topic_complications`: `app_id`,
  `label`, `value`, `status`), and, additive, `complications.live`,
  `complications.catalog` and `complications.config` (see Complications).
  Unknown keys fail validation, so a client or a stub that drifts from the
  service is caught by the schema. Plugin topics carry any JSON.
- `complications` (legacy) and `device.summary` are **frozen**: their shape
  and timing do not change. `complications` carries provider records only, in
  its four-key shape; built-in readings appear only in `complications.live`,
  so a surface written before it never shows a value twice. A client
  subscribed to the family `complications.*` receives the new topics too.
- On reconnect the client sends `hello.resume` with the epoch, its session id
  and the last `seq` it saw per topic.
  - Same epoch and every topic's `seq` equals the server's latest: the session
    continues, `welcome.resumed: true`, its jobs stay attached and no snapshot
    is sent.
  - Anything else (new epoch, a gap, unknown session): `resumed: false` and a
    full snapshot: `menu`, one `event` with `snapshot: true` per subscribed
    topic, and a `job` frame for each of the session's live jobs.
  - There is no replay buffer.
  - Default session: the cursor and view always continue, since they live
    in the service. What resumes is the connection's topic stream: the
    topics in `resume.seq` are subscribed again, `resumed: true` when every
    listed `seq` is the server's latest, otherwise those topics get
    snapshots. The current `view` is sent either way.
- Each client has a bounded outbound queue (`welcome.limits.queue_frames`).
  When it fills, that client's queued topic updates are dropped and it gets a
  `snapshot: true` event per topic once it drains. Replies, prompts and job
  terminal states are never dropped. A slow client never holds up others.
- A client that sees a `seq` gap without `snapshot: true` treats that topic as
  stale until the next snapshot.

## Complications

A complication is one small status tile: a label, a value and a status
(`Batt 85%`, `Uplink OK`). Design: `docs/complications-spec.md`.

### Namespaces and ownership

`app_id` matches `^(wlanpi|plugin|local)(\.[a-z][a-z0-9_]*){1,3}$`, at most
64 characters.

| Namespace | Who may publish | Ownership |
| --- | --- | --- |
| `wlanpi.*` | Only the service (built-ins). Refused for every connection, root included | Reserved in code |
| `plugin.<name>.*` | The unix user named in that plugin's declaration, and root | `/usr/share/wlanpi-menud/plugins.d/<name>/complications.json`, root-owned, installed by a package; `<name>` is the directory name. The exact id must be declared |
| `local.<user>.*` | The principal whose unix username is `<user>`; root may publish any `local.*` | `SO_PEERCRED`; no declaration. Usernames are lowercased and other characters become `_` (a leading non-letter gets a `u` prefix) |

Who may publish: a connection on `local.sock` whose principal has role
`provider` or `admin`. `operator` and `viewer` may not; `proxy.sock` never
may.

### Publish, replace, remove

```
C complication.publish {app_id, value, label?, short?, status?, detail?, icon?, ttl_sec?, hold?}
S complication.ack     {app_id, state: "live", ttl_sec}
C complication.remove  {app_id}
S complication.ack     {app_id, state: "removed"}
```

- **Replace by `app_id`.** A publish replaces the whole record; omitted
  optional fields return to their defaults (a declared id's `label` and
  `icon` are its defaults). `label` is required for `local.*` ids.
- **TTL.** `ttl_sec` is 5 to 3600, default 30; out of range fails the schema
  (not clamped). Every publish restarts it. There is no "never expires". The
  service removes a record within 1 s of its deadline (a timer for the nearest
  deadline, not the poll tick). The catalog entry of a declared id remains.
- **`hold: "connection"`** also removes the record when the publishing
  connection closes, so a crashed daemon's tile goes at once.
- **No-change publish.** A publish whose visible fields equal the live record
  only restarts the TTL and causes no `event`: the cheap heartbeat.
- **Fan-out.** The `complications`, `complications.live` and
  `complications.catalog` topics publish at most once per
  `complications_fanout_sec` (0.5 s); changes inside the window collapse into
  one event. Every publish is still acknowledged.
- **Quotas.** 8 live records per publishing uid, 32 provider records in total
  (`complication_quota_per_uid`, `complication_quota_total`). A replace is not
  counted.
- **Restart.** Records are not persisted. A new `epoch` starts with built-ins
  only; providers publish again when they reconnect.
- **Sanitizing.** Control characters, ANSI escapes and bidirectional
  formatting characters are stripped from every text field. Surfaces still
  treat tile text as untrusted and escape it.
- **No secrets.** Every subscriber, `viewer` sessions included, sees
  complications. Never publish passphrases or keys.

Refusals use `error` with existing codes:

| Case | Code |
| --- | --- |
| Sent on `proxy.sock`; role not `provider`/`admin`; `app_id` outside the caller's namespace; `wlanpi.*`; quota reached | `FORBIDDEN` |
| Fails the schema; `plugin.*` id with no declaration; `label` missing on a `local.*` id | `BAD_REQUEST` |
| `complication.remove` for an id that is not live | `NOT_FOUND` |

`/usr/bin/wlanpi-complication` wraps these frames for scripts: one
invocation is one ephemeral connection (`publish`, `remove`, `list`,
`catalog`, `show <profile> [--set ids | --reset]`). Exit codes: 0 done, 2
usage, 3 refused (message on stderr), 4 service unreachable.

### Status vocabulary

Closed set: `ok`, `info`, `warn`, `error`, `off`, `unknown`.
`complication.publish` also accepts `warning`, stored as `warn`. A surface
draws an unrecognized status as `unknown`. Colour is never the only signal.

| Status | Meaning | Panel | TUI | WebUI | `status`/`kv` level |
| --- | --- | --- | --- | --- | --- |
| `ok` | Working as intended | Green square | green | success tag | `ok` |
| `info` | Neutral fact | Text colour, no marker | default | no tag | `info` |
| `warn` | Degraded, look soon | Amber square | yellow | warning tag | `warn` |
| `error` | Broken, act now | Red square | bold red | danger tag | `error` |
| `off` | Disabled or idle | Dimmed, hollow square | dim | muted tag | `info` |
| `unknown` | No reading yet | Dimmed, value `--` | dim | muted, value `—` | `info` |

### Text budgets

From the 128 px panel (21 columns): `label` 8, `value` 20 (12 fit beside an
8-character label), `short` 4, `detail` 64 (never on the panel), catalog
`title` 24, `description` 120. A surface that runs out of width cuts the
value with `…`, never the label. `icon` is one of `battery`, `bolt`,
`bluetooth`, `globe`, `wifi`, `ethernet`, `thermometer`, `cpu`, `users`,
`radio`, `shield`, `dot`.

### Catalog and live set

| Topic | Answers | Entry |
| --- | --- | --- |
| `complications.catalog` | What could be shown: every built-in id this device can produce, every declared plugin id, every `local.*` id live now | `{app_id, title, label, source: builtin\|plugin\|local, icon?, description?, node?}` |
| `complications.live` | What has a value now: built-ins and providers | `{app_id, label, value, status, short?, detail?, icon?, node?}` |

Both are sorted by `app_id`; order is a display concern. Built-ins
(`wlanpi.mode`, `.ip`, `.eth0`, `.wlan0`, `.wifi`, `.internet`, `.bluetooth`,
`.battery`, `.cpu_temp`, `.cpu`, `.profiler`, `.hotspot_clients`) have no TTL,
are derived from the same poll as `device.summary` (plus
`GET /network/interfaces/wlan0/wlan-link` for `wlanpi.wifi`), are absent when
the device cannot produce them and `unknown` when core did not answer. While
core is unavailable every `wlanpi.*` record leaves the live set.

## Complication display

### Profiles

A profile is a named ordered list (`^[a-z][a-z0-9_]*(\.[a-z0-9_]+)?$`, max
32): `panel.strip` (front panel status line), `panel` (front panel home rows),
`tui` (TUI footer), `webui` (status bar, Home card), and `default` for any
other name until it is configured.

The default session always uses `panel` (and `panel.strip`), whichever
surface is attached. An independent thin session uses `hello.surface`, else
`hello.client.name` mapped (`wlanpi-fpms` to `panel`, `wlanpi-menu-tui` to
`tui`, `wlanpi-webui` to `webui`), else `default`.

### `complications.configure`

```
C complications.configure {surface, show: [id or family, ... up to 24]}
C complications.configure {surface, reset: true}
S event {topic: "complications.config", id: <request id>, snapshot: true, data}
```

Exactly one of `show` and `reset`. Role `operator` or higher, on either
socket; `viewer` and `provider` get `FORBIDDEN`. An entry that is neither an
id nor a family (`plugin.*`, `plugin.acme.*`, `local.*`) is `BAD_REQUEST`;
an id not in the catalog is accepted, so a layout survives a reinstall. The
reply is sent whether or not the caller is subscribed; every other
subscriber gets the change as a normal event. Two saves of one profile: last
write wins.

`complications.config` data: `{"surfaces": {<profile>: {"show": [...],
"source": "user"|"site"|"default"}}}`.

### Resolution (normative)

1. Walk `show` in order.
2. An exact id yields that live record, if it is live.
3. A family yields every live record it matches that no exact entry anywhere
   in the list names and that no earlier entry has yielded, sorted by
   `app_id`.
4. Drop duplicates, keeping the first.
5. Cut to the surface's capacity.

A selected tile that is not live takes no slot. Test vectors:
`samples/complications/resolve-vectors.json`.

### Capacity belongs to the surface

The config decides which tiles and in what order; the surface decides how
many fit. Overflow is cut from the end, never reordered; alerts take rows
before tiles. For thin home views the service computes it from
`hello.capabilities.screen`: body rows are `(height_px - 27) // 12` (or
`rows - 2`, else 8), minus one strip row on `panel`, minus the alert rows
shown (at most 2): 7 at 128 px (5 with two alerts), 2 at 64 px. The thin
home `view` is a `kv` block, one row per tile with `level`, then one
`status` block per alert; its title is the hostname.

### Storage

| Layer | Path | Written by |
| --- | --- | --- |
| User | `/var/lib/wlanpi-menud/complications.json` (`StateDirectory=`) | `complications.configure`, atomically (temp file, rename) |
| Site | `/etc/wlanpi-menud/complications.json` | An administrator or an image build |
| Shipped | `/usr/share/wlanpi-menud/complications.d/defaults.json` | The package |

All three are `{"schema": 1, "surfaces": {<profile>: {"show": [...]}}}`.
Layers apply per profile; `reset` deletes that profile's user layer. A user
file that fails to parse is logged, renamed aside (`.bad-<time>`) and
ignored; the service never refuses to start over it.

## View blocks

`text`, `kv`, `table`, `list`, `qr`, `status`, `progress`, `confirm`. Every
block carries `text`, a plain-text rendering. A surface renders the types it
lists in `hello.capabilities.blocks` and uses `text` for the rest. `text`
blocks are mandatory for every surface.

## Error codes

| Code | Meaning | Fatal |
| --- | --- | --- |
| `AUTH_FAILED` | Missing, bad, expired or revoked credentials | Yes, close 4401 |
| `FORBIDDEN` | Role or visibility does not allow the node; prompt belongs to another session; a default-session confirmation another principal invoked | No |
| `CONFLICT` | Core returned 409; message is core's | No |
| `VERSION` | No common protocol version; frame before handshake | Yes, close 4426 |
| `BAD_REQUEST` | Frame fails the schema or is not valid in this mode | No (4400 before handshake) |
| `NOT_FOUND` | Unknown node, job, prompt or cursor | No |
| `CORE_UNAVAILABLE` | Core unreachable or 5xx, including `AUTH_CLOCK_NOT_SET` after the service's own token re-mint failed | No |
| `TIMEOUT` | Action exceeded `timeout_sec` | No |
| `INTERNAL` | Anything else | No |

The first four are the plan's contract. The rest are additions so every
failure has a typed code.

## Close codes

| Code | Meaning |
| --- | --- |
| 1000 | Normal |
| 1001 | Service stopping; reconnect with backoff (expect a new epoch) |
| 1008 | Origin check failed (proxy.sock) |
| 1013 | Session limit reached on that socket; retry later |
| 4400 | Malformed frame before the handshake, or a rich hello asking for the default session |
| 4401 | Authentication failed or revoked (WebUI logout closes every session with that WebUI session id) |
| 4426 | No common protocol version |

## Versioning

- `v` stays `1` for additive changes: a new optional field, a new enum value
  in a field the receiver already tolerates, a new topic. A receiver ignores
  fields it does not use, and an old client that omits a new field gets the
  behaviour it had before.
- A change that removes or renames a field, makes an optional field
  required, or changes the meaning of an existing value is a new protocol
  version, negotiated through `hello.protocol`.
- Additive changes so far:
  - Sessions: `hello.session`, `welcome.session`, `view.driven_by`,
    `view.answerable`, `prompt.invoked_by`. A client that omits
    `hello.session` keeps working: thin attaches to the default session,
    rich gets an independent session, as rich clients always did. Thin
    clients that need a private cursor (the pre-sessions behaviour) send
    `session: "new"`.
  - Device summary: optional `eth0` and `wlan0` per-interface IP fields
    in `device.summary` payload (additive v1; old clients ignore them).
  - Complications (`welcome.features: ["complications.2"]`): frames
    `complication.publish`, `complication.remove`,
    `complications.configure` and `complication.ack`; topics
    `complications.live`, `complications.catalog` and
    `complications.config`; optional `hello.surface`, `hello.ephemeral`,
    `welcome.features` and `kv` item `level`. `complications` and
    `device.summary` are unchanged. An old client never sends the new frames
    or subscribes to the new topics; a new client against an old service sees
    no `features` entry and stays on `complications` and `device.summary`.
    The thin home view's rows now come from the `panel` profile (same block
    types).

## Contract synchronization (WebUI)

`wlanpi-webui` vendors `frames.schema.json` at `protocol/frames.schema.json` to generate
its TypeScript types without cross-repo CI checkout dependencies. Whenever `frames.schema.json`
changes in this repository, re-run WebUI's vendor-sync script to update the vendored schema
and regenerated types:
```bash
cd /path/to/wlanpi-webui && ./scripts/vendor-sync.sh /path/to/wlanpi-menud
```

## Manifest notes (loader behaviour)

- Roots load in order: `menu.d/` built-ins, `plugins.d/<name>/`, then
  `/etc/wlanpi-menud/menu.d/` overrides. Each file is accepted or rejected
  whole; a rejected file registers nothing and the rest still load.
- Overrides may add nodes or replace a built-in node by reusing its id. They
  may not use `plugin.*` ids, replace plugin nodes, or turn a branch that has
  children into an action (the override is rejected, the built-in stays).
- Plugins: `plugin` must equal the directory name, every id is under
  `plugin.<name>`, ids colliding with anything registered are refused, and
  `handler` actions are refused (third parties use providers).
- `plugins.d/<name>/complications.json` is a complication declaration, not a
  menu manifest: validated by `complications.schema.json`, every `id` and
  `node` under `plugin.<name>.`, rejected as a whole (errors name the file and
  field) without affecting the plugin's menu manifests. A loaded declaration
  grants the `provider` role on `local.sock` to its `publisher.user`
  (`provider_uids` keeps working). SIGHUP reloads declarations with the
  manifests.
- `handler_action` takes `mutates` (default false), `shareable` and
  `fresh_sec`, with the same rules as core bindings: a mutating handler is
  never shared or cached, and `fresh_sec` needs `shareable`. For core
  bindings the HTTP method decides `mutates`.
- Besides `requires_core`, a node is hidden when its own core binding's
  route is not in core's OpenAPI document.
- Path placeholders must be valid parameter names bound to `path`.

## WebUI logout and revocation

- `revoke`: C to S frame on `local.sock`, restricted to local `admin` principals
  on `local.sock`. Carries `{"v":1, "type":"revoke", "id":..., "webui_session":...}`.
  Tells wlanpi-menud to close every connection and independent session bound to
  that WebUI session id (`Service.revoke_webui_session`, close code 4401).
  Any local admin UID on `local.sock` (including the default `wlanpi` user) may
  close WebUI menu connections for a given `webui_session` id; this is DoS-only
  with no privilege gain on a single-user device.
  The default session is never ended by revoke.
  No reply on success; non-admin callers (such as the operator group) or calls
  over `proxy.sock` are refused with an `error` frame with code `FORBIDDEN`.

## Not yet in the protocol

- Provider menu subtrees and provider `invoke` (Phase 6).
