# UX round review

Scope: tui `bbc7646..e0ddb69` (main), menud `5e4c010..298b81f` (phase1/service), webui `ea927d6..311cc44` (phase2/webui-menu-panel), checked against `FPMS Re-Architecture - UX TODO.md`. Read-only except this file. Mutants ran on scratchpad copies.

**Verdict: APPROVE.** Blockers 0, major 0, minor 4.

## Suites (all run by me)

| Suite | Result |
| --- | --- |
| menud pytest (.venv), incl. test_nav back-compat | 214 passed |
| menu-tui pytest | 23 passed |
| webui pytest | 482 passed |
| webui JS `node --test` | 22 passed |
| Container e2e (`fpms-verify:final`, run_verification.py) | 25/25 passed, incl. 5b click-to-view, 5c nav, 6e private thin |

## Checks against the brief

1. **TUI tree hidden, `t` toggles, breadcrumb intact: OK.** `app.py:210` defaults `show_menu_tree=False`, `app.py:785` gates the render, and `app.py:890` toggles (skipped during a prompt). The `test_tui.py:1110` test covers hidden-by-default and the breadcrumb. The footer shows "t tree" only while hidden. The README key list is updated.
2. **Nav frame is additive v1: OK.** The schema diff is one new `$defs/nav` plus one `oneOf` entry. No existing definition was touched, and `C2S` gained "nav" only. All pre-existing menud tests pass unchanged. Branch, view-action and invalid node (NOT_FOUND) behave per `PROTOCOL.md`, and a detached connection gets FORBIDDEN. Confirm and param nodes reached through nav still go through `jobs.invoke`, which returns prompts.
3. **Vendored schema and types: OK for schema, types by inspection.** `diff menud/protocol/frames.schema.json webui/protocol/frames.schema.json` is empty, and `protocol/README.md` records the new source commit. `frames.d.ts` gains `Nav` in the same shape as `Revoke`. I could not re-run the typegen: `npx` was denied by the sandbox. m4 covers this.
4. **Rich click on network.interfaces, one click: OK.** `menu.js:430-437` invokes immediately when there are no params and no confirm. The JS test passes, and the e2e 5b assertion passes (table, 0 prompts).
5. **Three-way mode: OK.** `applyMode` (menu.js ~769) maps rich, thin/new and thin/default, and the badge reflects all three. The JS tests cover the hellos and clean switches. e2e 6e covers the private thin session. m2 notes one race in the switch.
6. **Mutations.**
   - Removing the NOT_FOUND and visibility guards: `test_nav_invalid_node_and_detached` fails, as expected.
   - Removing the detached guard: the same test fails.
   - Removing only the visibility guard (`service.py:404`): all 131 tests still pass. See m3.
   - Breaking the JS auto-invoke (`if (false)`): JS test 4 fails. The e2e did not run on this mutant; the container run on the scratch copy printed nothing, apparently because the copied `.venv` symlinks do not resolve inside the container. See m1.

## Findings

- **m1 (minor): e2e 5b does not exercise `menu.js`.** `verify-container/run_verification.py:~631` sends a raw `invoke` frame over a Python websocket. A broken auto-invoke in `menu.js:430` can only fail the JS test (it does), never the e2e. The verification bar says "e2e click-to-view", but the e2e only proves the server side. Rename the test or add a browser-driven check.
- **m2 (minor): stale socket close during a mode switch.** `menu.js:99`: `ws.onclose` is not guarded by socket identity. `applyMode` calls `disconnect()` then `connect()` right away, which resets `isManualClose`. The old socket's late close event then sets "Disconnected" and schedules a reconnect. The reconnect no-ops at `connect()` (menu.js:68), but the status badge can flicker. Guard with `if (this !== ws) return`.
- **m3 (minor): the nav visibility guard has no test.** `service.py:404`: nothing covers a hidden branch node. Leaf nodes are also guarded in `jobs.invoke`, so only branch nav depends on this check. Add a hidden-branch FORBIDDEN test.
- **m4 (minor): TS typegen not re-run by me.** Only inspected, because `npx` was blocked. The CI regen check should confirm it. Separately, `menu.js:426` sends nav only when `node.view` is set, and no branch in `builtin.json` has a `view`. Branch landing views are therefore wired but never exercised.
