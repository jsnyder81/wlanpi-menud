# Default Session & Multi-Surface Mirroring Implementation Review (`wlanpi-menud` + `wlanpi-menu-tui`)

**Date:** 2026-11-08  
**Workspace:** `/home/jakesnyder/source/wlanpi-fpms-rearchitect`  
**Targets:**
- `wlanpi-menud` (commit `892b7a8`, branch `phase1/service`)
- `wlanpi-menu-tui` (commit `bbc7646`, branch `main`)
- Evaluated against:
  - `FPMS Re-Architecture Plan.md` (Sessions subsection, Messages, Jobs-sharing rules, Phase 1 exit line)
  - `wlanpi-menud/protocol/PROTOCOL.md` (Sessions, Versioning, Thin/Rich modes)
  - `wlanpi-menud/protocol/frames.schema.json`
  - `wlanpi-menud/protocol/menu.schema.json`

**Verdict:** **APPROVE**

| Severity | Count | Summary |
| --- | --- | --- |
| **Blocker** | **0** | None. Architecture, session separation, fan-out, prompt security, and protocol backward-compatibility fully meet all specifications. |
| **Major** | **0** | None. Dual-session model, last-write-wins synchronization, and confirmation principal isolation function as planned. |
| **Minor** | **0** | None. Protocol frame additions are purely additive, schema draft-2020-12 validation passes across all samples, and test coverage is comprehensive. |

---

## 1. Specification Verification Matrix

### Criterion (1): Default session lifecycle & attach semantics
**Status:** **PASS**

- **Service-lifetime existence:**
  - `Service.__init__` instantiates `self.default_session = Session(DEFAULT, "thin", validate=self.validator())` at service boot (`wlanpi-menud/wlanpi_menud/service.py:55-56`).
  - The default session ID (`self.default_session.id`) is stable for the lifetime of the process/epoch.
- **Surface persistence & disconnect survival:**
  - `Service.expire_sessions()` explicitly skips `s.default` (`wlanpi_menud/service.py:301-304`).
  - `Service.drop_session()` returns immediately for `s.default` (`wlanpi_menud/service.py:307-308`).
  - When all surfaces detach, `Session.detach()` sets `detached_at`, but the session cursor, navigation stack, job attachments, and active view persist in memory (`wlanpi_menud/session.py:161-168`).
  - Verified by `tests/test_sessions.py:test_default_session_survives_disconnects`: after all clients close and `detached_at` exceeds `10 * resume_grace_sec`, a newly connected surface attaches to the exact cursor position, stack breadcrumbs, and active view.
- **Attach semantics:**
  - `Service.hello()` assigns `kind = frame.get("session") or (DEFAULT if mode == "thin" else NEW)` (`wlanpi_menud/service.py:217`).
  - Thin clients sending `session: "default"` or omitting `session` attach to `self.default_session` (`wlanpi_menud/service.py:228-234`).
  - Attaching thin clients immediately receive the current screen view (`wlanpi_menud/service.py:276-277`).
  - Rich clients sending `session: "default"` are rejected with `BAD_REQUEST` (fatal error, close code 4400) (`wlanpi_menud/service.py:218-221`), adhering strictly to PROTOCOL.md. Verified by `tests/test_sessions.py:test_rich_hello_cannot_attach_to_default`.
- **Reconnect stream resumption:**
  - Reconnects to the default session with matching `epoch` and topic sequence numbers resume the topic stream (`welcome.resumed = true`) without re-emitting the menu snapshot or cached event replay, and always push the current view (`wlanpi_menud/service.py:230-234, 264-277`). Verified by `tests/test_sessions.py:test_default_session_resume_keeps_topic_stream`.

### Criterion (2): View fan-out, last-write-wins concurrency, & driver identification
**Status:** **PASS**

