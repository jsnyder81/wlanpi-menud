> **DRAFT — not filed.** Target repos: `WLAN-Pi/wlanpi-menud`, `WLAN-Pi/wlanpi-webui`.

# Title

Close the no-HTTP-traffic revocation window for WebUI menu attachments

## Labels suggestion

`security`, `auth`, `menud`, `webui` — Phase 2 follow-up (post-merge hardening).

## Context

In Phase 2 of the FPMS re-architecture, `wlanpi-menud` exposes a reverse-proxied WebSocket endpoint at `/ui/ws` authenticated via nginx `auth_request /auth/check`. Once connected, the WebSocket connection has `admin` menu execution permissions by default.

WebUI explicit actions (such as `POST /logout` or `POST /change_password`) revoke sessions immediately and issue `revoke` frames over `local.sock` to close any associated `/ui/ws` connections with close code `4401 ("WebUI session ended")`.

However, session revocations that are detected lazily occur without HTTP traffic:
1. **Idle timeout**: WebUI session reaches idle expiration, but no subsequent HTTP request occurs to trigger cookie validation.
2. **Account invalidation**: User leaves `sudo`, account is locked (`passwd -l`), or user password is changed out-of-band over SSH. `account_still_allowed()` detects this only when handling an HTTP request.
3. **Absolute session lifetime (`MAX_SESSION_AGE`) without HTTP interaction**: If a WebSocket client remains connected without making HTTP requests, the WebUI Flask backend never runs `_update_sessions()` to prune the sid for this client.

Because `/ui/ws` WebSocket frames travel directly between nginx and `wlanpi-menud` (bypassing Flask), a WebSocket connection opened with a valid cookie can outlive server-side invalidation if the client makes no further HTTP requests.

## Proposed Options

Two architectural options exist to close or bound this window:

### Option A: menud caps WebUI attachments at `MAX_SESSION_AGE`

`wlanpi-menud` records the connection start timestamp for each `webui` principal. When the connection lifetime reaches `MAX_SESSION_AGE` (default: 43,200 seconds / 12 hours, configurable via `menud.toml`), the server gracefully closes the WebSocket connection with code `4401 ("WebUI session expired")`.

- **Pros**:
  - Extremely simple to implement with zero inter-process overhead or polling.
  - Bounds exposure strictly to `MAX_SESSION_AGE`.
  - Consistent with normal token/session expiration paradigms.
- **Cons**:
  - Does not catch immediate out-of-band account changes (e.g., SSH password change or `passwd -l`) until the lifetime expires or the user makes an HTTP request.

### Option B: Periodic re-validation (similar to `_recheck_jwt`)

`wlanpi-menud` already implements periodic re-validation for JWT tokens via `_recheck_jwt` (`wlanpi_menud/server.py:243-258`). A similar mechanism can be implemented for `webui` attachments:
`wlanpi-menud` periodically (e.g. every 60 seconds) queries the local WebUI session registry (via an internal endpoint or reading `/run/wlanpi-webui/sessions.json` / checking `account_still_allowed()`). If the session ID is no longer present, valid, or active, `menud` immediately terminates the connection with code `4401`.

- **Pros**:
  - Actively terminates orphaned connections within the re-check interval (e.g. within 60s of account revocation).
  - Matches the existing pattern used for JWT clients.
- **Cons**:
  - Introduces periodic background checks / coupling between `menud` and WebUI session storage.
  - Requires handling timeout/failure modes if WebUI is temporarily unresponsive.

## Recommendation

Implement **Option A** as a baseline guarantee in `wlanpi-menud` so all WebUI attachments have a hard upper bound. If tighter guarantees are required for appliance deployments where SSH modifications are common, layer **Option B** using a lightweight local unix-socket query.
