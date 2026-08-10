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

The suite is fully async (`pytest-asyncio`, `auto` mode) and uses `httpx` for route tests. No external services are required — tests use an in-memory or temp-file SQLite database.

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

One line in `health_hub/contract/registry.py` plus a test. No other module changes needed.

```python
"new_type": TypeSpec("new_type", "new_type", RecordKind.INSTANT, "value_field", "unit", False),
```

The registry drives normalization, identity hashing, the debug coverage matrix, and `get_sync_health` automatically. Add a test in `tests/test_normalize.py` covering the new type, and a test in `tests/test_registry.py` confirming it is registered.

## Pull Requests

- Keep PRs focused — one concern per PR.
- Run `ruff check .` and `pytest tests/ -v` before pushing. CI will reject otherwise.
- Do not commit `.env`, `*.sqlite3`, `*.db`, or `tests/fixtures/payload_real.json` — they are in `.gitignore` and must stay out of history.
- Do not hardcode domains, tokens, timezones, or paths in source files. Everything personal comes from environment variables.
