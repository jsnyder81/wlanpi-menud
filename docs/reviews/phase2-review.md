# Phase 2 review: WebUI plugin

Scope: wlanpi-webui `66ebf68..398ac01` (209819e proxy + revoke, 398ac01 menu panel + Cockpit) and wlanpi-menud `7ed4c06..d30dc6c` (revoke frame). Checked against "FPMS Re-Architecture Plan.md" Phase 2, Authentication and WebUI browsers. Read-only review; only this file was written.

**Verdict: REQUEST-CHANGES** — 0 blockers, 2 major, 5 minor.

## Test runs (all executed by the reviewer)

| Suite | Result |
| --- | --- |
| wlanpi-menud pytest (`.venv`) | 208 passed |
| wlanpi-webui pytest, every file except `test_cli.py` | all pass (run per file) |
| wlanpi-webui `tests/test_cli.py` | **could not complete**: the run kills its own terminal session (SIGHUP) in this environment, even under `setsid`. The file and `wlanpi_webui/cli` are unchanged since 66ebf68, so it is probably environmental, but it is unverified. |
| WebUI JS (`node --test tests/js`) | 10/10 pass |
| Container e2e (`verify-container/run_verification.py`, debian:trixie + real nginx) | 7/7 pass |
| TS types: regenerated with `json-schema-to-typescript` into scratch and diffed against `static/js/types/frames.d.ts` | **diff empty** |

## Checklist

1. **nginx `/ui/ws`: PASS.**
   - `auth_request /auth/check`, and `X-WebUI-User`/`X-WebUI-Session` are set with `proxy_set_header` from `auth_request_set`, so they overwrite client values. The e2e forged-header test confirms it (`install/etc/nginx/conf.d/wlanpi-menud.conf:2-16`).
   - The only `proxy_pass` to menud is `unix:/run/wlanpi-menud/proxy/proxy.sock` (`:17`). Every other `proxy_pass` goes to `webui`, `librespeed` or `grafana`.
   - The snippet is linked only when menud is installed (webui `debian/postinst`, `install/etc/nginx/link.sh:45-52`; menud postinst/postrm manage the link too). `unlink.sh` removes it.
2. **Origin check: PASS.** `origin_ok` is enforced on `proxy.sock` (`wlanpi_menud/server.py:227`, close 1008) and tested (`tests/test_auth.py:57-64`) and in e2e test 3.
3. **Logout revokes menu sessions: PASS.**
   - `end_session` calls `revoke_menu_session(sid)` inside try/except (`wlanpi_webui/auth/auth.py:~300`), and the helper never raises (`wlanpi_webui/menud.py`).
   - Tests: menud down (`tests/test_menud.py:40`), handshake rejection, and a raising helper (`tests/test_auth.py:224`).
   - e2e test 7 shows the browser socket closed with 4401.
4. **Revoke authorization: PARTIAL, see M1.**
5. **View blocks and types: PASS.** `view_blocks.js:335-352` covers all 8 types, with a text fallback for unknown or malformed blocks and HTML escaping everywhere. The JS tests have one case per type plus the fallback. The types are generated: `scripts/generate-types.sh` and `package.json` are wired to the schema, and the diff was empty.
6. **Cockpit retirement: FAIL, see M2.**
7. **Suites and e2e:** see the table above.

## Findings

### Major

**M1. `revoke` is "any admin on `local.sock`", not WebUI-principal-only.**
- Where: `wlanpi_menud/service.py:393-399`, `wlanpi_menud/config.py:36`.
- Plan and brief: the revoke path should be restricted to the WebUI principal.
- Forged-call test: in the container I ran the real service and called `revoke_menu_session` from several uids against a live WebUI session.
  - An uid not on the allowlist (`nobody`) is refused at connect, so it can't revoke.
  - Uid `wlanpi`, a plain local login that is `admin` by default, **successfully closed another user's WebUI session with 4401**. Root can too.
  - An `operator` (the `wlanpi-menud` group) gets FORBIDDEN, which `tests/test_auth.py` covers.
- The check is `socket_kind == "local" and "admin" in roles`. The error text claims "webui principal", which is not what is enforced.
- The WebUI itself runs as `wlanpi` (`debian/wlanpi-webui.service:10`), so SO_PEERCRED cannot tell the WebUI backend from any other `wlanpi` process.
- Impact: a session-kill denial of service by an account that is already admin. It does not escalate privilege.
- Fix: either add a dedicated role or uid (for example a `webui` entry in `local_uids` that the revoke check requires), or accept the limitation and fix the plan, the error text and the tests. A test should assert that a plain `wlanpi` login is refused.

**M2. Cockpit retirement is a deprecation, and it leaves a broken link.**
- `398ac01` deletes `install/etc/cockpit/cockpit.conf` (`UrlRoot=/app/cockpit`). Everything that sends users to Cockpit is still live, and the redirects still send users to `:9090/app/cockpit/`, which no longer exists without that `UrlRoot`.
  - `wlanpi_webui/cockpit/cockpit.py:15-37` still serves `/cockpit` and `/<task>cockpit`, and `app.py:155-158` registers the blueprint.
  - Nginx redirects remain at `install/etc/nginx/wlanpi_webui.conf:119-125`.
  - The Cockpit card is in `templates/partials/apps_cards.html:70-81`, the dashboard launcher in `templates/partials/dashboard.html:71`, and `cockpit_installed` is still set in `app.py:429`.
  - Tests still assert the old behaviour: `tests/test_apps.py:88-96`, `tests/test_dashboard.py:109`, and `tests/test_menu.py:77` (`test_cockpit_deprecation`).
- On upgrade, the removed `/etc/cockpit/cockpit.conf` is a dpkg conffile. `debian/` has no `rm_conffile` handling, so the file stays on existing devices, and the WebUI keeps linking to it.
- Fix: remove the blueprint, cards, launchers, nginx redirects and tests, or restore `cockpit.conf` and drop the "retired" claim from the changelog. Docs (`README.md:31`, `UI_REDESIGN.md`) also still list Cockpit.

### Minor

- **m1. `revoke_menu_session` returns True even when menud rejects the revoke** (`wlanpi_webui/menud.py`). It never reads the reply, so a FORBIDDEN from M1 is silent and logout does not log it. It also blocks logout for up to 1 s per socket step when menud hangs.
- **m2. Browser reconnect loops** (`static/js/menu.js:79-90`). Only 4401 stops reconnects. A 1008 Origin refusal, or an nginx 401 on upgrade after logout (browser code 1006), retries forever.
- **m3. A duplicate `Host` header crashes the connection handler** (`server.py:227`, `MultipleValuesError`, close 1011). Nginx always sends a single Host, so it is not reachable through the proxy, but the handler should not raise.
- **m4. The snippet stays linked after a menud `remove` followed by a webui upgrade.** The webui postinst and `link.sh` test `/etc/wlanpi-menud/config.toml`, which survives `remove`, so the link returns and `/ui/ws` answers 502.
- **m5. `test_cli.py` kills the test runner's session** in this environment. Run it in CI or on a device to confirm it is not a regression.

## Not blocking

The e2e `/auth/check` is a fake WebUI server, not the real Flask app. The real endpoint is covered by `tests/test_auth.py`, but the nginx-to-Flask path itself has not been tested end to end.
