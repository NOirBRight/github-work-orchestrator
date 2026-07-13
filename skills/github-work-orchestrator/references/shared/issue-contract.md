# Fresh-worker-ready Issue contract

An actionable Issue lets a new Worker understand and verify the work without
private conversation history.

## Required content

Include:

1. **Problem or outcome** — observed behavior and desired result.
2. **Scope and non-goals** — owned surfaces plus explicit exclusions.
3. **Acceptance criteria** — observable, checkable completion conditions.
4. **Verification or evidence** — commands, reproductions, fixtures, traces, or
   manual evidence required.
5. **Expected hotset** — likely files/components when evidence supports it.
6. **Dependencies** — native blocked-by edges and sub-issues when unambiguous.
7. **Taxonomy** — exactly one canonical lifecycle label and exactly one
   existing repository type label.

Preserve reporter facts, logs, screenshots, prior decisions, and relevant
discussion. Separate facts from inference. Exclude credentials, task IDs, local
paths, raw private traces, and unnecessary personal data.

## Readiness classification

- Use `needs-triage` when scope or ownership remains ambiguous.
- Use `needs-info` when a named fact, evidence item, policy, or decision is
  missing.
- Use `ready-for-human` when a maintainer-only action is required.
- Use `ready-for-agent` when a fresh Worker can execute the contract.

An open dependency does not make the contract unready; it removes the Issue
from the current frontier. Priority and Milestone decisions belong to the
Orchestrator or maintainer, not Intake.

## Safe updates

Re-read the current Issue immediately before editing. Preserve meaning and
reporter evidence. Add missing sections or reorganize only when the intended
contract is clear. Never replace a body from stale data.

Use a duplicate only when an existing Issue owns the same outcome and scope.
Link related Issues when ownership differs. Add native dependencies only for
hard, unambiguous blocking edges.

## Readback gate

After every create or update, read the Issue from GitHub and verify title, body,
labels, dependency edges, state, and URL. The contract is ready only when every
required content item is present, the label pair is exact, and no write changed
priority, Milestone, assignee, or merge order outside the role's authority.
