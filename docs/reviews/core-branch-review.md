# Review: wlanpi-core `feat/menu-rearchitecture-core`

Reviewer: read-only worker. Date: 2026-11-09. Branch HEAD: `3bfd313`. Base: `main` @ `df7ac8a`.
Draft: `wlanpi-menud/docs/core-issues/01-reserve-wlanpi-menud-did.md`.

## Verdict: APPROVE (0 blocker, 0 major, 2 minor)

## Scope checks
- **Exactly one commit** on top of `main` (`3bfd313 feat(auth): reserve wlanpi-menud device id`). PASS.
- **Working tree clean** (before and after mutation checks; mutated files restored). PASS.
- **Deferred drafts 02-05 absent.** PASS.
  - `docs/openapi.json` paths matching mode/cloud/capture/introspect/token: only `/api/v1/auth/token` and the pre-existing `/api/v1/streaming/capture` WebSocket (a different route from the deferred `/wifi/capture/sessions`).
  - No `mode/switch`, `cloud-test`, `wifi/capture/sessions` or `token/introspect` routes in `wlanpi_core/`. Grep hits are only pre-existing streaming code and "planned" docstrings.
  - The commit touches 9 files, none of them new route modules.

## (a) Shape vs draft
Matches the draft:
- `wlanpi_core/core/auth.py:24` adds `RESERVED_DEVICE_IDS = frozenset({"wlanpi-webui","wlanpi-menud"})`. `PAM_CLIENT_DEVICE_ID` is kept, as the draft says.
- `auth_api.py:~313` uses `device_id in RESERVED_DEVICE_IDS and caller is not CallerKind.LOCALHOST_HMAC`, which gives 403 with the unchanged detail string `device_id is reserved for on-device use`. The bearer-own-did rule is unchanged.
- Docs: `API-INTEGRATION-GUIDE.md` §1.1 and the rotation note, the `openapi_docs.py` auth table, and the `generate_token` docstring. Changelog entry added.
- Tests:
  - `test_auth_token_api.py`: HMAC mint and bearer 403, parametrized over both reserved ids, including a real-TestClient HMAC path.
  - `test_pam_api.py`: PAM routes reject a menud bearer (3 routes), and a menud bearer authenticates `/system/device/info`.
  - `test_auth.py`: set contents; `verify_local_auth` rejects menud; `verify_auth_wrapper` accepts menud.

## (b) Suite and lint
- Full suite at HEAD (repo `.tox/py313`): **1268 passed, 20 skipped, 2 xfailed**.
- `ruff check wlanpi_core tests`: clean. `ruff format --check`: 233 files already formatted.
- mypy and `tox -e lint` were not run (not installed here); the change is type-trivial.

## (c) OpenAPI
- `app.openapi()` regenerated in-process equals the committed `docs/openapi.json` (in sync), so `openapicheck` should pass.
- The two description strings (info table and `/auth/token`) match the source docstrings.

## (d) Mutation checks (all reverted; tree clean)
| Mutation | Result |
|---|---|
| Mint guard narrowed back to `== "wlanpi-webui"` | 5 failures in `test_auth_token_api.py`, all menud cases |
| Mint guard removed (`if False:`) | 10 failures (both ids) |
| PAM pin loosened to `not in RESERVED_DEVICE_IDS` (menud accepted on PAM) | 4 failures: `test_pam_routes_reject_wlanpi_menud_bearer` x3 and `test_verify_local_auth_rejects_wlanpi_menud_bearer` |

The guards are covered by tests that fail when they are weakened.

## (e) getjwt
`wlanpi_core/cli/getjwt.py` is untouched by the diff (0 lines). It still signs with the root HMAC and takes any device-id string. `test_reserved_device_id_accepts_real_localhost_hmac_request[wlanpi-menud]` exercises a real HMAC-signed localhost POST that mints `wlanpi-menud`. Unaffected.

## Findings
- **minor** `debian/changelog:1` vs `wlanpi_core/__version__.py:8` and `docs/openapi.json:6`: the changelog is bumped to `2.4.14-1` but `__version__` and the OpenAPI `info.version` stay at `2.4.13`. This is consistent with core's "bump in release commit" pattern (`c75c6cf`), but `52f81eb` bumped `__version__` in a fix commit. Confirm the maintainers' preference; it is harmless as is.
- **minor** `tests/test_pam_api.py` (`test_wlanpi_menud_bearer_authenticates_system_device_info`): the acceptance criterion "getjwt token authenticates `/system/device/info`" is covered with `verify_jwt_token` mocked, not with a token minted and then verified end to end. Mint (real HMAC) and bearer auth (mocked) are covered separately, so nothing is unprotected. An end-to-end test is optional.
