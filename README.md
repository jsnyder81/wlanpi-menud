# wlanpi-menud

Headless menu service for the WLAN Pi. It owns the menu, sessions and jobs;
surfaces (TUI, WebUI, front panel, mobile) connect over one authenticated
WebSocket and render render-neutral view blocks. Every device operation goes
to wlanpi-core.

Phase 0 contents:

| Path | What |
| --- | --- |
| `protocol/menu.schema.json` | JSON Schema for menu manifests (`menu.d/*.json`) |
| `protocol/frames.schema.json` | JSON Schema for every WebSocket frame, protocol v1 |
| `protocol/PROTOCOL.md` | Protocol semantics the schema cannot express |
| `protocol/samples/` | Example manifest (the full built-in menu) and one example per frame type |
| `protocol/tests/` | Validates samples against both schemas |
| `docs/` | Core audit, parity list, resource budget, upgrade and rollback plan |

## Develop

```sh
uv venv --python 3.13
uv pip install -e '.[test]'
.venv/bin/pytest
```
