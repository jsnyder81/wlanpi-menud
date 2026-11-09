# Phase 2 final review: WebUI plugin

Scope: every Phase 2 commit.
- wlanpi-webui `66ebf68..bcbf7d0`: 209819e, 398ac01, b748c61, 5e3d270, bcbf7d0.
- wlanpi-menud `7ed4c06..e75988d` on `phase1/service`: d30dc6c, 7cfe793, 5553b06, e75988d.
- Workspace `verify-container`: 85adfa5, 42fd2c9, 2f9d8fd.

Checked against "FPMS Re-Architecture Plan.md": Phase 2, Authentication and authorization, View blocks, Two client modes and Sessions. I also read the full review history (`phase2-review.md`, `phase2-rereview.md`, `phase2-cleanup-verify.md`). This review was read-only: this file is the only repo change, and every experiment ran in the scratchpad or in throwaway containers.

**Verdict: REQUEST-CHANGES.** 0 blockers, 1 major, 4 minor.

All 7 exit checks pass, all suites are green, and the 3 punch-list notes are closed. The one major finding is a revocation gap. A server-side revocation without logout, such as a password change, does not reach wlanpi-menud, so a menu WebSocket with `admin` menu rights outlives the WebUI session. The fix is small (about 10 lines in `auth.py`). Once it lands, Phase 2 is ready to merge toward the February release.

## Suite results (all run by me in this review)

| Suite | Result |
| --- | --- |
| wlanpi-menud pytest (`.venv`) | **210 passed** |
| wlanpi-webui pytest, per file (host) | alerts 7, apps 25, auth 59, beacon 17, dashboard 28, menud 6, menu 3, network 22, profiler 63, session 44, settings 30, speedtest 20, system 29, ui 37, utils 65. All pass. `test_app.py` collects 0 tests, as before. |
| wlanpi-webui `tests/test_cli.py`, host (under `setsid -w`, node present) | **25 passed**, 3 out of 3 runs |
| `tests/test_cli.py`, clean debian:trixie (`fpms-verify:final`, with and without `-t`) | **24 passed, 1 skipped** (needs node), exit 0. No SIGHUP. |
| `tests/test_cli.py`, same image with `ps` removed | **23 passed, 2 skipped**. The procps guard skips `test_shell_owns_its_terminal`. |
| WebUI JS `npm test` | **16/16 pass**: 10 view_blocks tests and 6 reconnect tests |
| Committed container e2e (`verify-container`, image rebuilt from the current Dockerfile) | **8/8 pass** |
| Reviewer e2e: real Flask + real nginx + real menud, with a TUI and 3 browser tabs at once (scratch `final_e2e.py`, see criteria 1 and 2) | **18/18 pass**, plus one informational probe that found M1 |
| TS types: `json-schema-to-typescript@16 --unreachableDefinitions` into the scratchpad, diffed with `static/js/types/frames.d.ts` | **diff empty**, `revoke` included |

## Exit criteria

1. **The TUI and two browser tabs run at once against one service: PASS** (my e2e). The committed e2e never has more than one browser connected at a time (see m1), so I wrote a scratch e2e. It runs in the same `fpms-verify` image with the real `install/etc/nginx` config. The real Flask app (`create_app`) serves `/run/wlanpi_webui.sock`, only PAM is stubbed, and the real menud runs on fakecore. With everything connected at once:
   - The TUI is on `local.sock`, thin mode, `default` session.
   - Tab 1 is alice, rich mode, `new` session.
   - Tab 2 is alice, a thin mirror on `default`.
   - Tab 3 is bob, rich mode, `new` session.

   Results:
   - TUI input fans out to the mirror, with `driven_by.self=false`. Mirror input drives the TUI.
   - Tabs 1 and 3 both invoke `network.interfaces` and get results.
   - The three sessions are separate: two independent sessions and the default one.
2. **Logout ends that user's menu sessions and leaves the default session alone: PASS.**
   - A real `POST /logout` through nginx returned 302. It ran Flask `end_session`, which sent `revoke_menu_session` over `local.sock`.
   - Tab 1 and Tab 2, both alice's, closed with **4401 "WebUI session ended"**. The mirror attachment was dropped and its default session kept.
   - Bob's Tab 3 stayed open and usable.
   - The TUI kept driving the default session. `default_id` is still in `service.sessions`, and a new TUI attach gets the same `session_id`.
   - Alice's old cookie now gets 401 at `/ui/ws`.
   - The unit pins are `tests/test_auth.py` revoke tests (mutation-checked in the cleanup review). See M1 for the paths that are not logout.
