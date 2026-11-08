"""Epoch, resume, snapshots, bounded queues, publish-on-change."""

from __future__ import annotations

import asyncio
import json

from wlanpi_menud.outqueue import OutQueue


async def subscribed(h, mode="rich"):
    c = await h.connect()
    w = await c.hello(mode)
    await c.send("subscribe", topics=["device.summary", "alerts"])
    seqs = {}
    for _ in range(2):
        ev = await c.expect(type="event")
        assert ev["snapshot"] is True
        seqs[ev["topic"]] = ev["seq"]
    return c, w, seqs


async def test_resume_same_epoch_unbroken_sequence(harness, fake):
    c, w, seqs = await subscribed(harness)
    await c.close()
    await asyncio.sleep(0.05)
    c2 = await harness.connect()
    w2 = await c2.hello("rich", resume={"epoch": w["epoch"], "session_id": w["session_id"], "seq": seqs,
                                        "menu_revision": harness.service.menu_revision})
    assert w2["resumed"] is True and w2["session_id"] == w["session_id"] and w2["epoch"] == w["epoch"]
    await asyncio.sleep(0.1)
    assert [f["type"] for f in c2.frames] == ["welcome"], "no snapshot after a clean resume"
    # Still subscribed: a change arrives as a normal update.
    fake.cpu_temp = "70.0C"
    await harness.poller.poll_once()
    ev = await c2.expect(lambda f: f["topic"] == "device.summary", type="event")
    assert ev["seq"] == seqs["device.summary"] + 1 and not ev.get("snapshot")


async def test_resume_with_gap_gets_snapshot_and_live_jobs(harness, fake):
    fake.speedtest_delay = 1
    c, w, seqs = await subscribed(harness)
    mid = await c.send("invoke", node="utils.speedtest.run")
    job = await c.expect(lambda f: f["id"] == mid, type="job")
    await c.close()
    fake.cpu_temp = "71.0C"  # a change the client misses
    await harness.poller.poll_once()
    c2 = await harness.connect()
    w2 = await c2.hello("rich", resume={"epoch": w["epoch"], "session_id": w["session_id"], "seq": seqs})
    assert w2["resumed"] is False and w2["session_id"] == w["session_id"]
    await c2.expect(type="menu")
    snaps = [await c2.expect(type="event") for _ in range(2)]
    assert all(s["snapshot"] for s in snaps)
    assert {s["topic"]: s["seq"] for s in snaps}["device.summary"] == seqs["device.summary"] + 1
    live = await c2.expect(type="job")
    assert live["job_id"] == job["job_id"] and live["state"] in ("queued", "progress")
    done = await c2.expect(lambda f: f["state"] == "result", type="job", timeout=3)
    assert done["job_id"] == job["job_id"]


async def test_new_epoch_after_restart_means_full_snapshot(harnesses, fake):
    h1 = await harnesses()
    c, w, seqs = await subscribed(h1)
    await h1.stop()
    h2 = await harnesses()
    assert h2.service.epoch != w["epoch"]
    c2 = await h2.connect()
    w2 = await c2.hello("rich", resume={"epoch": w["epoch"], "session_id": w["session_id"], "seq": seqs})
    assert w2["resumed"] is False and w2["session_id"] != w["session_id"] and w2["epoch"] == h2.service.epoch
    await c2.expect(type="menu")


async def test_service_stop_closes_1001(harnesses):
    h = await harnesses()
    c = await h.connect()
    await c.hello()
    await h.service.aclose()
    assert await c.wait_closed() == 1001


async def test_resume_needs_the_same_principal(harnesses):
    import os
    h = await harnesses()
    c, w, seqs = await subscribed(h)
    await c.close()
    # Same epoch and session id but a different principal must not take the session over.
    h.service.local_policy.uids[os.getuid()] = ("viewer",)
    c2 = await h.connect()
    w2 = await c2.hello("rich", resume={"epoch": w["epoch"], "session_id": w["session_id"], "seq": seqs})
    assert w2["resumed"] is True  # same uid -> same principal key; roles refreshed
    assert w2["roles"] == ["viewer"]
    c3 = await h.connect("proxy", headers={"X-WebUI-User": "mallory", "X-WebUI-Session": "x"})
    w3 = await c3.hello("rich", resume={"epoch": w["epoch"], "session_id": w["session_id"], "seq": seqs})
    assert w3["resumed"] is False and w3["session_id"] != w["session_id"]


