# Reusable dual-axis review

Formal review belongs to the Campaign, never the implementation Worker.

## Review ownership

- `fast`: Campaign directly evaluates Spec and Quality axes.
- `standard`/`strict`: one `Spec Reviewer` and one `Quality Reviewer`, both
  direct Paseo subagents of the Campaign, role `review`, category `audit`.
- Reviewers are read-only and use the Campaign Control Workspace. They do not
  edit, integrate, clean resources, spawn Agents, or communicate with each
  other.

Use `review_policy.py plan-review`. The pair is created lazily, retained across
candidates, and has two dedicated Review slots separate from the three Worker
slots. A partial create retains the successful axis and creates only the
missing one. An error/closed, wrongly parented, duplicate, or unverified
Reviewer blocks final review.

Every plan re-reads Campaign and global capacity immediately before Reviewer
creation. A reservation made during Worker dispatch is not authority after
foreign Agents arrive. With one free slot, create one missing axis and preserve
it; with zero, queue the candidate and fail closed rather than exceed the cap.
Reviewer readback includes exact Campaign parentage and repository/campaign/
role/review-axis labels. Use the fixed Agent names `Spec Reviewer` and
`Quality Reviewer`.

## Candidate lock

Both axes receive the same immutable lock:

```text
dispatch_id, candidate_sha, base_sha, diff_sha256, acceptance_sha256,
review_round, scope, previous_candidate_sha
```

Spec checks the Issue, durable decisions, scope/non-goals, Hotset, and acceptance
criteria. Quality checks repository standards, architecture, security, tests,
failure behavior, and maintainability. Neither axis can waive the other.

`REVIEW_RESULT` also declares `axis`, `verdict`, and `findings`. The room helper
checks the Reviewer's exact `review_axis` label. One result leaves the pair
incomplete. Duplicate axes, different locks, forged labels, cross-Campaign
events, or conflicting Signal-IDs block the Dispatch. The Campaign may report a
verdict only when both axes are complete; either `fail` returns to the original
Worker.

Before dispatching the pair, the Campaign persists and reads back a
`campaign-verified-candidate` lock receipt. Pass those receipts to room
replay/wait with `--review-locks`; matching Reviewer claims cannot self-authorize
an arbitrary or stale lock. A delta receipt is valid only alongside the exact
prior round receipt. Recovery keeps the complete immutable lock plus both
Reviewer Agent IDs, not just candidate SHA/round, and compares it before
resuming.

Compile replay receipts with `identity-plan` scope `review-dispatch`. Supply
exact read-backed assignments for both axes: reusable Reviewer Agent, static
Campaign/axis labels, exact Campaign parent, and the complete current lock.
Reviewers never gain a static `dispatch_id` label; the dynamic
`reusable-reviewer` authority carries the current Dispatch and is replaced only
after the next assignment is persisted/read back. The Campaign direct-child
receipt may list both assigned Reviewers.

## Queue and delta rounds

The pair reviews one candidate at a time. Queue verified candidates by durable
verified-ready timestamp then Issue number. A later candidate never causes a
second pair.

After a fix, increment `review_round`, set `scope=delta`, and include the prior
candidate SHA. Both axes review the same new candidate and delta. If the
acceptance boundary or base materially changed, create a new full candidate
round instead of mislabeling it delta and require new evidence explicitly.
