# Contributing

This is a beta under evaluation. Read README.md and docs/STATUS.md before relying on a feature.

Use Python 3.12. Run `python scripts/bootstrap.py` with uv installed and access to PyPI.
Use the resulting `.venv` interpreter for `scripts/check.py`, `scripts/run.py`, and `scripts/build.py`.
Development caches, tests, and generated packages stay under this folder.

Keep provider and platform integrations separate. Do not add terminal embedding, simulated
keystroke delivery, automatic approval bypasses, provider database edits, or directory-based
session identity. Preserve uncertainty instead of inventing runtime or delivery states.

Run the core tests and Ruff before submitting changes. Test GUI changes with the offscreen
smoke command and then interactively on the affected OS. Record provider versions and platform
limitations. Use synthetic session names in screenshots and test fixtures.

Never include `.local`, `.cache`, `.venv`, provider histories, login credentials, user messages,
or signed release secrets in commits. The source is MIT licensed; preserve dependency notices.
Use docs/ROADMAP.md to distinguish implemented work from native acceptance.

