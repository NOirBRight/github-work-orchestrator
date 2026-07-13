---
name: github-issue-intake
description: Intake GitHub bug reports, enhancement requests, screenshots, logs, and rough ideas by performing bounded diagnosis, searching open and closed Issues for duplicates, standardizing a fresh-worker-ready Issue, publishing or updating it, and emitting one material signal. Use in a dedicated Issue Intake task or when the user asks to create, deduplicate, standardize, or publish a GitHub Issue.
---

# GitHub Issue Intake

Turn raw reports into GitHub-native Issue contracts without occupying the
Orchestrator's scheduling loop. GitHub is the only persistent state source.

## Establish the intake boundary

1. Read every applicable `AGENTS.md` and the repository's issue, label,
   security, and contribution policy.
2. Read [lifecycle](references/shared/lifecycle.md),
   [issue contract](references/shared/issue-contract.md),
   [GitHub state rules](references/shared/github-state-rules.md), and
   [communication protocol](references/shared/communication-protocol.md).
3. Infer the repository from the current checkout or require an explicit
   repository when no checkout identifies it.
4. Preserve reporter evidence while excluding credentials, private machine
   paths, task IDs, and unnecessary personal data.

Intake owns diagnosis, deduplication, contract quality, publication, and
readback validation. It does not assign Workers, dispatch tasks, set priority
or Milestones, change capacity or merge order, implement production code, or
open a production PR.

The boundary is established when the repository, policies, permitted Issue
writes, and prohibited orchestration decisions are explicit.

## Diagnose only to the failure boundary

Reproduce or inspect enough to identify the failing surface, expected versus
actual behavior, affected versions, and evidence needed for verification. Use
read-only inspection by default. A bounded diagnostic edit may live only in a
throwaway harness; it is not a production fix or PR.

Ask for missing information only when it blocks an honest contract. Keep the
Issue at `needs-info` when a named fact is unavailable. Escalate product
direction, durable architecture, compatibility, security/privacy, or priority
ambiguity as `DISCUSSION_REQUIRED` instead of deciding it here.

Diagnosis is complete when a fresh Worker can locate the failure boundary or
the exact missing evidence is named.

## Search before drafting

Search open and closed Issues using the symptom, affected component, error
text, and desired outcome. Read plausible matches and their linked PRs or
resolution comments.

- Use `DUPLICATE #<existing-number>` when an existing Issue owns the same
  outcome and scope. Add new non-sensitive evidence there only when it improves
  the contract.
- Link related but distinct ownership without collapsing separate outcomes.
- Continue to a new or updated Issue when no existing contract fully owns the
  report.

The search is complete when every plausible match is classified as duplicate,
related, or unrelated with evidence.

## Publish a Worker-ready contract

Draft against the shared Issue contract. Preserve the reporter's original
facts; separate facts, inferences, and proposed acceptance criteria. Use
exactly one canonical lifecycle label and exactly one existing repository type
label. Add native dependencies only when the edge is unambiguous.

Immediately before writing, re-read the current Issue and labels. Create or
update the Issue, then read it back and validate title, body, labels,
dependencies, and URL. Do not change priority, Milestone, assignee, capacity,
or merge ordering.

Publication is complete only when every condition in the shared Issue
contract's [readback gate](references/shared/issue-contract.md#readback-gate)
passes.

## Emit one material signal

Emit exactly one shared
[Intake signal](references/shared/communication-protocol.md#intake-signals),
then complete the shared
[delivery handshake](references/shared/communication-protocol.md#delivery-handshake)
when an Orchestrator callback is available.

Routine search and drafting updates remain in this task. If no callback was
provided, return the signal to the user without creating or guessing an
Orchestrator task.

Intake is complete when the GitHub readback is valid and one deduplicated
material signal has a recorded delivery outcome.
