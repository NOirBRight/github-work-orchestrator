# Contributing

This repository uses `main` as its only integration branch. Changes are
accepted through pull requests and must preserve the public
`start -> advance -> inspect` seam and its five public statuses.

This repository's GitHub Actions acceptance is disabled. Repository release
acceptance is Local Verification Only.

Use a Python 3.13 virtual environment installed from the retained hash-locked
requirements file before running local acceptance:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes -r .github/requirements-ci-win-py313.txt
```

Before opening a pull request:

1. Run `python scripts/quick_validate.py`.
2. Run the relevant focused tests and the full `python -m pytest -q` suite.
3. Keep changes inside the Ticket's declared claims and explain any newly
   discovered dependency instead of silently expanding the pull request.

Do not commit `.tmp/` or `.paseo-permission-audit-temp/`; these paths are
local audit and scratch directories.
