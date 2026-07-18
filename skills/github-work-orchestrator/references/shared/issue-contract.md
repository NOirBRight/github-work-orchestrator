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
8. **Execution contract** — for new or materially rewritten work, the v3
   verification fields defined below.

Preserve reporter facts, logs, screenshots, prior decisions, and relevant
discussion. Separate facts from inference. Exclude credentials, task IDs, local
paths, raw private traces, and unnecessary personal data.

## Execution contract v3

Use the shared [verification policy](verification-policy.md#execution-contract)
as the sole authority for the exact fields and allowed values. Copy that
template into the Issue without creating labels for it; do not maintain a
second field definition here.

The Issue owns product acceptance. The repository owns its verification
matrix. Intake proposes the class but does not invent additional acceptance or
verification commands. When scope, non-goals, acceptance, and verification
conflict, keep the contract out of `ready-for-agent` until the conflict is
resolved. A public/persisted contract or durable architecture gray area uses
`discussion-required` and must be recorded before dispatch.

Legacy Issues may migrate when they are next materially rewritten. Active work
must not be restarted only to add these fields.

For deterministic parallel admission, new or materially rewritten Issues use
this exact machine-readable section:

```text
## Expected hotset
- `src/component`
- `tests/component`
```

Each bullet contains one backticked canonical repository-relative path. Reject
absolute paths, empty segments, `.` and `..`. A legacy Issue without a valid
section may still execute, but its Dispatch claims the repository exclusively
and therefore cannot be automatically parallelized.

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
required content item is present, the label pair is exact, every v3 field is
valid, `Architecture-Decision` is `resolved`, and no write changed priority,
Milestone, assignee, or merge order outside the role's authority.
