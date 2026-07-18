# Verification policy

## Execution contract v3

Every new/materially rewritten ready Issue records:

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

Private Dispatch adds exact base/worktree/branch/Hotset, permissions,
campaign/dispatch/room, `relationship: subagent`, parent Agent,
`notify_on_finish: true`, and advertised unattended mode. Provider/model/mode
are runtime binding, not Issue readiness.

## Classes and independent axes

- `fast`: targeted checks and diff hygiene; Campaign directly performs Spec and
  Quality axes.
- `standard`: targeted checks, relevant full suite, CI, and independent Spec +
  Quality Paseo Reviewers.
- `strict`: standard gates plus required protocol/security/migration/release/
  cross-boundary manual evidence.

Workers never perform formal review. After a locally green candidate, CI,
Review Pair, and safe manual evidence may run in parallel.

Each Campaign lazily creates exactly one reusable `Spec Reviewer` and one
reusable `Quality Reviewer`, using dedicated Review slots independent from the
three Worker slots. The pair handles one candidate at a time and later
candidates queue by verified-ready time then Issue number. Partial creation
retains the successful Reviewer and creates only the missing axis. Re-read
Campaign/global counts before every create; reservations do not override newly
arrived foreign Agents.

Both axes lock identical `candidate_sha`, `base_sha`, diff SHA-256, acceptance
SHA-256, round, scope, and previous candidate SHA. Spec checks Issue/decision/
scope/Hotset/acceptance. Quality checks standards/architecture/security/tests/
maintainability. Reviewers do not communicate. The Campaign only aggregates;
missing/duplicate/forged/cross-Campaign/mismatched results cannot form a
verdict. Either failure returns to the same Worker and both axes review the next
delta. The Campaign persists and reads back that immutable lock before dispatch;
room replay rejects a result without the matching Campaign-issued receipt and
exact delta lineage. Never downgrade because Worker capacity is full.

## Candidate-first integration

Produce a locally green candidate before first push. Merge only after all
applicable gates. Compare candidate and integrated trees; identical trees do
not require repeat evidence unless repository/artifact identity requires it.

If Integration Control is dirty/unavailable, preserve user WIP and keep the
verified candidate `WAITING_INTEGRATION`. Never stash/reset automatically.

## Runtime verification

For Workers verify Agent ID, parentage, Provider/mode, labels, room preflight,
permissions, worktree/branch/base, terminal event, Git head/dirty state,
push/PR, changed paths, Hotset, commands, and acceptance. Provider identity is
diagnostic, not an acceptance shortcut.

For Campaign also verify direct Coordinator parent, unique Campaign ID,
Campaign Control Workspace/branch/head, local-only/no-feature-commit state,
Provider Binding, scope, typed capacity, Review Pair, and Integration Lease.
Changed `dev` invalidates merge admission until refresh and affected evidence.
