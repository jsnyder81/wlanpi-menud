> **DRAFT — not filed.** File via GitHub when Phase 0 exits. Target repo: `WLAN-Pi/wlanpi-core`.

# Title

Add a token introspection endpoint (`POST /auth/token/introspect`) that accepts the `wlanpi-menud` token

## Labels suggestion

`enhancement`, `auth` — priority: later work (Phase 4, remote clients); not needed for the first release.

## Context

The FPMS re-architecture (*FPMS Re-Architecture Plan.md*, section **“wlanpi-core
integration”** → “Core changes”, row “Token introspection, accepting the
`wlanpi-menud` token — Remote JWT clients — Returns `valid`, `did`, `exp`,
`jti`”) adds remote clients that authenticate to `wlanpi-menud` with **their
own core JWT** (plan §Runtime connections: nginx → proxy socket → login with a
core token). The menu service must answer “is this caller's token still valid,
and whose is it?” on every connection without holding core's signing keys or
guessing from the JWT payload alone — revocation lives in core's database.

Verified against `main` at `df7ac8a` (OpenAPI 2.4.13): **no introspection
endpoint exists.** The auth surface in `docs/openapi.json` is exactly
`/api/v1/auth/token` (POST mint, DELETE revoke current token). Internally the
whole answer is already computed on every API call:
`TokenManager.verify_token()` (`wlanpi_core/core/token.py:335`) returns
`TokenValidationResult{is_valid, error, payload, device_id, key_id}`
(`token.py:64-72`), checks the revocation flag in the DB
(`token.py:353-354`), and the payload carries `sub`, `iss`, `did`, `kid`, `jti`,
`iat`, `exp` (`token.py:364`). There is just no HTTP way to ask.

## Proposed API shape

RFC 7662-style, named to sit beside the existing `/auth/token` pair, with the
four fields the plan already committed to:

```
POST /api/v1/auth/token/introspect
Authorization: Bearer <service jwt>   (did=wlanpi-menud or wlanpi-webui)
                                       (or localhost HMAC)
```

```json
{ "token": "<jwt-under-test>" }
```

**200** — `TokenIntrospection`:

```json
{ "valid": true, "did": "phone-jake", "exp": 1763483200, "jti": "b3c1…" }
```

| Field | Notes |
| --- | --- |
| `valid` | Signature + expiry + `bexp` boot-relative check + revocation flag, i.e. exactly what `verify_token` decides (`token.py:335-383`). Invalid token → **200 with `valid: false`** (introspection, not authentication). |
| `did` / `exp` / `jti` | From the payload (`token.py:364`); omitted when `valid: false`. |
| `reason` (optional, `exclude_none`) | Machine code for `valid: false` (`TOKEN_EXPIRED`, `TOKEN_REVOKED`, `AUTH_CLOCK_NOT_SET`, …) so the service can distinguish “re-mint mine” from “reject theirs”. |

Errors: **401** caller auth failed; **403** caller is a bearer that is neither
`wlanpi-menud` nor `wlanpi-webui` (introspection is a service privilege, same
spirit as the PAM restriction in `core/auth.py:62-69`). Never accept or echo
the token itself into logs — reuse the masking already in `verify_token`
(`token.py:337`).

Implementation notes: thin wrapper — new schema in `schemas/auth/`, route in
`api/api_v1/endpoints/auth_api.py` beside `POST /auth/token` (:274), one
`await request.app.state.token_manager.verify_token(body.token)` call, map
`TokenValidationResult` → response. Add the reserved-id check from
`01-reserve-wlanpi-menud-did.md` so the `wlanpi-menud` caller id is real before
this ships.

## Acceptance criteria

- [ ] `POST /auth/token/introspect` returns `valid: true` with `did`/`exp`/`jti` for a live token, `valid: false` (+ `reason`) for expired, revoked (`DELETE /auth/token`), and garbage tokens — always 200.
- [ ] A token revoked between two calls flips `valid` to `false` (DB check, not just crypto).
- [ ] Only localhost HMAC or a bearer with `did` in {`wlanpi-webui`, `wlanpi-menud`} can call it; other bearers 403.
- [ ] Route documented in `docs/openapi.json` and `docs/API-INTEGRATION-GUIDE.md`; unit tests for each `reason` code.

## Who needs it

- **menud service (remote mode, Phase 4)**: login on `proxy.sock` validates the
  remote user's core JWT via this endpoint instead of trusting the client's
  word or shipping keys.
- **Later mobile app / remote clients**: same login path.
- **Not needed for the first release** — local surfaces authenticate via
  `SO_PEERCRED`; the service's own calls use its minted token directly.
