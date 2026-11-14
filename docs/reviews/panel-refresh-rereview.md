# Panel Visual Refresh Re-review

Scope: `wlanpi-fpms` branch `phase3/menud-client`, checked against `panel-refresh-review.md`. The fixes were made by **amending** the reviewed commit, not with new fix commits: `048c8df` became `fb81ff1` ("feat(render): implement modern visual language for OLED micro-display", committed 2026-11-14 02:31 UTC). It is still one commit on `6aeae3f`, one ahead of origin and not pushed. The working tree is clean. I diffed `048c8df..fb81ff1`, re-rendered views myself and looked at the PNGs, then re-ran the mutations and stress hooks in a scratch copy (`git archive HEAD`). The only file I wrote in any repo is this one.

## Verdict: APPROVE

All six blockers are closed and every suite, lint and type check is green. The deb builds and its payload is byte-identical to HEAD. The device still runs the pre-refresh build. Three should-fix items are still open (mode indicator source, the "thin rule lines" claim, and bundled non-rendering changes); none of them blocks. They are listed at the end.

## Blockers

| # | Prior blocker | Status | Evidence |
|---|---|---|---|
| 1 | `ruff format --check` fails on 3 files | **CLOSED** | `ruff format --check fpms tests`: 47 files already formatted. `ruff check`: clean. `mypy fpms`: clean, 29 files. |
| 2 | Right-edge clipping in text blocks and list labels | **CLOSED** | Text, KV, progress, confirm and QR captions wrap to `COLS - 1`. List `room` is `COLS - (3 if mark else 1)`, details use `COLS - 3`, status uses `COLS - 3`. The new `overlong` golden (128 and 64) renders cleanly; I checked the PNG: ellipsised title, breadcrumb, selected and unselected long labels and detail, and wrapped paragraph. `test_no_right_edge_clipping` hooks `ImageDraw.text` over all sample views. My own stress hook covers long list/status/KV/confirm/progress, title with page counter and 6 breadcrumbs, a long mode string, oversized complications and a QR caption, at both heights. Nothing is drawn past x=128 except the table case under Minor below (ink still ends at x=127). |
| 3 | QR doesn't fit the 64px body | **CLOSED** | `_qr` now gets `body_h=effective_body_h`. At 64px the body is 36px, so the code is 33x33 and shows whole at scroll 0. The caption scrolls below it (max_scroll 9). At 128px the code is 66x66 with max_scroll 0. Checked visually. Data too large for one pixel per module still falls back to text, as before. |
| 4 | Progress "elapsed" shows the panel's uptime | **CLOSED** | The `tick` fallback is gone (`render.py:481`). Elapsed now comes only from frame `elapsed`/`elapsed_sec`, which the current schema doesn't allow, so no elapsed is shown today. The fixture's `elapsed: "0:12"` was removed. |
| 5 | Goldens can't see the title or the table rule | **CLOSED** | `image_to_ascii` now classifies in RGB: on the title bar, white text is off against the blue bar, and the `#205493` rule is off against the yellow header. Mutations re-run in the scratch copy: **M2** (title → "ZZZZ"): **23 failed**. **M3** (title text removed): **23 failed**. **M7** (table header rule removed): **2 failed** (table 128 and 64). The baseline in the same copy passes 25/25. |
| 6 | Fixtures aren't schema-valid | **CLOSED** (schema part; see should-fix S1 for the mode part) | Every `SAMPLE_VIEWS` entry is now a full `view` frame (`v`, `type`, `id`). `mode` and `complications` moved into `render()` keyword arguments, the way the panel passes them. `unavailable_view()` also builds a valid frame. `test_all_sample_views_valid_schema` validates them against the vendored schema and checks `SAMPLE_COMPLICATIONS` against `topic_complications`. The pin works: a view with `mode`, a view with `complications`, or a progress block with `elapsed` is each **rejected** by that schema. The vendored schema is still byte-identical to `wlanpi-menud/protocol/frames.schema.json`. |

