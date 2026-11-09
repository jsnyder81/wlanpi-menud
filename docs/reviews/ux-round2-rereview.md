# UX round 2 re-review

Scope: fix commits menud `524181b`, webui `b851d42`, tui `a5383fd`, verify-container `ecdfaf0`, checked against `ux-round2-review.md`. Read-only except this file. Mutants ran on scratchpad copies.

**Verdict: APPROVE.** Blockers 0, major 0, minor 2 (non-blocking observations).

## Suites (all re-run by me)

| Suite | Result |
| --- | --- |
| menud pytest | 216 passed |
| menu-tui pytest | 25 passed (was 24; +1 latest-state test) |
| webui pytest | 482 passed |
| webui JS `node --test tests/js/*.test.mjs` | 27 passed (was 26; +1 m4 test) |
| Container e2e (`fpms-verify:final`) | 30/30 passed (was 28; +5f, +7c2) |

## Findings against the previous review

- **B1 (`menu.js:414`): fixed, and the mocks are faithful.**
  - The check is now `newCardEl.parentNode !== container && newCardEl.parentElement !== container`.
  - The JS tests and `drive_menu_click.mjs` give `children` an `HTMLCollection` shape with no `indexOf` (a Proxy exposing only `length`, `item`, `namedItem`, indexes and the iterator).
  - Mutant 1: reverting only the :414 line to `children.indexOf(...)` makes the JS suite fail, 12 of 27.
  - Mutant 2: the same revert in a scratch tree run through the container e2e gives 26/30. Tests 5b, 5d, 5e and 5f fail. The previous review had 5b, 5d and 5e failing the same way.
  - Real tree: 5b, 5d and 5e are green.
- **m1: fixed.** `network.adapter_summary` is gone from `10-network.json`, the handler, and the nav tests. The parity row for Network > Interfaces notes that "Adapters" was dropped.
- **m2: fixed.** `handlers/network.py` shows the "Ethernet" or "WLAN" label only on the first row of each group. Subsequent rows have an empty section cell.
- **m3: fixed.**
  - E2e 6f asserts `driven_by.self`, surface `wlanpi-webui-side-mirror`, and principal alice on the side socket.
  - New 7c2 asserts the side-mirror socket gets close 4401 on logout.
- **m4: fixed.** `menu.js` drops a stale `id` from a folded card that is no longer a child of the container and creates a fresh element, for both view and job cards. The new test, "Item m4: re-invoking folded node creates fresh card", fails when those two hunks are removed.
- **m5: fixed.**
  - `poller.py` fills `eth0` and `wlan0` from `addr_info`.
  - E2e 5f asserts the strip shows "Eth0 IP" and 192.0.2.10. It fails under the :414 mutant.
- **m6: fixed.** `test_render_coalescing_latest_state` records every painted `#screen-view` and asserts that "Content 5" is painted and that "Content 2", "Content 3" and "Content 4" never are. Both mutants (render immediately, and a no-op timer flush) fail it and the burst test. The unmutated tree passes both.

## Protocol diff: additive-only

The diff of `524181b` for `protocol/` is two schema lines, one PROTOCOL.md note, and the invalid sample.

- `frames.schema.json` adds only optional `eth0` and `wlan0` (`["string","null"]`) to `topic_device_summary`. Nothing is removed, required or loosened.
  - A payload with `eth0` and `wlan0` validates.
  - The old payload (hostname only) still validates.
  - `additionalProperties: false` is intact.
- `invalid/frames/event-summary-unknown-key.json` now uses `unknown_key`. The schema still rejects it with 1 error, so the strictness guard is still tested.
- `PROTOCOL.md` documents the addition: "Device summary: optional `eth0` and `wlan0` ... (additive v1; old clients ignore them)".
- The webui vendored `protocol/frames.schema.json` is identical to the menud source (`diff` empty). Its README source commit is updated to `524181b`.
- `frames.d.ts` was regenerated with the pinned `json-schema-to-typescript@16.0.0` (cached local install, same flags as `npm run generate:types`). The diff against the committed file is empty. The generated types don't include the `topic_device_summary` shape at all, so the file has no `eth0` or `wlan0` and didn't need to change.

## Minor observations (non-blocking)

- **o1.** There is no menud unit test for the poller's new `eth0`/`wlan0` extraction, and no valid sample frame carries them. Coverage comes only from e2e 5f, and 5f asserts Eth0 only, not WLAN0.
- **o2.** `poller.py:145` builds `ifaces_list` in one dense line with a nested comprehension. It works, but is hard to read.

## Reproduce

```
cd wlanpi-menud && .venv/bin/python -m pytest -q
cd wlanpi-menu-tui && .venv/bin/python -m pytest -q
cd wlanpi-webui && .venv/bin/python -m pytest -q && node --test tests/js/*.test.mjs
docker run --rm -v $PWD:/src:ro fpms-verify:final python3 /src/verify-container/run_verification.py
```