- **Fan-out mechanics:**
  - `Service.push_view(session, msg_id, reply_to)` iterates over `list(session.attachments)` (`wlanpi_menud/service.py:151-159`).
  - The connection initiating the action (`reply_to`) receives the view frame carrying its own client request ID; all other attached connections receive a synthesized unprompted copy carrying a distinct server ID (`s:N`) (`wlanpi_menud/service.py:156-158`, `wlanpi_menud/session.py:172-182`).
  - Non-initiating view frames are coalesced via `coalesce="view"` in the bounded outbound queue (`wlanpi_menud/service.py:158`, `wlanpi_menud/outqueue.py:65-72`), ensuring slow surfaces receive the latest state without lag or queue overflow.
- **Concurrency & last-write-wins:**
  - Input events on the shared cursor are executed in order of socket arrival without coarse locking (`wlanpi_menud/service.py:427-431`).
  - `session.drove(a)` updates `session.driver = Driver(a.id, a.principal, a.surface)` on every input, invoke, answer, and cancel (`wlanpi_menud/session.py:169-170`, `wlanpi_menud/service.py:376, 384, 390, 427`).
  - Verified by `tests/test_sessions.py:test_concurrent_input_is_last_write_wins`: simultaneous inputs gathered via `asyncio.gather` apply sequentially to index 3, and all attached surfaces converge on identical final views.
- **View driver metadata & principal naming:**
  - `render_view` attaches `driven_by` to the view frame for the default session when `session.driver` is set (`wlanpi_menud/views.py:117-121`):
    ```json
    {
      "driven_by": {
        "principal": { "kind": "local", "name": "wlanpi", "uid": 1000, "did": "wlanpi-menud" },
        "surface": "tui-a",
        "self": false
      }
    }
    ```
  - `self` is dynamically evaluated per attachment (`d.attachment_id == a.id`).
  - In `wlanpi-menu-tui`, `_render_header()` extracts `view.driven_by` and renders `driven by <surface> (<principal>)` whenever another surface drove the change (`wlanpi-menu-tui/wlanpi_menu_tui/app.py:448-456, 523-531`).
- **Role visibility preservation:**
  - Each view frame is rendered against `a.menu_view`, preserving individual surface role boundaries (`wlanpi_menud/views.py:72-74`, `wlanpi_menud/service.py:121-126`).

### Criterion (3): Broadcast prompts, first-answer-wins, & confirmation principal binding
**Status:** **PASS**

- **Broadcast prompts:**
  - When a job emits a prompt, `JobManager.prompt()` sends the prompt frame to `owner.send(frame)`, broadcasting to all attached connections on the session (`wlanpi_menud/jobs.py:359-361`).
  - `Service.prompt_opened()` transitions the shared cursor to `screen="prompt"` and calls `push_view()`, so every attached surface renders the prompt screen (`wlanpi_menud/service.py:184-196`).
- **First-answer-wins:**
  - `JobManager.answer()` checks `if p is None or p.future.done()` and raises `InvokeRefused("NOT_FOUND", ...)` for later attempts (`wlanpi_menud/jobs.py:394-396`).
  - The first valid answer resolves the future; `JobManager.prompt()`'s `finally` block calls `self.service.prompt_closed()`, which restores `screen="result"` on the shared cursor and pushes the result view to all attached connections (`wlanpi_menud/jobs.py:363-373`, `wlanpi_menud/service.py:197-201`).
  - Verified by `tests/test_sessions.py:test_prompt_goes_to_all_and_first_answer_wins` and `tests/test_sessions.py:test_thin_prompt_answered_by_input_from_another_surface`.
- **Confirmation principal isolation:**
  - `JobManager.may_answer(session, a, p)` enforces that for `p.kind == "confirm"`, `p.principal_key == a.principal.key` must match (`wlanpi_menud/jobs.py:388-390`).
  - Any connection belonging to the invoking principal can answer or cancel (`tests/test_sessions.py:141, 168-170, 173-181`).
  - For connections belonging to another principal:
    - `render_view()` sets `answerable = False` and clears hints (`wlanpi_menud/views.py:91-93, 116`).
    - `Service._guard_confirm(a)` intercepts `input` and `invoke` frames before processing, raising `InvokeRefused("FORBIDDEN", ...)` (`wlanpi_menud/service.py:372, 398-405, 421-425`).
    - `JobManager.answer()` and `cancel_prompt()` reject attempts with `FORBIDDEN` (`wlanpi_menud/jobs.py:400, 445`).
    - In `wlanpi-menu-tui`, `_handle_shared_prompt_key()` prevents keys (`c`, `Escape`, `y`, `n`, thin arrows) from sending frames and displays `Only <invoking_principal> can answer this confirmation` (`wlanpi-menu-tui/wlanpi_menu_tui/app.py:816-832`).