## Prior should-fix items

| Item | Status |
|---|---|
| Home complications row below the fold at 64px | **Fixed.** The row is now first in the body on home. At 64px the frame shows `12:34`, the battery and Wi-Fi glyphs, then Host and IP, with max_scroll 0. |
| Confirm at 64px shows a half-cut row, and the scrollbar covers the pill | **Fixed.** The 4px spacer is gone and buttons are `ROW_H` high, so the body is back on the 12px grid. At 64px max_scroll is 0, so there is no scrollbar, and the right pill ends at x=124. |
| Status lines with a 0px right margin | **Fixed** (`COLS - 3`). |
| Commit message said "monochrome" | **Fixed.** It now says "OLED micro-display". |
| Fixtures not schema-valid | Fixed (blocker 6). |
| Table "thin rule lines" claim | **Not addressed** (S2). |
| Where the mode indicator comes from on the device | **Not addressed** (S1). |
| Schema re-sync and `test_live_menud.py:117` bundled in the rendering commit | **Not addressed** (S3). |
| Progress fill 1px from the border; `app_id` substring icon match | Unchanged. Cosmetic, carried forward. |

## Tests, build, device

| Check | Result |
|---|---|
| `pytest` (whole repo) | **157 passed**, 0 skipped. That's 153 + overlong at 2 sizes + the schema test + the clipping test. |
| `ruff format --check` / `ruff check` / `mypy` | clean / clean / clean (29 files) |
| Mutations M2 / M3 / M7 | 23 fail / 23 fail / 2 fail (all now caught) |
| deb (`build-package-native.sh trixie`, in a scratch copy) | exit 0. `wlanpi-fpms_1.4.26_arm64.deb` (7.77 MB) plus dbgsym. The payload's `render.py`, `glyphs.py` and `panel.py` are **byte-identical to `fb81ff1`**. |
| Device untouched (read-only `ssh wlanpi@10.254.102.60`) | **Confirmed pre-refresh.** On-device `render.py` sha256 prefix `c3a097fd2e606f39` = `6aeae3f` (`048c8df` is `6a2f9caa…`, `fb81ff1` is `3b58c40d…`). There's no `glyphs.py` on the device. `wlanpi-fpms` 1.4.26 was installed 2026-11-11 08:17 -0600 (the phase-3 deploy), and the service is active. |

## Remaining items (non-blocking)

- **S1: the mode indicator never appears on the device.** `panel.py:318-325` still passes no `mode=`, so the right side of the header shows only the badge or page counter. Every golden is rendered with `mode="classic"` and shows "CLASSIC", and the commit message still lists "right-aligned mode indicator" as a feature. Either wire it from `device.summary.mode` or drop `mode=` from the snapshot calls and the commit message.
- **S2: "thin rule lines" is still claimed.** The only rule is the one under the table header. Data rows have none. Fix the commit message or draw them.
- **S3: non-rendering changes are still bundled.** The +195-line schema re-sync and the `test_live_menud.py:117` loosening are still in this commit, and the message doesn't explain either. Split them out, or add a line to the message.
- **Minor: the ASCII classifier is tied to DarkTheme RGB values** (`render.py:706-731`: blue bar range, `#205493`). If the theme changes, the goldens could go blind to the title or rule again without any test failing. Derive the colours from `THEME` (`page_title_background`, `page_table_row_separator`). In `test_all_sample_views_valid_schema`, `if topic_schema:` would also skip silently if the def disappeared; assert it exists instead.
- **Minor: full-width table rows have a 0px right margin.** A row filling all 21 columns at x=2 (for example a 5-column scan with a long SSID) has an advance of 128.3 px. The ink ends at x=127 (`textbbox` right edge is 128), so nothing is visibly cut, but there's no 2 px margin. The in-tree clipping test would flag this case if a fixture hit it.
