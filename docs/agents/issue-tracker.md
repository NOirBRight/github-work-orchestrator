# Issue tracker: GitHub

Issues and PRDs for this repository live as GitHub Issues. Use the `gh` CLI
for all operations.

## Repository

- GitHub repository: `NOirBRight/github-work-orchestrator`
- Infer the repository from `git remote -v` when operating inside the clone.

## Conventions

- Create one issue per independently executable ticket.
- Read the complete issue body, labels, and comments before acting.
- Apply or remove labels with GitHub Issue operations.
- Do not close or modify a parent issue unless explicitly instructed.

## Pull requests as a triage surface

**PRs as a request surface: no.**

Pull requests are delivery artifacts, not unplanned feature-request intake.
They do not enter `/triage` merely because they are open.

## When a skill says "publish to the issue tracker"

Create a GitHub Issue in this repository.

## When a skill says "fetch the relevant ticket"

Read the GitHub Issue body, labels, and comments.

## Blocking relationships

Use GitHub native Issue dependencies as the canonical, UI-visible blocking
relationship. The dependent Issue is `blocked by` each prerequisite Issue.
Also list the references under the Issue body's `## Blocked by` section so the
contract remains readable outside GitHub's dependency UI.

If native dependencies are unavailable, the body references are the durable
fallback. A ticket is executable only when every referenced blocker is closed.

## Frontier

The executable frontier consists of open `ready-for-agent` Issues whose
blocking Issues are all closed and which are not already claimed. Dependency
order, not Issue number alone, determines readiness.
