# Resource budget: wlanpi-menud and wlanpi-menu-tui

Phase 0 deliverable: a RAM and CPU ceiling for the service plus the TUI on the smallest supported board. It is checked at Phase 1 exit ("usage stays inside the resource budget") and in the risk table ("Memory and CPU on smaller boards").

## Smallest supported board

Core lists the models `M4`, `M4+`, `R4`, `R5`, `Pro` and `Go` (`transmit_policy.py`, `constants.py`). FPMS still carries the `pinctrl-bcm2835` GPIO label next to `bcm2711` (Pi 4 / CM4) and `rp1` (Pi 5), so a BCM2837-class board may still be in use.

**Assumption to confirm with the hardware owners: the smallest board has 512 MiB of RAM and four Cortex-A53 cores.** That is the conservative case. If every supported model has at least 1 GiB, the ceilings below still apply; there is just more headroom.

On a 512 MiB board, wlanpi-core (gunicorn), nginx, FPMS, the profiler, kismet and the kernel already share the RAM. The menu stack is an addition while FPMS keeps running (Phases 1 and 2), so it has to be small in absolute terms, not as a share of what is free.

## Baseline measurements

These are import-time and running RSS figures from a dev host: **aarch64**, CPython 3.13.14, fastapi 0.162.2, uvicorn 0.54.0, httpx 0.28.1, pydantic 2.14.3, textual 8.2.8. A Pi with the same interpreter and wheels should land close to these, but the Phase 1 exit numbers must come from the boards.

| Process shape | RSS |
| --- | --- |
| Bare CPython 3.13 | 15 MiB |
| `websockets` + `httpx` | 32 MiB |
| + `pydantic` | 37 MiB |
| + `jsonschema` | 39 MiB |
| PoC stack: `fastapi` + `uvicorn` + `httpx` + `pydantic` + `websockets` | 51 MiB |
| PoC stack + `jsonschema` | 54 MiB |
| `textual` imported | 27 MiB |
| Textual app running (header, 60-item list, 200-row table, footer, 80x24, after 30 key presses) | 46 MiB |

Two findings:

- FastAPI and uvicorn add about 14 MiB, and the service needs no HTTP routes: it speaks WebSocket on two Unix sockets and has no TCP listener. A plain `websockets` server (asyncio, `unix_serve`) would start at about 39 MiB with pydantic and jsonschema. Recommendation: build on `websockets` directly unless Phase 1 finds a concrete need for FastAPI.
- `jsonschema` costs 2 to 13 MiB depending on what else is loaded, and it is only needed at startup and on manifest reload. That is acceptable; if the budget gets tight, validate frames with the generated pydantic models instead, and use jsonschema only for manifests.

## Ceilings

| | wlanpi-menud | wlanpi-menu-tui (per SSH session) | Service + one TUI |
| --- | --- | --- | --- |
| Steady RSS (idle, menu loaded, 1 to 2 sessions) | **≤ 64 MiB** | **≤ 48 MiB** | **≤ 112 MiB** |
| Peak RSS (16 sessions, a paged 500-network scan, a speedtest) | ≤ 96 MiB | ≤ 64 MiB | ≤ 160 MiB |
| systemd `MemoryHigh` / `MemoryMax` | 80M / 112M (inert where the image disables the memory cgroup, see below) | n/a (user process); the TUI drops cached result pages beyond 4 | |
| Idle CPU (60 s average, nothing on screen changing) | ≤ 1 % of one core | ≤ 0.5 % of one core | ≤ 1.5 % |
| CPU while a job runs | Not capped by quota; `CPUWeight=50` so core and capture work win | Render only | |
| Start to ready | ≤ 5 s (`Type=notify`) | ≤ 3 s to first view | |
| Input to view latency (thin mode, local socket) | ≤ 50 ms p95 at the service | ≤ 100 ms p95 key to screen | |

On a 512 MiB board, 112 MiB steady is about 22 % of RAM. Treat it as the hard line: going over it means removing a dependency, not raising the number.

### What drives memory in the service, and the bounds

| Item | Bound |
| --- | --- |
| Sessions | 16 on `local.sock`, 16 on `proxy.sock`; the 17th gets close 1013 (try again later) |
| Outbound queue per client | 64 frames **and** 1 MiB, whichever comes first; then drop topic updates and resnapshot (plan: bounded queues) |
| Max frame | 256 KiB (`welcome.limits.max_frame_bytes`); bigger results are paged |
| Result page | 16 rows for thin clients, 100 for rich; cursors expire after 5 minutes |
| Shared result cache | Only `shareable` reads, each kept for its `fresh_sec` (max 3600 s), 4 MiB total, LRU |
| Live jobs | 8 concurrent, 1 per node per session; core is the authority on conflicts |
| Manifests | About 75 built-in nodes today; budget for 500 nodes including plugins (well under 1 MiB) |

### What drives CPU, and the bounds

