"""Job rules: sharing, caching, confirmations, cancel, conflicts, prompts, paging."""

from __future__ import annotations

import asyncio


async def rich(h, sock="local"):
    c = await h.connect(sock)
    await c.hello("rich")
    await c.expect(type="menu")
    return c


async def invoke(c, node, **kw):
    mid = await c.send("invoke", node=node, **kw)
    first = await c.expect(lambda f: f["id"] == mid and f["type"] in ("job", "error"))
    return first


async def final(c, job_id, timeout=3.0):
    return await c.expect(lambda f: f["job_id"] == job_id and f["state"] in ("result", "error", "cancelled"),
                          type="job", timeout=timeout)


async def test_shareable_read_cached_within_fresh_sec(harness, fake):
    a, b = await rich(harness), await rich(harness)
    qa = await invoke(a, "network.lldp")
    ra = await final(a, qa["job_id"])
    assert ra["state"] == "result" and not ra.get("shared")
    qb = await invoke(b, "network.lldp")
    assert qb["job_id"] == qa["job_id"] and qb["shared"] is True and qb["state"] == "result"
    assert "cached_at" in qb and qb["result"]["blocks"] == ra["result"]["blocks"]
    assert fake.calls["GET /network/info/"] == 1


async def test_running_shareable_read_is_attached_not_rerun(harness, fake):
    fake.speedtest_delay = 0.3
    a, b = await rich(harness), await rich(harness)
    qa = await invoke(a, "utils.speedtest.run")
    await asyncio.wait_for(fake.speedtest_started.wait(), 2)
    qb = await invoke(b, "utils.speedtest.run")
    assert qb["job_id"] == qa["job_id"] and qb["shared"] is True and qb["state"] == "progress"
    ra, rb = await final(a, qa["job_id"]), await final(b, qa["job_id"])
    assert ra["state"] == rb["state"] == "result"
    assert fake.calls["GET /utils/speedtest"] == 1


async def test_secret_result_never_shared_or_cached(harness, fake):
    fake.mode = "hotspot"
    harness.poller.invalidate_context()
    await harness.poller.poll_once()
    a, b = await rich(harness), await rich(harness)
    ja = await invoke(a, "utils.ssid_passphrase")
    ra = await final(a, ja["job_id"])
    kv = next(bl for bl in ra["result"]["blocks"] if bl["type"] == "kv")
    assert {"key": "Passphrase", "value": "s3cret-pass", "secret": True} in kv["items"]
    jb = await invoke(b, "utils.ssid_passphrase")
    assert jb["job_id"] != ja["job_id"] and not jb.get("shared")
    await final(b, jb["job_id"])
    assert fake.calls["GET /system/hotspot/ssid-passphrase"] == 2
    # And nothing secret leaks into topics.
    assert "s3cret" not in str(harness.service.hub.data)


async def test_mutations_never_shared_or_cached(harness, fake):
    a, b = await rich(harness), await rich(harness)
    ja = await invoke(a, "system.reboot", confirmed=True)
    jb = await invoke(b, "system.reboot", confirmed=True)
    assert ja["job_id"] != jb["job_id"]
    assert not ja.get("shared") and not jb.get("shared")
    await final(a, ja["job_id"]), await final(b, jb["job_id"])
    ja2 = await invoke(a, "system.reboot", confirmed=True)
    await final(a, ja2["job_id"])
    assert fake.reboots == 3


async def test_confirmation_bound_to_invoking_session(harness, fake):
    a, b = await rich(harness), await rich(harness)
    ja = await invoke(a, "system.reboot")
    prompt = await a.expect(type="prompt")
    assert prompt["kind"] == "confirm" and prompt["job_id"] == ja["job_id"]
    assert not any(f["type"] == "prompt" for f in b.frames), "the prompt goes only to the invoker"
    mid = await b.send("answer", prompt_id=prompt["prompt_id"], confirmed=True)
    err = await b.expect(lambda f: f["id"] == mid, type="error")
    assert err["code"] == "FORBIDDEN"
    assert fake.reboots == 0
    await a.send("answer", prompt_id=prompt["prompt_id"], confirmed=True)
    assert (await final(a, ja["job_id"]))["state"] == "result"
    assert fake.reboots == 1


