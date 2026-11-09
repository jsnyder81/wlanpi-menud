# UX round 2 review

Scope: menud `280294b..151fccc` (phase1/service), webui `e5e803f..cc00c0a` (phase2/webui-menu-panel), tui `e0ddb69..9fabfc9` (main), plus verify-container `08236a4`. Checked against `FPMS Re-Architecture - UX TODO.md` items 5-10 and the round-2 verification bar. Read-only except this file. Mutants ran on scratchpad copies.

**Verdict: REQUEST-CHANGES.** Blockers 1, major 0, minor 5.

## Suites (all run by me)

| Suite | Result |
| --- | --- |
| menud pytest | 216 passed |
| menu-tui pytest | 24 passed |
| webui pytest | 482 passed |
| webui JS `node --test tests/js/*.test.mjs` | 26 passed |
| Container e2e (`fpms-verify:final`, `run_verification.py`) | 28/28 passed |
| Protocol schema diff `280294b..HEAD` (`frames.schema.json`, `PROTOCOL.md`) | empty; the webui vendored copy is identical |

## Blocker

**B1. `menu.js:414` throws in a real browser, so no result card is ever rendered.**
`pruneToLatestResult` ends with `container.children.indexOf(newCardEl) === -1`. In a browser `children` is an `HTMLCollection`, which has no `indexOf`. That branch runs whenever there is no `#rich-previous-result` yet. The previous-result `<details>` is only created when an active card already exists, and that needs a first card to have been appended. So the first card is never appended, and every later call hits the same branch. The TypeError is swallowed by the frame handler's try/catch, so the Results column stays empty for every view and job.

The JS tests and `drive_menu_click.mjs` use `children = []` mocks, which have `indexOf`, so everything passes. I confirmed the failure two ways:
- Reusing the unit harness with `children` replaced by a Proxy that hides `indexOf` (the `HTMLCollection` shape): the first view leaves `results.children.length` at 0, and Item 7 fails (`0 !== 1`).
- Running `drive_menu_click.mjs` with the same Proxy in the container: e2e 5b, 5d and 5e fail (25/28), because the table never renders.

This breaks round-1 item 2 (click-to-view) as well as item 7, in every real browser.

Fix: replace the check with `newCardEl.parentNode !== container` (or drop it, since `appendChild` of an existing child just moves it). Make the test mocks `HTMLCollection`-faithful (array-like without array methods), so the tests can catch this.

## Checks against the brief

- **(5) One Network interfaces entry: OK, with notes.**
  - `network.wlan_interfaces` is gone from `builtin.json`, `share/menu.d/10-network.json`, the handlers and the sample frames. It remains only in `fakecore.py`, the docs, and `poc_action_map.json` (the map points it at `network.interfaces`).
  - Parity rows 1-2 are updated: row 2 is marked "merged" with "(reserved)".
  - The container e2e 5d asserts Ethernet, WLAN and adapter sub-rows in the frame data. `test_jobs.py` covers the handler.
  - Notes: m1 and m2 below. I did not verify the table on the panel surface; `view.menu.json` was only updated for the label change. The TUI renders the generic table block.
- **(6) Zebra stripes: OK.** `uk-table-striped` is absent from `view_blocks.js` and all templates; it remains only in vendored `uikit*.css`. `app.css:515-523` has hover rules plus a transparent-odd-row neutraliser. Both tables keep `uk-table-hover`.
- **(7) Results pane bounded: logic OK, broken in browsers (B1).** With array-based mocks, a scripted 20-click loop in the container (altering `drive_menu_click.mjs` in the scratchpad) ends at 2 children (the active card plus the previous-result `<details>`). With browser-faithful `children` the pane never renders, so the bound is moot.
- **(8) Rich + mirror simultaneously: OK in code, partially tested.** `openSideMirror` (`menu.js:914`) opens a second socket with hello `thin/default`, independent of `currentMode`. Handlers are guarded by socket identity. `closeSideMirror` closes only that socket. The JS test asserts the second hello. Container e2e 6f runs two live connections for the same cookie, sends frames concurrently, closes the mirror, and the rich socket keeps working. Gaps: m3 and m4.
- **(9) Device strip: OK in code.** Rich mode subscribes to `device.summary` and `alerts` in `onWelcome` (`menu.js:255`), and the strip renders from `event` frames (`menu.js:265-323`) using the kv renderer. The JS test covers it. Note m5.
- **(10) TUI coalescing: OK.**
  - `_mark_dirty` renders the leading frame at once and then flushes at most once per 60 ms from `current_view` (`app.py:226-237`). `view` and `event` frames are coalesced; other frames render at once.
  - The burst test (5 views, at most 2 refreshes, final content "Content 5") passes.
  - Mutant A (`_mark_dirty` renders immediately): the burst test fails (`Expected <= 2 refreshes`).
  - Mutant B (timer flush is a no-op, so the latest state is never rendered): the burst test fails on the "Content 5" assertion.
  - Input latency is unregressed: `on_key` reaches `await self._send_frame(build_input(...))` (`app.py:1011`) without awaiting any render, and the coalescer is synchronous. There is no separate stale-state test (m6).
- **Protocol: no frame changes this round.** The diff of the schema and PROTOCOL.md is empty, and the vendored schema matches.

## Minor findings

- **m1.** `network.adapter_summary` ("Adapters") is still a separate menu entry next to Interfaces. The feedback called out "interfaces and adapters" as redundant. Sub-rows now carry MAC/Type/Driver, so the Adapters screen duplicates part of them. Decide whether to drop or keep it; the TODO's "one entry" reading is satisfied only for WLAN interfaces.
- **m2.** The grouping is a repeated "Section" column (`handlers/network.py`), not group header rows. It works, but the repeated "Ethernet" or "WLAN" label on every row is noisy; consider blank repeats.
- **m3.** E2e 6f does not assert the driven-by indicator or that logout revokes the side-mirror socket (it is closed before test 7). 7b and 7c cover the rich and mirror sockets, which use the same revoke mechanism, but the brief's "logout revokes both" is not asserted on the side socket.
- **m4.** Calling the same node twice after the card has been folded into `<details>` reuses the folded element by id (`menu.js:445`, `:702`). `insertBefore` then moves it out, and the previous-result summary becomes stale and empty. This is cosmetic.
- **m5.** The `device.summary` payload carries `ip`, not `eth0` or `wlan0` (`poller.py:145-160`). The strip handles both, but per-interface IPs never show. The strip has no e2e coverage.
- **m6.** There is no separate "latest-state" test. The single burst test covers both coalescing and latest-state through the final-content assertion; mutant B fails it.

## Reproduce

```
cd wlanpi-menud && .venv/bin/python -m pytest -q
cd wlanpi-menu-tui && .venv/bin/python -m pytest -q
cd wlanpi-webui && .venv/bin/python -m pytest -q && node --test tests/js/*.test.mjs
docker run --rm -v $PWD:/src:ro fpms-verify:final python3 /src/verify-container/run_verification.py
```
