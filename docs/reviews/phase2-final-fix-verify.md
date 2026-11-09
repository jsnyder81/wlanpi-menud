# Phase 2 final-review fix verification

Scope: the fixes for `phase2-final-review.md`, checked against the user decisions: fix all five findings, and M1 sub-item 3 gets a drafted follow-up only.
- wlanpi-webui `phase2/webui-menu-panel`: 33e7904 (M1), 21fbb2a (m2), 21536e3 (m3), and the branch rebuilt on `origin/main` (m4).
- wlanpi-menud `phase1/service`: 3310f6e (review docs), 182bf0b (follow-up draft).
- Workspace `verify-container`: dff04a3 (m1).

This review was read-only. This file is the only repo change. Mutants, the CI simulation and the extra e2e ran in the scratchpad or in throwaway containers.

**Verdict: REQUEST-CHANGES.** 1 item, small and limited to CI config. M1, m1, m3, m4 and the follow-up draft are verified. m2 is done locally, but its new CI job cannot pass on GitHub as written (see F1). Once F1 is fixed, Phase 2 is ready to merge toward the February release, with **wlanpi-menud `phase1/service` merged first**.

## Suite results (all run by me)

| Suite | Result |
| --- | --- |
| wlanpi-menud pytest (`.venv`) | **210 passed** |
| wlanpi-webui pytest, per file (host) | alerts 7, apps 25, auth **61** (+2 new), beacon 17, dashboard 28, menud 6, menu 3, network 22, profiler 63, session 44, settings 30, speedtest 20, system 29, ui 37, utils 65. **437 passed**, 0 failed. `test_app.py` collects 0, as before. |
| `tests/test_cli.py`, host under `setsid -w` | **25 passed** |
| `tests/test_cli.py`, clean debian:trixie (`fpms-verify:fixverify`, rebuilt from the current Dockerfile) | **24 passed, 1 skipped** (needs node), exit 0 |
| WebUI JS `npm test` | **17/17 pass**: 10 view_blocks tests and 7 reconnect tests |
| Committed container e2e (`verify-container/run_verification.py`, image rebuilt) | **22/22 pass** |
| Scratch e2e: committed e2e with Test 9 swapped for a real cross-device `POST /change_password` | **24/24 pass** |
| TS regen: `json-schema-to-typescript@16` against menud `phase1/service` schema | **diff empty** |
| CI regen step simulated: same script with menud `main` schema | **diff 32+/150-, exit 1** (see F1) |

## Item-by-item

### M1: password change and MAX_SESSION_AGE prune revoke menu sessions: VERIFIED

- Code: `wlanpi_webui/auth/auth.py:209-227` collects `pruned_sids` inside the lock and calls `revoke_menu_session` for each one after the lock is released, with try/except. `auth.py:537-547` does the same for `_revoke_user_sessions`, which `change_password` calls at `:614` on success. This matches the fix the final review asked for, items 1 and 2.
- Tests: `tests/test_auth.py:234` asserts that exactly alice's two sids are revoked, bob's sid is kept, and the registry is updated. `:282` checks that a raising menud does not break the password change. `tests/test_session.py:250` (`test_login_prunes_expired_records`) now asserts `"old" in revoked`.
- **Mutation check** (scratch `git archive` copies, `test_auth.py` and `test_session.py`):
  - Revoke removed from `_revoke_user_sessions`: `test_password_change_revokes_other_user_sessions` **FAILS**.
  - Revoke removed from the prune loop: `test_login_prunes_expired_records` **FAILS**.
  - Both removed: both tests **FAIL**.
  - 3 other failures (`test_active_session_reaches_app`, `test_facts_render`, `test_stats_fragment_has_stat_rows`) also fail in an *unmutated* scratch copy. They are path-dependent (`/etc/nginx/ssl` CA bundle) and have nothing to do with the mutation. In-repo they pass.
