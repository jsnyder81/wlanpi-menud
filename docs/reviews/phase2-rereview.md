# Phase 2 re-review

Scope: fix commits wlanpi-menud `7cfe793` (and `5553b06`, docs only) and wlanpi-webui `b748c61`, checked against `phase2-review.md` and the user-final decisions (M1 accepted as-is, M2 Cockpit fully restored). Read-only except this file.

**Verdict: REQUEST-CHANGES**. 0 blockers, 0 major, 3 minor. M2, m1, m2, m3 and m4 are fixed and verified. M1 is accepted, and the docs and error text are correct. The remaining items are the weak M1 test pins and the undocumented `test_cli.py` result (m5).

## Test runs (all executed by the reviewer)

| Suite | Result |
| --- | --- |
| wlanpi-menud pytest (`.venv`) | **210 passed** (was 208) |
| wlanpi-webui pytest, per file, except `test_cli.py` | all pass (alerts 7, apps 25, auth 59, beacon 17, dashboard 28, menud 6, menu 3, network 22, profiler 63, session 44, settings 30, speedtest 20, system 29, ui 37, utils 65). `test_app.py` collects 0 tests, as before. |
| WebUI JS (`node --test tests/js/*.test.mjs`) | 10/10 pass. Note that `node --test tests/js` (a directory) fails with MODULE_NOT_FOUND; use the `package.json` glob. |
| Container e2e (`verify-container`, debian:trixie + real nginx, run with `-v repo:/src:ro`) | **7/7 pass** |
| `test_cli.py` in a clean debian:trixie container (with and without `-t`) | **still killed by SIGHUP**, exit 129, after 6 of 25 tests. See m5. |
| m3 reviewer socket test (scratchpad `test_dup_host.py`, raw HTTP against the real proxy.sock server) | duplicate `Host` and duplicate `X-WebUI-User` pass, see below |
| m4 simulation in the container | pass, see below |

## Decision checks

- **M1 (accepted as-is): PASS with a test-quality note.**
  - The error text now reads "revoke is restricted to local admin principals on local.sock" (`wlanpi_menud/service.py:395`).
  - `protocol/PROTOCOL.md` ("WebUI logout and revocation") and `README.md` document the local-admin semantics (any local admin uid, including `wlanpi`, may revoke), that it is DoS-only with no privilege gain, and that the default session is never ended by revoke. That matches the code: `revoke_attachment` only drops non-default sessions (`service.py:336-346`).
  - New test `tests/test_auth.py:178` pins admin revoke (closes the target with 4401) and the operator FORBIDDEN with the new message. See finding F1 for the assertions that are weaker than they look.
- **M2 (Cockpit fully restored): PASS.**
  - `git diff 66ebf68..HEAD --stat` over `install/etc/cockpit`, `wlanpi_webui/cockpit`, `tests/test_apps.py`, `tests/test_dashboard.py`, `tests/test_menu.py`, `apps_cards.html` and `dashboard.html` is **empty**. `cockpit.conf`, `cockpit.py` and `__init__.py` are tracked again.
  - The `:9090/app/cockpit/` redirects are unchanged. The only diff to `wlanpi_webui.conf` is the +3 `/ui/ws` lines.
  - `debian/changelog` has no retirement claim (the 1.21.19 entry only adds the Menu panel). The Menu panel is intact (`test_menu.py` 3 passed, the JS tests pass, and the e2e passes).
  - `tests/test_menu.py` is a new file relative to 66ebf68 (the Menu blueprint tests). It no longer contains `test_cockpit_deprecation`.