async def test_confirmation_declined_cancels(harness, fake):
    a = await rich(harness)
    ja = await invoke(a, "system.shutdown")
    p = await a.expect(type="prompt")
    await a.send("answer", prompt_id=p["prompt_id"], confirmed=False)
    assert (await final(a, ja["job_id"]))["state"] == "cancelled"


async def test_thin_confirm_ignores_confirmed_flag(harness, fake):
    t = await harness.connect()
    await t.hello()
    await invoke(t, "system.reboot", confirmed=True)
    p = await t.expect(type="prompt")
    assert p["kind"] == "confirm"
    v = await t.expect(lambda f: f["screen"] == "prompt", type="view")
    assert v["blocks"][0]["type"] == "confirm" and v["blocks"][0]["selected"] == "cancel"
    await t.input("down")
    await t.input("center")  # confirm on the panel
    await t.expect(lambda f: f["state"] == "result", type="job")
    assert fake.reboots == 1


async def test_cancel_detaches_only_while_others_attached(harness, fake):
    fake.speedtest_delay = 0.4
    a, b = await rich(harness), await rich(harness)
    ja = await invoke(a, "utils.speedtest.run")  # cancellable read
    await asyncio.wait_for(fake.speedtest_started.wait(), 2)
    await invoke(b, "utils.speedtest.run")
    mid = await a.send("cancel", job_id=ja["job_id"])
    c = await a.expect(lambda f: f["id"] == mid, type="job")
    assert c["state"] == "cancelled"
    rb = await final(b, ja["job_id"])
    assert rb["state"] == "result" and not fake.speedtest_cancelled


async def test_last_detach_stops_a_cancellable_job(harness, fake):
    fake.speedtest_delay = 5
    a = await rich(harness)
    ja = await invoke(a, "utils.speedtest.run")
    await asyncio.wait_for(fake.speedtest_started.wait(), 2)
    await a.send("cancel", job_id=ja["job_id"])
    await asyncio.sleep(0.1)
    job = harness.service.jobs.jobs[ja["job_id"]]
    assert job.state == "cancelled" and fake.speedtest_cancelled


async def test_last_detach_keeps_a_non_cancellable_job(harness, fake):
    fake.scan_networks = 2
    fake.scan_delay = 0.2
    a = await rich(harness)
    ja = await invoke(a, "apps.scanner.scan")  # not cancellable
    await asyncio.sleep(0.05)
    await a.send("cancel", job_id=ja["job_id"])
    await asyncio.sleep(0.4)
    job = harness.service.jobs.jobs[ja["job_id"]]
    assert job.state == "result", "a non-cancellable action runs to completion after the last detach"


async def test_core_409_passes_through_as_conflict(harness, fake):
    a = await rich(harness)
    j = await invoke(a, "apps.profiler.purge", confirmed=True)
    r = await final(a, j["job_id"])
    assert r["state"] == "error"
    assert r["error"] == {"code": "CONFLICT", "message": "Stop the profiler before purging", "core_status": 409}
    fake.scan_conflict = True
    j = await invoke(a, "apps.scanner.scan")
    r = await final(a, j["job_id"])
    assert r["error"]["code"] == "CONFLICT" and "already running" in r["error"]["message"]


async def test_needs_selection_prompts_then_reruns(harness, fake):
    fake.scan_candidates = [{"iface": "wlan1", "namespace": "root", "label": "wlan1 (mt7921u)"},
                            {"iface": "wlan2", "namespace": "ns1", "label": "wlan2 (ns1)"}]
    a = await rich(harness)
    j = await invoke(a, "apps.scanner.scan_nohidden")
    p = await a.expect(type="prompt")
    assert p["kind"] == "select" and p["job_id"] == j["job_id"]
    assert [o["value"] for o in p["options"]] == [{"iface": "wlan1", "namespace": "root"},
                                                  {"iface": "wlan2", "namespace": "ns1"}]
    await a.send("answer", prompt_id=p["prompt_id"], value={"iface": "wlan2", "namespace": "ns1"})
    r = await final(a, j["job_id"])
    assert r["state"] == "result" and r["result"]["blocks"][0]["type"] == "table"
    req = [c for c in fake.router.calls if "scan" in str(c.request.url)][-1].request
    assert req.url.params["iface"] == "wlan2" and req.url.params["namespace"] == "ns1"
    assert req.url.params["hidden"] == "false"



