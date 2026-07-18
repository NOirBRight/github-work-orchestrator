# Direction and architecture discussion loop

Long-running execution needs a human-visible place to resolve gray areas. Use
this loop for material direction and architecture choices, not for routine
status reporting or ordinary implementation details.

## Decision authority

- Let a Worker decide local, reversible implementation details that remain
  inside accepted criteria, architecture invariants, and owned files.
- Let the Orchestrator synthesize evidence, compare options, recommend a path,
  and decide scheduling or integration mechanics inside accepted direction.
- Reserve product direction, Milestone priority, public or persisted contracts,
  architecture ownership, security/privacy posture, compatibility policy, and
  irreversible migrations for the maintainer or explicitly named decision
  owner.
- Use subagents only to gather bounded evidence or critique options. The
  Orchestrator owns the brief; a subagent does not make or record the decision.

## Run a direction checkpoint

Run one checkpoint before sustained dispatch for a new project or Milestone,
and again only when accepted direction becomes stale or contradicted. Read the
current Milestone, roadmap, domain context, ADRs, relevant Issues, and recent
architecture-changing PRs. Present a concise execution charter containing:

1. Target outcome and explicit non-goals.
2. Architecture invariants, seams, and component ownership.
3. Public compatibility, security, data, and migration boundaries.
4. Quality and evidence thresholds.
5. The authority envelope for Orchestrator and Workers.
6. Material unresolved decisions that could change downstream work.

Keep the charter in the Orchestrator discussion unless the repository already
has an authoritative home for it. Update that existing source after acceptance;
do not create a duplicate planning ledger. If no material choice is open, say
so once and continue without repeated confirmation.

## Open a discussion gate

Open a gate when at least one condition holds:

- two or more plausible interpretations produce materially different behavior;
- a change crosses component ownership or introduces a shared abstraction,
  dependency, runtime, storage model, or protocol seam;
- a choice changes a public API, persisted data, authentication, privacy,
  security, compatibility, or failure policy;
- the work conflicts with an ADR, roadmap, Milestone outcome, or accepted
  architecture invariant;
- the choice is hard to reverse or would force migration of downstream work;
- evidence invalidates an assumption used by the current plan; or
- an Issue must expand in a way that changes other Issues or active hotsets.

Do not open a gate for naming, formatting, test organization, a reversible local
refactor, or a routine bug fix whose acceptance criteria already determine the
behavior.

## Prepare one discussion packet

Bundle related gray areas and ask for at most three decisions at once. Include:

```text
DISCUSSION_GATE
- Context: <what is true now>
- Decision: <one precise choice>
- Why now: <what work depends on it>
- Options: <A/B/C with concrete tradeoffs>
- Recommendation: <one path and why>
- Impact: <Issues, architecture, compatibility, risk>
- Safe work: <what can continue while this is open>
```

Prefer a recommendation over an open-ended brainstorm. Distinguish known facts,
inference, and preference. Do not force a decision when a small read-only spike
can first remove uncertainty.

## Operate while the gate is open

- Pause writes and integration only for the affected decision boundary.
- Continue safe investigation, reversible preparation, and unrelated lanes.
- Send one `ASK` and one `DECISION_GATE` per material gate; do not send progress
  chatter or repeat an unchanged packet.
- If the gate persists beyond the immediate exchange, reflect it in GitHub:
  use `needs-info` for missing policy/evidence/choice, or `ready-for-human` for
  a maintainer-only action or approval. Do not invent a new status label.

## Resolve and enforce the decision

Record the outcome where future work will find it:

- use the originating Issue comment for a local tactical choice;
- use the repository's ADR convention for durable architecture;
- update the existing Milestone, roadmap, or domain context for project
  direction; and
- revise affected acceptance criteria, dependency edges, labels, and Worker
  prompts before resuming.

At review, verify the diff against the accepted decision as well as the Issue.
Reopen the gate if implementation evidence contradicts the chosen assumptions.

Publish `REPLY` only after durable GitHub readback and reference the original
ASK Signal-ID through `in_reply_to`. The same idle owner continues the Dispatch;
do not create a replacement merely because the decision took time.
