"""The one polling loop: cheap idle ticks, context tiering, publish on change, keep-alive."""

from __future__ import annotations

import asyncio

from test_jobs import final, invoke, rich

from wlanpi_menud.config import Config
from wlanpi_menud.core import CoreClient, TokenSource

CONTEXT = ("GET /system/device/info", "GET /system/device/model", "GET /bluetooth/status",
           "GET /network/config/status", "GET /network/interfaces")
FAST = ("GET /system/device/stats", "GET /system/battery", "GET /profiler/status")


async def test_idle_tick_reads_only_the_fast_tier(harness, fake):
    before = dict(fake.calls)
    for _ in range(3):
        await harness.poller.poll_once()
    for k in FAST:
        assert fake.calls[k] - before.get(k, 0) == 3, k
    for k in CONTEXT:
        assert fake.calls[k] == before.get(k, 0), k  # read once at start, not per tick


async def test_unchanged_tick_publishes_nothing(harness, fake):
    hub = harness.service.hub
    seq = dict(hub.seq)
    await harness.poller.poll_once()
    assert hub.seq == seq
    fake.cpu_temp = "51.0C"
    await harness.poller.poll_once()
    assert hub.seq["device.summary"] == seq["device.summary"] + 1
    assert hub.seq["alerts"] == seq["alerts"] and hub.seq["jobs"] == seq["jobs"]


async def test_context_rereads_after_interval(harnesses, fake):
    h = await harnesses(context_interval_sec=0.0)
    n = fake.calls["GET /system/device/info"]
    await h.poller.poll_once()
    assert fake.calls["GET /system/device/info"] == n + 1


async def test_mutating_job_refreshes_context_now(harness, fake):
    t = await harness.connect()
    await t.hello()
    assert harness.poller._wake.is_set() is False
    await invoke(t, "system.reboot")
    await t.expect(type="prompt")
    await t.input("down")
    await t.input("center")
    await t.expect(lambda f: f["state"] == "result", type="job")
    assert harness.poller._wake.is_set() and harness.poller._context_at == 0.0
    n = fake.calls["GET /system/device/info"]
    await harness.poller.poll_once()
    assert fake.calls["GET /system/device/info"] == n + 1


async def test_read_only_job_does_not_refresh_context(harness, fake):
    a = await rich(harness)
    j = await invoke(a, "apps.scanner.scan")
    await final(a, j["job_id"])
    assert not harness.poller._wake.is_set()


async def test_wake_runs_a_tick_without_waiting_the_interval(harnesses, fake):
    h = await harnesses(poll_interval_sec=3600)
    task = asyncio.create_task(h.poller.run())
    try:
        await asyncio.sleep(0.05)
        n = fake.calls["GET /system/device/stats"]
        h.poller.invalidate_context()
        for _ in range(50):
            if fake.calls["GET /system/device/stats"] > n:
                break
            await asyncio.sleep(0.02)
        assert fake.calls["GET /system/device/stats"] == n + 1
        assert fake.calls["GET /system/device/info"] >= 2
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_core_down_still_detected_between_context_reads(harness, fake):
    fake.down = True
    await harness.poller.poll_once()
    assert harness.service.hub.data["device.summary"] == {"core": "unavailable"}
    fake.down = False
    await harness.poller.poll_once()  # context re-read straight away on recovery
    assert harness.service.hub.data["device.summary"]["hostname"] == "wlanpi-test"


async def test_mode_change_reaches_menu_after_context_interval(harnesses, fake):
    h = await harnesses(context_interval_sec=0.0)
    c = await h.connect()
    await c.hello("rich")
    await c.expect(type="menu")
    fake.mode = "hotspot"
    await h.poller.poll_once()
    m = await c.expect(type="menu")
    assert m["device_mode"] == "hotspot"


def test_pool_keepalive_outlives_poll_interval():
    cfg = Config()
    assert cfg.core_keepalive_sec > cfg.poll_interval_sec * 2
    assert cfg.core_keepalive_sec < 65  # nginx keepalive_timeout on Debian
    client = CoreClient("https://127.0.0.1:31415", TokenSource(("x",)), keepalive=cfg.core_keepalive_sec)
    pool = client._http._transport._pool
    assert pool._keepalive_expiry == cfg.core_keepalive_sec



async def test_unusable_openapi_is_retried_slowly_not_every_tick(harness, fake):
    orig = fake.openapi
    fake.openapi = lambda: {"openapi": "3.1.0", "paths": {}}
    harness.poller._routes_at = 0.0
    try:
        n = fake.calls["GET /openapi.json"]
        for _ in range(3):
            await harness.poller.poll_once()
        assert fake.calls["GET /openapi.json"] == n + 1
    finally:
        fake.openapi = orig
