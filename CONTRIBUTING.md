# Contributing
- Python 3.11+, `pip install -e ".[dev,docker,ui]"`; run `make lint type test` before pushing.
- Keep the core (`domain`, `core`, `workflow`, `verification`, `sandbox`) free of FastAPI/Streamlit imports.
- New providers implement `agents/providers/base.CodeProvider`; new sandboxes implement `sandbox/base.Sandbox` and MUST fail closed.
- Never log prompts, code, or secrets. Metric labels must be bounded (no task IDs / free text).
- Tests: do not present mocked sandbox tests as isolation proof; put real-isolation tests under `-m docker`.
- Never commit benchmark numbers that were not produced by `safeloop benchmark`.
- Schema changes need a migration plan (Alembic) before release.
