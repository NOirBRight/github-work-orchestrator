---
status: amended by ADR-0061
amends: ADR-0018, ADR-0024, ADR-0026, ADR-0044, ADR-0051, ADR-0052, ADR-0053, ADR-0055, ADR-0057
---

# Make RuntimeGateway the only Runtime boundary

RuntimeGateway is V8's only external execution boundary. ExecutionKernel,
PlanControl, and CandidateGate exchange typed Runtime requests and receipts
with it; they never construct Codex, Claude Code, Paseo, shell, Agent, session,
or provider commands directly.

Every production adapter and the deterministic in-memory adapter implements
the same private provider-neutral interface:

```text
prepare(RuntimeActionSpec) -> PrepareReceipt | RuntimeFailure
observe(stable_action_id) -> RuntimeObservation | RuntimeFailure
command(binding_ref, RuntimeCommand) -> CommandReceipt | RuntimeFailure
events(after_cursor) -> RuntimeEventPage
```

`prepare` may stage identity, workspace, and Artifact-backed Prompt but cannot
start semantic execution. `observe` must prove the complete binding and Prompt
receipt—including repository, Campaign, Plan Revision, Work Run, stable
action, selected Profile, Agent, session, workspace, Runtime Binding,
lifecycle, permission, and fence state—before RuntimeGateway issues the
closed-union `start` or `resume` command. The other allowed commands are
`park`, `interrupt`, `permission_response`, `fence`, and `retire`. Production
and in-memory implementations pass the same contract suite; Profile and
permission policy remain in RuntimeGateway.

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

RuntimeGateway cannot expand authority. A Coordinator may propose only an
alternative already covered by the same frozen authority subtree. Any new or
broader operation or resource, or a changed authority root, requires an
explicitly recorded human Decision, deterministic recompilation, and a
successor Plan Revision. A semantic Coordinator Decision can never expand
authority. Interactive-wait grace, parking, Slot release, and binding bounds
remain ExecutionKernel policy; RuntimeGateway only performs and proves the
requested `park`, `resume`, or `permission_response` command and authoritative
`observe`; allow and deny are `permission_response` payload outcomes, not
additional adapter commands.

Availability fallback is allowed only before any Agent identity may exist for
the stable action key and only for the one configured fallback Profile. After
identity exists, RuntimeGateway recovers and reads back the same binding. It
may produce terminal-binding Evidence proving action, Agent, session,
workspace, terminal state, fencing, and checkpoint, but only ExecutionKernel
may use the single replacement binding with the configured `recovery_worker`
assignment. Timeout, permission delay, ambiguity, or capacity pressure after
identity never changes CLI or creates a replacement Agent.

Live provider unavailability after identity opens or reuses one persisted
episode bound to the exact stable action, Runtime Binding, Profile, provider,
CLI, Agent, session, workspace, accepted Prompt, and frozen authority.
RuntimeGateway supplies uniquely identified authoritative live-observation
receipts and may only read back or resume that same binding. ExecutionKernel
persists the episode, each accepted receipt, and its counter before returning
`Wait(RuntimeProviderUnavailable, next_check_at)` for the initial and
first-retry observations. After persisting the second retry, which is the third
authoritative live-unavailable observation, it returns the human-owned
`Decision(RuntimeProviderRecoveryRequired)`.

Only a uniquely persisted authoritative observation receipt advances that
counter. Cached facts, replayed callbacks or wakes, restart, and repeated
`advance` without a new live observation neither consume nor reset it.
Transport unavailability advances only its independent counter and is never
double-counted as provider unavailability. The episode releases no Slot or
claim, consumes no semantic, Candidate, or replacement budget, and authorizes
no fallback, new `prepare` or create, assignment switch, Profile/provider/CLI
switch, daemon restart, park/terminal/fence inference, or replacement. Only
independent terminal-binding Evidence may enter the existing one-replacement
path. Same-binding readback or resume after provider recovery closes the
episode. The complete response matrix remains defined only in the canonical
[`Runtime failure taxonomy`](../design/gwo-v8-lean-architecture.md#runtime-failure-taxonomy).

RuntimeGateway makes no semantic delivery, Review, repair, scheduling, budget,
or model-ranking decision. `review_primary` and `review_strong`, or primary and
availability fallback, may map to the same user-configured Runtime Profile. V8
adds no model evaluator, price router, implicit strength ordering, or fallback
chain.

Live Agent sessions are intentionally not portable between CLIs. Ticket
contracts, Authority Grants, Policy Witnesses, action identities, workspace
checkpoints, Candidate SHAs, and typed Evidence remain portable and
recoverable.

The integrated adapter definition is
[`RuntimeGateway adapter contract`](../design/gwo-v8-lean-architecture.md#runtimegateway-adapter-contract).
Provider, configuration, and transport behavior follows the single
[`Runtime failure taxonomy`](../design/gwo-v8-lean-architecture.md#runtime-failure-taxonomy).