- **m1: PASS.** `revoke_menu_session` (`wlanpi_webui/menud.py:74-141`) reads the reply and logs a warning for an `error` frame, a handshake failure, a timeout or any exception. It returns False and never raises. `tests/test_menud.py` covers the error reply and the timeout, and `test_auth.py` covers the raising-helper case. See F2 for the design note.
- **m2: PASS (code), not unit-tested.** `static/js/menu.js` `onclose` stops on 1008 with a "Menu unavailable (policy violation / origin mismatch)" fallback, and on the second consecutive 1006 with "Menu unavailable." The counter resets on any received frame. 4401 behaviour is unchanged. No JS test covers the reconnect logic (F3).
- **m3: PASS (verified).** A raw-socket test against the real proxy.sock server:
  - Duplicate `Host`: handshake completes, then `{"type":"error","code":"FORBIDDEN","message":"duplicate 'Host' header","fatal":true,"close_code":1008}`, with a clean close and no traceback or MultipleValuesError.
  - Duplicate `X-WebUI-User`: the same clean FORBIDDEN/1008, and the server stays usable.
  - Duplicate `Origin` is rejected by the websockets library with `400 Bad Request` before the handler runs. That is also a clean refusal.
  - The new `test_proxy_duplicate_host_header_closes_1008` (`tests/test_auth.py:70`) covers the Host case.
- **m4: PASS (verified in the container).** I ran the `debian/postinst` block (lines 67-83) and `install/etc/nginx/link.sh` with a simulated `remove`: unit file gone, `/etc/wlanpi-menud/config.toml` still present, and the dpkg status is not "ok installed".
  - Menud installed: the link is created.
  - Removed, then a webui upgrade (postinst): the stale link is removed and not re-created.
  - Removed, `link.sh`: no link.
  - `tests/test_install.py` covers menud's postrm unlinking.
- **m5: NOT DONE.** See below.

## Findings

### Minor

**F1. The new M1 test pins are partly vacuous** (`wlanpi-menud/tests/test_auth.py:202-212`).
- `h_op` is a second, separate `Harness`/`Service`, so `assert not s2.closed.is_set()` (`:209`) can never fail. It does not show that an operator's revoke leaves WebUI session S2 alive. Do it on the same service (a second attachment under a different principal), or drop the line.
- The default-session assertions (`:187`, `:212`) are trivially true, because no WebUI-principal attachment is bound to the default session. Attach a `webui_session="S1"` client with `session="default"`, then revoke S1. Assert that the connection closes 4401 while `default_session.id` stays in `service.sessions` and other attachments survive.
- Neither gap hides a code bug (I read `revoke_attachment`, which does keep the default session), but the user's requirement was that tests pin these properties.

**F2. `revoke_menu_session` treats 0.2 s of silence as success** (`wlanpi_webui/menud.py:117-135`). The protocol has no success reply, so the read window is a design necessity, but a slow FORBIDDEN could be logged as success. This is low risk, since the reply is local and immediate. A one-line comment saying "no reply = accepted, best effort" would document it. No change is required.

**F3. m2 reconnect logic has no automated test** (`static/js/menu.js` `onclose`). The JS suite covers `view_blocks` only. A small node test with a fake WebSocket would pin the 1008 and 1006x2 stop behaviour.

**m5 (still open). `tests/test_cli.py` is not documented, and the SIGHUP is not specific to the first reviewer's host.**
- I reproduced it in a clean `debian:trixie` container, with and without `-t`: the test process receives SIGHUP (exit 129) during `test_output_reports_whether_the_shell_is_alive`, after 6 passing tests. I found no note about it in the READMEs, TROUBLESHOOTING.md, CONTRIBUTING.md or INTERNALS.md.
- The file and `wlanpi_webui/cli` are unchanged since 66ebf68 (the Menu work does not touch them), so it is pre-existing and not a regression from this work.
- `wlanpi_webui/cli/cli.py:101` (`close()`: `os.killpg(os.getpgid(self.proc.pid), signal.SIGHUP)`) is the likely hazard. With `setsid --ctty` the pid may be a short-lived wrapper, so the group lookup can hit the test runner's own group. I did not root-cause it.
- Ask: document the result (a CI log or note) or fix the test or `close()`.

## Not blocking

- The e2e `/auth/check` is still a fake WebUI server (unchanged from the first review).
- `link.sh` has no trailing newline (pre-existing style).
