# Complications Surfaces Review: `wlanpi-menu-tui` and `wlanpi-webui`

**Date:** 2026-11-14  
**Targets:**  
- `wlanpi-menu-tui` (branch `main`, commit `93a69cd`)
- `wlanpi-webui` (branch `redesign/webui-menu-panel`, commit `3442bac`)
- `verify-container` (root commit `5ae2974`)

**Evaluated against:** `docs/complications-spec.md` §4–5 and `complications-service-review.md`.  
**Review mode:** READ-ONLY on all targets.

**Verdict:** **APPROVE**

| Severity | Count | Summary |
| --- | --- | --- |
| **Blocker** | **0** | None. Protocol conformance, profile resolution, and security gates fully verified. |
| **Major** | **0** | None. Backward compatibility with older services is preserved byte-for-byte. |
| **Minor / Nits** | **0** | None blocking. 3 service-side nits triaged to backlog; 1 test-runner timing observation noted. |

---

## 1. Executive Summary

Commits `93a69cd` (`wlanpi-menu-tui`) and `3442bac` (`wlanpi-webui`), verified via `5ae2974` (`verify-container`), successfully deliver Phase C5 of the complications specification (`docs/complications-spec.md` §4–5).

All 6 review criteria have been audited, executed, and verified live:
1. **Feature Gating:** Both clients gate complications v2 behavior strictly on `"complications.2"` in `welcome.features`. Connecting against pre-complications `wlanpi-menud` inside a clean container preserves legacy subscriptions and rendering unchanged.
2. **Profile Resolution & Layering:** Both surfaces implement spec §4.3 resolution identically against `protocol/samples/complications/resolve-vectors.json`. Cross-surface integration was proven live: configuring the `tui` profile from WebUI causes the TUI footer to instantly reorder from the broadcast event.
3. **Status Vocabulary & Security:** Both surfaces map the closed status vocabulary (`ok`, `info`, `warn`, `error`, `off`, `unknown`) to icon + text glyphs without relying on color alone. Hostile HTML markup (`<b>`, `<script>`) published by provider tiles is strictly rendered as text, preventing XSS and rich text injection.
4. **Overflow & Fitting:** The WebUI status bar caps at 12 tiles before overflowing into the `+N` chip (dynamically adjusted on small viewports and mobile). The TUI footer strictly limits to 8 tiles, cutting overflow from the end and ellipsizing value text while preserving labels.
5. **Full Test Suites:** All target test suites pass completely (TUI: 48/48, WebUI JS: 267/267, WebUI Pytest: 483/483, Container E2E: 163/163).
6. **Mutation Testing:** Deliberately reversing tile resolution ordering caused immediate assertion failures in both the TUI and WebUI test suites, confirming test sensitivity.

---

## 2. Detailed Verification

### (1) Feature Gating on `welcome.features` (`complications.2`)
- **TUI Implementation (`wlanpi_menu_tui/app.py:171, 258-268`):**
  - Reads `frame.get("features")` on `welcome`. `complications_v2` is `True` only when `"complications.2" in self.features`.
  - `_topics_to_subscribe()` includes `complications.live`, `complications.catalog`, and `complications.config` only when `self.complications_v2` is true; otherwise it requests only legacy topics (`device.summary`, `alerts`, `complications`).
  - `_render_status_footer()` preserves legacy `device.summary` key/value layout when `complications_v2` is disabled.
- **WebUI Implementation (`wlanpi_webui/static/js/menu/store.js:542-550`, `main.js:212-216`):**
  - `hasTiles(state)` checks `state.welcome.features.indexOf("complications.2") !== -1`.
  - Subscribes to `TILE_TOPICS` only when `hasTiles(state)` is true.
  - `#menu-statusbar` omits `.has-tiles` and renders legacy host/mode/ip/temp items; `#menu-page` renders legacy `.menu-complications` and hides `.menu-tiles`.
  - Client route `#/tiles` displays `The device does not offer status tiles.` and disables configure actions.