- **Container e2e**:
  - Committed Test 9 (`verify-container/run_verification.py:775-811`): carol's live rich `/ui/ws`, reached through real nginx, closes with **4401 "WebUI session ended"**. The next invoke gets nothing, and the old cookie then gets 401. Pass.
  - Test 9 calls `wauth._revoke_user_sessions("carol")` in-process (`:788-790`) instead of going through the route. To cover "another device's password change" end to end, I ran a scratch variant. It mints a *second* carol session (a different sid) and sends a real `POST /change_password` through nginx HTTPS, with only PAM stubbed. Result: 302, and device 1's socket closes 4401 and its cookie gets 401. **24/24 pass.** The route→revoke link is also pinned by the unit test above. Folding the real POST into Test 9 would be nice but is not required (N1).
- Sub-item 3 (no-HTTP-traffic window) is drafted only, as decided. See item 6.

### m1: exit-criteria e2e committed: VERIFIED

`dff04a3` folds the scratch e2e into `run_verification.py`:
- It runs the real Flask `create_app` on `/run/wlanpi_webui.sock` behind the real `install/etc/nginx` config, with only `account_still_allowed` and the PAM/systemd probes stubbed (`:405-420`).
- Test 6 has a TUI on `local.sock` (thin, `default`), alice rich (`new`), an alice thin mirror on `default`, and bob rich, all at once. Mirroring works both ways (`driven_by.self=false`), and both users' invokes run concurrently.
- Test 7 sends a **real `POST /logout` through nginx** (302). Both alice tabs close 4401. Bob stays open and usable. The TUI keeps driving the session, `default_id` is still in `service.sessions`, a new TUI attach gets the same `session_id`, and alice's old cookie gets 401.
- 22/22 pass.

### m2: generator pinned, CI regen-diff step: PARTIAL (F1)

- Pinned to `json-schema-to-typescript@16` in `package.json:7` and `scripts/generate-types.sh:18`, and the devDependency is `16.0.0`. A local regen against menud `phase1/service` gives an empty diff.
- The CI job exists: `.github/workflows/lint.yml:57-79` (`schema-types`) is wired into `slack-workflow-status.needs`. It **cannot pass on GitHub** as written. See F1.

### m3: backoff and Retry: VERIFIED

- `static/js/menu.js`:
  - 4401 (`:101-104`) and 1008 (`:106-110`) still `return` before any reconnect, so they remain hard stops. 1008 shows no Retry button.
  - 1006 now always calls `scheduleReconnect`, with the delay doubling 2→4→8→16→32→60 s and capped at 60 s (`scheduleReconnect`, `MAX_RECONNECT_DELAY`).
  - From the 2nd consecutive 1006 on, it shows "Menu unavailable." with a Retry button in both the rich and mirror panes.
  - Retry (`retryConnect`, delegated click on `.menu-retry-btn`) clears the timer, resets the delay and the counter, and connects at once.
  - A received frame or a non-1006 close resets both the delay and the counter.
- JS tests (`tests/js/menu_reconnect.test.mjs`, run against the real `menu.js` via `readFileSync`):
  - `:201` walks the full sequence 2000/4000/8000/16000/32000/60000/60000 and checks that Retry markup appears in both panes with a timer still scheduled.
  - `:270` checks that Retry reconnects at once, clears the timer and resets the backoff to 2 s.
  - `:401` checks that 4401 schedules no timer and does not overwrite the panes.
  - `:176` checks that 1008 schedules no timer.
  - The earlier counter-reset tests (`:298`, `:333`, `:374`) still pass.
- Nit (not blocking): each 1006 after the 2nd fires the "Menu unavailable." toast again, at up to one every 60 s. That is acceptable.

### m4: branch and history hygiene: VERIFIED

- wlanpi-webui: local `main` == `origin/main` == `66ebf68`. All 8 Phase 2 commits are on `phase2/webui-menu-panel`, based on `origin/main`.
- No commit message claims Cockpit retirement. The only message that mentions Cockpit is `4a0bc7f` "revert Cockpit removal", which is accurate.
- The net diff `origin/main..phase2/webui-menu-panel` over the Cockpit paths (`install/etc/cockpit`, `wlanpi_webui/cockpit`, `tests/test_apps.py`, `tests/test_dashboard.py`, the apps/dashboard partials) is **empty**, and `debian/changelog` has no Cockpit line.
- One remaining point: the *diff* of `6dd0a23` still removes `cockpit.conf` and adds a "Retire the Cockpit plugin path" changelog entry, which `4a0bc7f` reverts. No message claims it, and the net is zero, so this meets the criterion. Squash-merging the PR (or a fixup) would also keep that intermediate state off `main` (N2).
- wlanpi-menud: all 7 `docs/reviews/*.md`, including `phase2-cleanup-verify.md` and `phase2-final-review.md`, are tracked (3310f6e, 5553b06). The working tree is clean.

