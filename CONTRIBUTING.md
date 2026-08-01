# Contributing

This repository uses `main` as its only integration branch. Changes are
accepted through pull requests and must preserve the public
`start -> advance -> inspect` seam and its five public statuses.

The acceptance workflow runs on GitHub-hosted Windows runners for pushes to
`main` and for pull requests from branches in this repository. Pull requests
from external forks are intentionally skipped for now; they must not use
`pull_request_target`, repository secrets, or self-hosted runner labels. A
maintainer may copy a reviewed change to a trusted same-repository branch
before requesting acceptance.

Before opening a pull request:

1. Run `python scripts/quick_validate.py`.
2. Run the relevant focused tests and the full `python -m pytest -q` suite.
3. Keep changes inside the issue's declared claims and explain any newly
   discovered dependency instead of silently expanding the pull request.

Do not commit `.tmp/` or `.paseo-permission-audit-temp/`; these paths are
local audit and scratch directories.