async def test_needs_selection_thin_panel_select_then_rerun(harness, fake):
    """The front-panel/thin path: core says needsSelection, the panel picks with keys, the scan reruns."""
    fake.scan_candidates = [{"iface": "wlan1", "namespace": "root", "label": "wlan1 (mt7921u)"},
                            {"iface": "wlan2", "namespace": "ns1", "label": "wlan2 (ns1)"}]
    t = await harness.connect()
    await t.hello()
    j = await invoke(t, "apps.scanner.scan")
    p = await t.expect(type="prompt")
    assert p["kind"] == "select" and p["job_id"] == j["job_id"] and len(p["options"]) == 2
    v = await t.expect(lambda f: f["screen"] == "prompt", type="view")
    assert any("wlan2 (ns1)" in str(b) for b in v["blocks"])
    await t.input("down")
    await t.input("center")
    r = await t.expect(lambda f: f["job_id"] == j["job_id"] and f["state"] in ("result", "error"), type="job")
    assert r["state"] == "result"
    scans = [c.request for c in fake.router.calls if "/utils/wlan/scan" in str(c.request.url)]
    assert "iface" not in scans[-2].url.params  # first call: core asked which adapter
    assert scans[-1].url.params["iface"] == "wlan2" and scans[-1].url.params["namespace"] == "ns1"


async def test_needs_selection_cancelled_does_not_rerun(harness, fake):
    fake.scan_candidates = [{"iface": "wlan1", "namespace": "root", "label": "wlan1"},
                            {"iface": "wlan2", "namespace": "root", "label": "wlan2"}]
    a = await rich(harness)
    j = await invoke(a, "apps.scanner.scan")
    p = await a.expect(type="prompt")
    await a.send("answer", prompt_id=p["prompt_id"], cancel=True)
    r = await final(a, j["job_id"])
    assert r["state"] == "cancelled"
    assert fake.calls["GET /utils/wlan/scan"] == 1


async def test_needs_selection_twice_then_gives_up(harness, fake):
    """Core keeps asking (adapter vanished between calls): at most two prompts, then the empty answer renders."""
    fake.scan_candidates = [{"iface": "wlan1", "namespace": "root", "label": "wlan1"},
                            {"iface": "wlan2", "namespace": "root", "label": "wlan2"}]
    fake.scan_always_ask = True
    a = await rich(harness)
    j = await invoke(a, "apps.scanner.scan_nohidden")
    for _ in range(2):
        p = await a.expect(type="prompt")
        await a.send("answer", prompt_id=p["prompt_id"], value={"iface": "wlan1", "namespace": "root"})
    r = await final(a, j["job_id"])
    assert r["state"] in ("result", "error")
    assert fake.calls["GET /utils/wlan/scan"] == 3

async def test_paging_first_page_and_cursor(harness, fake):
    fake.scan_networks = 45
    a = await rich(harness)
    j = await invoke(a, "apps.scanner.scan")
    r = await final(a, j["job_id"])
    res = r["result"]
    assert res["total"] == 45 and len(res["blocks"][0]["rows"]) == 20 and res["cursor"]
    seen = [row[0] for row in res["blocks"][0]["rows"]]
    cursor = res["cursor"]
    while cursor:
        mid = await a.send("page", job_id=j["job_id"], cursor=cursor)
        pg = await a.expect(lambda f, mid=mid: f["id"] == mid, type="job")
        seen += [row[0] for row in pg["result"]["blocks"][0]["rows"]]
        cursor = pg["result"]["cursor"]
    assert seen == [f"net{i:03d}" for i in range(45)]
    mid = await a.send("page", job_id=j["job_id"], cursor="cbogus")
    assert (await a.expect(lambda f: f["id"] == mid, type="error"))["code"] == "NOT_FOUND"
    b = await rich(harness)
    mid = await b.send("page", job_id=j["job_id"], cursor=res["cursor"])
    assert (await b.expect(lambda f: f["id"] == mid, type="error"))["code"] == "NOT_FOUND", "not attached"


async def test_thin_paging_with_up_down(harness, fake):
    fake.scan_networks = 45
    t = await harness.connect()
    await t.hello()
    await invoke(t, "apps.scanner.scan")
    v = await t.expect(lambda f: f["screen"] == "result", type="view")
    assert v["page"] == {"index": 0, "count": 3, "more": True}
    v = await t.input("down")
    assert v["page"]["index"] == 1 and v["blocks"][0]["rows"][0][0] == "net020"
    v = await t.input("down")
    v = await t.input("down")
    assert v["page"] == {"index": 2, "count": 3, "more": False} and len(v["blocks"][0]["rows"]) == 5