async def test_publish_only_on_change(harness, fake):
    hub = harness.service.hub
    before = dict(hub.seq)
    await harness.poller.poll_once()
    await harness.poller.poll_once()
    assert hub.seq["device.summary"] == before["device.summary"]
    assert hub.seq["alerts"] == before["alerts"]
    fake.cpu_temp = "80.0C"
    await harness.poller.poll_once()
    assert hub.seq["device.summary"] == before["device.summary"] + 1


async def test_core_down_shows_unavailable_home(harness, fake):
    t = await harness.connect()
    await t.hello()
    v = await t.expect(type="view")
    assert v["screen"] == "home"
    fake.down = True
    await harness.poller.poll_once()
    v = await t.expect(lambda f: f["screen"] == "unavailable", type="view")
    assert v["blocks"][0]["level"] == "error"


async def test_complications_ttl(harness):
    c = await harness.connect()
    await c.hello("rich")
    await c.send("subscribe", topics=["complications"])
    await c.expect(type="event")
    harness.service.publish_complication("gps", "GPS", "8 sats", "ok", ttl_seconds=30)
    ev = await c.expect(lambda f: f["topic"] == "complications", type="event")
    assert ev["data"] == [{"app_id": "gps", "label": "GPS", "value": "8 sats", "status": "ok"}]
    harness.service.complications["gps"]["expires_at"] = 0  # TTL elapsed
    await harness.poller.poll_once()
    ev = await c.expect(lambda f: f["topic"] == "complications", type="event")
    assert ev["data"] == [] and ev["seq"] == 3


# ---------------------------------------------------------------------------- bounded queues

def _event(topic, seq):
    return {"v": 1, "type": "event", "id": f"s:{seq}", "topic": topic, "seq": seq, "data": {"n": seq}}


async def test_outqueue_drops_topic_updates_then_resnapshots():
    snaps = []

    def resnapshot(topics):
        snaps.append(set(topics))
        return [dict(_event(t, 99), snapshot=True) for t in sorted(topics)]

    q = OutQueue(max_frames=4, max_bytes=1 << 20, resnapshot=resnapshot)
    for i in range(1, 4):
        assert q.put_event(_event("device.summary", i), "device.summary")
    q.put({"v": 1, "type": "job", "id": "c1", "job_id": "jabcd", "node": "a", "state": "queued"})
    assert not q.put_event(_event("device.summary", 4), "device.summary"), "full: topic update dropped"
    assert not q.put_event(_event("device.summary", 5), "device.summary"), "stale topic stays dropped"
    # Essential frames are never dropped, even over the bound.
    for i in range(6):
        q.put({"v": 1, "type": "error", "id": f"e{i}", "code": "INTERNAL", "message": "x"})
    drained = []
    while (d := q.get_nowait()) is not None:
        drained.append(json.loads(d))
    types = [f["type"] for f in drained]
    assert types.count("error") == 6 and types.count("job") == 1
    assert drained[-1] == dict(_event("device.summary", 99), snapshot=True), "snapshot once drained"
    assert snaps == [{"device.summary"}]


async def test_outqueue_coalesces_views():
    q = OutQueue(8, 1 << 20)
    for i in range(5):
        q.put({"v": 1, "type": "view", "id": f"s:{i}", "screen": "home", "blocks": []}, coalesce="view")
    assert len(q) == 1 and json.loads(q.get_nowait())["id"] == "s:4"


async def test_slow_client_does_not_hold_up_others(harnesses, fake):
    h = await harnesses(queue_frames=4)
    ws, ss, _ = await subscribed(h)
    fast = await h.connect()
    await fast.hello("rich")
    await fast.send("subscribe", topics=["device.summary"])
    await fast.expect(type="event")
    slow_session = h.service.sessions[ss["session_id"]]
    # Simulate a stalled socket: stop draining the slow client's queue.
    slow_session.queue.get_nowait = lambda: None  # type: ignore[method-assign]

    async def never():
        await asyncio.Event().wait()
    slow_session.queue.wait = never  # type: ignore[method-assign]
    for i in range(10):
        fake.cpu_temp = f"{50 + i}.0C"
        await h.poller.poll_once()
    got = [f for f in fast.frames if f["type"] == "event" and not f.get("snapshot")]
    await asyncio.sleep(0.1)
    got = [f for f in fast.frames if f["type"] == "event" and not f.get("snapshot")]
    assert len(got) == 10, "the fast client saw every update"
    q = slow_session.queue
    assert len(q) <= 4 and "device.summary" in q.stale and q.dropped > 0
