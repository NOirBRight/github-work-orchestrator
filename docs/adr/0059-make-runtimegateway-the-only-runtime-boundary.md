---
status: amended by ADR-0061
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
provider name. The same frozen Ticket contract, authority-subtree digest, and
action key can therefore be rendered for Codex, Claude Code, Paseo, or another
compatible execution model without changing PlanSpec or ExecutionKernel.

The permission broker is internal to RuntimeGateway. It may automatically allow
one exact normalized request only when its operation ID and resource ID are
covered by both the frozen Authority Grant and the referenced Policy Witness.
The request record also binds request identity, Runtime Binding, and
authority-subtree digest. RuntimeGateway approves the individual request ID,
never an open-ended `--all` grant. An unmatched, ambiguous, or
higher-authority request returns `PermissionRequired` to ExecutionKernel.

RuntimeGateway cannot expand authority. A Coordinator may propose one
alternative already covered by the frozen grant but cannot grant the original
higher authority. Expansion requires a durable Decision and successor Plan
Revision with a newly compiled authority root. Interactive-wait grace, parking,
Slot release, and binding bounds remain ExecutionKernel policy; RuntimeGateway
only performs and proves the requested park, resume, allow, deny, or readback
operation.

Availability fallback is allowed only before any Agent identity may exist for
the stable action key and only for the one configured fallback Profile. After
identity exists, RuntimeGateway recovers and reads back the same binding. It
may produce terminal-binding Evidence proving action, Agent, session,
workspace, terminal state, fencing, and checkpoint, but only ExecutionKernel
may use the single replacement binding with the configured `recovery_worker`
assignment. Timeout, permission delay, ambiguity, or capacity pressure after
identity never changes CLI or creates a replacement Agent.

RuntimeGateway makes no semantic delivery, Review, repair, scheduling, budget,
or model-ranking decision. `review_primary` and `review_strong`, or primary and
availability fallback, may map to the same user-configured Runtime Profile. V8
adds no model evaluator, price router, implicit strength ordering, or fallback
chain.

Live Agent sessions are intentionally not portable between CLIs. Ticket
contracts, Authority Grants, Policy Witnesses, action identities, workspace
checkpoints, Candidate SHAs, and typed Evidence remain portable and
recoverable.