- **One polling loop** (until core has an event stream), in two tiers so an idle tick stays cheap:
  - *Fast tier, every `poll_interval_sec` (5 s)*: `/system/device/stats`, `/system/battery`, `/profiler/status`, plus `/system/hotspot/clients` in hotspot mode only (core returns 409 otherwise). These are the homepage values that move.
  - *Context tier, every `context_interval_sec` (30 s)*: `/system/device/info`, `/system/device/model`, `/bluetooth/status`, `/network/config/status`, `/network/interfaces`. These give identity, mode, Bluetooth power and adapters, which drive menu visibility. A **mutating job finishing** (mode switch, Bluetooth toggle, and so on) re-reads the context at once and wakes the loop, so menu changes made from the menu show up without the 30 s wait. Changes made outside the menu (a USB adapter plugged in, a mode set over SSH) show up within 30 s. A failed stats read also re-reads the context in the same tick, so "core is down" is still detected within one tick.
  - Reachability every 60 s, the OpenAPI route table every 10 minutes (retried after 60 s, not every tick, when core's answer is unusable).
  - That is about 0.8 core calls/s at idle (was 1.3–1.8 with every endpoint on every tick). Topics publish only on change; `device.summary` still changes most ticks, because cpu and temperature move.
- **No per-session timers.** Thin views are re-rendered only on input or when a subscribed value changes.
- **TLS to core**: one pooled httpx client, keep-alive **55 s** (`core_keepalive_sec`). httpx's default keep-alive expiry is 5 s, the same as the poll interval, so before this fix the pool had dropped every connection by the next tick and *every* core call paid a full TLS handshake. That was the main cause of the 8–12 % spikes per tick seen on the M4+. 55 s stays under nginx's `keepalive_timeout` (65 s on Debian), so the client always closes first and never reuses a socket nginx has just closed.
- **Logging**: httpx and httpcore log at WARNING unless `--log-level DEBUG`. At INFO they wrote one journal line per core call, about 4,300 lines an hour, and that cost CPU in journald as well as in the service.
- **TUI**: Textual renders only on change; no polling (surfaces never poll).

### Idle CPU: before and after the poll-loop fix

Method (`tools/idle_bench/bench.py`): `wlanpi-menud` runs as a real process (`python -m wlanpi_menud`, INFO logging) against an **HTTPS** fake core: stdlib `http.server`, HTTP/1.1 keep-alive, a self-signed RSA-2048 cert pinned through `core_cert`, the vendored OpenAPI route table, and M4+-shaped payloads with cpu and temperature changing on every call. Two idle clients stay connected throughout (one thin on the home screen, one rich subscribed to all four topics, as the TUI is). After 12 s of warm-up, service CPU is `utime + stime` from `/proc/<pid>/stat` over a 90 s window. The fake core counts calls and new TLS connections. "Before" is commit 7f34921 run from a git worktree with the same venv. The dev host is aarch64 (12 cores, CPython 3.13.14), so absolute numbers are lower than on a CM4. The ratios and the call and handshake counts carry over.

| 90 s idle window | Before (7f34921) | After |
| --- | --- | --- |
| Service CPU, % of one core | 0.71, 0.68, 0.86 (mean 0.75) | 0.21, 0.40, 0.20, 0.39 (mean 0.30) |
| Core calls per second | 1.34 | 0.78 (3 per 5 s tick + 5 per 30 s + reachability) |
| New TLS connections | 120 (every call) | 0 |
| Journal lines (whole run) | 169 | 22 |
| RSS | 45–47 MiB | 44–45 MiB |

On the M4+ (`wlanpi-9be`, 2026-11-08) the service measured 1.9–2.2 % idle before the fix. Per-call handshakes cost relatively more on a Cortex-A72 than on the dev host, so the drop on the board should be at least as large. **Re-measure on the board** with `pidstat -p $(systemctl show -p MainPID --value wlanpi-menud) 60 1` before closing the Phase 1 CPU line.

## systemd unit vs this budget

The unit (`install/lib/systemd/system/wlanpi-menud.service`) now carries the budget's values:

| Setting | Budget | Unit | Note |
| --- | --- | --- | --- |
| `Type=` | `notify` | `notify`, `NotifyAccess=main` | The service sends `READY=1` itself once both sockets are bound (a datagram to `$NOTIFY_SOCKET`, no libsystemd), and `STOPPING=1` on shutdown. The start-to-ready time includes the first poll (up to 15 s if core is slow). |
| `MemoryHigh=` / `MemoryMax=` | 80M / 112M | 80M / 112M | Was `MemoryMax=96M` with no `MemoryHigh`; 96 MiB is the *peak RSS* ceiling, not the cgroup limit. |
| `CPUWeight=` | 50 | 50 | Was missing (default 100). |

**The memory limits are not enforced on the current WLAN Pi image.** The 26.10 image boots with `cgroup_disable=memory` on the kernel command line (seen on `wlanpi-9be`), so the memory controller is off: `MemoryHigh=` and `MemoryMax=` are accepted but do nothing, and `memory.current` and `memory.peak` do not exist. The limits stay in the unit so that they apply on any image that enables the controller. Until then the RSS ceilings are checked by measurement only (below), and a leak would surface as OOM pressure on the whole board rather than a contained restart. `CPUWeight=` needs only the cpu controller and does apply.

## Measurement plan (Phase 1 exit)
## Measurement plan (Phase 1 exit)

Run on every supported model, starting with the smallest:

1. Boot, start `wlanpi-menud`, wait 10 minutes idle. Record PSS from `smem -P wlanpi-menud`, `VmRSS`/`VmHWM` from `/proc/<pid>/status`, and CPU with `pidstat -p <pid> 60 10`. Where the memory controller is enabled, also record `memory.current` and `memory.peak` from `/sys/fs/cgroup/system.slice/wlanpi-menud.service/`. On images booted with `cgroup_disable=memory` (26.10) those files do not exist; use `VmHWM` as the peak.
2. Two TUI sessions over SSH, navigating independently for 5 minutes (scripted key presses). Record TUI PSS and service CPU.
3. Run a scan (paged through), a speedtest and a profiler start/stop. Record peaks.
4. Connect 16 thin clients that read slowly (a test client with a 100 ms read delay). Check the service stays under `MemoryHigh` (by `VmHWM` where the memory cgroup is off) and that the other sessions' latency stays within budget.
5. Record the same figures for today's FPMS on the same board as the reference.

Fail the exit if any steady figure goes over its ceiling. CI should run the same scripted scenario against a mocked core on aarch64 and fail on a regression of more than 10 %.
