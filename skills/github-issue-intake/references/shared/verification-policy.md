# Verification policy

## Execution contract v3

Every new or materially rewritten ready Issue records:

```text
Execution-Contract: v3
Execution-Mode: inline | paseo-agent
Agent-Role: orchestrator | intake | implementation | review | monitor
Role-Category: planning | research | impl | ui | audit
Integration-Branch: dev
Done-When: <observable terminal condition>
Verification-Class: fast | standard | strict
Verification-Commands: <nonempty commands>
Manual-Evidence: none | <required evidence>
Architecture-Decision: resolved | discussion-required
Review-Owner: orchestrator
```

Private dispatch adds exact base SHA, feature branch, hotset, permission
requirements, campaign/dispatch IDs, room, `relationship: subagent`, exact
parent Agent, `notify_on_finish: true`, and the advertised high-autonomy runtime
mode. Provider/model and mode are resolved at runtime and are not part of Issue
readiness.

## Verification classes

- `fast`: targeted checks and diff hygiene; direct Orchestrator review.
- `standard`: targeted checks, one relevant full suite, and one Standards/Spec
  review owned by the Orchestrator.
- `strict`: standard gates plus required manual evidence for protocol,
  security, migration, release, or cross-boundary work.

Workers never run the formal review. The locally green candidate starts CI,
review, and safe manual evidence in parallel. Review fixes are delta-only unless
the accepted boundary changed.

## Candidate-first integration pipeline

Produce one locally green candidate before the first push. Do not serialize CI,
formal review, and independent manual evidence. Merge only after all applicable
gates pass. Compare candidate and integrated Git trees after merge; when the
trees are identical, do not rebuild or repeat evidence unless repository or
release-artifact identity requires it.

## Runtime verification

For delegated work, verify room preflight, exact Agent ID and labels, idle/busy
state, parent Agent ID, runtime mode, pending permissions, worktree, branch,
base SHA, terminal event, commit/PR, and command results. Provider identity is
diagnostic evidence, not an acceptance gate. Missing or contradictory runtime
evidence fails closed.

For a Campaign Orchestrator, additionally verify its direct Repository
Coordinator parent, unique `campaign_id`, `planning` role category, Campaign
Provider Binding receipt, admitted Hotset, campaign/global capacity, and the
current repository Integration Lease. A changed `dev` SHA invalidates merge
admission until the Campaign refreshes its base and reruns affected evidence.