3. **nginx `/ui/ws`: PASS.**
   - `auth_request /auth/check` (`install/etc/nginx/conf.d/wlanpi-menud.conf:3`).
   - `X-WebUI-User`/`-Session` are set from `auth_request_set` with `proxy_set_header` (`:14-15`), so client copies are dropped. The committed e2e test 4 shows that forged headers do not survive.
   - `/auth/check` is `internal` (`wlanpi_webui.conf:53-54`).
   - The only menud upstream is `unix:/run/wlanpi-menud/proxy/proxy.sock` (`:17`). grep finds no other non-webui `proxy_pass`.
   - menud reads the WebUI headers only in `_proxy_principal` (`wlanpi_menud/server.py:223-241`); `local.sock` ignores them.
   - `proxy.sock` is 0660 (`tests/test_auth.py:325`).
   - Origin check: `origin_ok` compares the Origin netloc to Host (`wlanpi_menud/auth.py:126-138`), and a mismatch closes 1008. Committed e2e test 3 covers it. A duplicate Host or WebUI header gives a clean FORBIDDEN/1008 (`server.py:228-234`).
4. **View blocks: PASS.**
   - `view_blocks.js:329-352` covers all 8 types (text, kv, table, list, qr, status, progress, confirm).
   - `renderFallback` (`:40-55`) prefers `block.text` and falls back to escaped JSON for unknown or malformed blocks.
   - Every interpolation I traced in `view_blocks.js` and `menu.js` goes through `escapeHtml`/`escapeAttr`. That matters because scan results carry SSIDs, which anyone nearby can set.
   - There is one JS test per type plus the fallback.
   - The generated types match the schema (diff empty), but nothing uses them (m2).
5. **Mirror-panel thin attach: PASS.** The committed e2e test 6 (TUI plus browser mirror sharing one `session_id`, input both ways) passes, and my e2e repeats it with a rich tab and a second user connected at the same time.
6. **Cockpit unchanged since 66ebf68: PASS.**
   - `git diff 66ebf68..HEAD -- install/etc/cockpit wlanpi_webui/cockpit tests/test_apps.py tests/test_dashboard.py templates/partials/apps_cards.html templates/partials/dashboard.html` is **empty**.
   - `cockpit.conf`, `cockpit.py` and `__init__.py` are tracked.
   - The only diff lines that mention Cockpit are context lines: the unchanged `/app/cockpit/` redirect next to the new `include`, and the nav `_nav` expression, which only gains a `menu` branch.
   - The changelog makes no retirement claim.
7. **Suites: PASS.** See the table above.

## Punch-list notes from phase2-cleanup-verify.md

1. **Real-uid SO_PEERCRED container test: CLOSED.** Verify-container `2f9d8fd`, test 7 (`run_verification.py:~720-760`):
   - It creates a real uid 1001 and runs the client through `sudo -u operator`, so the kernel's SO_PEERCRED carries 1001 with nothing monkeypatched.
   - It asserts the welcome `principal.uid == 1001`, `operator` in roles and `admin` absent.
   - It asserts the revoke error is `FORBIDDEN` with the "local admin principals" message, and that alice's live browser socket gets no frame in 0.4 s.
   - `sudo` is in the Dockerfile. Passed in my run.
2. **JS counter reset on a non-1006 close: CLOSED.** `bcbf7d0` adds `tests/js/menu_reconnect.test.mjs:225`: 1006, then 1000, then 1006 still reconnects. Test `:266` covers a frame followed by a non-1006 close. Both run against the real `menu.js` (`:115-117`) and pass.
3. **procps guard: CLOSED.** `tests/test_cli.py:338` has `@pytest.mark.skipif(not shutil.which("ps"))`. With `ps` removed, the test is skipped rather than failing (23 passed, 2 skipped). `verify-container/Dockerfile` also installs procps.

## Findings

### Major

**M1. Server-side WebUI session revocation that is not a logout never reaches menud, so menu WebSockets outlive revoked sessions.**
- Where: `wlanpi-webui/wlanpi_webui/auth/auth.py:530-533` (`_revoke_user_sessions`), called from the password-change success path at `:600`. Related: `app.py:225-252`, whose `end_session()` calls run only when *that* browser makes another HTTP request.
- Reproduction (my e2e, last step):
  1. carol opens a rich `/ui/ws`.
  2. `_revoke_user_sessions("carol")` runs, as it does on a successful password change.
  3. The socket gets **no close frame**, and a new `invoke network.interfaces` **still returns a result**.
- The same happens for every revocation that is detected lazily: idle timeout, `MAX_SESSION_AGE`, `account_still_allowed()` failing (left sudo, locked, password changed over SSH) and the boot-id check. Each one calls `end_session()`, and so `revoke_menu_session`, only on that cookie's next HTTP request.
- Impact: menud gives WebUI principals `admin` menu rights by default (`wlanpi_menud/config.py:42`). A WebSocket opened with a stolen cookie, or a forgotten tab, keeps admin-level device control (menu mutations) after the user changes their password to get rid of it.
  - That undoes the guarantee from wlanpi-webui #175/#177 ("end sessions when the account changes"), which the CLI keeps because it rechecks the registry on every request.
  - The plan states the logout path explicitly, and that path works. This is the long-lived channel bypassing the WebUI's other revocations.
