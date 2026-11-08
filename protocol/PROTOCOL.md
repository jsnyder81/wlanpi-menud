# wlanpi-menud protocol v1

`frames.schema.json` defines the shape of every frame. This file covers the
rules a schema cannot express. `menu.schema.json` defines manifests. Where this
file and the schemas disagree, fix one of them; never let a surface paper over
the gap.

## Transport

| Socket | Who | Auth |
| --- | --- | --- |
| `/run/wlanpi-menud/local.sock` | TUI, panel, local providers, WebUI backend (logout) | Kernel `SO_PEERCRED` uid and gid. No `auth` frame. |
| `/run/wlanpi-menud/proxy/proxy.sock` (mode 0660, nginx group, in a setgid `www-data` directory) | nginx `/ui/ws`: WebUI browsers, later the mobile app | `X-WebUI-User` and `X-WebUI-Session` from nginx `auth_request`, or a core JWT in the first frame. Peer credentials ignored. `Origin` must match the device host (else close 1008). |

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

## Thin and rich mode

| | Thin (panel, TUI) | Rich (WebUI, mobile) |
| --- | --- | --- |
| Cursor | Held by the server, one per session (the default session's is shared) | Held by the client |
| Client sends | `input` (up, down, left, right, center, key1 to key3), `invoke` for shortcuts | `invoke` on a node id |
| Server sends | `view` after every input and when shown data changes | No `view` frames |
| Parameters | One `prompt` per parameter | Client renders a form from `menu` node params; missing ones are still prompted |
| Paging | up/down on a result page; the server fetches pages on demand | `page` with the cursor |

Both modes share the same `menu`, jobs, prompts and results. `input` from a
rich session is `BAD_REQUEST`. Two independent sessions never share a
cursor; connections attached to the default session always do.

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
- The four built-in topics have typed `data` in `frames.schema.json`:
  `device.summary` (`topic_device_summary`), `alerts` (`topic_alerts`), `jobs`
  (`topic_jobs`) and `complications` (`topic_complications`: `app_id`,
  `label`, `value`, `status`). Unknown keys fail validation, so a client or a
  stub that drifts from the service is caught by the schema. Plugin topics
  carry any JSON.
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
- `handler_action` takes `mutates` (default false), `shareable` and
  `fresh_sec`, with the same rules as core bindings: a mutating handler is
  never shared or cached, and `fresh_sec` needs `shareable`. For core
  bindings the HTTP method decides `mutates`.
- Besides `requires_core`, a node is hidden when its own core binding's
  route is not in core's OpenAPI document.
- Path placeholders must be valid parameter names bound to `path`.

## Not yet in the protocol

- WebUI logout: the plan has the WebUI backend tell wlanpi-menud over
  `local.sock` to close every session with a WebUI session id. The service
  implements it (`Service.revoke_webui_session`, close 4401), but there is
  no frame for it yet; it needs a `revoke` request reserved for the WebUI
  backend's uid. Decide with the WebUI work (Phase 2).
- Provider registration and `publish` (Phase 6). Complications already
  have TTL handling in the service (`Service.publish_complication`).
