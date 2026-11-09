"""The default session and independent sessions (plan: "Sessions").

* N connections attach to one default session: one cursor, views fan out to
  all of them, last write wins, each view names who drove it.
* Prompts on the default session go to every attached connection and the
  first answer wins; a confirmation stays with the invoking principal.
* The default session never ends; an independent session ends when its last
  connection detaches (after the resume grace).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

WEBUI = {"X-WebUI-User": "alice", "X-WebUI-Session": "W1"}


async def attach(h, name: str, *, sock: str = "local", headers: dict[str, str] | None = None,
                 mode: str = "thin", **extra: Any):
    c = await h.connect(sock, headers=headers)
    await c.send("hello", mode=mode, protocol=[1], client={"name": name},
                 capabilities={"blocks": ["text", "kv", "list", "confirm"], "screen": {"rows": 8, "cols": 16}},
                 **extra)
    w = await c.expect(type="welcome")
    if mode == "thin":
        await c.expect(type="view")
    return c, w


def views(c) -> list[dict[str, Any]]:
    return [f for f in c.frames if f["type"] == "view"]


async def settle() -> None:
    await asyncio.sleep(0.1)


async def test_thin_hello_without_session_attaches_to_default(harness):
    _, wa = await attach(harness, "tui-a")
    _, wb = await attach(harness, "tui-b", session="default")
    assert wa["session"] == wb["session"] == "default"
    assert wa["session_id"] == wb["session_id"] == harness.service.default_session.id
    _, wr = await attach(harness, "webui", mode="rich")
    assert wr["session"] == "new" and wr["session_id"] != wa["session_id"], "rich defaults to its own session"


async def test_rich_hello_cannot_attach_to_default(harness):
    c = await harness.connect()
    await c.send("hello", mode="rich", protocol=[1], session="default")
    err = await c.expect(type="error")
    assert err["code"] == "BAD_REQUEST" and err["fatal"] is True
    assert await c.wait_closed() == 4400


async def test_attach_fans_out_views_to_every_connection(harness):
    a, _ = await attach(harness, "tui-a")
    b, _ = await attach(harness, "tui-b")
    va = await a.input("down")  # home -> top menu, driven by A
    vb = await b.expect(lambda f: f["screen"] == "menu", type="view")
    assert va["id"].startswith("c") and vb["id"].startswith("s:"), "A gets the reply, B an unprompted copy"
    for k in ("screen", "node", "title", "blocks"):
        assert va[k] == vb[k]
    assert va["driven_by"]["self"] is True and vb["driven_by"]["self"] is False
    assert vb["driven_by"]["surface"] == "tui-a" and vb["driven_by"]["principal"]["kind"] == "local"

    await a.input("center")  # into Network
    vb = await b.expect(lambda f: f.get("breadcrumb") == ["Network"], type="view")
    assert vb["screen"] == "menu"
    # B drives the same cursor from where A left it.
    vb2 = await b.input("down")
    va2 = await a.expect(lambda f: f["node"] == vb2["node"], type="view")
    assert vb2["breadcrumb"] == ["Network"] and va2["driven_by"]["surface"] == "tui-b"
    assert harness.service.default_session.cursor.stack == ("network",)


async def test_concurrent_input_is_last_write_wins(harness):
    a, _ = await attach(harness, "tui-a")
    b, _ = await attach(harness, "tui-b")
    await a.input("down")
    await settle()
    # Both press at once: every key is applied in arrival order to the one cursor.
    await asyncio.gather(a.send("input", key="down"), b.send("input", key="down"), a.send("input", key="down"))
    await settle()
    cur = harness.service.default_session.cursor
    assert cur.screen == "menu" and cur.index == 3
    la, lb = views(a)[-1], views(b)[-1]
    assert la["node"] == lb["node"] and la["blocks"] == lb["blocks"], "both surfaces end on the same screen"
    assert la["driven_by"]["surface"] == lb["driven_by"]["surface"]


async def test_prompt_goes_to_all_and_first_answer_wins(harness, fake):
    a, _ = await attach(harness, "tui-a")
    b, _ = await attach(harness, "tui-b")
    mid = await a.send("invoke", node="system.reg_domain.set")
    job = await a.expect(lambda f: f["id"] == mid, type="job")
    pa = await a.expect(type="prompt")
    pb = await b.expect(type="prompt")
    assert pa["prompt_id"] == pb["prompt_id"] and pa["kind"] == "select"
    vb = await b.expect(lambda f: f["screen"] == "prompt", type="view")
    assert vb["answerable"] is True
    # B answers first; A's later answer finds the prompt resolved.
    await b.send("answer", prompt_id=pb["prompt_id"], value="AU")
    mid = await a.send("answer", prompt_id=pa["prompt_id"], value="US")
    err = await a.expect(lambda f: f["id"] == mid, type="error")
    assert err["code"] == "NOT_FOUND"
    # The node's confirm follows; it is A's (the invoker's) alone, but B is the same uid here.
    ca = await a.expect(lambda f: f["kind"] == "confirm", type="prompt")
    await b.expect(lambda f: f["prompt_id"] == ca["prompt_id"], type="prompt")
    await a.send("answer", prompt_id=ca["prompt_id"], confirmed=True)
    done = await a.expect(lambda f: f["job_id"] == job["job_id"] and f["state"] == "result", type="job")
    assert done["state"] == "result"
    # Both see the prompt resolved: the shared screen moved on to the result.
    await settle()
    assert views(a)[-1]["screen"] == views(b)[-1]["screen"] == "result"
    assert views(a)[-1]["driven_by"]["surface"] == "tui-a"


async def test_thin_prompt_answered_by_input_from_another_surface(harness):
    a, _ = await attach(harness, "tui-a")
    b, _ = await attach(harness, "tui-b")
    await a.send("invoke", node="system.reg_domain.set")
    await b.expect(lambda f: f["screen"] == "prompt", type="view")
    await b.input("down")  # move the shared highlight
    va = await a.expect(lambda f: f["screen"] == "prompt" and f["blocks"][-1].get("selected") == 1, type="view")
    assert va["driven_by"]["surface"] == "tui-b"
    await a.input("center")  # A answers with B's highlight
    vb = await b.expect(lambda f: f["screen"] == "prompt" and f["blocks"][-1]["type"] == "confirm", type="view")
    assert vb["answerable"] is True  # same uid: same principal as the invoker
    await b.input("down")
    await b.input("center")
    vr = await a.expect(lambda f: f["screen"] == "result", type="view")
    assert vr["node"] == "system.reg_domain.set" and vr["driven_by"]["surface"] == "tui-b"


async def test_confirmation_bound_to_invoking_principal(harness, fake):
    a, _ = await attach(harness, "tui-a")  # local uid
    a2, _ = await attach(harness, "tui-a2")  # same principal, another connection
    w, ww = await attach(harness, "webui-mirror", sock="proxy", headers=WEBUI)  # another principal
    assert ww["session"] == "default" and ww["principal"]["kind"] == "webui"
    mid = await a.send("invoke", node="system.reboot")
    job = await a.expect(lambda f: f["id"] == mid, type="job")
    pw = await w.expect(type="prompt")
    assert pw["kind"] == "confirm" and pw["invoked_by"]["kind"] == "local"
    vw = await w.expect(lambda f: f["screen"] == "prompt", type="view")
    va2 = await a2.expect(lambda f: f["screen"] == "prompt", type="view")
    assert vw["answerable"] is False and vw["hints"] == {}, "the WebUI sees the confirm but cannot answer"
    assert va2["answerable"] is True and va2["hints"]

    # The other principal can neither answer, cancel nor navigate away.
    mid = await w.send("answer", prompt_id=pw["prompt_id"], confirmed=True)
    assert (await w.expect(lambda f: f["id"] == mid, type="error"))["code"] == "FORBIDDEN"
    mid = await w.send("answer", prompt_id=pw["prompt_id"], cancel=True)
    assert (await w.expect(lambda f: f["id"] == mid, type="error"))["code"] == "FORBIDDEN"
    for key in ("left", "key3", "key1"):
        kid = await w.send("input", key=key)
        assert (await w.expect(lambda f, k=kid: f["id"] == k, type="error"))["code"] == "FORBIDDEN"
        v = await w.expect(lambda f, k=kid: f["id"] == k, type="view")
        assert v["screen"] == "prompt"
    mid = await w.send("invoke", node="network.lldp")
    assert (await w.expect(lambda f: f["id"] == mid, type="error"))["code"] == "FORBIDDEN"
    assert harness.service.default_session.cursor.screen == "prompt" and fake.reboots == 0

    # Any connection of the invoking principal may answer it.
    await a2.send("answer", prompt_id=pw["prompt_id"], confirmed=True)
    await a.expect(lambda f: f["job_id"] == job["job_id"] and f["state"] == "result", type="job")
    assert fake.reboots == 1


async def test_confirmation_cancel_by_invoking_principal(harness, fake):
    a, _ = await attach(harness, "tui-a")
    b, _ = await attach(harness, "tui-b")  # same uid: same principal
    await a.send("invoke", node="system.shutdown")
    await b.expect(lambda f: f["screen"] == "prompt", type="view")
    await b.input("left")  # cancel from the other connection of the same principal
    j = await a.expect(lambda f: f["state"] == "cancelled", type="job")
    assert j["node"] == "system.shutdown"


async def test_confirmation_aborted_when_invoker_leaves(harness, fake):
    a, _ = await attach(harness, "tui-a")
    w, _ = await attach(harness, "webui-mirror", sock="proxy", headers=WEBUI)
    await a.send("invoke", node="system.reboot")
    await w.expect(lambda f: f["screen"] == "prompt", type="view")
    await a.close()
    j = await w.expect(lambda f: f["state"] == "cancelled", type="job")
    assert j["node"] == "system.reboot"
    v = await w.expect(lambda f: f["screen"] == "result", type="view")
    assert v["blocks"][0]["text"] == "Cancelled", "the shared screen is not stranded on a confirm"
    assert fake.reboots == 0


async def test_default_session_survives_disconnects(harness):
    a, wa = await attach(harness, "tui-a")
    await a.input("down")
    await a.input("center")  # Network
    await a.input("down")
    await a.close()
    await settle()
    svc = harness.service
    d = svc.default_session
    assert not d.connected and d.cursor.stack == ("network",)
    d.detached_at = time.time() - 10 * svc.config.resume_grace_sec
    svc.expire_sessions()
    assert svc.sessions[d.id] is d, "the default session never ends"
    # A new surface wakes up to the current screen.
    b, wb = await attach(harness, "panel")
    v = views(b)[-1]
    assert wb["session_id"] == wa["session_id"] and v["breadcrumb"] == ["Network"]
    assert v["node"] == "network.adapter_summary" and v["driven_by"]["surface"] == "tui-a"
    assert v["driven_by"]["self"] is False


async def test_default_session_resume_keeps_topic_stream(harness, fake):
    a, wa = await attach(harness, "tui-a")
    await a.send("subscribe", topics=["device.summary"])
    ev = await a.expect(type="event")
    await a.close()
    await settle()
    b, wb = await attach(harness, "tui-a", resume={"epoch": wa["epoch"], "session_id": wa["session_id"],
                                                   "seq": {"device.summary": ev["seq"]},
                                                   "menu_revision": harness.service.menu_revision})
    assert wb["resumed"] is True and wb["session"] == "default"
    assert [f["type"] for f in b.frames] == ["welcome", "view"], "no menu or snapshot, but always the view"
    fake.cpu_temp = "77.0C"
    await harness.poller.poll_once()
    up = await b.expect(lambda f: f["topic"] == "device.summary", type="event")
    assert up["seq"] == ev["seq"] + 1 and not up.get("snapshot")


async def test_independent_session_navigates_privately(harness):
    a, _ = await attach(harness, "tui-a")
    n, wn = await attach(harness, "tui-new", session="new")
    assert wn["session"] == "new" and wn["session_id"] != harness.service.default_session.id
    await n.input("down")
    vn = await n.input("down")
    await settle()
    assert vn["node"] == "bluetooth" and "driven_by" not in vn
    assert all(v["screen"] == "home" for v in views(a)), "the default session saw nothing"
    await a.input("down")
    await settle()
    assert views(n)[-1]["node"] == "bluetooth", "and the independent one saw nothing of A"
    # Its prompts stay private too.
    await n.send("invoke", node="system.reg_domain.set")
    await n.expect(type="prompt")
    await settle()
    assert not any(f["type"] == "prompt" for f in a.frames)


async def test_independent_session_ends_after_last_detach(harness, fake):
    fake.speedtest_delay = 5
    n, wn = await attach(harness, "tui-new", session="new")
    mid = await n.send("invoke", node="utils.speedtest.run")
    job = await n.expect(lambda f: f["id"] == mid, type="job")
    svc = harness.service
    s = svc.sessions[wn["session_id"]]
    await n.close()
    await settle()
    assert not s.connected and wn["session_id"] in svc.sessions, "kept for the resume grace"
    s.detached_at = time.time() - svc.config.resume_grace_sec - 1
    svc.expire_sessions()
    assert wn["session_id"] not in svc.sessions
    j = svc.jobs.jobs[job["job_id"]]
    assert s.id not in j.attached
    await settle()
    assert j.state == "cancelled", "nobody left attached to a cancellable job"


async def test_default_session_job_survives_invoker_disconnect(harness, fake):
    fake.speedtest_delay = 0.3
    a, _ = await attach(harness, "tui-a")
    b, _ = await attach(harness, "panel")
    await a.send("invoke", node="utils.speedtest.run")
    await b.expect(lambda f: f["screen"] == "busy", type="view")
    await a.close()
    v = await b.expect(lambda f: f["screen"] == "result", type="view", timeout=3)
    assert v["node"] == "utils.speedtest.run"


@pytest.mark.parametrize("n", [3])
async def test_view_count_scales_with_attached(harness, n):
    cs = [(await attach(harness, f"tui-{i}"))[0] for i in range(n)]
    await cs[0].input("down")
    await settle()
    assert all(views(c)[-1]["screen"] == "menu" for c in cs)
    assert len(harness.service.default_session.attachments) == n
