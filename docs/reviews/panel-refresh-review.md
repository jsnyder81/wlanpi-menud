# Panel Visual Refresh Review

Scope: `wlanpi-fpms` branch `phase3/menud-client`, commits since `6aeae3f`. This is one commit, `048c8df` ("feat(render): implement modern visual language for monochrome OLED"), which is not pushed. The working tree is clean.

Method: read the diff and `render.py`/`glyphs.py`, then re-rendered every sample view at 128x128 and 128x64 myself. I looked at real RGB PNGs at 2-4x, not only the ASCII goldens. I hooked `ImageDraw.text` to find text drawn past the panel edge or over other text, and ran mutation tests against a scratch copy of the tree. Nothing in any repo was changed except this file.

## Verdict: REQUEST-CHANGES

The rendering work is mostly sound and the protocol layer is untouched. Six things block the merge: CI's format check fails, text is clipped at the right edge at both sizes (including in a committed golden), the QR can't be scanned at 64px, real progress bars show the panel's uptime as "elapsed", and the goldens can't see the header title or the table rule. Details follow.

## 1. Protocol, client and frame handling unchanged: PASS (with notes)

`git diff --stat 6aeae3f..048c8df`:

- `protocol.py` and `client.py` are **unchanged**.
- `panel.py` has a 6-line change (`panel.py:315-326`). It reads `client.topic("complications")` on the home screen only and passes it to `render()`. That topic is already in `protocol.TOPICS` and its shape (`app_id/label/value/status`) matches `topic_complications`, so no new frames, subscriptions or handling were added. **Acceptable.**
- `screen/virtual.py` adds `to_ascii()` (`virtual.py:68-74`), which is test-only. **Acceptable.**
- `tests/menud_client/fixtures/frames.schema.json` changes by +195 lines. It is now **byte-identical** to `wlanpi-menud/protocol/frames.schema.json`, so this is a vendored re-sync for complications.2, not a protocol change. It falls outside a rendering-and-assets-only commit and should go in its own commit.
- `tests/menud_client/test_live_menud.py:117` loosens the home assertion to `startswith(("Host:", "Mode:"))`. That isn't rendering work; it needs a reason in the commit message or its own commit.

## 2. Block treatments, both sizes, self-rendered and mutation-tested

| Treatment | 128x128 | 128x64 | Do the goldens catch a regression? |
|---|---|---|---|
| Header inversion (blue bar) | yes | yes | yes. M4, header bg set to black: **21/21 fail** |
| Header **title text** | yes (PNG) | yes (PNG) | **NO.** M2, title replaced with "ZZZZ": 21 pass. M3, title text removed: **21 pass** |
| Selection pill + arrow glyph | yes | yes | yes |
| Status glyphs ok/warn/error | filled dot / hollow dot / alert | ok only (rest scrolls) | yes. M8, warn glyph changed to alert: 1 fail |
| Progress fill math | 65% gives 77/119 px | yes | yes. M1, `/100` changed to `/90`: **2 fail** |
| KV dotted leaders | yes | yes | yes. M5, leaders removed: 5 fail |
| Table header + "rule lines" | inverted header | yes | **NO.** M7, rule removed: 21 pass |
| Confirm pills (selected inverted) | yes | yes, but with a half row above (see 3.4) | yes. M6: 3 fail |
| Home complications row | yes (clock, battery and wifi glyphs, values) | **not visible**, below the fold | partly (128 only) |

**Blocking: the goldens can't see the title or the table rule.** `image_to_ascii` (`render.py:710`) counts any pixel with luminance above 64 as on. The title bar `#0071bc` is about 87 and the title text is white, so both are on and the title vanishes from every golden. The table rule `#205493` (about 75) is drawn on the last row of the yellow header (`render.py:285-288`), which is already on. So header-title and table-rule regressions pass the suite. Fix: threshold per colour, or snapshot a reduced palette (for example a distinct character per theme colour), then re-run M2, M3 and M7 to confirm they fail.

**Should fix: the table "thin rule lines" claim.** The only rule is one line under the header, painted inside the header's own last pixel row (`render.py:285`). Data rows have no rules.

**Should fix: the fixtures aren't valid protocol frames.** Checked against the vendored schema with jsonschema, 9 of 10 sample views fail. `view` has `additionalProperties: false` and no `mode` or `complications` field, and `block_progress` has no `elapsed`. As a result:

- The **"CLASSIC" mode indicator** in every golden never appears on the device. `panel.py:319-326` passes no `mode=` and menud sends none in `view`, so on hardware the right side of the header shows only the jobs badge or page counter.
- The progress golden's `0:12` comes from a field menud cannot send.

Build the fixtures as schema-valid frames, and pass `mode` and `complications` through `render()` keyword arguments the way the panel does. If the mode indicator is wanted, have the panel take it from `device.summary.mode` (`panel.py:319`).

## 3. Text clipping, both sizes

I hooked `ImageDraw.text` and flagged any text box past x=128, using the sample views plus stress views: 5-column tables with long SSIDs, 6-level breadcrumbs, long list, KV and status labels, 16-character confirm labels, oversized complication values, titles with badges and page counters, and a full footer with a "driven by" tag. DejaVu Sans Mono 10 is 6.016 px per character and `COLS` is 21.

### 3.1 Blocking: text blocks clip by 1-2 px at both sizes (`render.py:180`)

