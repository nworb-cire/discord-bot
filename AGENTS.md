Run unit tests with `UV_CACHE_DIR=.uv-cache uv run pytest tests`.

Commit changes after each logical section of work. Commit frequently, and don't be afraid to amend commits when a feature is currently being worked on so as to retain a "WIP" state.

If a commit fails, there is a high likelihood that it is because a pre-commit hook failed or modified files. Try again if the files were modified with black or ruff. If the unit tests (also part of the pre-commit hook) failed, fix the tests or code and try again.

## Project Preferences

- Use `loguru` (`from loguru import logger`) for runtime output. Avoid `print` in application or sync flows.
- Prefer module entrypoints (`python -m ...`) with a thin `if __name__ == "__main__"` block over standalone ad-hoc script files, unless explicitly requested.
- Prefer configuration-driven jobs: read inputs from `Settings`/env vars and avoid CLI flags unless explicitly requested.
- Keep safety controls configurable (for example, via `*_SAFETY_CUTOFF`) and avoid hardcoded dates in metric/log key names.
- For integration/sync workflows, prefer a single orchestrator class that owns fetch/load, plan, apply, and logging steps.
- Keep credential files untracked and do not stage them in commits.
