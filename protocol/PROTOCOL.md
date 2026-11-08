# wlanpi-menud protocol v1

`frames.schema.json` defines the shape of every frame. This file covers the
rules a schema cannot express. `menu.schema.json` defines manifests. Where this
file and the schemas disagree, fix one of them; never let a surface paper over
the gap.

## Transport

| Socket | Who | Auth |
| --- | --- | --- |
| `/run/wlanpi-menud/local.sock` | TUI, panel, local providers, WebUI backend (logout) | Kernel `SO_PEERCRED` uid and gid. No `auth` frame. |
| `/run/wlanpi-menud/proxy.sock` (mode 0660, nginx group) | nginx `/ui/ws`: WebUI browsers, later the mobile app | `X-WebUI-User` and `X-WebUI-Session` from nginx `auth_request`, or a core JWT in the first frame. Peer credentials ignored. `Origin` must match the device host (else close 1008). |

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
4. `welcome` carries `session_id`, `epoch`, the principal, roles, the
   negotiated version and whether the session `resumed`.

## Thin and rich mode

| | Thin (panel, TUI) | Rich (WebUI, mobile) |
| --- | --- | --- |
| Cursor | Held by the server, one per session | Held by the client |
| Client sends | `input` (up, down, left, right, center, key1 to key3), `invoke` for shortcuts | `invoke` on a node id |
| Server sends | `view` after every input and when shown data changes | No `view` frames |
| Parameters | One `prompt` per parameter | Client renders a form from `menu` node params; missing ones are still prompted |
| Paging | up/down on a result page; the server fetches pages on demand | `page` with the cursor |

Both modes share the same `menu`, jobs, prompts and results. `input` from a
rich session is `BAD_REQUEST`. Two thin sessions never share a cursor.

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
- A prompt is bound to the session and principal it was sent to. An `answer`
  from anywhere else is `FORBIDDEN`. An answer carries exactly one of `value`,
  `values`, `confirmed` or `cancel`.
- An unanswered prompt expires after `expires_sec`; the job then ends
  `cancelled`.

## Events, epoch and reconnect

- The service picks a new random `epoch` each time it starts.
- `event.seq` starts at 1 per topic per epoch and increments by one for each
  change the service publishes. The service publishes only on change.
- On reconnect the client sends `hello.resume` with the epoch, its session id
  and the last `seq` it saw per topic.
  - Same epoch and every topic's `seq` equals the server's latest: the session
    continues, `welcome.resumed: true`, its jobs stay attached and no snapshot
    is sent.
  - Anything else (new epoch, a gap, unknown session): `resumed: false` and a
    full snapshot: `menu`, one `event` with `snapshot: true` per subscribed
    topic, and a `job` frame for each of the session's live jobs.
  - There is no replay buffer.
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
| `FORBIDDEN` | Role or visibility does not allow the node; prompt belongs to another session | No |
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
| 4400 | Malformed frame before the handshake |
| 4401 | Authentication failed or revoked (WebUI logout closes every session with that WebUI session id) |
| 4426 | No common protocol version |
