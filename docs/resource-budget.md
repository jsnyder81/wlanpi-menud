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
| systemd `MemoryHigh` / `MemoryMax` | 80M / 112M | n/a (user process); the TUI drops cached result pages beyond 4 | |
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

- **One polling loop** (until core has an event stream): homepage and status every 5 s, profiler status every 2 s only while the home page or a profiler view is open on some session, hotspot clients only in hotspot mode (core returns 409 otherwise). Each tick is a few local HTTPS calls; publish only on change. That is roughly 1 to 3 core calls per second at worst, so idle service CPU stays well under 1 %.
- **No per-session timers.** Thin views are re-rendered only on input or when a subscribed value changes.
- **TLS to core**: keep one pooled httpx client with keep-alive, so the handshake cost is paid once rather than per call.
- **TUI**: Textual renders only on change; no polling (surfaces never poll).

## Measurement plan (Phase 1 exit)

Run on every supported model, starting with the smallest:

1. Boot, start `wlanpi-menud`, wait 10 minutes idle. Record `memory.current` and `memory.peak` from `/sys/fs/cgroup/system.slice/wlanpi-menud.service/`, PSS from `smem -P wlanpi-menud`, and CPU with `pidstat -p <pid> 60 10`.
2. Two TUI sessions over SSH, navigating independently for 5 minutes (scripted key presses). Record TUI PSS and service CPU.
3. Run a scan (paged through), a speedtest and a profiler start/stop. Record peaks.
4. Connect 16 thin clients that read slowly (a test client with a 100 ms read delay). Check the service stays under `MemoryHigh` and that the other sessions' latency stays within budget.
5. Record the same figures for today's FPMS on the same board as the reference.

Fail the exit if any steady figure goes over its ceiling. CI should run the same scripted scenario against a mocked core on aarch64 and fail on a regression of more than 10 %.
