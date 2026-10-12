# Contributing

## Development Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with test tokens
```

## Running Tests

```bash
pytest tests/ -v
```

The suite is fully async (`pytest-asyncio`, `auto` mode) and uses `httpx` for route tests. No external services are required — integration tests use temporary SQLite files with isolated read and write connections.

The server uses MCP Python SDK 2.3 or later within the 2.x series. `tests/test_mcp_modern.py` verifies protocol revision `2026-07-28` directly, with per-request metadata and no initialization handshake or session IDs.

## Linting

```bash
ruff check .
```

CI runs `ruff check .` and `pytest tests/ -v` on every push and pull request. Both must pass.

## Code Style

- One file = one responsibility. No `utils.py` or `helpers.py`.
- Files ≤ 200 lines, functions ≤ 50 lines.
- Pure functions in `contract/`, `ingest/normalize.py`, `ingest/identity.py`, `aggregate/` — no I/O. These modules must not import `aiosqlite` or touch the network/filesystem. They take data in and return data out.
- SQL is always parameterized (`?` placeholders). Never format strings into SQL.
- English only — in code, comments, docstrings, and error messages.
- See [SPEC.md](SPEC.md) for the full requirements.

## Adding a New Health Connect Type

Add a `TypeSpec` in `health_hub/contract/registry.py` with a value model. Numeric fields use `scalar_values`; compound records use a model from `health_hub/contract/values.py`. Use `JsonValues` when no value-field contract is established, so stored fields remain available without inventing their meaning.

```python
"new_type": TypeSpec("new_type", "new_type", RecordKind.INSTANT, "value_field", "unit", False, scalar_values("value_field", "unit")),
```

The registry drives normalization, identity hashing, the debug coverage matrix, `get_sync_health`, `list_data_types`, and the `health_records` input enum and output schemas. Add normalization and registry tests, and include the type in the synthetic MCP round-trip fixture in `tests/test_mcp_records.py`. MCP tests use bearer authentication and a temporary-file database. Date-dependent tests must control the clock.

## Pull Requests

- Keep PRs focused — one concern per PR.
- Run `ruff check .` and `pytest tests/ -v` before pushing. CI will reject otherwise.
- Do not commit `.env`, `*.sqlite3`, `*.db`, or `tests/fixtures/payload_real.json` — they are in `.gitignore` and must stay out of history.
- Do not hardcode domains, tokens, timezones, or paths in source files. Everything personal comes from environment variables.
