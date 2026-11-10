# WebUI Menu Panel Redesign Review

Scope: Review of branch `redesign/webui-menu-panel` in `wlanpi-webui` (branched from `phase2/webui-menu-panel` HEAD) against `wlanpi-menud/docs/webui-menu-redesign.md` and user decisions (two-click mutations, Playwright e2e in real Chromium, single surface at `/menu`).

**Verdict: APPROVE.** Blockers: 0, Major: 0, Minor: 0.

---

## 1. Test Suite Summary

All test suites were independently executed in the workspace and container environment:

| Suite | Command / Target | Result | Notes |
|---|---|---|---|
| **WebUI JS Unit & DOM** | `cd wlanpi-webui && node --test tests/js/*.test.mjs` | **230 passed, 0 failed** (54 suites) | jsdom DOM harness, Ajv schema checks, contrast, conventions |
| **WebUI Python Unit** | `cd wlanpi-webui && .venv/bin/pytest` | **483 passed, 0 failed** | Full Python unit suite |
| **Menud Python Unit** | `cd wlanpi-menud && .venv/bin/pytest -q` | **216 passed, 0 failed** | Core service, protocol, and poller tests |
| **Menu-TUI Python Unit** | `cd wlanpi-menu-tui && .venv/bin/pytest -q` | **25 passed, 0 failed** | TUI client tests |
| **Container E2E & Protocol** | `docker run --rm -v "$PWD:/src:ro" fpms-verify:redesign python3 /src/verify-container/run_verification.py` | **147 / 147 passed** | Tests 1–9 + Test 10 (Playwright e2e with 119 browser assertions in headless Chromium) |

---

## 2. Verification of Requirements

### (1) Protocol & Schema Integrity
- **Menud Protocol diff**: `git -C wlanpi-menud diff phase1/service -- protocol/ PROTOCOL.md` is completely **EMPTY**.
- **Vendored Schema diff**: `git -C wlanpi-webui diff phase2/webui-menu-panel..redesign/webui-menu-panel -- protocol/` is completely **EMPTY**.
- **Schema Identity**: `diff -u wlanpi-webui/protocol/frames.schema.json wlanpi-menud/protocol/frames.schema.json` is byte-identical (0 diff).
- The protocol and vendored schemas remain untouched, adhering strictly to the frozen protocol constraint.