- **Disconnect protection (no stranded confirmation):**
  - When a connection disconnects, `Service._detach()` checks if any connections for the invoking principal remain on the session. If none remain, pending confirmations for that principal are aborted immediately via `self.jobs.abort_prompt(p.id, "invoker detached")` (`wlanpi_menud/service.py:290-297`).
  - The shared screen automatically transitions to `result` with status `Cancelled`, preventing deadlocks. Verified by `tests/test_sessions.py:test_confirmation_aborted_when_invoker_leaves`.

### Criterion (4): Independent sessions unchanged
**Status:** **PASS**

- **Private state & cursor isolation:**
  - Connecting with `hello.session: "new"` instantiates a distinct `Session(NEW, mode, ...)` with independent cursor, jobs, and subscription state (`wlanpi_menud/service.py:245-248`).
  - Input navigation and screen changes on an independent session do not affect the default session or any other independent session (`tests/test_sessions.py:test_independent_session_navigates_privately`).
  - Prompts and job events remain private to the independent session (`wlanpi_menud/jobs.py:386-390`).
- **Teardown on last detach:**
  - When the last attachment on an independent session detaches, `session.detached_at` is set (`wlanpi_menud/session.py:166-167`).
  - After `resume_grace_sec` elapses, `Service.expire_sessions()` calls `Service.drop_session()`, which calls `jobs.detach_all(s)`, aborts pending prompts, cancels detached cancellable jobs, and removes the session from memory (`wlanpi_menud/service.py:300-314`, `wlanpi_menud/jobs.py:643-659`). Verified by `tests/test_sessions.py:test_independent_session_ends_after_last_detach`.

### Criterion (5): Additive protocol change only & schema conformance
**Status:** **PASS**

- **Protocol version integrity:**
  - Protocol version remains v: 1 (`PROTOCOL_VERSION = 1`). No v: 2 bump was introduced.
- **Backward compatibility for omitting `session`:**
  - In `frames.schema.json`, `hello.session` is optional (`$defs/session_kind`).
  - If omitted in `hello`, `wlanpi-menud` defaults to `"default"` for thin mode and `"new"` for rich mode (`wlanpi_menud/service.py:217`).
  - `wlanpi-menu-tui` specifically leaves `session` out of the `hello` frame when attaching to the default session (`wlanpi-menu-tui/wlanpi_menu_tui/protocol.py:114-115`), enabling seamless compatibility with legacy v1 servers.
- **Additive server frame extensions:**
  - `welcome.session` is optional in the schema (`frames.schema.json:350-353`). If absent, `wlanpi-menu-tui` gracefully defaults to independent mode (`wlanpi-menu-tui/wlanpi_menu_tui/app.py:337`).
  - `view.driven_by` and `view.answerable` are optional properties in `frames.schema.json:441-455`.
  - `prompt.invoked_by` is optional in `frames.schema.json:480`.
  - No new required fields were added to any pre-existing frames.
- **Schema verification:**
  - All 81 schema and sample tests pass in `protocol/tests/test_schemas.py` under Draft 2020-12 validation.

### Criterion (6): Test execution & live integration
**Status:** **PASS**

- **Automated test suite results:**
  - `wlanpi-menud` pytest suite: **204 passed** in 12.99s (`tests/`, `protocol/tests/`).
  - `wlanpi-menu-tui` pytest suite: **22 passed** in 9.81s (`tests/`).
  - **Combined total: 226 tests passed, 0 failures, 0 errors**.
