---
name: github-issue-intake
description: Turn reports, logs, screenshots, and rough ideas into deduplicated GitHub Issues with provider-neutral v3 Paseo execution contracts. Report the result through the gwo kernel mailbox. Use for Issue intake, diagnosis, deduplication, or publication.
---

# GitHub Issue Intake

Own diagnosis, duplicate search, contract standardization, publication/readback,
and one material Intake result. Do not prioritize the backlog, claim execution,
select implementation Providers, or create implementation Agents.

## Build a fresh-Agent-ready Issue

Preserve reporter facts and separate them from inference. Establish problem or
outcome, scope/non-goals, acceptance, verification/manual evidence, expected
hotset, dependencies, and exactly one lifecycle/type label.

For new or materially rewritten work, format Expected Hotset as one or more
backticked canonical repository-relative path bullets under the exact
`## Expected hotset` heading. A missing or invalid legacy Hotset forces
repository-exclusive execution and prevents automatic parallel admission.

For new or materially rewritten work, include the v3 fields from
[verification policy](references/shared/verification-policy.md). Use
`Execution-Mode: paseo-agent`, role `implementation`, category `impl` or `ui`,
and integration branch `dev` unless the accepted scope is genuinely inline.
Provider/model never belongs in the Issue contract.

Search open and closed Issues before publication. Create a duplicate only when
the existing Issue owns the same outcome and scope; otherwise link related work.
Re-read before writes and verify the final GitHub body, labels, dependencies,
state, and URL after every create/update.

## Report through the gwo kernel mailbox

When a Task Group label is supplied, post one material `COMPLETED`,
`DISCUSSION_REQUIRED`, or `BLOCKED` event with validated Issue evidence through
the gwo kernel:

```text
python <skill>/scripts/gwo.py send --to <coordinator> --type status --signal-id <id> --payload <json>
```

Use the payload fields from the v3 contract and the observed Issue URL/number.
Do not use mention, finish notification, or a second room as Issue proof. Without
a Coordinator, return the same concise result to the caller; GitHub remains the
durable record.
