# WebUI menu panel redesign

Status: IMPLEMENTED and VERIFIED APPROVE (Nov 10, 2026) on wlanpi-webui branch redesign/webui-menu-panel; review: docs/reviews/webui-redesign-review.md.
Scope: the browser menu surface in `wlanpi-webui` (`/menu`). Greenfield: the current plugin does not need to be maintained.
Inputs: *FPMS Re-Architecture Plan.md*, *FPMS Re-Architecture - UX TODO.md* (rounds 1 and 2), `protocol/PROTOCOL.md`, `protocol/frames.schema.json`, and the current plugin on `wlanpi-webui` branch `phase2/webui-menu-panel`.

## Summary

The current panel asks the user to choose a protocol shape (Rich, Thin private, Thin mirror) before they can do anything, and then splits each task across a menu column and a separate "Results & Activity" column. Most of the round 1 and round 2 feedback traces back to those two choices.

The redesign has five decisions:

1. **One surface, no mode control.** `/menu` is always a rich private session. The front panel mirror is an optional pane on that page, not a mode. Thin private is removed from the UI and kept only as a developer URL flag.
2. **Every menu node is a page.** The panel has one current location, held in the URL hash (`/menu#network.interfaces`). A branch page lists its children; an action page is its form, its run control and its result. There is no separate results column.
3. **Reading is automatic, changing is explicit.** Opening a read-only action with no parameters runs it. Anything that mutates, needs parameters or needs confirmation waits for a Run button, so a click, a reload or the browser Back button can never change the device.
4. **Device status is always visible.** A one-line status bar sits above every page; the Home page shows the full device card, alerts and complications.
5. **A lost connection never wipes the screen.** Content stays, marked stale, under a banner with a visible retry countdown. Only sign-out and version mismatch are terminal.

`frames.schema.json` is unchanged. The client uses a smaller part of the protocol than today (`nav` and `view` are no longer used on the rich connection).

---

## 1. Review of the current panel

### 1.1 What the feedback says

| # | Feedback (UX TODO) | What the user was trying to do | Underlying cause |
| --- | --- | --- | --- |
| 2 | "Network > Interfaces shows 'No actions run yet'… there are no mechanisms to execute an action here" | See the interfaces | Navigation and results live in two columns with no fixed relationship; a click in the left column did not produce anything in the right one |
| 3 | "There is also no way to change from Rich to Thin. The only option is to mirror panel" | Understand what the toggle offered | Protocol vocabulary (rich, thin, mirror) is the primary control on the page |
| 8 | "We don't have the ability to do Rich and mirror the display" | Use the menu and watch the panel | Mirror was modelled as a mode, so it excluded the menu |
| 9 | "The main menu page should display info similar to the home page of the thin display" | See device state on arrival | The root showed only branch cards; device info lived in another mode |
| 7 | "We have a running list of results — limit to the most recent" | Find the result of the thing just clicked | Results were a log, detached from the node that produced them |
| 6 | "Alternating rows, white text on white background is not visible" | Read a table | UIkit light-theme utility classes used on a themed surface |
| 5 | "WLAN interfaces and adapters seems somewhat redundant" | Find the one place interfaces live | Menu content (fixed in the manifest; not a panel problem) |

Each round fixed the reported symptom by adding a control: a third mode, a mirror checkbox, a device strip, a "previous result" fold. The page now has two overlapping ways to reach the mirror and four containers that can show a result.

### 1.2 Information architecture

- **The first decision is the wrong one.** The header leads with `Mode: Rich (Private) | Thin (Private) | Thin (Mirror panel)` and a `Show panel mirror` checkbox (`templates/partials/menu.html:13-23`). A user who wants the IP address has to parse a transport distinction first. Two badges repeat it: `Active (rich)` and `Rich (private)` (`menu.js:251-253`, `:239-249`).
- **Two controls reach the same thing.** "Thin (Mirror panel)" in the select and the "Show panel mirror" checkbox both attach to the default session, through different code paths and different DOM (`#mirror-mode-container` and `#side-mirror-container` duplicate the screen and D-pad markup, `menu.html:28-83` and `:122-185`). Selecting both shows the panel twice.
- **Thin (private) has no user purpose in a browser.** It shows the same menu and the same results as rich mode through an 8-row screen and a D-pad. It exists because the protocol allows it, and it is labelled so that it sounds like a peer of the other two.
- **Location and outcome are separated.** The left card is "where am I", the right card is "what happened". A result card carries a title but nothing ties it to the highlighted menu item, and after navigating elsewhere the old result stays on screen next to an unrelated menu level.
- **The right column can hold four different things**: the action detail form (`#rich-action-detail`), a job card, a `nav` view card (badge `VIEW`), and the folded previous result. Which one appears depends on node attributes the user cannot see.
- **Device information has no stable home.** It is a strip inside the menu card (`#rich-device-summary`), absent in thin modes, where it is part of the server-rendered home screen instead.

### 1.3 Interaction model

- **Click means three different things.** `handleNodeClick` (`menu.js:599-624`) drills into a branch, or runs an action immediately, or opens a detail form with a Run button. The item gives no cue which will happen.
- **Mutations run on a single click.** The immediate-run rule is "no params and no confirm"; it does not look at `mutates`. Bluetooth > Turn On, Kismet > Start and the mode switches are in that set. This matches the panel, where select means run, but in a browser a list item reads as navigation.
- **Two result paths.** Branch landing views go through `nav` and arrive as `view` frames; actions go through `invoke` and arrive as `job` frames. The same data can be rendered by either path, with different chrome.
- **Location is not addressable.** `currentBranchId` is a JS variable. Reload returns to the root; browser Back leaves the panel; a result cannot be linked.
- **Mode changes reconnect.** `applyMode` closes the socket and opens another (`menu.js:1105-1131`), dropping the private session, its jobs and its results.
- **Keyboard capture is global.** In thin modes a `window` keydown handler takes the arrow keys and Enter whenever focus is not in a form field (`menu.js:1340-1361`), so the page cannot be scrolled by keyboard and Enter on a focused button also sends `center`.
- **Prompts are a non-dismissable modal** (`esc-close: false; bg-close: false`) with no visible expiry, although prompts expire server-side (`expires_sec`).

### 1.4 Connection handling

- Status is a badge with seven possible texts (`Connecting...`, `Connected (rich)`, `Active (rich)`, `Disconnected`, `Connection error`, `Unavailable`, `Session Revoked`), some of which differ only in implementation stage.
- After two abnormal closes the menu is replaced with an "Menu unavailable" alert (`showMenuUnavailable`), discarding what the user was reading.
- Backoff runs from 2 s to 60 s with no countdown; a user cannot tell a 2 s wait from a 60 s one.
- Sending while disconnected raises the toast "Cannot send frame: not connected".
- The client never sends `hello.resume`, so every reconnect is a new session: live jobs are detached and results are lost, although the protocol supports continuing.
- Core being down (`device.summary` = `{"core": "unavailable"}`) is not distinguished from the socket being down.