async def test_parameters_rich_form_and_thin_select(harness, fake):
    a = await rich(harness)
    j = await invoke(a, "system.reg_domain.set")
    p = await a.expect(type="prompt")
    assert p["kind"] == "form" and p["fields"][0]["name"] == "country"
    mid = await a.send("answer", prompt_id=p["prompt_id"], values={"country": "XX"})
    assert (await a.expect(lambda f: f["id"] == mid, type="error"))["code"] == "BAD_REQUEST"
    await a.send("answer", prompt_id=p["prompt_id"], values={"country": "GB"})
    c = await a.expect(type="prompt")
    assert c["kind"] == "confirm"
    await a.send("answer", prompt_id=c["prompt_id"], confirmed=True)
    await final(a, j["job_id"])
    body = [x for x in fake.router.calls if "reg-domain/set" in str(x.request.url)][-1].request.content
    assert b'"country": "GB"' in body or b'"country":"GB"' in body

    mid = await a.send("invoke", node="system.reg_domain.set", params={"country": "ZZ"})
    assert (await a.expect(lambda f: f["id"] == mid, type="error"))["code"] == "BAD_REQUEST"

    t = await harness.connect()
    await t.hello()
    await invoke(t, "system.reg_domain.set")
    p = await t.expect(type="prompt")
    assert p["kind"] == "select" and p["options"][0] == {"value": "US", "label": "US"}


async def test_mid_job_prompts_from_a_handler(harness, fake):
    a = await rich(harness)
    j = await invoke(a, "system.timezone.set")
    p1 = await a.expect(type="prompt")
    assert [o["value"] for o in p1["options"]] == ["America", "Europe"]
    await a.send("answer", prompt_id=p1["prompt_id"], value="Europe")
    p2 = await a.expect(type="prompt")
    await a.send("answer", prompt_id=p2["prompt_id"], value="Europe/Prague")
    r = await final(a, j["job_id"])
    assert r["state"] == "result" and fake.timezone == "Europe/Prague"


async def test_forbidden_hidden_and_unknown_nodes(harness):
    a = await rich(harness)
    for node, code in (("utils.cloud.mist", "FORBIDDEN"), ("no.such.node", "NOT_FOUND"), ("network", "BAD_REQUEST")):
        r = await invoke(a, node)
        assert r["type"] == "error" and r["code"] == code, node


async def test_viewer_cannot_run_mutations(harnesses):
    import os
    h = await harnesses(local_uids={os.getuid(): ("viewer",)})
    v = await rich(h)
    r = await invoke(v, "system.reboot", confirmed=True)
    assert r["code"] == "FORBIDDEN"


async def test_timeout_and_prompt_expiry(harnesses, fake):
    h = await harnesses(prompt_expires_sec=1)
    fake.speedtest_delay = 5
    # speedtest has timeout_sec 120 in the manifest; patch the node for the test.
    node = h.service.menu.get("utils.speedtest.run")
    node.raw["action"]["timeout_sec"] = 1
    a = await rich(h)
    j = await invoke(a, "utils.speedtest.run")
    r = await final(a, j["job_id"], timeout=3)
    assert r["error"]["code"] == "TIMEOUT"
    j = await invoke(a, "system.reboot")
    await a.expect(type="prompt")
    r = await final(a, j["job_id"], timeout=3)
    assert r["state"] == "cancelled" and fake.reboots == 0


async def test_jobs_topic(harness, fake):
    fake.speedtest_delay = 0.3
    a = await rich(harness)
    await a.send("subscribe", topics=["jobs"])
    await a.expect(type="event")
    j = await invoke(a, "utils.speedtest.run")
    ev = await a.expect(lambda f: f["topic"] == "jobs" and f["data"], type="event")
    assert ev["data"][0]["node"] == "utils.speedtest.run" and ev["data"][0]["job_id"] == j["job_id"]
    await final(a, j["job_id"])
    ev = await a.expect(lambda f: f["topic"] == "jobs" and f["data"] == [], type="event")
    assert not ev.get("snapshot")