### (2) Every Design Section Implemented (20 Wireframes Walked)
All 20 wireframe specifications defined in `wlanpi-menud/docs/webui-menu-redesign.md` Section 4 are verified by the unit test suites and the Playwright e2e test suite:
- **4.1 Home, desktop**: Verified in `pages.test.mjs:13` and `drive_menu_ui.mjs:173` (Section 1). Device summary card, alerts, complications, section cards, no transport mode selector.
- **4.2 Branch page**: Verified in `pages.test.mjs:110` and `drive_menu_ui.mjs:191` (Section 2). Child node list, breadcrumbs, sidebar hierarchy synchronization, row cues.
- **4.3 Action page, read-only (runs on arrival)**: Verified in `pages.test.mjs:164` and `drive_menu_ui.mjs:191` (Section 2). Auto-invocation on arrival for read-only actions (`network.interfaces`), grouped table rendering, in-place refresh.
- **4.4 Action page with parameters, idle**: Verified in `pages.test.mjs:253` and `drive_menu_ui.mjs:327` (Section 5). Schema-generated parameter forms (enum, integer, string, adapter), native validation, waits for explicit Run.
- **4.5 Running**: Verified in `pages.test.mjs:327` and `drive_menu_ui.mjs:401` (Section 7). Progress bar, percentage / indeterminate states, Cancel button for cancellable jobs, `aria-busy` attribute.
- **4.6 Result with paging**: Verified in `pages.test.mjs:381` and `drive_menu_ui.mjs:327` (Section 5). "Load more" with cursor, server row count display ("Showing 50 of 213"), table row appending capped at 1,000 DOM rows.
- **4.7 Mutating action, idle, then inline confirm**: Verified in `pages.test.mjs:429` and `drive_menu_ui.mjs:238` (Section 4). Warning line "This changes the device.", initial Run button swaps to inline confirm row with focus defaulted to Cancel.
- **4.8 Error and cancelled**: Verified in `pages.test.mjs:513` and `drive_menu_ui.mjs:359, 401`. Per-code inline messages, "Try again", cancellation timestamps.
- **4.9 Prompt dialog**: Verified in `prompt_dialog.test.mjs:40-230` and `drive_menu_ui.mjs:359` (Section 6). Modal for mid-job inputs (select, confirm, integer, string, form), expiry countdown, cancel on Esc or dismiss.
- **4.10 Activity popover**: Verified in `pages.test.mjs:791` and `drive_menu_ui.mjs:401` (Section 7). Live job chip ("1 running"), popover listing jobs by menu breadcrumb with progress and cancellation controls.
- **4.11 Front panel pane, docked (≥ 1200 px)**: Verified in `front_panel.test.mjs:21-140` and `drive_menu_ui.mjs:464` (Section 7p). Fixed-ratio screen frame, hint-mapped key buttons, sticky docked right column.
- **4.12 Front panel pane, shared confirmation unanswerable**: Verified in `front_panel.test.mjs:231`. Non-interactive view when `answerable: false`, "Waiting for <user> to confirm on device".
- **4.13 Not available (unknown / hidden node)**: Verified in `pages.test.mjs:564` and `router.test.mjs`. Graceful "This item is not available" screen with "Go to Home" button.
- **4.14 Connecting (first load)**: Verified in `pages.test.mjs:596`. Skeleton placeholders and "Connecting to the device menu…" without error banner.
- **4.15 Connection lost, waiting and reconnecting**: Verified in `pages.test.mjs:614` and `drive_menu_ui.mjs:609` (Section 8). Content preserved and dimmed, countdown banner, Retry now button.
- **4.16 Terminal states**: Verified in `pages.test.mjs:733` and `drive_menu_ui.mjs:799` (Section 9). Signed-out (4401), Refused (1008), Reload needed (4426/4400) with dedicated recovery actions.
- **4.17 Core unavailable (socket live)**: Verified in `pages.test.mjs:770`. Status bar notification and inline error blocks while menu navigation stays intact.
- **4.18 Phone: Home, branch, action (390 px viewport)**: Verified in `drive_menu_ui.mjs:741` (Section 10). Drill-down navigation, touch targets ≥ 44 px, collapsed breadcrumbs, no horizontal page scroll.
- **4.19 Phone and tablet: Front panel sheet**: Verified in `front_panel.test.mjs:325` and `drive_menu_ui.mjs:741` (Section 10). Bottom sheet modal (expanded ≤ 50% vh, collapsed to 45 px bar showing screen title).
- **4.20 Front panel pane: connection lost**: Verified in `front_panel.test.mjs:267` and `drive_menu_ui.mjs:609` (Section 8e). Pane-scoped offline banner and key disabling without affecting Connection A.

### (3) Safety: Scripted Proof of Zero Mutating Frames
Scripted proof verified in `verify-container/drive_menu_ui.mjs:238-325` (Section 4):
- **Monitoring setup**: Three independent capture layers: browser WebSocket client frame log (`d.invokes()`), server-side menud audit log (`/invokes`), and fakecore backend mutation count (`/core` reboots).
- **Opening a mutating node** (`system.reboot`): **0** frames sent, **0** audit entries, **0** reboots (`z.frames === 0 && z.audit === 0 && z.reboots === 0`).
- **Browser Reload on mutating page**: **0** frames sent, **0** audit entries, **0** reboots.
- **Browser Back and Forward**: **0** frames sent, **0** audit entries, **0** reboots.
- **Pressing Run button**: Swaps to inline confirmation; **0** frames sent.
- **Pressing Cancel**: Reverts confirm row; **0** frames sent.
- **Pressing Confirm**: Sends exactly **1** invoke frame with `confirmed: true`; audited and executed by core.
- **Returning to page via Back**: Restores cached result; does **not** re-invoke.
- **Unconfirmed mutations** (e.g. actions with `mutates: true` without confirm dialog): Verified that two clicks (open, Run) are strictly required; opening sends **0** invokes; clicking Run sends exactly **1** invoke.
- **Read-only actions** (`network.interfaces`): Verified that navigating to the node sends exactly **1** invoke automatically, displaying results immediately upon arrival.