The refresh moved text from x=1 to x=3 (`body.row(..., x=2)`), but `wrap()` still fills to `COLS`=21. 21 x 6.016 + 3 = 129.3 px, so any full-width line loses its last column. **The committed `text_128x128.txt` and `text_128x64.txt` goldens already show "Operating normally on" with the "n" cut** (x 3..129). Fix: wrap to `COLS - 1` here, or keep x=0.

### 3.2 Blocking: long unselected list labels overflow (`render.py:342` and `render.py:377`)

For an unselected item with no branch mark, `room = COLS` (21) but the text starts at `tx = 4`, so "A very long menu lab…" runs to x=130. Fix: take the 4 px indent out of `room` (`COLS - 1` for unselected rows). With a branch mark, the clipped label ends at x=118.0 and the ">" starts at 117.98, which touches without visible overlap. Worth one more character of margin.

### 3.3 Minor: status lines end exactly at the edge (`render.py:421`, `render.py:441`)

19 characters from x=13 reach x=127.3 (box right edge at 128). They aren't cut, but the right margin is 0 px while every other block keeps 2 px.

### 3.4 Clean

No clipping or overlap in: title vs badge, page counter or long mode (`title_cols`, `render.py:757`); footer legend vs "driven by" tag; KV key/value/leaders (the one-row test at `render.py:188` is exactly right); 5-column tables (priority drop and dropped-column sub-rows work); 6-level breadcrumbs (ellipsised at `COLS`); confirm pills (stacking when labels are long works); complications (items that don't fit are dropped).

### 3.5 64px floor

- **Blocking: the QR can't be scanned at 64px** (`render.py:571`). `room` comes from the module constant `BODY_H` (the 128px body) instead of the effective body height, so a 66x66 code goes into a 24px window. At scroll 0 the top half shows and at max scroll the bottom half shows; the whole code never appears. Fix: pass the effective body height into `_blocks`/`_qr`.
- **Should fix: the home complications row is below the fold at 64px** (`render.py:672-673`). It is appended after the blocks, and home has no focus, so at 64px only Host and IP show (max_scroll 14). The "home complications row" claim holds only at 128. Put the row at the top of the body on home, or pin it above the footer.
- **Should fix: confirm at 64px shows a half-cut text row.** The 4px spacer (`render.py:509`) and 15px buttons (`render.py:551`, `render.py:559`) take the body off the 12px grid, so the clamped scroll is 19 and the "system?" line is cut through the middle at the top. That breaks the "whole-row boundary" comment at `render.py:797`. The 2px scrollbar also paints over the right pill's edge.

## 4. Tests, lint, types, deb

| Check | Result |
|---|---|
| Branch suite (`pytest`, whole repo) | **153 passed**, 0 skipped |
| Base `6aeae3f` suite (in a scratch copy) | 105 passed + 3 skipped = **108**. The 3 are the live-menud tests, skipped only because the scratch copy has no sibling `wlanpi-menud`. All base test files are unchanged on the branch except `test_live_menud.py:117` (see §1), and they all pass on the branch. |
| `ruff check fpms tests` | clean |
| `mypy fpms` (tox lint env) | clean, 29 files |
| **`ruff format --check fpms tests`** (tox `formatcheck`, run in CI by `python-format-police.yml`) | **FAIL: 3 files would be reformatted:** `fpms/modules/menud_client/panel.py:316`, `fpms/modules/menud_client/render.py:298`, `tests/menud_client/test_snapshots.py:90`. Base passed. **Blocking.** The commit message's "ruff clean" covers only `ruff check`. |
| deb | `wlanpi-fpms_1.4.26_arm64.deb` built 2026-11-13 23:06. The payload's `render.py`, `glyphs.py`, `panel.py` and `virtual.py` are **byte-identical to HEAD**. |

## 5. Device untouched: not independently verified

I did not contact the device. There is no repo-side sign of a deploy: no plan commit after `695d21b` (2026-11-13 03:20), and the commit is unpushed. I was not allowed to read the implementing worker's session transcript, so unlike `phase3-panel-review.md` §8 I **can't confirm** there was no `ssh`, `scp` or `build-and-deploy.sh` run to the device. The coordinator should check that transcript if it matters.

## Other findings

- **Blocking (device-visible): progress "elapsed" is the panel's uptime** (`render.py:480-482`). With no `elapsed` field (and menud never sends one; see §2), it falls back to `tick`, which `panel.py:364` increments once a second from panel start. Any real progress block shows something like "121:40" after the panel has run two hours. Drop the fallback, or measure from when the job or busy view first appeared.
- Progress fill sits 1 px from the left border and touches the right border at 100% (`render.py:472-474`). Cosmetic.
- The commit message says "monochrome OLED", but the panel is RGB (`DISPLAY_MODE = "RGB"`, DarkTheme) and the refresh uses `LEVEL_COLOURS`. Fix the wording.
- The complications icon is inferred by substring match on `app_id` (`render.py:609-622`), so `"bat"` would also match `plugin.acme.combat`. Prefer the `icon` field from `complications.live` when the client moves to complications.2.

## Required before APPROVE

1. Pass `ruff format` on the 3 files.
2. Fix text-block and list-label right-edge clipping (`render.py:180`, `render.py:342`/`render.py:377`) and regenerate the `text` goldens.
3. Size the QR to the effective body height (`render.py:571`).
4. Remove the uptime-based elapsed fallback (`render.py:481-482`).
5. Make the goldens see the title text and table rule, and show that mutations M2, M3 and M7 then fail.
6. Make the fixtures schema-valid and decide where the header mode indicator really comes from on the device.