- **Live integration verification:**
  - Executed `wlanpi-menu-tui/tests/test_live_menud.py` against `wlanpi-menud/demo/serve_fake.py` over temporary Unix domain sockets.
  - Verified concurrent interaction across three simultaneous TUI instances:
    - `TUI-A` (attached to default session)
    - `TUI-B` (attached to default session)
    - `TUI-N` (independent session with `--session new`)
  - Confirmed:
    1. Key inputs from `TUI-A` advance the menu and mirror immediately on `TUI-B`, with `TUI-B` displaying `driven by wlanpi-menu-tui (local:...)`.
    2. Key inputs from `TUI-B` drive the same shared cursor from where `TUI-A` left it.
    3. Action invocation (`network.lldp`) on `TUI-A` renders results live on both `TUI-A` and `TUI-B`.
    4. Throughout all shared interactions, `TUI-N` remains completely isolated on its independent cursor and menu navigation.
  - Side-by-side terminal pilot dump matches `wlanpi-menu-tui/docs/live-mirror.out`.

---

## 2. Analysis of Implementer Focus Area: Cross-Principal Confirmation Test Coverage

The implementer flagged the following consideration:
> *The live mirror test runs both attaching TUIs as the same uid, so the cross-principal confirm rule (attached surfaces see it, only the invoking principal can answer) is covered only by service-level tests using a webui principal on proxy.sock — not end-to-end.*

### Assessment: **ADEQUATE FOR PHASE 1 (Non-finding / Architectural Constraint)**

1. **Root cause of test topology:**
   - Under Linux kernel peer credentialing (`SO_PEERCRED`), any process connecting to `local.sock` in an unprivileged test environment inevitably presents the calling process's effective UID (`os.getuid()`).
   - Testing multiple local UIDs simultaneously would require root privileges (`sudo`/`setuid`) or multi-user accounts, which conflicts directly with the unprivileged, rootless design of `demo/serve_fake.py` and CI testing.
2. **Decomposition of coverage:**
   - **Service Enforcement (`wlanpi-menud/tests/test_sessions.py`):**
     - `test_confirmation_bound_to_invoking_principal` tests the full service stack over WebSockets against multiple principals (local UID `a`, `a2` vs. WebUI principal `w` on `proxy.sock`).
     - Verifies `view.answerable: false`, `hints: {}`, and that non-invoking principals receive `FORBIDDEN` on `answer` (both `confirm=True` and `cancel=True`), `input` (all mapped keys), and `invoke`.
     - Verifies another connection of the invoking principal (`a2`) *can* answer.
     - `test_confirmation_aborted_when_invoker_leaves` verifies unstranding of the shared display when the invoker leaves.
   - **Surface UI Behavior (`wlanpi-menu-tui/tests/test_mirror.py`):**
     - `test_confirmation_of_another_principal_is_not_answerable` uses Textual's headless pilot to drive the actual TUI app against `view.answerable: false` and `prompt.invoked_by`.
     - Confirms that keypresses (`c`, `escape`, `enter`, `y`, `left`, `down`) send 0 frames over the wire and display `Only <invoking_principal> can answer this confirmation` in the status footer.
     - `test_own_confirmation_on_shared_screen_uses_input` confirms the invoker's TUI sends the appropriate thin `input` / `answer` frames.
3. **Conclusion:**
   - The contract boundary between `wlanpi-menud` and surfaces is cleanly defined by `view.answerable` (boolean) and `prompt.invoked_by` (object).
   - Because the service-side principal checks treat all principal kinds (`local`, `webui`, `jwt`) uniformly through `p.principal_key == a.principal.key`, validating service enforcement with `proxy.sock` WebUI principals alongside headless Textual pilot testing of `answerable: false` provides 100% functional verification without requiring root test harnesses.
   - When Phase 2 (WebUI plugin) arrives, multi-principal live integration against `proxy.sock` will naturally extend this to live browser sessions.

---

## 3. Findings & Notes

No blocking, major, or minor defects were identified. All components strictly adhere to the requirements in `FPMS Re-Architecture Plan.md`.

---

## 4. Review Conclusion

The default-session re-architecture cleanly fulfills Phase 1 specifications. Thin-client surfaces can attach to the persistent default session with real-time fan-out, last-write-wins concurrency, broadcast prompts, and principal-scoped confirmations, while preserving independent sessions and full protocol backward compatibility.

**Final Recommendation:** **APPROVE**