### (4) Mirror Pane (Front Panel)
Verified in `front_panel.test.mjs` and `drive_menu_ui.mjs:464-540, 799-840` (Sections 7p and 9):
- **Second Connection**: Opening the Front panel pane connects Connection B (`hello {mode: "thin", session: "default"}`); Connection A remains independent (`mode: "rich", session: "new"`).
- **Driven-By Awareness**:
  - Input from an external TUI client updates the webui pane with `"Driven from wlanpi-menu-tui (wlanpi)"`.
  - Input from the pane updates the TUI client with `driven_by.surface: "wlanpi-webui-front-panel"` and `principal.name: "erin"`, while the webui pane displays `"You are driving"`.
- **Selective Detach**: Closing the pane detaches only Connection B (server attachments drop from 2 to 1); Connection A continues uninterrupted and performs refreshes.
- **Dual Revocation on Logout**: Executing `POST /logout` causes menud to send `AUTH_FAILED` error frames and close **both** Connection A and Connection B with code 4401. Both display the signed-out state and cease reconnection attempts.

### (5) Resume Banner and Backoff Countdown
Verified in `connection.test.mjs`, `pages.test.mjs:614`, and `drive_menu_ui.mjs:609-687` (Section 8):
- **Restart handling**: Restarting menud in the container immediately presents a `#menu-banner` stating `"The menu service is restarting. Retrying in ... s"`.
- **Visible Countdown**: `MutationObserver` logs verify that the countdown decrements visibly through elapsed seconds and transitions to `"Reconnecting…"`.
- **Stale State Preservation**: Content remains fully rendered and dimmed (`.is-stale`), action buttons are disabled, and timestamp displays `"as of HH:MM:SS"`.
- **Resume Protocol**: The reconnecting WebSocket sends `hello.resume` carrying `epoch`, `session_id`, and per-topic sequence numbers.
- **Seamless Recovery**: Upon server return with a new epoch (`resumed: false`), the client refreshes subscriptions and menu snapshot, refreshes auto-run pages exactly once (`network.interfaces` frames +1, audit +1), and never triggers re-execution of mutating pages (`system.reboot` reboots = 0).

### (6) JS Mocks and HTMLCollection Faithfulness
- **JSDOM Adoption**: All DOM tests (`pages.test.mjs`, `front_panel.test.mjs`, `prompt_dialog.test.mjs`) use real jsdom environments where `element.children` is an authentic `HTMLCollection` lacking `Array.prototype` methods like `indexOf` and `forEach`.
- **Mutation Check Proof**:
  - Tested in an isolated scratch copy by reintroducing `el.children.indexOf(...)` inside `renderPage` in `pages.js`.
  - Running `node --test tests/js/pages.test.mjs` resulted in immediate and widespread test failures with `TypeError: el.children.indexOf is not a function`.
  - Confirms that any regression relying on array-like methods on DOM collections is caught by the test harness.

### (7) Accessibility and Theming
- **Axe-core Automated Audit**: Executed via Playwright in `drive_menu_ui.mjs:544-608` (Section 11) over Home, action results, forms, and open pane in both light and dark themes (8 complete scans): **0 serious or critical violations**.
- **Keyboard Navigation**:
  - Full Tab-only walk verified from Home through breadcrumb hierarchy to an action result.
  - Shortcut `/` successfully focuses the search filter input.
  - Arrow keys and Enter are focus-scoped to the Front panel pane when focused; global window keystrokes are not intercepted.
- **WCAG Contrast Ratios**:
  - Checked via `contrast.test.mjs` computing WCAG 2.1 formulas for all token combinations defined in `app.css` and referenced in `menu.css`.
  - All text/surface pairs satisfy ≥ 4.5:1.
  - All control boundaries and active selected row highlights satisfy ≥ 3.0:1 across both light and dark themes.

### (8) Code Conventions & Architecture
Verified via `conventions.test.mjs`:
- Zero light-theme UIkit utilities (`uk-table-striped`, `uk-background-muted`, `uk-card-secondary`).
- Zero inline `style=` attributes and zero script-assigned `.style` properties.
- Zero hardcoded colors outside `app.css` design tokens.
- Zero hardcoded menu node IDs or labels in JavaScript source files (`server owns the menu`).
- Zero transport vocabulary ("rich", "thin", "mirror", "session") in user-visible copy or templates.

---

## Conclusion

The implementation on branch `redesign/webui-menu-panel` fully satisfies all design goals, safety constraints, wireframe specifications, and architectural requirements laid out in `wlanpi-menud/docs/webui-menu-redesign.md`.

**Verdict: APPROVE.**
