> **DRAFT — not filed.** File via GitHub when Phase 0 exits. Target repo: `WLAN-Pi/wlanpi-core`.

# Title

Reserve `did=wlanpi-menud` as an on-device device id (alongside `wlanpi-webui`)

## Labels suggestion

`enhancement`, `auth`, `parity` — priority: needed for the first release (Phase 1 of the menu re-architecture).

## Context

The FPMS re-architecture (*FPMS Re-Architecture Plan.md*, section **“wlanpi-core
integration”** → “Core changes”, row “Reserve `did=wlanpi-menud` and allow
`getjwt wlanpi-menud` through the root helper”) introduces `wlanpi-menud`, a new
on-device menu service that runs as the unprivileged `wlanpi` user and calls
core over `https://127.0.0.1:31415`. Because core's HMAC secret directory is
root-only (0700), the service mints its token through a root helper
(`/usr/libexec/wlanpi-menud/get-core-token`) wrapping `getjwt wlanpi-menud` —
the same pattern the WebUI uses for `wlanpi-webui`.

Verified against `main` at `df7ac8a` (OpenAPI 2.4.13):

- The only reserved device id is `wlanpi-webui`: `PAM_CLIENT_DEVICE_ID` in
  `wlanpi_core/core/auth.py:23`, enforced in `POST /auth/token`
  (`wlanpi_core/api/api_v1/endpoints/auth_api.py:313-319` — non-HMAC callers
  get 403 when minting it; bearers may mint only their own `did`,
  `auth_api.py:320-322`).
- `getjwt` takes any device-id string with no allowlist
  (`wlanpi_core/cli/getjwt.py:109`) and signs the mint request with the root
  HMAC (`getjwt.py:77-90`).

So `sudo getjwt wlanpi-menud` already produces a valid token today. What is
missing is the **reservation**: `wlanpi-menud` is not declared a reserved
on-device id anywhere in core, so nothing guarantees it stays mintable only by
root HMAC callers as the mint paths evolve (e.g. a future “bearer may mint
device ids” change, or a pairing flow that widens `POST /auth/token`). Core
also needs to keep the menud token away from the PAM endpoints, which today
accept only `wlanpi-webui` (`wlanpi_core/core/auth.py:69`) — that already
holds, and should be pinned with a test.

## Proposed change (consistent with existing conventions)

No new endpoints. Small, explicit reservation:

1. In `wlanpi_core/core/auth.py`, introduce a reserved-id set and keep the PAM
   constant as-is:

   ```python
   PAM_CLIENT_DEVICE_ID = "wlanpi-webui"
   RESERVED_DEVICE_IDS = frozenset({"wlanpi-webui", "wlanpi-menud"})
   ```

2. In `POST /auth/token` (`auth_api.py`), replace the single-id check with
   `device_id in RESERVED_DEVICE_IDS and caller is not CallerKind.LOCALHOST_HMAC`
   → 403 `device_id is reserved for on-device use` (same detail string, so
   existing clients are unaffected).
3. Document `wlanpi-menud` in `docs/API-INTEGRATION-GUIDE.md` §auth and the
   `openapi_docs.py` auth description table next to `wlanpi-webui`.
4. Add tests: HMAC can mint `wlanpi-menud`; a bearer (any did) minting it gets
   403; a `wlanpi-menud` token authenticates normal API routes but is rejected
   by the PAM endpoints (403), matching the existing `wlanpi-webui` tests.

## Acceptance criteria

- [ ] `RESERVED_DEVICE_IDS` (or equivalent) contains `wlanpi-webui` and `wlanpi-menud`; `POST /auth/token` 403s non-HMAC minting of either.
- [ ] `sudo getjwt wlanpi-menud` returns a token that authenticates against `GET /api/v1/system/device/info`.
- [ ] A `wlanpi-menud` bearer cannot mint any other `did` (403, unchanged rule) and cannot call the PAM endpoints (403).
- [ ] Docs list the reserved ids; changelog entry added.

## Who needs it

- **wlanpi-menud (service)**: its entire core integration — every menu action
  runs through core with this token (plan: “Real auth: … a reserved
  `wlanpi-menud` core token for the service's own calls”).
- **Panel/TUI surfaces**: indirectly — they act through the service.
- Needed for the **first release** (Phase 1); nothing in the menu works
  against a hardened core without a supported way to mint the service token.