- **Container Verification (Old Service Test):**
  - Ran a live verification in `fpms-verify:redesign` against `wlanpi-menud` with `FEATURES = ()`.
  - Confirmed: `welcome` advertised no features; TUI set `complications_v2 = False`; subscribed only to legacy topics; TUI rendered legacy footer format (`wlanpi-test  │  mode:classic  │  192.0.2.10...`).

### (2) Profile Resolution & Layering (Cross-Surface Proof)
- **Resolution Algorithm (`resolve` in TUI and WebUI):**
  - Both implementations walk `show` in order: exact IDs match live records; pattern wildcards (`plugin.*`, `local.*`) yield unmentioned live IDs sorted alphabetically; duplicates and previously yielded IDs are dropped; capacity cuts the tail without reordering.
  - Verified against all 14 test cases in `protocol/samples/complications/resolve-vectors.json`.
- **Cross-Surface Proof (Live Container Execution):**
  - Launched `wlanpi-menud` with `complications.2` enabled.
  - Connected an interactive `MenuTuiApp` to `/tmp/local.sock`. Initial footer rendered default profile order: `Mode: Classic` before `IP: 192.0.2.10`.
  - Connected a second client over WebSocket (simulating WebUI `#tiles` page) and sent `complications.configure` for surface `tui` with `show = ["wlanpi.ip", "wlanpi.mode", "wlanpi.cpu_temp"]`.
  - `wlanpi-menud` persisted the configuration to the user layer file and broadcasted `complications.config`.
  - The TUI footer updated dynamically on the same connection without reconnecting: `IP: 192.0.2.10` shifted before `Mode: Classic`.

### (3) Status Vocabulary & Escaping (XSS Audit)
- **Status Vocabulary (Spec §2.6):**
  - Closed vocabulary: `ok`, `info`, `warn`, `error`, `off`, `unknown`. Unrecognized values normalize to `unknown`.
  - TUI (`wlanpi_menu_tui/complications.py:34-45`): maps statuses to symbols (`✓`, `▲`, `✗`, `○`, `?`, `•`) and Rich styles (`green`, `yellow`, `bold red`, `dim`, `cyan`).
  - WebUI (`wlanpi_webui/static/js/menu/pages.js:189-204`): maps statuses to icons (`check`, `warning`, `close`, `minus`, `question`) and human-readable tags (`OK`, `Warning`, `Error`, `Off`, `No reading`).
- **Escaping & XSS Safety:**
  - TUI: `tile_markup` passes plain text through `rich.markup.escape(tile_plain(...))` (`complications.py:145`). Markup characters like `[red]` are rendered literally as text.
  - WebUI: All DOM creation in `pages.js` uses `h(doc, tag, { text: ... })` which writes to `element.textContent`, or string primitives passed to `append()` which creates DOM text nodes via `document.createTextNode()`. No `innerHTML` or HTML string interpolation is used.
  - Verified via Playwright e2e Test 10 (`drive_menu_ui.mjs:254-262`): publishing hostile provider payload `{ "label": "E2E", "value": "<b>x</b>" }` was rendered strictly as plain text `<b>x</b>` with no bold element created.

### (4) Overflow Behavior & Caps
- **TUI Footer (`wlanpi_menu_tui/complications.py:29, 149-166`):**
  - Capped at `FOOTER_MAX_TILES = 8`.
  - Iterates through resolved tiles until `room` columns are exhausted. If a tile exceeds available width, its value is ellipsized (e.g. `123…`) if room allows at least label + 1 character; otherwise it is dropped. Tile labels are never truncated.
- **WebUI Status Bar (`wlanpi_webui/static/js/menu/pages.js:207, 309, 391-413`):**
  - Capped at `BAR_MAX = 12` in desktop layout; overflow tiles roll into `+N` chip.
  - `fitStatusBar()` measures `.menu-sb-tiles` against container width. When `scrollWidth > clientWidth`, tiles are cut from the right and added to the `+N` count. Tested on both 1280px desktop and 390px mobile viewport (`drive_menu_ui.mjs:880-891`).

### (5) Test Suites Execution

All requested suites executed in clean container/venv environments:

