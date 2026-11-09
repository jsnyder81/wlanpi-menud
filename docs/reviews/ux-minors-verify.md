# UX round minors 1-3: verification

**Verdict: APPROVE.** Blockers 0, major 0, minor 0, nits 2. Checked against `ux-round-review.md` m1-m3.
Commits: menud `56b9cc2`, webui `e5e803f`, workspace `fccf36b`. Read-only except this file; all mutations were made in scratch copies or read-only bind-mount overlays.

## Suites (all re-run by me)

| Suite | Result |
| --- | --- |
| menud pytest (.venv) | 215 passed |
| menu-tui pytest | 23 passed |
| webui pytest | 482 passed |
| webui JS (`node --test tests/js/*.test.mjs`) | 23 passed, 0 failed |
| Container e2e (`fpms-verify:uxminors`, rebuilt from current Dockerfile with nodejs) | 25/25 passed, incl. 5b, 5c, 6e |

## m1: e2e click-to-view drives menu.js: FIXED
`verify-container/drive_menu_click.mjs` fetches the real `/menu` page via nginx/Flask, loads the real `view_blocks.js` and `menu.js` into a node `vm` context with a mock DOM and a live WebSocket to menud, then dispatches a click on `#menu-panel`. The click goes through the real delegated handler to `handleNodeClick` (`menu.js:~442`).
Mutation checks (read-only overlay of the mutated `menu.js` onto `/src`):
- A: replaced `invokeNode(node.id, {});` (`menu.js:446`) with a comment. Test 5b FAILED (`table=False`), 24/25.
- B: `if (!hasParams && !hasConfirm)` changed to `if (false)` (always go through the detail pane). Test 5b FAILED, 24/25.
Both mutations now fail the e2e, which the old raw-frame test could not do.
Caveat (not a defect): the DOM is a hand-written mock, not a browser. That is adequate for the "drives menu.js" bar.

## m2: stale onclose guard: FIXED
`menu.js:~104` `ws.onclose` now starts with `if (this !== ws || ws !== currentSocket) return;`. The same guard is on onopen, onmessage and onerror. New JS test `menu_mode.test.mjs` "guards against stale ws.onclose..." overrides `ws1.close` to defer the close, switches mode, then fires `ws1.onclose` late, including 1006. It asserts the status stays "Active (thin)" and no new timer is scheduled.
Mutation checks (scratch copy, `menu_mode.test.mjs`; baseline 6/6 pass):
- Guard removed from onclose: 5 pass, 1 FAIL (the stale-close test).
- Guard removed from all handlers: the same single failure.

## m3: nav visibility guard pinned: FIXED
`tests/test_nav.py::test_nav_visibility_guard` covers three cases, each expecting FORBIDDEN: a route-gated hidden branch (`utils.cloud`), a hidden leaf (`utils.cloud.mist`), and a mode-filtered branch (`apps` in hotspot). It also asserts the cursor did not move.
Mutation check (full scratch copy, `service.py:404-405` guard deleted): baseline 13/13 pass; mutated gives 1 FAIL (`test_nav_visibility_guard`, TimeoutError because nav succeeds, no error frame arrives), 12 pass.

## Nits (non-blocking)
1. The `onopen`, `onmessage` and `onerror` stale guards (`menu.js:~85,92,138`) are not pinned. Removing each one individually leaves the JS suite green. Only the onclose guard (the stated m2 scope) is tested.
2. m4 from the prior review (TS typegen re-run, and no `builtin.json` branch having a `view`) was out of scope here and is unchanged.
