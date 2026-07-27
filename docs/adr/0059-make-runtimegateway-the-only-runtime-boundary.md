---
status: accepted
amends: ADR-0018, ADR-0026, ADR-0044, ADR-0051, ADR-0052, ADR-0053, ADR-0055, ADR-0057
---

# Make RuntimeGateway the only Runtime boundary

RuntimeGateway is V8's only external execution boundary. ExecutionKernel,
PlanControl, and CandidateGate exchange typed Runtime requests and receipts
with it; they never construct Codex, Claude Code, Paseo, shell, Agent, session,
or provider commands directly.

RuntimeGateway hides:

- deterministic Runtime Profile resolution from host-global configuration and
  repository override;
- internal Adapters for each supported execution model and CLI;
- Worker and Coordinator materialization, resume, observation, checkpoint,
  fencing, and retirement;
- Review and specialist Internal Subagent launch, including selection of a CLI
  different from the parent Worker;
- stable Agent, session, action, Prompt, and Workspace identity readback;
- Artifact-backed Prompt and output transport without silent truncation;
- structured permission request readback and exact authorization enforcement;
- pre-identity availability fallback and post-identity same-binding recovery;
  and
- bounded command duration, transport retry, and typed Runtime errors.

Provider Adapters remain replaceable implementation plugins behind this
boundary. They advertise truthful capabilities, but no caller branches on a
provider name. The same frozen Work Contract, authority digest, and action key
can therefore be rendered for Codex, Claude Code, Paseo, or another compatible
execution model without changing PlanSpec or ExecutionKernel.

The permission broker is internal to RuntimeGateway. It may automatically allow
one exact structured request only when the requested operation, resource, and
authority are completely covered by the frozen Effect Contract and repository
Permission Policy. It approves the individual request ID, never an open-ended
`--all` grant. An unmatched, ambiguous, or higher-authority request returns
`PermissionRequired` to ExecutionKernel.

RuntimeGateway cannot expand authority. A Coordinator may propose one
lower-privilege alternative under the existing contract but cannot grant the
original higher authority. If no authorized alternative exists, a human
Decision is required. Interactive Wait Grace, parking, Slot release, and
Attempt budgets remain ExecutionKernel policy; RuntimeGateway only performs
and proves the requested park, resume, allow, deny, or readback operation.

Availability fallback is allowed only before any Agent identity may exist for
the stable action key and only for the one configured fallback Profile. After
identity exists, RuntimeGateway recovers and reads back the same binding. It
may produce a Terminal Binding Receipt proving terminal state, fencing, and
Workspace checkpoint, but only ExecutionKernel may spend the second Worker
Attempt on a Recovery Worker. Timeout, permission delay, ambiguity, or capacity
pressure after identity never changes CLI or creates a replacement Agent.

RuntimeGateway makes no semantic delivery, Review, repair, scheduling, budget,
or model-ranking decision. `primary` and `strong`, or primary and fallback,
may map to the same user-configured Runtime Profile. V8 adds no model evaluator,
price router, implicit strength ordering, or fallback chain.

Live Agent sessions are intentionally not portable between CLIs. Work
Contracts, action identities, Workspace checkpoints, Candidate SHAs, and typed
Evidence remain portable and recoverable.