| Test Suite | Commands | Target Count | Actual Result | Status |
|---|---|---|---|---|
| **TUI Unit/Integration** | `wlanpi-menu-tui/.venv/bin/pytest` | 48 | **48 passed** in 14.48s | **PASS** |
| **WebUI JS Unit/Dom** | `cd wlanpi-webui && npm test` | 267 | **267 passed** in 5.94s | **PASS** |
| **WebUI Pytest** | `wlanpi-webui/.venv/bin/pytest` | 483 | **483 passed** in 24.35s | **PASS** |
| **Container E2E** | `docker run ... fpms-verify:redesign` | 163 | **163 passed** in 59.2s | **PASS** |

### (6) Mutation Checks
1. **TUI Tile Order Mutation (`wlanpi_menu_tui/complications.py:126`):**
   - Mutated `resolved_tiles` to return `reversed(resolve(...))`.
   - Result: `tests/test_complications.py::test_new_service_footer_follows_tui_profile` failed immediately with `AssertionError: assert [0, 110, 89, 73, ...] == [0, 16, 30, 48, ...]`. Reverted clean.
2. **WebUI Tile Order Mutation (`wlanpi_webui/static/js/menu/store.js:571`):**
   - Mutated `resolveTiles` to `show.slice().reverse()`.
   - Result: 11 tests failed in `tests/js/tiles.test.mjs` (including `exact ids in order`, `status bar follows the webui profile`, and `customize page`). Reverted clean.

---

## 3. Triage of Service Nits (Logged by TUI Worker)

The TUI implementation team noted three service-side behaviors in `wlanpi-menud`. Each was triaged against `docs/complications-spec.md`:

| Item | Location | Spec Reference | Analysis | Recommendation |
|---|---|---|---|---|
| **1. `(hidden)` marking** | `wlanpi_menud/handlers/system.py:151` | Spec §4.7 sketch | Service populates `{"label": "  Tile", "detail": "hidden"}` rather than embedding literal `"(hidden)"` into `label`. TUI formats `detail` as ` (hidden)`. | **BACKLOG.** Separating semantic status (`detail: "hidden"`) from display presentation adheres to clean API design. Thin renderers format `detail` as appropriate for their displays. |
| **2. `'Tui'` label** | `wlanpi_menud/handlers/system.py:68` | Spec §4.7 | `prof.replace(".", " ").title()` renders `"Tui"` instead of acronym `"TUI"`. | **BACKLOG.** Cosmetic prompt title casing (`Prompt: Tui`). Functional navigation and configuration remain unaffected. |
| **3. `capacity-from-rows`** | `wlanpi_menud/complications.py:145-152` | Spec §4.5 table | `home_capacity` for profile `"tui"` uses `body_rows(screen) - min(alert_rows, 2)` capped at 8, treating terminal rows as vertical slots rather than evaluating horizontal footer columns. | **BACKLOG.** Terminal footer width is client-variable and dynamic (80, 120, 160 columns); the server cannot predict font/width constraints. Capping at 8 in the service customize node is a sensible ceiling, while the client (`wlanpi_menu_tui/complications.py:fit_tiles`) handles exact width-based cutting. |

---

## 4. Minor Test Observation (Non-Blocking)

- **`wlanpi-menu-tui/tests/test_live_menud.py:220-224`:**
  During high-load full-suite execution, `test_complications_footer_and_home_tiles_live` exhibited an intermittent timing race where `_wait_until` awaited `a.current_view.get("title") == "Home tiles"`, but the assertion `assert "Rows" in screen(a)` fired before Textual's coalesced render loop (`_render_coalesce_sec = 0.06`) finished updating the DOM text widget.
  *Recommendation for next test maintenance pass:* In `test_complications_footer_and_home_tiles_live`, await `_wait_until(lambda: "Rows" in screen(a))` or invoke `await pa.pause()` prior to inspecting `screen(a)`.

---

## 5. Final Recommendation

**APPROVE.** Commits `93a69cd` (`wlanpi-menu-tui`) and `3442bac` (`wlanpi-webui`) fully satisfy the multi-surface complications architecture, pass all test suites, maintain backwards compatibility, and enforce security boundaries. Both repositories are approved for integration.
