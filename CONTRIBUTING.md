# Contributing

This repository uses `main` as its only integration branch. Changes are
accepted through pull requests and must preserve the public
`start -> advance -> inspect` seam and its five public statuses.

GitHub Actions are disabled, and repository acceptance is local-only.

Before opening a pull request:

1. Run `python scripts/quick_validate.py`.
2. Run the relevant focused tests and the full `python -m pytest -q` suite.
3. Keep changes inside the Ticket's declared claims and explain any newly
   discovered dependency instead of silently expanding the pull request.

Do not commit `.tmp/` or `.paseo-permission-audit-temp/`; these paths are
local audit and scratch directories.
