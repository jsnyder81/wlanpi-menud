# Phase 2 cleanup verification

Scope: menud `e75988d`, webui `5e3d270`, verify-container `42fd2c9`, checked against `phase2-rereview.md` (F1, F2, F3, m5). Read-only except this file. All mutations were made in scratch copies under the scratchpad; no repo file was changed.

**Verdict: APPROVE** (0 blocker, 0 major, 3 minor non-blocking notes).

## Suite results (all re-run by the reviewer)

| Suite | Result |
| --- | --- |
| wlanpi-menud pytest (`.venv`) | **210 passed** |
| wlanpi-webui pytest, per file | all pass: alerts 7, apps 25, auth 59, beacon 17, **cli 25**, dashboard 28, menud 6, menu 3, network 22, profiler 63, session 44, settings 30, speedtest 20, system 29, ui 37, utils 65 (= 456; `test_app.py` collects 0, as before) |
| WebUI JS `npm test` (`node --test tests/js/*.test.mjs`) | **14/14 pass** (was 10; +4 reconnect tests) |
| Container e2e (`fpms-verify`, debian:trixie + real nginx) | **7/7 pass** |
| `test_cli.py` in clean debian:trixie (with and without `-t`) | **24 passed, 1 skipped (needs node)**, 8/8 consecutive runs. Previously SIGHUP exit 129. |

## F1 – revoke-auth tests: mutation checks (menud, real Service, real local.sock/proxy.sock)

Targeted `pytest tests/test_auth.py -k revoke` (5 tests; all pass on baseline). Each mutation was applied to `wlanpi_menud/service.py` in a scratch copy.

| Mutation | Result |
| --- | --- |
| Drop the admin check (`if a.socket_kind != "local" or "admin" not in roles` → `if False`) | **FAILS** 3 tests (`..._non_webui_principal_forbidden`, `..._admin_operator_and_default_session`, `..._on_proxy_forbidden`) |
| Allow revoke over proxy.sock (drop the `socket_kind` clause) | **FAILS** `test_revoke_frame_on_proxy_forbidden` (`test_auth.py:274`) |
| `revoke_webui_session` ignores the session id (closes every WebUI session) | **FAILS** `test_revoke_frame_closes_matching_sessions_only` (`:165`) |
| Revoke is a no-op | **FAILS** 2 tests |
| Revoke also drops the default session (remove BOTH guards: `revoke_attachment` `if not a.session.default` and `drop_session` `if s.default: return`) | **FAILS** `test_revoke_admin_operator_and_default_session` (`:257`) |
| Remove only ONE of those two default guards | survives, **by design**: the guards are redundant (defence in depth), so either alone is an equivalent mutant. Not a test gap. |

The previous vacuity is gone. Operator and admin attachments are now on the same `Service`, so `assert not s2.closed.is_set()` after the operator's FORBIDDEN is meaningful (it fails under the admin-check mutation). The default-session case now attaches a WebUI mirror (`S_MIRROR`) to the default session and asserts the session object stays in `service.sessions` with the other attachment alive.

**Minor note 1 (non-blocking) – identities are not literally SO_PEERCRED-distinct.** `test_auth.py:186-193` monkeypatches `server.peer_credentials` to call the real `getsockopt(SO_PEERCRED)` and then override the uid for the "operator" connection. Sockets, Service and the allowlist→roles mapping are real, and the real SO_PEERCRED path is covered by `test_local_peercred_allowed/refused` and `test_local_group_allowlist`. A second genuine uid would need root (`os.setuid` in a forked client), which the container e2e could do, since it runs as root. Optional follow-up only.

## F2 – 0.2 s read window

Fixed by documentation. `wlanpi_webui/menud.py:78-82` docstring now states that there is no success reply, silence means accepted, the 0.2 s window only catches an immediate error frame, and late replies do not block logout. Matches the code at `menud.py:~117-135`. PASS.

## F3 – JS reconnect tests

`tests/js/menu_reconnect.test.mjs` loads the **real** `static/js/menu.js` into a `vm` context and mocks only the browser environment (WebSocket, DOM, timers). The tests assert on scheduled timers and rendered output, so they are not tautological. Mutations of `menu.js` in a scratch copy:

| Mutation | Result |
| --- | --- |
| 1008 handler removed (falls through to reconnect) | **FAILS** test 1 |
| 1006 threshold 2 → 3 | **FAILS** test 2 |
| 4401 handler removed | **FAILS** test 4 |
| Frame receipt no longer resets the counter (`menu.js:86`) | **FAILS** test 3 |
| Non-1006 close no longer resets the counter (`menu.js:116`) | survives |

**Minor note 2 (non-blocking):** nothing pins the reset on a non-1006 close (`menu.js:115-117`). One more test (1006, then 1000, then 1006 still reconnects) would cover it.

## m5 – SIGHUP root cause

Established with evidence and **really fixed** (`wlanpi_webui/cli/cli.py:99-130`).

- **Mechanism:** `Session.close` called `os.killpg(os.getpgid(proc.pid), SIGHUP)`. `setsid --ctty` forks when its caller is a process-group leader, so right after `Popen` the child pid is still in the *caller's* process group. Reproduction in clean trixie (`/tmp/claude-1000/-home-jakesnyder-source-wlanpi-fpms-rearchitect/4728fdac-cfe5-4e17-a84b-f20496e03acb/scratchpad/race.py`): immediately after `Popen(["setsid","--ctty",...])`, `os.getpgid(child) == os.getpgrp()` in **200/200** attempts. So a prompt `close()` SIGHUPs the test runner's own group. It passes on the host only because the shell is usually slow to be closed.
- **Before/after, same clean trixie container, same inputs:** pre-fix `cli.py` (from `5e3d270^`) gives `Hangup`, exit 129. Post-fix gives 24 passed, 1 skipped, with and without `-t`, 8/8 repeated runs.
- The fix refuses to killpg its own group: it waits up to ~100 ms for the child to detach, then falls back to `proc.send_signal` / `proc.kill` (also on the SIGKILL path).

Repro (from the repo root):
```sh
docker run --rm -v $PWD:/src:ro debian:trixie sh -c 'apt-get update -qq; apt-get install -y -qq python3 util-linux procps; cd /src/wlanpi-webui && PYTHONPATH=.:.venv/lib/python3.13/site-packages python3 -m pytest -q -p no:cacheprovider tests/test_cli.py'
```
To see the old failure, swap in `git show 5e3d270^:wlanpi_webui/cli/cli.py`.

**Minor note 3 (non-blocking):** `tests/test_cli.py::test_shell_owns_its_terminal` (`:356`) fails without `procps` (`ps`) installed. This dependency is undocumented. Add procps to the test docs/CI image, or skip the test when `ps` is missing. `verify-container/Dockerfile` already has it (`42fd2c9`).

## Conclusion

F1, F2, F3 and m5 are resolved. The mutation checks show the revoke tests and JS tests fail when the asserted behaviour is reverted (the single surviving default-guard mutant is equivalent by design). There are no required changes.
