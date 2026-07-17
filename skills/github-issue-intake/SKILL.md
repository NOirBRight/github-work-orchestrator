---
name: github-issue-intake
description: Turn reports, logs, screenshots, and rough ideas into deduplicated GitHub Issues with provider-neutral v3 Paseo execution contracts and one material campaign-room signal. Use for Issue intake, diagnosis, deduplication, or publication.
---

# GitHub Issue Intake

Own diagnosis, duplicate search, contract standardization, publication/readback,
and one material Intake result. Do not prioritize the backlog, claim execution,
select implementation Providers, or create implementation Agents.

## Build a fresh-Agent-ready Issue

Preserve reporter facts and separate them from inference. Establish problem or
outcome, scope/non-goals, acceptance, verification/manual evidence, expected
hotset, dependencies, and exactly one lifecycle/type label.

For new or materially rewritten work, include the v3 fields from
[verification policy](references/shared/verification-policy.md). Use
`Execution-Mode: paseo-agent`, role `implementation`, category `impl` or `ui`,
and integration branch `dev` unless the accepted scope is genuinely inline.
Provider/model never belongs in the Issue contract.

Search open and closed Issues before publication. Create a duplicate only when
the existing Issue owns the same outcome and scope; otherwise link related work.
Re-read before writes and verify the final GitHub body, labels, dependencies,
state, and URL after every create/update.

## Report through the campaign room

When a campaign room is supplied, pass the packaged room preflight and post one
material `COMPLETED`, `DISCUSSION_REQUIRED`, or `BLOCKED` event with validated
Issue evidence. Do not use mention delivery as proof. Without a room, return the
same concise result to the caller; GitHub remains the durable record.