### 1.5 Visual and accessibility

- The zebra-stripe incident was one instance of a wider pattern: UIkit light-theme utilities and hard-coded colours on a themed page. Still present: `uk-background-muted` on `pre` blocks and on the selected list row (`view_blocks.js:51,73,176`), a hard-coded `#1e87f0` badge (`:185`), and inline hex colours for the whole simulated screen (`menu.html:43-56`).
- Table column `priority` (the schema's hint for narrow surfaces) is ignored; wide tables scroll horizontally on phones.
- The secret reveal uses an inline `onclick` and does not expose its state.
- Menu items are click targets built in JS; the breadcrumb links are `href="#"`.
- The JS unit tests run against hand-written DOM mocks. Round 2's blocker (`HTMLCollection.indexOf`) passed those tests and failed in a browser.

---

## 2. Design

### 2.1 Principles

1. The user picks a destination, never a transport. "Rich", "thin", "session", "mirror" and "mode" do not appear in the UI. ("Mode" is reserved for the device mode: Classic, Hotspot, Server, Bridge.)
2. One node, one page, one result. The result of an action is shown on that action's page and nowhere else.
3. Navigation is safe. Moving around never changes the device.
4. The server owns the menu. The client renders the `menu` frame; it holds no labels, ordering, visibility rules or node-specific behaviour.
5. State survives. Reload, Back, a dropped socket and a visit to another WebUI page all return the user to where they were.

### 2.2 Session shapes

| Shape (plan) | Presentation | Connection |
| --- | --- | --- |
| Rich, private | **The page itself.** Always on. No label. | Connection A: `hello {mode: "rich", session: "new"}`. Opened when `/menu` loads. |
| Thin, mirror of the default session | **"Front panel" pane.** A toggle button in the page header opens it beside the menu. Off by default; the choice is remembered per browser. | Connection B: `hello {mode: "thin", session: "default"}`. Open only while the pane is open. |
| Thin, private | **Not in the UI.** Reachable as `/menu?panel=private` for renderer development and e2e: the Front panel pane, bound to its own session, titled "Panel preview". | Connection B with `session: "new"`. |

This keeps the plan's semantics: all three shapes remain reachable from the WebUI, the mirror is still a thin attach on a second connection (round 2 item 8), and the logout revoke covers both connections because they share the WebUI session id. What changes is that the user sees one surface and one option.

Two lines of the plan describe the old presentation and need rewording when this lands (the designer brief is read-only on the plan): *Sessions* bullet 3 ("via its mode control") and *Phase 2 UX polish round* item 4.

### 2.3 Information architecture

```
/menu
├── Status bar            always visible: host, mode, IP, temp, battery, alerts
├── Home (#)              device card, alerts, complications, section cards
├── Branch page (#<id>)   children of a branch node
├── Action page (#<id>)   form (if any) + run control + result
├── Activity              popover: running jobs
└── Front panel           optional pane: the device's own screen + keys
```

There are three page types and they map one-to-one onto what the `menu` frame contains: the root, `kind: "branch"`, `kind: "action"`.

### 2.4 Navigation model

**Location.** The current node id is the URL hash: `/menu#network.interfaces`. The hash is the node id as sent by the server; the client builds no paths. Empty hash is Home. Browser Back and Forward work, reload restores the page, and links can be shared. A hash that does not resolve in the current `menu` (hidden by role, removed by a device mode switch, typo) shows the "not available" page (section 4.13) rather than an error.

**Moving.** Three ways, all rendering the same `menu` frame:

- **Drill.** A branch page lists its children as rows; selecting one opens its page. This is the only navigation on phones and matches the front panel's own model.
- **Breadcrumb.** `Menu › Network › Interfaces` above the page title. Each segment is a link.
- **Overview sidebar** (viewport ≥ 960 px). The whole tree, top-level sections expanded to one level, the current path expanded and marked. The built-in tree is about 70 nodes in 6 sections with a maximum depth of 4, so it fits without virtualisation.

A **filter box** at the top of the sidebar (and on Home on phones) narrows the tree by label as the user types. It filters server-supplied labels and adds no menu knowledge.

**Row cues.** Each row shows what selecting it will do, derived only from fields on the `menu_node`:

| Node | Row shows | Selecting it |
| --- | --- | --- |
| `kind: branch` | label, child count, chevron | Opens the branch page |
| action, no `params`, no `confirm`, `mutates` false | label | Opens the page and runs the action |
| action with `params` | label with a trailing ellipsis ("Scan…") | Opens the page with the form |
| action with `mutates` true | label, "changes device" marker (icon plus text, not colour alone) | Opens the page with a Run button |
| any node with `provider` | small provider tag | As above |

**The run rule.** An action runs on arrival only if `mutates` is false, it has no `params` and no `confirm`. Everything else waits for an explicit Run. `menu_node.mutates` is already sent by the service (`wlanpi_menud/menu.py:232`). If the field is absent on a node, the client treats the node as mutating.

### 2.5 Action page

An action page has a fixed order: header, form (if `params`), run control, result.

- **Header.** Breadcrumb, title (`label`), and for mutating nodes the line "This changes the device."
- **Form.** Built from `params` exactly as the schema defines them (enum, integer, string, adapter), with labels, defaults, min/max and native validation. Submitting sends `invoke` with `params`.
- **Run control.**
  - Auto-run pages: a Refresh button next to the result's timestamp.
  - Pages with a form or a mutation: a Run button. It is disabled while a job for this node is live and while disconnected.
  - Nodes with `confirm`: pressing Run swaps the button row for the confirm text with `cancel_label` and `confirm_label` buttons; focus moves to Cancel. Confirming sends `invoke` with `confirmed: true`, which the schema provides for rich clients that have shown the confirm text. Destructive confirms use the danger button style.
- **Result.** One region with five states:

| State | From | Shown |
| --- | --- | --- |
| Idle | no job yet | For Run pages: one line saying what Run will do. Never "No actions run yet". |
| Running | `job` `queued` / `progress` | Progress bar (`pct`, or indeterminate when null), `progress.message`, Cancel if `cancellable` |
| Result | `job` `result` | `result.title`, view blocks, "Updated 12:04:31", "shared result" note when `shared` with `cached_at`, Load more when `cursor` is set |
| Error | `job` `error`, or `error` frame for the request | Message by code (section 2.9), core's own message where there is one, Try again |
| Cancelled | `job` `cancelled` | "Cancelled", Run again |

**Result memory.** The client keeps the last result per node for the life of the page (bounded to the 20 most recent nodes). Returning to an action page shows the previous result at once; a read-only page then refreshes it in place, a Run page shows it with its timestamp until the user runs again. This replaces both the running list and the "previous result" fold: the DOM holds one result at a time, and the history is the menu itself.

**Leaving a running job.** Navigating away does not cancel. The job continues, appears in Activity, and its result is stored against its node. If the user is elsewhere when it finishes, a notification offers "View result".

### 2.6 Paging

- `job.result.cursor` present: a "Load more" button under the result, with "Showing 50 of 213" when `total` is sent. It sends `page {job_id, cursor}` and appends the returned rows to the existing table when the columns match, otherwise appends the blocks.
- No cursor: nothing is shown.
- Appended rows are capped at 1,000 in the DOM; past that the oldest page is dropped with a note. This keeps the round 2 bound (constant DOM size under repeated use).
- The Front panel pane pages as the device does (up and down); the server pages for it.

### 2.7 Prompts

Server-sent `prompt` frames on connection A (missing parameters, mid-job selection such as the scan adapter, a confirmation the client did not pre-confirm) open one dialog component:

| `kind` | Control |
| --- | --- |
| `select` | Radio list of `options` (`label`, `detail`) |
| `confirm` | `message`, `cancel_label` / `confirm_label`, focus on Cancel |
| `integer` | Number input with `minimum`, `maximum`, `default` |
| `string` | Text input |
| `form` | One control per field |

The dialog names the node that asked ("Scanner › Scan needs an adapter"), shows time remaining from `expires_sec`, and can be dismissed with Esc or Cancel, both of which send `answer {cancel: true}`. If the prompt expires or is resolved elsewhere the dialog closes and the action page shows Cancelled. A dialog is used rather than an inline form because a prompt can arrive for a job whose page the user has left.

Prompts for the default session are handled only in the Front panel pane, as screen content (section 2.10).

### 2.8 Device information and activity

**Status bar** (every page): hostname, device mode, primary IP, CPU temperature, battery when present, and an alert count. It is fed by `device.summary` and `alerts` on connection A. Fields that are null are omitted rather than shown as dashes. Selecting it goes Home.

**Home page**: the full device card (hostname, model, software version, mode, IP, eth0, wlan0, CPU, temperature, uptime, battery, Bluetooth, profiler, reachability, WLAN adapters, hotspot clients), the alert lines, the `complications` entries, then the top-level sections as cards.

**Activity chip** (page header, shown only when something is running): "1 running". It opens a popover listing live jobs with their node label (a link to the action page), progress, and Cancel where `cancellable`. Own jobs come from `job` frames; the `jobs` topic adds jobs started elsewhere on the device, shown without controls ("started from another surface").

Subscriptions on connection A: `device.summary`, `alerts`, `complications`, `jobs`.

### 2.9 Connection states

One state machine per connection. Connection A drives the page; connection B drives only the pane.

```
            ┌────────── retry now / online / tab visible ─────────┐
            ▼                                                     │
idle ─► connecting ─► handshaking ─► live ──close──► waiting(n s) ┘
            │              │           │
            └──────────────┴───────────┴─► terminal: signed-out | refused | reload-needed
```

| State | Trigger | Page | Copy |
| --- | --- | --- | --- |
| Connecting (first load) | socket opening, before `menu` | Skeleton: status bar and sidebar placeholders | "Connecting to the device menu…" |
| Live | `welcome` and `menu` received | Normal | No indicator. Being connected is not news. |
| Waiting | close 1006 / 1001 / 1013, network error | Content kept and dimmed; Run, Refresh, Cancel and Load more disabled; banner | "Connection lost. Retrying in 4 s." + Retry now |
| Reconnecting | timer fired | Same, banner changes | "Reconnecting…" |
| Service restarting | close 1001 | Same as Waiting | "The menu service is restarting. Retrying in 2 s." |
| Busy | close 1013, or `retry_after_sec` | Same as Waiting, longer delay | "The device has too many menu sessions open. Retrying in 10 s." |
| Signed out | close 4401 / `AUTH_FAILED` | Content kept and dimmed; no retry | "You have been signed out." + Sign in |
| Refused | close 1008 | No retry | "This address is not allowed to open the device menu. Open the WebUI using the device's own hostname or IP." |
| Reload needed | close 4426 / 4400 | No retry | "This page is out of date." + Reload |
| Core unavailable | `device.summary` = `{core: "unavailable"}`, or `CORE_UNAVAILABLE` on a job | Page stays live; status bar shows the condition; affected results show the error state | "Device services are not responding. The menu will work again when they are back." |

Rules:

- **Backoff**: 1, 2, 4, 8, 15, 30 s, capped at 30 s, with ±20 % jitter. The countdown is visible. It resets after a `welcome`.
- **Reconnect at once** on the browser `online` event, when a hidden tab becomes visible, and on Retry now. Do not run timers while the tab is hidden.
- **Resume**: the client sends `hello.resume` with the `epoch`, `session_id` and per-topic `seq` from the last `welcome` and events. On `resumed: true` nothing is redrawn. On `resumed: false` the client replaces the menu and topic data from the snapshot, adopts the `job` frames the server sends for live jobs, and refreshes the current page if it is an auto-run page. It never re-runs a Run page.
- **Menu changes while live** (`menu` frame with a new `revision`, for example after a device mode switch): the tree is replaced; if the current node still exists the page is kept, otherwise the "not available" page is shown.
- **Request errors** (`error` frames with a request `id`) are shown where the request was made, not as toasts:

| Code | Copy |
| --- | --- |
| `FORBIDDEN` | "Your account is not allowed to do this." |
| `CONFLICT` | Core's message, as sent |
| `NOT_FOUND` | "This item is no longer available." |
| `CORE_UNAVAILABLE` | "Device services are not responding." |
| `TIMEOUT` | "This took too long and was stopped." |
| `BAD_REQUEST`, `INTERNAL` | "Something went wrong." plus the server message in a details element |

- **Leaving `/menu`** by htmx navigation closes both connections with 1000. The private session ends after the server's resume grace; returning within it resumes.

### 2.10 Front panel pane

The pane shows the device's default session: what is on the physical display, and the same keys.

- **Header**: "Front panel", a live indicator, Close.
- **Screen**: the current `view` rendered with the shared view-block renderers inside a fixed-ratio frame: `title`, `breadcrumb`, `blocks`, and the `page` position when sent. It uses theme tokens and the monospace stack; the selected row is an inverted bar (text colour as background) so its contrast holds in both themes.
- **Who is driving**: from `view.driven_by`. "You are driving", or "Driven from wlanpi-menu-tui (wlanpi)". Hidden until someone has driven.
- **Keys**: Up, Down, Left, Right, OK, plus Key 1 to Key 3 when the view's `hints` name them. Each key's caption comes from `hints` ("Back", "Select", "Again"); the client holds no key meanings. Buttons send `input`.
- **Keyboard**: arrow keys, Enter and Backspace (left) are captured **only while focus is inside the pane**. The page keeps normal keyboard behaviour everywhere else. The screen region is focusable and says so ("Click or tab here to use arrow keys").
- **Shared prompts**: shown as the screen's own content (`screen: "prompt"`). When `answerable` is false the keys are disabled and the pane says "Waiting for <invoked_by> to confirm". `prompt` frames on connection B are not turned into dialogs; the view is the single source.
- **Notice on first open**: "Buttons here move the device's own screen. Anyone looking at the device sees the same thing."
- **Its own connection state**, shown inside the pane with the same copy as section 2.9. A pane failure never affects the menu.

"Panel preview" (`?panel=private`) is the same component with `session: "new"`, without the driving line and the notice.

### 2.11 Responsive behaviour

| Viewport | Sidebar | Content | Front panel | Status bar |
| --- | --- | --- | --- | --- |
| ≥ 1200 px (`@l`) | 240 px column | Flexible | Docked right column, 320 px, sticky | Full line |
| 960–1199 px (`@m`) | 240 px column | Flexible | Bottom sheet, collapsible to a title bar | Full line |
| < 960 px | None; drill and breadcrumb; filter on Home | Full width | Bottom sheet, full width; expanded it covers the lower half, collapsed it is a 44 px bar showing the screen title | Host, IP and alert count; the rest on Home |

- Touch targets are at least 44 px high. Rows are full-width links.
- Tables drop columns by `priority` (highest number first) until they fit, then scroll horizontally. Dropped columns are reachable through a per-row "details" expander.
- The breadcrumb collapses to a Back link to the parent plus the current title below 640 px.
- The collapsed bottom sheet adds matching bottom padding to the page so it never covers the last row.

### 2.12 UIkit and WebUI conventions

- The blueprint (`wlanpi_webui/menu/`), the `/menu` route, the htmx partial/extends pair and the nav entries in `base.html` stay as they are.
- Markup uses the UIkit components the WebUI already ships: `uk-card`, `uk-breadcrumb`, `uk-nav` (sidebar), `uk-list`, `uk-table`, `uk-form-stacked`, `uk-modal` (prompt dialog), `uk-drop` (activity), `uk-alert` (banners), `uk-icon`.
- Colour comes only from the `app.css` tokens (`--bg-surface`, `--surface-2`, `--text`, `--text-muted`, `--border`, the status pairs). No inline colour, no `uk-background-muted`, no `uk-table-striped`, no `uk-card-secondary`.
- Scripts stay plain deferred files with no bundler, loaded from `base.html`.

---

## 3. Constraints

### 3.1 Protocol unchanged

No change to `frames.schema.json`, `PROTOCOL.md` semantics, or the vendored copy and generated types.

| Frame | Connection A (menu) | Connection B (front panel) |
| --- | --- | --- |
| `hello` | `rich`, `new`, `resume` on reconnect | `thin`, `default` (`new` for preview), `resume` |
| `subscribe` | `device.summary`, `alerts`, `complications`, `jobs` | none |
| `invoke` | yes, with `params` and `confirmed` | no |
| `page`, `cancel`, `answer` | yes | no (`input` answers shared prompts) |
| `input` | no (refused for rich) | yes |
| `nav` | **no longer sent** | no |
| `welcome`, `menu`, `job`, `prompt`, `event`, `error` | handled | `welcome`, `error` handled; others ignored |
| `view` | **no longer expected**; ignored if received | handled |

`nav` stays in the protocol for the TUI and other clients; the browser simply stops needing it, because action results come from jobs and branch pages come from the `menu` frame.

### 3.2 Server is the source of truth

The client may: index the `menu` nodes by id and parent, sort siblings by `order`, filter by label text, and choose a control from `kind`, `params`, `confirm`, `mutates`, `cancellable`, `provider`.

The client may not: hard-code a node id, label, icon or ordering; hide or add nodes; special-case a node's behaviour; decide visibility by role or device mode. A lint test enforces the first of these (section 5.4).

One consequence: sections have no per-node icons unless the protocol later carries them. Home cards use the label and child count.

### 3.3 Thin and mirror rendering

`view_blocks.js` remains the single renderer for all eight block types on both connections, with the mandatory `text` fallback for unknown types. The Front panel pane keeps the existing dual-connection behaviour, `driven_by`, `answerable` and the revoke-on-logout coverage, and the existing container assertions for them stay.

### 3.4 Accessibility

- **Structure**: the sidebar is a `nav` landmark with real links and `aria-current="page"`; branch pages are lists of links; the page title is an `h2`. No custom ARIA tree widget: links and Tab order are enough for a tree this size and avoid a second keyboard model.
- **Focus**: on navigation, focus moves to the page title (`tabindex="-1"`). Dialogs trap focus and return it. `:focus-visible` uses the WebUI's existing ring.
- **Keyboard**: every control is a native link, button or form field. `/` focuses the filter. Arrow keys are captured only inside the Front panel pane.
- **Announcements**: one polite live region reports job state changes ("Scan finished", "Connection lost"); the result region sets `aria-busy` while running. Errors use `role="alert"`.
- **Contrast**: text pairs meet 4.5:1 and control boundaries 3:1 in both themes, checked by a test over the tokens (section 5.4). Status is never colour alone: levels carry an icon and a word.
- **Secrets**: `kv` items with `secret` are masked with a real toggle button exposing `aria-pressed`.
- **Motion**: no animation beyond UIkit defaults; progress respects `prefers-reduced-motion`.

### 3.5 Parity with the front panel

Everything the panel shows is reachable in the browser without opening the pane:

| On the panel | In the browser |
| --- | --- |
| Home: host, mode, IP, model, temperature | Status bar and Home device card (plus the fields the panel has no room for) |
| Home: alerts, complications | Status bar count; Home lists |
| Home: "core unavailable" | Status bar condition and Home notice |
| Menu levels, order, visibility | Same `menu` frame |
| Result screens | Action page, same blocks |
| Key hints | Buttons with the same captions (pane); Run, Refresh, Back (page) |
| Busy screen | Running state |
| Prompt and confirm screens | Prompt dialog; inline confirm |
| "Driven by" | Front panel pane |

Note: the brief refers to a 128x64 panel; the plan describes 128x128. Nothing here depends on the size, since the server pages thin views from `hello.capabilities.screen`.

---

## 4. Wireframes

Desktop frames are drawn at ≥ 1200 px. `[ ]` is a button, `( )` a radio, `›` a link chevron.

### 4.1 Home, desktop

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ wlanpi-a1b2 · Classic · 192.168.1.42 · 48 °C · 🔋 87%            ⚠ 1 alert   │
├──────────────────────────────────────────────────────────────────────────────┤
│ Device menu                                              [ Front panel ]     │
├───────────────┬──────────────────────────────────────────────────────────────┤
│ [Filter…    ] │ ┌ Device ─────────────────────────────────────────────────┐  │
│               │ │ Hostname  wlanpi-a1b2      Model     WLAN Pi Pro        │  │
│ ▸ Home        │ │ Mode      Classic          Software  3.4.0              │  │
│   Network     │ │ IP        192.168.1.42     Uptime    2 d 4 h            │  │
│   Bluetooth   │ │ eth0      192.168.1.42     CPU       3 %   48 °C        │  │
│   Utils       │ │ wlan0     —not connected   Battery   87 % charging      │  │
│   Modes       │ │ Bluetooth off              Profiler  stopped            │  │
│   Apps        │ └─────────────────────────────────────────────────────────┘  │
│   System      │ ⚠ Warning  Time not synchronised                             │
│               │                                                              │
│               │ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐           │
│               │ │ Network    › │ │ Bluetooth  › │ │ Utils      › │           │
│               │ │ 7 items      │ │ 4 items      │ │ 6 items      │           │
│               │ └──────────────┘ └──────────────┘ └──────────────┘           │
│               │ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐           │
│               │ │ Modes      › │ │ Apps       › │ │ System     › │           │
│               │ │ 4 items      │ │ 3 items      │ │ 7 items      │           │
│               │ └──────────────┘ └──────────────┘ └──────────────┘           │
└───────────────┴──────────────────────────────────────────────────────────────┘
```

### 4.2 Branch page

```
├───────────────┬──────────────────────────────────────────────────────────────┤
│ [Filter…    ] │ Menu › Apps › Scanner                                        │
│               │                                                              │
│   Home        │ Scanner                                                      │
│   Network     │ ┌──────────────────────────────────────────────────────────┐ │
│   Bluetooth   │ │ Scan…                                                  › │ │
│   Utils       │ ├──────────────────────────────────────────────────────────┤ │
│   Modes       │ │ Scan (no hidden)…                                      › │ │
│ ▾ Apps        │ ├──────────────────────────────────────────────────────────┤ │
│     Kismet    │ │ Scan to CSV                         ⚡ changes device  › │ │
│     Profiler  │ ├──────────────────────────────────────────────────────────┤ │
│   ▸ Scanner   │ │ Scan to PCAP                                 2 items   › │ │
│   System      │ └──────────────────────────────────────────────────────────┘ │
└───────────────┴──────────────────────────────────────────────────────────────┘
```

### 4.3 Action page, read-only (runs on arrival)

```
│ Menu › Network › Interfaces                                                  │
│                                                                              │
│ Interfaces                                  Updated 12:04:31   [ Refresh ]   │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ Ethernet                                                                 │ │
│ │ Name     State   IP address        MAC                 Speed             │ │
│ │ eth0     up      192.168.1.42/24   dc:a6:32:00:00:01   1000              │ │
│ │ WLAN                                                                     │ │
│ │ Name     State   IP address        MAC                 Driver            │ │
│ │ wlan0    down    —                 00:c0:ca:00:00:02   mt7921u           │ │
│ │ wlan1    down    —                 00:c0:ca:00:00:03   iwlwifi           │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
```

All rows share one background; hover is one step lighter (`--surface-2`).

### 4.4 Action page with parameters, idle

```
│ Menu › Apps › Scanner › Scan                                                 │
│                                                                              │
│ Scan                                                                         │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ Adapter                                                                  │ │
│ │ [ wlan0                                                              ▾ ] │ │
│ │                                                              [ Run ]     │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│ Choose an adapter and select Run to scan.                                    │
```

### 4.5 Running

```
│ Scan                                                                         │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ Adapter  [ wlan0                                                     ▾ ] │ │
│ │                                                       [ Run ] (disabled) │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ Scanning on wlan0…                                          [ Cancel ]   │ │
│ │ ████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  35 %               │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
```

### 4.6 Result with paging

```
│ Scan                                        Updated 12:06:10                 │
│ ┌ form as 4.4 ─────────────────────────────────────────────────── [ Run ] ─┐ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ SSID             BSSID               Ch    Signal   Security             │ │
│ │ corp-wifi        aa:bb:cc:00:11:22   36    -52      WPA3                 │ │
│ │ guest            aa:bb:cc:00:11:23   36    -53      Open                 │ │
│ │ …                                                                        │ │
│ ├──────────────────────────────────────────────────────────────────────────┤ │
│ │ Showing 50 of 213                                      [ Load more ]     │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
```

### 4.7 Mutating action, idle, then inline confirm

```
│ Menu › System › Reboot                                                       │
│                                                                              │
│ Reboot                                                                       │
│ ⚡ This changes the device.                                                  │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ Restarts the WLAN Pi.                                      [ Reboot ]    │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │

        after pressing the button (focus on Cancel):

│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ Reboot the WLAN Pi now?                     [ Cancel ]  [ Reboot ]       │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
```

The button label is the node's `label` when there is no `confirm`, and `confirm.confirm_label` in the confirm row. The descriptive line is `confirm.message` when present, otherwise omitted.

### 4.8 Error and cancelled

```
│ Public IPv4                                                    [ Try again ] │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ ✖ Error  Device services are not responding.                             │ │
│ │ ▸ Details                                                                │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │

│ Scan                                                                         │
│ ┌ form ────────────────────────────────────────────────────────── [ Run ] ─┐ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│ Cancelled at 12:07:02.                                                       │
```

### 4.9 Prompt dialog (select; other kinds swap the control)

```
        ┌──────────────────────────────────────────────────┐
        │ Scanner › Scan needs an adapter                  │
        │                                                  │
        │ (•) wlan0     mt7921u · 2.4/5/6 GHz              │
        │ ( ) wlan1     iwlwifi · 2.4/5 GHz                │
        │                                                  │
        │ Expires in 0:52          [ Cancel ]  [ Continue ]│
        └──────────────────────────────────────────────────┘
```

### 4.10 Activity popover

```
│ Device menu                               ● 2 running   [ Front panel ]      │
│                                 ┌─────────────────────────────────────────┐  │
│                                 │ Speedtest › Run Test        40 %        │  │
│                                 │ ████████░░░░░░░░░░░░        [ Cancel ]  │  │
│                                 ├─────────────────────────────────────────┤  │
│                                 │ Scanner › Scan to PCAP                  │  │
│                                 │ started from another surface            │  │
│                                 └─────────────────────────────────────────┘  │
```

### 4.11 Front panel pane, docked (≥ 1200 px)

```
│ Device menu                                            [ Front panel ✓ ]     │
├───────────────┬──────────────────────────────────┬───────────────────────────┤
│ [Filter…    ] │ Menu › Network › Interfaces      │ Front panel    ● live  ✕  │
│               │                                  │ ┌───────────────────────┐ │
│   Home        │ Interfaces    12:04:31 [Refresh] │ │ Network               │ │
│ ▾ Network     │ ┌──────────────────────────────┐ │ │───────────────────────│ │
│   ▸ Interfaces│ │ Ethernet                     │ │ │ Interfaces            │ │
│     Eth0 IP…  │ │ eth0   up   192.168.1.42/24  │ │ │█Eth0 IP Config███████ │ │
│     Eth0 VLAN │ │ WLAN                         │ │ │ Eth0 VLAN             │ │
│     LLDP …    │ │ wlan0  down —                │ │ │ LLDP Neighbour        │ │
│   Bluetooth   │ └──────────────────────────────┘ │ │                 2 / 7 │ │
│   Utils       │                                  │ └───────────────────────┘ │
│   Modes       │                                  │ Driven from the TUI       │
│   Apps        │                                  │ (wlanpi)                  │
│   System      │                                  │         [  ▲  ]           │
│               │                                  │ [◀ Back][ OK  ][ ▶ ]      │
│               │                                  │         [  ▼  ]  Select   │
│               │                                  │ Click the screen to use   │
│               │                                  │ arrow keys.               │
└───────────────┴──────────────────────────────────┴───────────────────────────┘
```

### 4.12 Front panel pane, shared confirmation this user cannot answer

```
│ Front panel    ● live  ✕  │
│ ┌───────────────────────┐ │
│ │ Reboot                │ │
│ │───────────────────────│ │
│ │ Reboot the WLAN Pi    │ │
│ │ now?                  │ │
│ │  [Cancel]   Reboot    │ │
│ └───────────────────────┘ │
│ Waiting for wlanpi to     │
│ confirm on the device.    │
│   keys disabled           │
```

### 4.13 Not available (unknown or hidden node)

```
│ Menu                                                                         │
│                                                                              │
│ This item is not available                                                   │
│ It may be hidden in the current device mode, or your account may not have    │
│ access to it.                                                 [ Go to Home ] │
```

### 4.14 Connecting (first load)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ░░░░░░░░░░ · ░░░░░░ · ░░░░░░░░░░░░                                           │
├──────────────────────────────────────────────────────────────────────────────┤
│ Device menu                                                                  │
├───────────────┬──────────────────────────────────────────────────────────────┤
│ ░░░░░░░░      │                                                              │
│ ░░░░░░░░░░    │            ◌  Connecting to the device menu…                 │
│ ░░░░░░        │                                                              │
└───────────────┴──────────────────────────────────────────────────────────────┘
```

### 4.15 Connection lost, waiting and reconnecting

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ⚠ Connection lost. Retrying in 4 s.                          [ Retry now ]   │
├──────────────────────────────────────────────────────────────────────────────┤
│ wlanpi-a1b2 · Classic · 192.168.1.42 · 48 °C            (as of 12:04:31)     │
├───────────────┬──────────────────────────────────────────────────────────────┤
│  sidebar,     │ Interfaces          Updated 12:04:31   [ Refresh ] disabled  │
│  dimmed       │  last result, dimmed                                         │
└───────────────┴──────────────────────────────────────────────────────────────┘

│ ◌ Reconnecting…                                                              │
```

### 4.16 Terminal states

```
│ ✖ You have been signed out.                                    [ Sign in ]   │

│ ✖ This page is out of date.                                     [ Reload ]   │

│ ✖ This address is not allowed to open the device menu. Open the WebUI        │
│   using the device's own hostname or IP.                                     │
```

### 4.17 Core unavailable (socket live)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ✖ Device services are not responding · 192.168.1.42                          │
├──────────────────────────────────────────────────────────────────────────────┤
│  menu stays usable; action pages show the Error state from 4.8               │
```

### 4.18 Phone: Home, branch, action

```
┌──────────────────────────┐ ┌──────────────────────────┐ ┌──────────────────────────┐
│ wlanpi-a1b2 · .1.42  ⚠ 1 │ │ wlanpi-a1b2 · .1.42  ⚠ 1 │ │ wlanpi-a1b2 · .1.42  ⚠ 1 │
├──────────────────────────┤ ├──────────────────────────┤ ├──────────────────────────┤
│ Device menu   [ Panel ]  │ │ ‹ Menu                   │ │ ‹ Network                │
│ [Filter…              ]  │ │ Network                  │ │ Interfaces    [ Refresh ]│
│ ┌ Device ──────────────┐ │ │ ┌──────────────────────┐ │ │ Updated 12:04:31         │
│ │ Mode     Classic     │ │ │ │ Interfaces         › │ │ │ ┌──────────────────────┐ │
│ │ IP       192.168.1.42│ │ │ ├──────────────────────┤ │ │ │ Ethernet             │ │
│ │ Temp     48 °C       │ │ │ │ Eth0 IP Config     › │ │ │ │ eth0  up  .1.42/24 ▸ │ │
│ │ Battery  87 %        │ │ │ ├──────────────────────┤ │ │ │ WLAN                 │ │
│ │ ▸ More               │ │ │ │ Eth0 VLAN          › │ │ │ │ wlan0 down —       ▸ │ │
│ └──────────────────────┘ │ │ ├──────────────────────┤ │ │ │ wlan1 down —       ▸ │ │
│ ⚠ Time not synchronised  │ │ │ LLDP Neighbour     › │ │ │ └──────────────────────┘ │
│ ┌──────────────────────┐ │ │ ├──────────────────────┤ │ │  ▸ on a row shows the    │
│ │ Network   7 items  › │ │ │ │ CDP Neighbour      › │ │ │    dropped columns       │
│ ├──────────────────────┤ │ │ ├──────────────────────┤ │ │                          │
│ │ Bluetooth 4 items  › │ │ │ │ Public IPv4        › │ │ │                          │
│ ├──────────────────────┤ │ │ ├──────────────────────┤ │ │                          │
│ │ Utils     6 items  › │ │ │ │ Public IPv6        › │ │ │                          │
│ └──────────────────────┘ │ │ └──────────────────────┘ │ │                          │
└──────────────────────────┘ └──────────────────────────┘ └──────────────────────────┘
```

### 4.19 Phone and tablet: Front panel sheet, expanded and collapsed

```
┌──────────────────────────┐ ┌──────────────────────────┐
│ wlanpi-a1b2 · .1.42  ⚠ 1 │ │ wlanpi-a1b2 · .1.42  ⚠ 1 │
├──────────────────────────┤ ├──────────────────────────┤
│ ‹ Network                │ │ ‹ Network                │
│ Interfaces    [ Refresh ]│ │ Interfaces    [ Refresh ]│
│ ┌──────────────────────┐ │ │ ┌──────────────────────┐ │
│ │ eth0  up  .1.42/24 ▸ │ │ │ │ Ethernet             │ │
├──────────────────────────┤ │ │ eth0  up  .1.42/24 ▸ │ │
│ Front panel  ● live ▾  ✕ │ │ │ WLAN                 │ │
│ ┌──────────────────────┐ │ │ │ wlan0 down —       ▸ │ │
│ │ Network              │ │ │ │ wlan1 down —       ▸ │ │
│ │ Interfaces           │ │ │ └──────────────────────┘ │
│ │█Eth0 IP Config██████ │ │ │                          │
│ │ Eth0 VLAN      2 / 7 │ │ │                          │
│ └──────────────────────┘ │ │                          │
│ You are driving          │ │                          │
│        [ ▲ ]             │ │                          │
│ [◀ Back][ OK ][ ▶ ]      │ ├──────────────────────────┤
│        [ ▼ ]    Select   │ │ Front panel · Network ▴ ✕│
└──────────────────────────┘ └──────────────────────────┘
```

### 4.20 Front panel pane: its own connection lost

```
│ Front panel   ⚠ offline ✕ │
│ ┌───────────────────────┐ │
│ │ last screen, dimmed   │ │
│ └───────────────────────┘ │
│ Connection lost.          │
│ Retrying in 8 s.          │
│ [ Retry now ]             │
│   keys disabled           │
```

---

## 5. Implementation plan

All paths are in `wlanpi-webui` unless stated. No change in `wlanpi-menud` apart from this document.

### 5.1 Components

New files under `wlanpi_webui/static/js/menu/`, each a plain deferred script exporting through the same UMD wrapper `view_blocks.js` uses, so Node can load them without a bundler.

| File | Responsibility | DOM? |
| --- | --- | --- |
| `connection.js` | `MenuConnection`: socket lifecycle, state machine from section 2.9, backoff with jitter and countdown ticks, `hello` with `resume`, request ids, close-code classification, `online` / `visibilitychange` hooks. Takes `WebSocket`, timers and clock by injection. Used twice (A and B). | No |
| `store.js` | State and pure reducers: menu index (by id, children by parent, sorted by `order`), `device`, `alerts`, `complications`, device jobs, own jobs by `job_id`, last result per node (LRU 20), active prompt. `applyFrame(state, frame)` returns the next state. Selectors: `childrenOf`, `pathTo`, `runPolicy(node)` (auto, run, confirm). | No |
| `router.js` | Hash to node id and back; `hashchange` subscription; `resolve(state, hash)` gives home, branch, action or not-available. | Minimal |
| `pages.js` | Renderers: status bar, sidebar and filter, Home, branch page, action page (form, run control, result states), not-available, banners. Each is `render(container, viewModel)`. | Yes |
| `prompt_dialog.js` | The prompt dialog for the five kinds, expiry countdown, answer and cancel. | Yes |
| `activity.js` | Activity chip and popover. | Yes |
| `front_panel.js` | The pane: screen frame, driven-by, hint-driven keys, focus-scoped keyboard, docked and sheet layouts, own connection states, `?panel=private`. | Yes |
| `main.js` | Wiring: create connections, subscribe store to frames, route, render, delegated event handling, htmx lifecycle (`init` on swap-in, close both sockets on swap-out), `localStorage` for the pane preference. Exposes a small `window.wlanpiMenu` for tests (`getState`, `connections`). | Yes |

Other new or rewritten files:

| File | Change |
| --- | --- |
| `templates/partials/menu.html` | Rewritten: static shell only (status bar, header, sidebar, content, pane and dialog mount points, live region). About 60 lines, no inline styles. Root keeps `id="menu-panel"` and `data-panel="menu"`. |
| `static/css/menu.css` | New: layout grid, sidebar, rows, result states, front panel frame and sheet, stale dimming. Tokens only. Linked from `base.html` after `app.css`. |
| `templates/base.html` | Replace the `menu.js` script tag with the `menu/*.js` tags in dependency order; add the `menu.css` link. Nav entries unchanged. |
| `static/js/view_blocks.js` | Kept, with edits (section 5.3). |

### 5.2 Deleted

- `wlanpi_webui/static/js/menu.js` (1,411 lines), including the mode control, both mirror implementations, the detail pane, `pruneToLatestResult`, `renderRichView` and the `nav` path.
- All markup in `templates/partials/menu.html` (mode select, mirror checkbox, two simulated screens, two D-pads, Results & Activity card).
- `tests/js/menu_mode.test.mjs`, `tests/js/menu_reconnect.test.mjs`, `tests/js/menu_ux_round2.test.mjs`. They assert the old DOM and API. Their intent is carried into the new suites (section 5.4 lists the mapping).
- `verify-container/drive_menu_click.mjs` and `verify-container/drive_device_menu_click.mjs` (the `vm` harness with hand-written DOM mocks).
- The `.uk-table-striped` neutralising rule in `app.css` once no markup uses the class.

### 5.3 Reused

- Unchanged: `wlanpi_webui/menu/` blueprint and route, `templates/extends/menu.html`, the nav entries and `navSection` hook in `app.js`, `wlanpi_webui/menud.py` and the logout revoke in `auth/auth.py`, the nginx `/ui/ws` config and `auth_request`, `protocol/frames.schema.json` (vendored), `scripts/vendor-sync.sh`, `scripts/generate-types.sh`, `static/js/types/frames.d.ts`, the Python tests (`tests/test_menu.py`, `test_menud.py`, `test_auth.py`).
- `view_blocks.js`, same public API (`renderBlocks`, `renderBlock`, `mountQrCodes`, `escapeHtml`, `escapeAttr`), with these edits:
  - remove `uk-background-muted` and the hard-coded badge colour; selected list row and `pre` blocks use `menu.css` classes;
  - tables honour column `priority` (data attributes; `pages.js` drops columns by container width) and gain the per-row details expander;
  - `list` items with `node` render as links to `#<node>`;
  - the secret toggle becomes a delegated handler with `aria-pressed`; the inline `onclick` and `window.wlanpiToggleSecret` go;
  - `renderConfirm` gains a non-interactive variant for the Front panel screen.
- `tests/js/view_blocks.test.mjs`: kept and extended for the edits above.
- From the old `menu.js`, as reference only: the close-code handling and the stale-socket guard (`this !== ws`) are re-expressed in `connection.js`.

### 5.4 JS tests

Runner stays `node --test tests/js/*.test.mjs`. Two changes to how tests are written:

1. Logic is tested without a DOM. `connection.js` and `store.js` have none, so the largest share of behaviour needs no mocks.
2. DOM tests run on **jsdom** (new devDependency), not hand-written mocks. This removes the class of bug that passed round 2 (`HTMLCollection` behaving like an array).

| Suite | Covers | Replaces |
| --- | --- | --- |
| `connection.test.mjs` | Every transition in 2.9 with a fake socket and fake timers: backoff sequence and cap, jitter bounds, reset on `welcome`, 4401 / 1008 / 4426 / 4400 terminal, 1013 and `retry_after_sec`, 1001 copy state, no timers while hidden, immediate retry on `online`, `hello.resume` contents, stale-socket events ignored, no reconnect loop when closed by the client | `menu_reconnect.test.mjs` |
| `store.test.mjs` | `applyFrame` for every S-to-C frame type using the samples in `wlanpi-menud/protocol/samples`; menu replace on new `revision`; `runPolicy` truth table including `mutates` absent; result LRU bound; job adoption after a non-resumed reconnect; `seq` gap marks a topic stale; 100 invokes leave state size constant | `menu_ux_round2.test.mjs` (bounds) |
| `router.test.mjs` | Hash round-trip for dotted ids; unknown and hidden ids resolve to not-available; menu replacement while on a removed node | new |
| `pages.test.mjs` (jsdom) | One test per wireframe 4.1 to 4.8 and 4.13 to 4.17: structure, row cues, auto-run sends exactly one `invoke`, mutating page sends none until Run, confirm flow sends `confirmed: true`, Load more sends `page` and appends, stale state disables controls, focus moves to the title | `menu_mode.test.mjs`, `menu_ux_round2.test.mjs` (click-to-view, device strip) |
| `prompt_dialog.test.mjs` (jsdom) | Five kinds; exactly one of `value` / `values` / `confirmed` / `cancel` in the answer; Esc cancels; expiry closes | part of `menu_mode.test.mjs` |
| `front_panel.test.mjs` (jsdom) | Second connection opens only when the pane opens and closes with it; `hello` is thin/default (thin/new with `?panel=private`); keys built from `hints`; arrow keys ignored unless focus is in the pane; `answerable: false` disables keys; `driven_by` text; pane failure leaves connection A untouched | `menu_ux_round2.test.mjs` (dual connection) |
| `conventions.test.mjs` | Static checks over `static/js/menu/`, `view_blocks.js`, `menu.css` and the partial: no `uk-table-striped`, `uk-background-muted` or `uk-card-secondary`; no inline `style=` and no colour literals outside `app.css`; no string literal matching a built-in node id or label (guards 3.2); no "rich", "thin", "mirror" or "session" in user-visible strings | new |
| `contrast.test.mjs` | Parses the light and dark token blocks in `app.css` and computes WCAG ratios for every foreground/background pair `menu.css` uses: 4.5:1 for text, 3:1 for control boundaries and the selected-row bar | new (the zebra-stripe guard) |

Schema conformance: every frame the client builds in tests is validated against the vendored `frames.schema.json` with `ajv` (devDependency), so a client-side drift fails in unit tests rather than in the container.

### 5.5 Container e2e

`verify-container/run_verification.py` keeps its structure (menud, Flask, nginx, minted sessions).

- **Kept as they are** (protocol level, raw WebSocket): handshake and auth, Origin check, invoke and block types, `nav` (still a protocol feature, used by the TUI), default-session fan-out and `driven_by`, private thin session (6e), dual connection (6f), logout revoke of both connections, grouped interfaces table.
- **Replaced**: the two `drive_*.mjs` `vm` harnesses become one browser-driven script, `drive_menu_ui.mjs`, run with **Playwright and headless Chromium** against the real `/menu` through nginx. This is the test layer that would have caught the round 2 blocker. It adds roughly 300 MB to the verify image; if that is not acceptable, the fallback is the same script on jsdom with Node's built-in `WebSocket`, which keeps real markup, real scripts and a real backend but not a real layout engine.
- **UI assertions** (each maps to a decision in this document):
  1. `/menu` loads with no mode control; Home shows hostname and IP from the stub core.
  2. Home → Network → Interfaces by clicks: the grouped table appears with no further click; the URL hash is `#network.interfaces`.
  3. Reload on that URL restores the page and its result; Back returns to Network.
  4. Opening a mutating node sends no `invoke` (asserted in menud's audit log); pressing Run, then confirming, sends one.
  5. An action with parameters shows the form; Run returns a table; Load more appends rows and the count changes.
  6. A job that needs selection opens the dialog; choosing an option completes the job.
  7. Front panel: toggling opens a second socket; an Up key press there changes the view a raw TUI-style client on the default session receives, and that client's input updates the pane with a "Driven from" line. The private menu is unaffected.
  8. Restarting menud mid-session: banner with countdown appears, content remains, the page recovers without a reload, and an auto-run page refreshes once.
  9. Logout closes both sockets and shows the signed-out state.
  10. At a 390 px viewport: no sidebar, drill navigation works, the pane opens as a sheet, no horizontal page scroll.
  11. Accessibility: `axe-core` reports no serious or critical violations on Home, a result page and the open pane, in both themes; a Tab-only walk reaches a result from Home.
  12. 100 consecutive Refresh presses leave the result region's node count unchanged.

### 5.6 Order of work

| Step | Content | Exit |
| --- | --- | --- |
| 1 | `connection.js`, `store.js`, `router.js` with their suites; jsdom and ajv added | Logic suites green; no UI yet |
| 2 | New partial, `menu.css`, `pages.js`, `main.js`; `view_blocks.js` edits | Home, branch and action pages work against a device; old `menu.js` removed from `base.html` |
| 3 | `prompt_dialog.js`, `activity.js`, paging | Scan with adapter selection and Load more work |
| 4 | `front_panel.js`, responsive sheet, `?panel=private` | Dual connection e2e green |
| 5 | Conventions and contrast suites, Playwright e2e, delete the old files and tests | All suites green; verification bar below met |

Steps 1 and 2 are the minimum that replaces the current panel; 3 to 5 can land as separate reviews.

### 5.7 Verification bar

- No control on `/menu` names a protocol mode.
- Every action result appears on its node's page; the page holds one result region.
- No mutating node runs without an explicit Run press (unit and e2e).
- Reload and Back preserve location.
- A dropped connection keeps content on screen and recovers without a reload.
- Front panel pane and menu work at the same time; logout closes both.
- `frames.schema.json` and the vendored copy are byte-identical to before.
- Contrast and conventions suites green in both themes.

### 5.8 Open items for the coordinator

1. **Plan wording.** *Sessions* bullet 3 and *Phase 2 UX polish round* item 4 describe the three-way mode control. They need rewording to "one surface plus the Front panel pane; thin private available by URL flag".
2. **Playwright in the verify image**, or the jsdom fallback (section 5.5).
3. **Single-click mutations.** The run rule makes Bluetooth > Turn On and similar nodes two clicks in the browser (open, Run) where the panel takes one. This is deliberate; say so if one-click parity with the panel matters more than safe navigation.
4. **Section icons.** Not possible without a protocol field. Out of scope here by the "schema unchanged" constraint; worth an additive `icon` on `menu_node` later if the cards feel bare.
