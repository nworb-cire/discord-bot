Run unit tests with `UV_CACHE_DIR=.uv-cache uv run pytest tests`.

Commit changes after each logical section of work. Commit frequently, and don't be afraid to amend commits when a feature is currently being worked on so as to retain a "WIP" state.

If a commit fails, there is a high likelihood that it is because a pre-commit hook failed or modified files. Try again if the files were modified with black or ruff. If the unit tests (also part of the pre-commit hook) failed, fix the tests or code and try again.

Never commit without running tests.

Never skip commit hooks.