### 6: follow-up issue draft: VERIFIED, not filed

- `wlanpi-menud/docs/core-issues/06-close-no-http-traffic-revocation-window.md` (182bf0b) is headed **"DRAFT — not filed."**
- It covers idle timeout, account invalidation (sudo removed, `passwd -l`, password changed over SSH), and MAX_SESSION_AGE without HTTP traffic.
- It proposes Option A (menud caps WebUI attachments at MAX_SESSION_AGE) and Option B (periodic re-validation in the style of `_recheck_jwt`), and recommends A, then B.
- `gh issue list --search "revocation window"` finds nothing on `WLAN-Pi/wlanpi-webui` or `jsnyder81/wlanpi-menud`, and `WLAN-Pi/wlanpi-menud` does not exist. **Not filed.**
- Nit: the draft sits in `docs/core-issues/`, which otherwise holds wlanpi-core drafts. Consider `docs/issues/`.

## Findings

### F1 (must fix before merge): the `schema-types` CI job cannot pass on GitHub

- Where: `wlanpi-webui/.github/workflows/lint.yml:65-69`.
- Problem: the job checks out `repository: WLAN-Pi/wlanpi-menud` with no `ref:`.
  1. That repository does not exist (`gh`: "Could not resolve to a Repository with the name 'WLAN-Pi/wlanpi-menud'"; `git ls-remote` asks for credentials). The checkout step fails on every run.
  2. Even after menud is published there, the default branch would be used. menud `main` (`46697d7`) has none of the Phase 1 and 2 schema changes, `revoke` among them. I ran the job's exact script in a scratch clone against menud `main`'s schema: `frames.d.ts` 32+/150-, **`git diff --exit-code` returns 1**. Against `phase1/service` the job passes (rc 0).
- Impact: the job is red on every webui run, and it gates `slack-workflow-status`. The PR cannot go green, and the protection m2 asked for never actually runs.
- Fix (pick one):
  - (a) Point `repository:` at the menud repo that exists today and pin `ref:` to the menud commit or tag whose schema `frames.d.ts` was generated from. Bump the pin whenever the schema changes.
  - (b) Vendor `frames.schema.json` into wlanpi-webui, for example `protocol/frames.schema.json` with a "copied from menud@<sha>" header. The CI then regenerates from the vendored copy, and the schema bump shows up in the webui diff.

  Whichever is chosen, keep the regen step as it is. Rerun the job, or the `act`-style simulation above, and confirm exit 0.

### Not blocking

- **N1.** Committed Test 9 simulates the password change in-process (`run_verification.py:788-790`). The real route works, as my scratch variant showed (24/24). Swapping in the ~12-line real `POST /change_password` from a second minted session would pin the whole chain in one test.
- **N2.** Squash-merge the webui PR, or fold `4a0bc7f`'s Cockpit restore into `6dd0a23`, so `main` never carries a Cockpit-retired intermediate commit.
- **N3.** `package.json` pins the devDependency to `16.0.0`, but `npx …@16` resolves the newest 16.x. That is fine for a major-version pin. Use `@16.0.0` in both places if exact reproducibility matters.
- **N4.** A password change now makes one synchronous `local.sock` round trip per revoked sid (about 1 ms each, up to about 1.2 s each if menud hangs). This is acceptable for an interactive form.

## Merge readiness

The Phase 2 product code is **ready to merge toward the February release**:
- The security regression (M1) is closed and mutation-pinned.
- The exit criteria are pinned in a committed e2e against the real stack.
- Reconnect behaviour is fixed and tested.
- The branch history is clean.
- The remaining window (M1.3) is a drafted, unfiled follow-up, as decided.

The blocker is F1, CI only. Fix the `schema-types` checkout target and pin, see it pass, then merge **wlanpi-menud `phase1/service` first**, because webui's protocol and types depend on it, and then the wlanpi-webui `phase2/webui-menu-panel` PR. After that I would approve without another full review. Rerunning the CI job is enough.
