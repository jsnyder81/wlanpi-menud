"""Idle-CPU benchmark: a real wlanpi-menud process against an HTTPS fake core, two idle clients.

usage: .venv/bin/python tools/idle_bench/bench.py <checkout> <seconds> <label>   (PROF=1 adds cProfile)

<checkout> is the tree whose wlanpi_menud is run (cwd-first import), so a git
worktree of another commit can be measured with the same venv. Prints one JSON
line: service CPU % of one core over the window (utime+stime from /proc), core
calls/s, new TLS connections, frames/min per client and RSS. See
docs/resource-budget.md for the method and the recorded numbers.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from websockets.asyncio.client import unix_connect

HERE = Path(__file__).resolve().parent
repo, secs, label = Path(sys.argv[1]).resolve(), float(sys.argv[2]), sys.argv[3]
work = Path(tempfile.mkdtemp(prefix=f"menud-bench-{label}-"))
print("work dir:", work, file=sys.stderr)
cert, key = work / "cert.pem", work / "key.pem"
if not cert.exists():
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", cert,
                    "-days", "2", "-subj", "/CN=wlanpi-test"], check=True, capture_output=True)
tok = work / "tok.sh"
tok.write_text('#!/bin/sh\necho \'{"access_token":"eyJhbGciOiJIUzI1NiJ9.eyJleHAiOjk5OTk5OTk5OTl9.c2ln"}\'\n')
tok.chmod(0o755)
port = 31415 + (os.getpid() % 1000)
sd = Path(tempfile.mkdtemp(prefix="mb-"))  # AF_UNIX path limit
cfg = work / "config.toml"
cfg.write_text(f'''builtin_dir = "{repo}/share/menu.d"
plugins_dir = "{work}/plugins.d"
override_dir = "{work}/etc-menu.d"
local_sock = "{sd}/local.sock"
proxy_sock = "{sd}/proxy/proxy.sock"
local_sock_group = ""
proxy_sock_group = ""
core_base_url = "https://127.0.0.1:{port}"
core_cert = "{cert}"
token_command = ["{tok}"]
[local_uids]
{os.getuid()} = ["admin"]
''')
countfile = work / "count"
sys.path.insert(0, str(repo))
from wlanpi_menud.core.generated import ROUTES

openapi = work / "openapi.json"
openapi.write_text(json.dumps({"openapi": "3.1.0", "paths": {
    "/api/v1" + p: {m.lower(): {}} for m, p in ROUTES}}))
core = subprocess.Popen([sys.executable, HERE / "fakecore_https.py", str(port), cert, key, countfile, openapi])
time.sleep(0.5)
log = open(work / "menud.log", "w")  # noqa: SIM115 - lives as long as the child
PROF = ["-m", "cProfile", "-o", str(work / "prof.out")] if os.environ.get("PROF") else []
menud = subprocess.Popen([repo / ".venv/bin/python", *PROF, "-m", "wlanpi_menud", "--config", cfg], stderr=log, cwd=repo)

def cpu(pid):
    f = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return (int(f[11]) + int(f[12])) / os.sysconf("SC_CLK_TCK")

async def client(mode, frames):
    for _ in range(100):
        if (sd / "local.sock").exists():
            break
        await asyncio.sleep(0.1)
    ws = await unix_connect(str(sd / "local.sock"), uri="ws://wlanpi.local/ui/ws")
    await ws.send(json.dumps({"v": 1, "type": "hello", "id": "c:1", "mode": mode, "protocol": [1],
                              "client": {"name": "bench"},
                              "capabilities": {"blocks": ["text", "kv", "list"], "screen": {"rows": 32, "cols": 100}}}))
    await ws.send(json.dumps({"v": 1, "type": "subscribe", "id": "c:2",
                              "topics": ["device.summary", "complications", "alerts", "jobs"]}))
    async for m in ws:
        frames[mode] = frames.get(mode, 0) + 1

async def main():
    frames = {}
    tasks = [asyncio.create_task(client("thin", frames)), asyncio.create_task(client("rich", frames))]
    await asyncio.sleep(12)  # startup + first ticks
    f0, c0, n0, t0 = dict(frames), cpu(menud.pid), tuple(map(int, countfile.read_text().split())), time.monotonic()
    await asyncio.sleep(secs)
    f1, c1, n1, t1 = dict(frames), cpu(menud.pid), tuple(map(int, countfile.read_text().split())), time.monotonic()
    for t in tasks:
        t.cancel()
    rss = [l for l in Path(f"/proc/{menud.pid}/status").read_text().splitlines() if l.startswith(("VmRSS", "VmHWM"))]
    el = t1 - t0
    res = {"label": label, "seconds": round(el, 1), "cpu_pct": round(100 * (c1 - c0) / el, 3),
           "cpu_sec": round(c1 - c0, 2), "core_calls_per_s": round((n1[0] - n0[0]) / el, 3), "new_tls_conns": n1[1] - n0[1],
           "frames_per_min": {k: round(60 * (f1.get(k, 0) - f0.get(k, 0)) / el, 1) for k in ("thin", "rich")},
           "rss": [l.split(":")[1].strip() for l in rss]}
    print(json.dumps(res), flush=True)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    menud.terminate()
    t0 = time.monotonic()
    while menud.poll() is None and time.monotonic() - t0 < 15:
        await asyncio.sleep(0.1)
    print("sigterm->exit sec:", round(time.monotonic() - t0, 2), "rc", menud.poll(), flush=True)

try:
    asyncio.run(asyncio.wait_for(main(), secs + 60))
finally:
    if menud.poll() is None:
        menud.kill()
    core.kill()
    log.close()
    print("menud log lines:", len((work / "menud.log").read_text().splitlines()), flush=True)
    os._exit(0)