- Fix (small):
  1. In `_revoke_user_sessions`, collect the sids being deleted and call `revoke_menu_session(sid)` for each, after the registry write and outside the lock. Use the same try/except as `end_session`.
  2. Also revoke the sids pruned for `MAX_SESSION_AGE` in `_update_sessions` (`auth.py:~214-216`).
  3. To close the idle/account-check window without HTTP traffic: either have menud cap WebUI attachments at `MAX_SESSION_AGE`, or have it re-validate through the WebUI periodically, as `_recheck_jwt` already does for JWTs (`server.py:243-258`). A follow-up issue is acceptable for this item. Items 1 and 2 should land before merge.
- Tests: a `test_auth.py` case asserting that a password change calls `revoke_menu_session` for each of the user's other sids, and the e2e step above turned into an assertion.

### Minor

**m1. The committed e2e does not exercise the Phase 2 exit criterion as written** (`verify-container/run_verification.py`).
- Test 6 (`:651-716`) has a TUI and *one* browser. The criterion is "the TUI and two browser tabs run concurrently".
- Test 8 (`:765-791`) calls `revoke_menu_session` directly against a fake `/auth/check` server (`:47-115`), instead of a real `/logout`. It also does not assert that the default session, a TUI or another user's tab survive.
- My scratch e2e (`/tmp/claude-1000/-home-jakesnyder-source-wlanpi-fpms-rearchitect/1691b291-2263-496a-95b6-94c295f8e97d/scratchpad/e2e/final_e2e.py`, 18/18) shows the real stack does all of this. It runs the real Flask `create_app` behind nginx with PAM stubbed, and mints sessions through `_login_session`.
- Ask: commit it, or fold it into `run_verification.py`, so the exit criterion stays pinned in CI.

**m2. The generated TS types are not used, and the generator is unpinned.**
- No JS file references `types/frames.d.ts`: no JSDoc `@typedef`/`import()` and no `tsc --checkJs`. The types document the protocol, but nothing type-checks against them.
- `package.json:7` and `scripts/generate-types.sh:18` run `npx --yes json-schema-to-typescript` with no version and no lockfile, so a future major version can silently change the output. Today's output matches v16.
- Ask: pin it (`json-schema-to-typescript@16`), add a CI step that regenerates and runs `git diff --exit-code`, and optionally add `// @ts-check` with JSDoc imports in `menu.js` and `view_blocks.js`.

**m3. Two 1006 closes in a row disable the menu until a page reload** (`static/js/menu.js:105-113`).
- A menud restart (a package upgrade) or a short Wi-Fi drop gives two 1006 closes, nginx 502, then 1006. After that the panel stops for good with "Menu unavailable."
- That is better than the earlier infinite loop, but it is harsh for an appliance UI people leave open.
- Ask: back off exponentially with a cap (for example 2, 4, 8 … 60 s) and add a "Retry" control. Keep the hard stop for 1008 and 4401.

**m4. Branch and history hygiene before merge.**
- wlanpi-webui has the 5 Phase 2 commits **directly on local `main`** (`main...origin/main [ahead 5]`). WLAN Pi workflow wants a feature branch and a PR.
- `398ac01`'s subject still says "retire Cockpit", although `b748c61` reverted that. Move the work to a branch, and squash or reword so the history does not claim a retirement that never shipped.
- In wlanpi-menud, `docs/reviews/phase2-cleanup-verify.md` is untracked. Commit it with this file.

### Not blocking, no action required

- `end_session` (`auth.py:300-305`) makes a blocking `local.sock` round trip on every session end. It does not depend on whether the user ever opened the menu, and it creates a throwaway `new` session because the helper's hello has no `session`. That costs about 1 ms locally, and at most about 1.2 s if menud hangs. It is acceptable. A cheaper path is possible later.
- `origin_ok` accepts a missing `Origin` (`wlanpi_menud/auth.py:128-129`), also on the WebUI-header path. That is fine: cross-site WebSocket hijacking needs a browser, and browsers always send Origin. Non-browser cookie theft is out of scope.
- The `kv` secret values are in the DOM, hidden (`view_blocks.js:116`). The secret-masking toggle needs them there.
- `cli.py:99-130` (the SIGHUP race fix) is correct. In production under gunicorn the worker is not a process-group leader, so `setsid` does not fork and the fallback path is not used.
- `link.sh` has no trailing newline (pre-existing).

## Overall assessment

Phase 2 quality is good.
- The protocol addition (`revoke`) is small, schema-validated and documented in PROTOCOL.md/README, including the accepted local-admin semantics.
- The nginx trust boundary is correct and tested against a real nginx.
- The browser client escapes consistently and handles 4401, 1008 and 1006.
- Cockpit is untouched.
- The review loop closed every earlier finding with tests that were mutation-checked.
- Each suite passed on every run, and the real Flask→nginx→menud logout path, which no earlier review exercised end to end, works.

The remaining gap is M1. It is a security-property regression in exactly the area Phase 2 owns (WebUI session lifetime and menu access), and it is cheap to fix. With M1 items 1 and 2 fixed and tested, and m1/m4 ideally handled in the same pass, I would approve Phase 2 for the February release. m2 and m3 can follow.
