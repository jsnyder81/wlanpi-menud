"""Authentication on both sockets and the handshake rules."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import time

from fakecore import make_jwt


async def test_local_peercred_allowed(harness):
    c = await harness.connect("local")
    w = await c.hello()
    assert w["principal"]["kind"] == "local" and w["principal"]["uid"] == os.getuid()
    assert w["roles"] == ["admin"] and w["principal"]["did"] == "wlanpi-menud"


async def test_local_peercred_refused(harnesses):
    h = await harnesses(local_uids={}, local_gids={})
    c = await h.connect("local")
    err = await c.expect(type="error")
    assert err["code"] == "AUTH_FAILED" and err["fatal"] and err["close_code"] == 4401
    assert f"uid {os.getuid()}" in err["message"]
    assert await c.wait_closed() == 4401


async def test_local_group_allowlist(harnesses):
    h = await harnesses(local_uids={}, local_gids={os.getgid(): ("operator",)})
    c = await h.connect("local")
    w = await c.hello()
    assert w["roles"] == ["operator"]


async def test_proxy_ignores_peer_credentials(harness):
    # Our uid is on the local allowlist, but proxy.sock must not trust it.
    c = await harness.connect("proxy")
    await c.send("hello", mode="rich", protocol=[1])
    err = await c.expect(type="error")
    assert err["code"] == "AUTH_FAILED" and err["close_code"] == 4401
    assert await c.wait_closed() == 4401


async def test_proxy_auth_timeout(harnesses):
    h = await harnesses(auth_timeout_sec=0.2)
    c = await h.connect("proxy")
    err = await c.expect(type="error")
    assert err["code"] == "AUTH_FAILED" and "in time" in err["message"]
    assert await c.wait_closed() == 4401


async def test_proxy_webui_headers(harness):
    c = await harness.connect("proxy", headers={"X-WebUI-User": "alice", "X-WebUI-Session": "sess-123"},
                              origin="http://wlanpi.local")
    w = await c.hello("rich")
    assert w["principal"] == {"kind": "webui", "name": "alice", "did": "wlanpi-menud"}


async def test_proxy_origin_mismatch_closes_1008(harness):
    c = await harness.connect("proxy", headers={"X-WebUI-User": "alice", "X-WebUI-Session": "s"},
                              origin="https://evil.example")
    err = await c.expect(type="error")
    assert err["close_code"] == 1008
    assert await c.wait_closed() == 1008


async def test_proxy_duplicate_host_header_closes_1008(harness):
    c = await harness.connect("proxy", headers=[("Host", "evil.example")])
    err = await c.expect(type="error")
    assert err["code"] == "FORBIDDEN"
    assert err["close_code"] == 1008
    assert await c.wait_closed() == 1008


async def test_proxy_token_in_url_refused(harness, fake):
    tok = make_jwt("phone-1")
    fake.remote_tokens[tok] = {"valid": True, "did": "phone-1", "exp": time.time() + 600, "jti": "x"}
    c = await harness.connect("proxy", path=f"/ui/ws?token={tok}")
    err = await c.expect(type="error")
    assert err["code"] == "AUTH_FAILED" and "URL" in err["message"]
    assert await c.wait_closed() == 4401


async def test_proxy_jwt_first_frame(harness, fake):
    tok = make_jwt("phone-1")
    fake.remote_tokens[tok] = {"valid": True, "did": "phone-1", "exp": time.time() + 600, "jti": "j1"}
    c = await harness.connect("proxy")
    await c.send("auth", token=tok)
    w = await c.hello("rich")
    assert w["principal"]["kind"] == "jwt" and w["principal"]["did"] == "phone-1"
    # Jobs for a JWT session use the user's own token at core.
    seen = []
    orig = fake._auth

    def spy(request):
        seen.append(request.headers["Authorization"])
        return orig(request)
    fake._auth = spy
    await c.send("invoke", node="network.lldp")
    await c.expect(lambda f: f["state"] == "result", type="job")
    assert f"Bearer {tok}" in seen


async def test_proxy_jwt_invalid(harness, fake):
    c = await harness.connect("proxy")
    await c.send("auth", token=make_jwt("unknown"))
    err = await c.expect(type="error")
    assert err["code"] == "AUTH_FAILED"
    assert await c.wait_closed() == 4401


async def test_proxy_jwt_without_core_introspection(harness, fake):
    fake.introspection = False
    tok = make_jwt("phone-1")
    fake.remote_tokens[tok] = {"valid": True, "did": "phone-1"}
    c = await harness.connect("proxy")
    await c.send("auth", token=tok)
    err = await c.expect(type="error")
    assert err["code"] == "AUTH_FAILED" and "introspection" in err["message"]


async def test_proxy_jwt_recheck_revokes(harnesses, fake):
    h = await harnesses(introspect_cache_sec=0.2)
    tok = make_jwt("phone-1")
    fake.remote_tokens[tok] = {"valid": True, "did": "phone-1", "exp": time.time() + 600}
    c = await h.connect("proxy")
    await c.send("auth", token=tok)
    await c.hello("rich")
    fake.remote_tokens[tok]["valid"] = False  # revoked at core
    err = await c.expect(type="error", timeout=3)
    assert err["code"] == "AUTH_FAILED"
    assert await c.wait_closed() == 4401


async def test_webui_logout_closes_that_sessions_connections(harness):
    hdr = {"X-WebUI-User": "alice", "X-WebUI-Session": "S1"}
    a1 = await harness.connect("proxy", headers=hdr)
    a2 = await harness.connect("proxy", headers=hdr)
    b = await harness.connect("proxy", headers={"X-WebUI-User": "bob", "X-WebUI-Session": "S2"})
    for c in (a1, a2, b):
        await c.hello("rich")
    assert harness.service.revoke_webui_session("S1") == 2
    assert await a1.wait_closed() == 4401 and await a2.wait_closed() == 4401
    await asyncio.sleep(0.05)
    assert not b.closed.is_set()


async def test_revoke_frame_closes_matching_sessions_only(harness):
    hdr = {"X-WebUI-User": "alice", "X-WebUI-Session": "S1"}
    a1 = await harness.connect("proxy", headers=hdr)
    a2 = await harness.connect("proxy", headers=hdr)
    b = await harness.connect("proxy", headers={"X-WebUI-User": "bob", "X-WebUI-Session": "S2"})
    for c in (a1, a2, b):
        await c.hello("rich")

    admin = await harness.connect("local")
    await admin.hello("rich")
    await admin.send("revoke", webui_session="S1")

    assert await a1.wait_closed() == 4401 and await a2.wait_closed() == 4401
    await asyncio.sleep(0.05)
    assert not b.closed.is_set()


async def test_revoke_frame_non_webui_principal_forbidden(harnesses):
    h = await harnesses(local_uids={}, local_gids={os.getgid(): ("operator",)})
    c = await h.connect("local")
    await c.hello("rich")
    await c.send("revoke", webui_session="S1")
    err = await c.expect(type="error")
    assert err["code"] == "FORBIDDEN"
    assert "local admin principals" in err["message"]


async def test_revoke_admin_operator_and_default_session(harnesses, monkeypatch):
    import wlanpi_menud.server as server_module
    from wlanpi_menud.auth import PeerCred

    uid = os.getuid()
    operator_uid = uid + 1000
    h = await harnesses(local_uids={uid: ("admin",), operator_uid: ("operator",)})

    real_peercred = server_module.peer_credentials
    simulated_uid = uid

    def fake_peercred(sock):
        cred = real_peercred(sock)
        return PeerCred(cred.pid, simulated_uid, cred.gid)

    monkeypatch.setattr(server_module, "peer_credentials", fake_peercred)

    # 1. Default session setup:
    # A local client (e.g. front panel) is attached to the default session
    default_client = await h.connect("local")
    w_default = await default_client.hello("thin", session="default")
    assert w_default["session"] == "default"
    assert h.service.default_session.id in h.service.sessions

    # A WebUI client mirrors the default session (thin mode)
    s_mirror = await h.connect("proxy", headers={"X-WebUI-User": "alice", "X-WebUI-Session": "S_MIRROR"})
    w_mirror = await s_mirror.hello("thin", session="default")
    assert w_mirror["session"] == "default"
    assert len(h.service.default_session.attachments) == 2

    # WebUI independent sessions on proxy
    s1 = await h.connect("proxy", headers={"X-WebUI-User": "alice", "X-WebUI-Session": "S1"})
    s2 = await h.connect("proxy", headers={"X-WebUI-User": "bob", "X-WebUI-Session": "S2"})
    for c in (s1, s2):
        await c.hello("rich")

    # 2. Local admin client connects with real peercred (simulated_uid = uid)
    admin = await h.connect("local")
    w_admin = await admin.hello("rich")
    assert "admin" in w_admin["roles"]

    # 3. Operator client connects on the REAL local.sock of the SAME service with operator peercred
    simulated_uid = operator_uid
    operator = await h.connect("local")
    w_op = await operator.hello("rich")
    assert "admin" not in w_op["roles"]
    assert "operator" in w_op["roles"]

    # Switch peercred back for any future local connections
    simulated_uid = uid

    # Operator role gets FORBIDDEN when attempting to revoke s2
    await operator.send("revoke", webui_session="S2")
    err = await operator.expect(type="error")
    assert err["code"] == "FORBIDDEN"
    assert "local admin principals" in err["message"]
    # Crucial pin: operator's attempt did NOT close s2 on this same service
    assert not s2.closed.is_set()

    # Plain wlanpi admin CAN revoke s2
    await admin.send("revoke", webui_session="S2")
    assert await s2.wait_closed() == 4401

    # Plain wlanpi admin also revokes s1
    await admin.send("revoke", webui_session="S1")
    assert await s1.wait_closed() == 4401

    # 4. Default-session immunity:
    # Revoke the WebUI session (S_MIRROR) that mirrors the default session
    await admin.send("revoke", webui_session="S_MIRROR")
    assert await s_mirror.wait_closed() == 4401

    # Allow server handler to complete detachment of the closed connection
    for _ in range(50):
        if len(h.service.default_session.attachments) == 1:
            break
        await asyncio.sleep(0.01)

    # Assert the default session object STILL exists in service.sessions
    assert h.service.default_session.id in h.service.sessions
    assert h.service.sessions[h.service.default_session.id] is h.service.default_session

    # Assert its other attachment (default_client) survived and remains intact
    assert not default_client.closed.is_set()
    assert default_client.ws.close_code is None
    assert len(h.service.default_session.attachments) == 1
    remaining_att = next(iter(h.service.default_session.attachments))
    assert remaining_att.principal.kind == "local"
    assert remaining_att.principal.uid == uid


async def test_revoke_frame_on_proxy_forbidden(harness):
    c = await harness.connect("proxy", headers={"X-WebUI-User": "alice", "X-WebUI-Session": "S1"})
    await c.hello("rich")
    await c.send("revoke", webui_session="S1")
    err = await c.expect(type="error")
    assert err["code"] == "FORBIDDEN"


async def test_frame_before_hello_is_version_error(harness):
    c = await harness.connect()
    await c.send("subscribe", topics=["alerts"])
    err = await c.expect(type="error")
    assert err["code"] == "VERSION" and err["close_code"] == 4426
    assert await c.wait_closed() == 4426


async def test_no_common_version(harness):
    c = await harness.connect()
    await c.send("hello", mode="thin", protocol=[2, 3])
    err = await c.expect(type="error")
    assert err["code"] == "VERSION" and err["supported"] == [1]
    assert await c.wait_closed() == 4426


async def test_malformed_before_handshake_4400(harness):
    c = await harness.connect()
    await c.raw("{nope")
    err = await c.expect(type="error")
    assert err["close_code"] == 4400
    assert await c.wait_closed() == 4400


async def test_bad_frame_after_handshake_is_not_fatal(harness):
    c = await harness.connect()
    await c.hello("rich")
    await c.raw(json.dumps({"v": 1, "type": "invoke", "id": "x1"}))  # no node
    err = await c.expect(type="error")
    assert err["id"] == "x1" and err["code"] == "BAD_REQUEST" and not err.get("fatal")
    await c.send("subscribe", topics=["alerts"])
    ev = await c.expect(type="event")
    assert ev["snapshot"] is True


async def test_session_limit(harnesses):
    h = await harnesses(sessions_per_socket=1)
    a = await h.connect()
    await a.hello()
    b = await h.connect()
    err = await b.expect(type="error")
    assert err["close_code"] == 1013
    assert await b.wait_closed() == 1013


async def test_sockets_are_unix_only_with_modes(harness):
    for p in (harness.config.local_sock, harness.config.proxy_sock):
        st = os.stat(p)
        assert stat.S_ISSOCK(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o660
    families = {s.family for srv in harness.server._servers for s in srv.sockets}
    assert families == {socket.AF_UNIX}
