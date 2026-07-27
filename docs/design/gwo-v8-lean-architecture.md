# GWO V8 lean architecture

Status: sole integrated current V8 mechanics contract. Implementation and
production cutover are incomplete.

`CONTEXT.md` owns ubiquitous language. Accepted ADRs own individual decisions
and their amendment history. This document integrates those decisions into one
current mechanics contract. The stabilization specification is a subordinate
dated requirement and Ticket-publication record; the lean roadmap owns
sequencing and exit criteria only.

V8 is a concurrent GitHub Ticket execution engine. LLMs perform bounded
semantic work; deterministic modules own scheduling, persistence, recovery,
verification orchestration, and repository delivery.

## Outcomes

V8 must:

- run independent Tickets concurrently, with four Worker Slots per Campaign by
  default;
- let semantic roles use independently configured Runtime Profiles without
  putting provider, model, or CLI facts in PlanSpec;
- keep the normal path moving after every semantic turn ends;
- freeze provider-neutral Authority Grants with the accepted Ticket contract;
- bound every semantic loop and infrastructure retry;
- combine compatible Candidates behind one exact PR and hosted-CI boundary;
- preserve exact Campaign, Work Run, Candidate, Evidence, Batch, and
  target-integration identity; and
- remain portable across Codex, Claude Code, Paseo, and compatible runtimes.

The framework succeeds when it removes coordination work from LLMs. Agent
count and protocol sophistication are not objectives.

## External API and status

The complete public orchestration surface is:

```text
start(repository, ready_refs, options?) -> CampaignHandle
advance(campaign_handle, wake_ref?) -> Running | Wait | Decision | Complete | Blocked
inspect(campaign_handle) -> Diagnostics
```

`CampaignHandle` is an opaque stable reference to `(repository, campaign_key)`.
It does not identify an Agent or Plan Revision and remains stable across
successor revisions of the same Campaign.

`Diagnostics` is a read-only projection containing the Campaign identity,
active Plan Revision digest, public status, named status reason, Work Run and
delivery summaries, outstanding event or due-time references, and Evidence
links. It is data, not another workflow actor.

`Diagnostics.status` and the result of `advance` use the same five values.
After authoritative readback, ExecutionKernel derives exactly one in this
order:

1. `Complete` when all required work and delivery are terminal and accepted;
2. otherwise `Running` when any semantic or deterministic action is active or
   currently due;
3. otherwise `Decision` when a named durable choice is required;
4. otherwise `Wait` when a named observable event or due time can continue the
   Campaign; and
5. otherwise `Blocked` when no authorized action, Decision, or wake remains.

Repair is a nested Work Run phase. A Work Run performing repair contributes
`Running`; Repair is never a sixth public status.

`options` may provide only the exact Runtime overrides described below. It
cannot rewrite Ticket contracts, expand Authority Grants, change PlanSpec
content, enlarge budgets, weaken Assurance Policy, or alter delivery policy.

## Five deep modules

| Module | Small interface, hidden behavior |
| --- | --- |
| PlanControl | Selected Ticket readback, one Campaign Planning Pass, PlanSpec v3 compilation, Authority Grant compilation, publication, activation, and readback |
| ExecutionKernel | The only persisted Campaign state machine, capacity owner, budget owner, and next-action authority |
| RuntimeGateway | Runtime selector resolution, multi-CLI execution, identity readback, exact permission handling, fallback, recovery, and retirement |
| CandidateGate | Complete Candidate diff audit, affected checks, Assurance Requirement derivation, Formal Review, Review Finding reconciliation, and consolidated repair |
| BatchIntegrator | Campaign-scoped compatibility, composition, exact verification, PR, hosted CI, serial integration, and delivery recovery |

Campaign Watchdog is an event-and-timer wake adapter, not a sixth domain
module. Runtime, Git, GitHub, CI, and filesystem adapters remain private seams
inside the module that owns their policy.

Deleting any of the five modules would spread its invariants across multiple
callers. A forwarding module that owns no invariant is prohibited.

## End-to-end flow

```mermaid
flowchart TD
    T["Selected Tickets"] --> PC["PlanControl"]
    PC --> CP["One Campaign Planning Pass"]
    CP --> PR["Immutable Plan Revision"]
    PR --> EK["ExecutionKernel"]
    EK --> W["Up to four concurrent Work Runs"]
    W --> CG["CandidateGate"]
    CG -->|Consolidated repair| W
    CG -->|Accepted Candidate| BI["BatchIntegrator"]
    BI --> LC["Exact-Batch local verification"]
    LC --> GH["One PR and hosted CI"]
    GH --> IN["Serial target integration"]
    IN --> EK
```

## PlanControl and Campaign planning

PlanControl snapshots the complete selected Ticket set, canonical blocker
relationships, Campaign source, target, and frozen repository policy. It
rejects invalid labels, missing contracts, blocker cycles, conflicting Ticket
claims, and oversized input before spending an LLM turn.

Each initial or successor Plan Revision receives one bounded Campaign Planning
Pass over that complete snapshot. Its output is private to PlanControl and may
contain only:

- admitted Ticket keys;
- justified dependency additions;
- genuine Exclusive Resources;
- factual Runtime capability requirements; and
- named Decision requirements.

The Coordinator cannot rewrite acceptance, add work, expand authority, select
a model or CLI, predict files, author lifecycle policy, or prescribe Worker
steps. A semantic Coordinator Decision can never expand authority. PlanControl
deterministically validates the private output and is the only PlanSpec
compiler. Compilation, publication, and readback retry the same validated
output and never repeat the Planning Pass.

A configured byte limit bounds the Planning input. Exceeding it returns a
named split-Campaign Decision; V8 neither truncates contracts nor creates an
automatic multi-call planning tree.

## PlanSpec v3 and frozen authority

PlanSpec v3 is a provider-, model-, and CLI-neutral Ticket Manifest. This
illustrative canonical shape shows the required ownership:

```yaml
schema_version: 3
repository: owner/repo
target_branch: main
campaign:
  key: campaign-key
  source: {ref: source-ref, digest: source-digest}
  contract: optional-frozen-parent-contract
  authority:
    policy_witness_digest: policy-digest
    grants:
      - {operation_id: repository.read.v1, resource_id: campaign.snapshot.v1}
policy: {ref: policy-ref, digest: policy-digest}
work:
  - key: issue:109
    source: {ref: issue-ref, digest: ticket-contract-digest}
    contract: complete-frozen-ticket-contract
    depends_on: []
    exclusive_resources: []
    capabilities: [git, local_check]
    authority:
      policy_witness_digest: policy-digest
      worker:
        grants:
          - {operation_id: workspace.write.v1, resource_id: work-run.workspace.v1}
      recovery_worker:
        grants:
          - {operation_id: workspace.write.v1, resource_id: work-run.workspace.v1}
      review:
        grants:
          - {operation_id: repository.read.v1, resource_id: review.subject.v1}
```

The Campaign Authority Grant permits only the read-only Coordinator planning
and Decision scope. Each PlanSpec work entry contains isolated-workspace
Authority Grants for `worker` and `recovery_worker` plus read-only grants for
Review Internal Subagents. Operation and resource identifiers are versioned
repository-policy identifiers, never provider permission strings.

PlanControl compiles every grant deterministically from the frozen Policy
Witness. Neither the Planning Pass, a semantic Coordinator Decision, nor
Campaign-start Runtime options can add an operation or resource. The canonical
PlanSpec digest is the authority root. The relevant authority-subtree digest
is persisted and read back through Work Run admission, Prompt acceptance,
Runtime Binding, Candidate receipt, Review Evidence, and accepted-Candidate
receipt.

Runtime permission policy belongs to the frozen grants. Deterministic
PlanControl and BatchIntegrator service authority is not semantic Runtime
authority and remains repository policy enforced by their private adapters.

PlanSpec contains no generic Agent DAG, lifecycle nodes, predicted paths,
checks, Review instructions, recovery ladder, risk, difficulty, model,
provider, CLI, Runtime binding, capacity, timeout, permission decision, or
integration node.

## Campaign and Plan Revision activation

One Campaign has exactly one active Plan Revision. Several Campaigns may be
active in one repository only when their Ticket claims are disjoint. Ticket
claim acquisition and Plan Revision activation fail closed on overlap.

Activation compare-and-swaps the key `(repository, campaign_key)` using
`expected_previous_revision_digest`, which is null for the initial revision.
A successor revision names the exact previous digest. `CampaignHandle` remains
stable while the active revision changes.

Every Activation Receipt records:

- repository;
- Campaign key;
- activated Plan Revision digest;
- expected previous revision digest; and
- repository writer generation.

The immutable Plan record and Activation Receipt are published and read back
before any Work Run is admitted. Pending activation cannot execute. After a
receipt exists, recovery rolls forward; rollback is a new durable action.

The writer generation remains repository-global and prevents simultaneous
production writers. The Integration Lease is also repository-global. Those
repository fences do not collapse independent disjoint Campaigns into one
Plan Revision.

## Runtime assignment

Runtime assignment uses exact selectors:

- Campaign-scoped `coordinator`;
- Ticket-scoped `worker`;
- Ticket-scoped `recovery_worker`;
- Ticket-scoped `review_primary`;
- Ticket-scoped `review_strong`; and
- Ticket-scoped `specialist:<policy-id>`.

The `coordinator` selector resolves in this order:

1. Campaign-start Coordinator override;
2. repository `coordinator` role mapping; and
3. host-global `coordinator` role mapping.

Each Ticket-scoped selector resolves in this order:

1. exact Campaign-start `(ticket_key, role)` override;
2. repository role mapping; and
3. host-global role mapping.

There is no Ticket-wide shorthand and no Ticket override for Coordinator.
Every mapping resolves one required primary Runtime Profile and at most one
optional availability fallback. Different selectors may intentionally resolve
to the same Profile. Missing required configuration fails closed.

Campaign-start overrides are persisted with the Campaign. For every stable
Runtime action, RuntimeGateway records the selector, configuration source,
resolved Profile digest, and whether the optional fallback was selected.
Retries, resume, readback, and same-binding recovery reuse that assignment.

Availability fallback is permitted only before any Agent identity may exist
for the stable action. After identity, RuntimeGateway recovers the same
binding. A replacement Worker binding requires terminal-binding Evidence and
uses the already resolved `recovery_worker` assignment.

PlanSpec may state factual capabilities, but it never contains a selector,
Profile, provider, model, reasoning setting, CLI, fallback, or configuration
source.

## RuntimeGateway adapter contract

RuntimeGateway owns one private provider-neutral adapter contract. Every
production adapter and the deterministic in-memory adapter implements exactly:

```text
prepare(RuntimeActionSpec) -> PrepareReceipt | RuntimeFailure
observe(stable_action_id) -> RuntimeObservation | RuntimeFailure
command(binding_ref, RuntimeCommand) -> CommandReceipt | RuntimeFailure
events(after_cursor) -> RuntimeEventPage
```

`prepare` is idempotent by stable action identity. It resolves or creates the
Agent, session, and isolated workspace and stages the Artifact-backed Prompt,
but it cannot begin semantic execution. `observe` authoritatively proves the
repository, Campaign, Plan Revision, Work Run, stable action, selected Profile,
Agent, session, workspace, and Runtime Binding identities, plus Prompt
acceptance, lifecycle, outstanding Permission Requests, and fencing state.

`RuntimeCommand` is a closed union:

```text
start | resume | park | interrupt | permission_response | fence | retire
```

RuntimeGateway may issue `start` or `resume` only after `observe` proves the
complete binding and accepted-Prompt receipt for that stable action. No
adapter has an implicit launch-on-prepare path. `events` provides cursor-based
wake hints; it never replaces `observe`.

The deterministic in-memory adapter passes the same contract suite and
failure cases as production adapters. It is not a looser fake or a second
policy implementation. Runtime Profile resolution, permission policy, and
fallback selection remain exclusively in RuntimeGateway. Retry bounds and
semantic budgets remain ExecutionKernel policy.

## Worker Slots and Work Run bounds

A Campaign has four Worker Slots by default. The setting is host-global with a
repository override; it is not inferred by an LLM and is absent from PlanSpec.
Each Campaign also has one fixed Coordinator semantic-control capacity that is
not a general scheduling Slot.

Only an unsatisfied Ticket dependency, a genuine Exclusive Resource, the
Campaign Worker Slot limit, or observed Runtime unavailability blocks an
otherwise eligible Work Run. Predicted file overlap does not block isolated
workspaces.

A Work Run retains its Worker Slot through affected checks, Formal Review, and
immediate consolidated repair. An accepted Candidate waiting for delivery or a
Runtime proven parked releases the Slot. Resumption reacquires capacity before
semantic execution continues. Review Internal Subagents consume no Worker
Slot.

Within one Plan Revision, each Work Run permits:

- at most three distinct Candidate SHAs submitted in total;
- one initial Worker binding; and
- at most one replacement Worker binding after terminal-binding Evidence.

Switching bindings does not reset the Candidate-submission bound or the Review
Finding ledger. Invalid Review transport may retry once through
`review_strong` and does not consume a Candidate submission. An unchanged
rejected Candidate cannot obtain another Formal Review.

## Runtime permissions, waits, and recovery

RuntimeGateway normalizes each Permission Request into the exact operation ID,
resource ID, request identity, Runtime Binding, and authority-subtree digest.
It auto-approves only when the exact request is covered by both the frozen
Authority Grant and its Policy Witness. An unmatched, ambiguous, or
higher-authority request returns `PermissionRequired`.

RuntimeGateway cannot expand authority. A Coordinator may propose only an
alternative already covered by the same frozen authority subtree. Any new or
broader operation or resource, or any changed authority root, requires an
explicitly recorded human Decision, deterministic recompilation, and a
successor Plan Revision. A semantic Coordinator Decision can never expand
authority.

An unmatched permission or bounded Coordinator-attention request enters a
three-minute interactive-wait grace. This default is host-global with a
repository override. The Work Run retains its Worker Slot during grace. After
expiry, ExecutionKernel releases the Slot only after RuntimeGateway proves the
binding parked. A later Decision is recorded until the Work Run reacquires a
Slot and resumes the same binding.

After thirty minutes without trusted state change, Campaign Watchdog first
requests zero-LLM Runtime, process, workspace, and Campaign readback. The stale
deadline is host-global with repository override. If one binding remains
genuinely ambiguous, that binding may receive one bounded Coordinator stale
diagnosis. Each initial or replacement binding has its own one-diagnosis
maximum; periodic LLM monitoring is prohibited.

Time, permission delay, capacity pressure after identity, and ambiguous
lifecycle never authorize a replacement. Only terminal-binding Evidence
proving the exact action, Agent, session, workspace, terminal state, fencing,
and checkpoint permits ExecutionKernel to start the one replacement binding.

## Runtime failure taxonomy

This table is the canonical Runtime failure policy. ADRs and subordinate
documents link here rather than restating variants.

| Authoritative observation | Required response | Exhausted or terminal result |
| --- | --- | --- |
| Cached provider-unavailable snapshot | Treat it as advisory and perform one live authoritative observation. It selects no fallback or replacement and consumes no retry, binding, semantic, or Candidate budget. | None from the cached fact alone. |
| Live provider recovery before identity | Use the primary Profile only when no fallback was durably selected. A durably selected fallback remains selected even if the primary later recovers. | Continue the already selected assignment. |
| Live provider unavailable before identity | Select the configured availability fallback at most once. Without a usable configured fallback, persist `Wait(RuntimeProviderUnavailable, next_check_at)` for the initial observation plus at most two retries. | `Blocked(RuntimeProviderUnavailable)` after the bounded observations. |
| Permanent configuration failure before identity | Do not select fallback and do not perform transport retry. | `Blocked(RuntimeConfigurationInvalid)`. |
| Permanent configuration failure after identity | Preserve and fence as required around the same binding. Replacement remains forbidden unless terminal-binding Evidence permits the one configured replacement. | Human `Decision(RuntimeConfigurationRepairRequired)` when replacement is not proven and authorized. |
| Runtime transport unavailable | Read back by stable action identity, then persist `Wait(RuntimeTransportUnavailable, next_check_at)` for the initial attempt plus at most two retries. Do not select fallback or replacement. | Before identity: `Blocked(RuntimeTransportUnavailable)`. After identity: human `Decision(RuntimeObservationUnavailable)`. |
| Live provider recovery after identity | Resume or read back the same binding only. Never change its Profile, provider, CLI, session, or workspace. | Existing same-binding lifecycle applies. |

No failure before accepted-Prompt readback consumes a semantic or Candidate
budget. After a failed create, RuntimeGateway first reads back the stable
action identity. It may remove only a proven action-owned empty workspace.
Identity or content ambiguity fences every attributable resource and returns a
named human Decision; it never guesses adoption, creates a duplicate, or
reuses unproven content.

RuntimeGateway and Campaign Watchdog never restart a provider daemon
automatically. A daemon restart can terminate unrelated Agents and is outside
the authorized recovery contract.

## CandidateGate

CandidateGate is the only Formal Review entry:

```text
immutable Candidate
  -> complete Candidate diff and scope/authority audit
  -> affected deterministic checks
  -> Assurance Requirement
  -> required Formal Review Internal Subagent
  -> Accepted | consolidated repair | Decision | Wait
```

Deterministic failure stops before LLM Review. Worker self-checks cannot become
Formal Review Evidence. An external `code-review` skill may supply heuristics,
but V8 owns the Review Subject, coverage, transport, typed output, identity,
budget, and lifecycle.

A Review Subject binds the exact base, Candidate, Ticket contract, standards,
Check Evidence, Assurance Requirement, Policy Witness, and protocol version.
A complete no-Review allowlist match may use zero Reviewer calls. Standard
Assurance uses one `review_primary` observation. Strict Assurance adds at most
one `specialist:<policy-id>` observation or human Decision. Invalid Review
transport may retry once through `review_strong`; a valid rejection is not
repeated against an unchanged Review Subject.

A changed Candidate creates a new Review Subject. The complete Artifact-backed
Review Finding ledger is preserved, and every earlier Review Finding receives
a typed disposition. CandidateGate returns one consolidated repair request
containing that ledger; Review Findings and repair context are never silently
truncated.

Acceptance emits a compact receipt binding Campaign, Work Run, Candidate,
authority-subtree digest, Review Subject, Assurance Requirement, and Evidence.
Only that receipt makes a Candidate eligible for delivery.

## BatchIntegrator

BatchIntegrator forms Batches only from accepted Candidates in the same
Campaign. V8.0 does not batch across Campaigns. Every Batch receipt preserves
Campaign identity and each member's Work Run identity.

The Batch member limit is an integer from one through four, default four. It is
a host-global setting with repository override and is absent from PlanSpec.
When the repository-global Integration Lease is free, BatchIntegrator:

1. chooses the oldest eligible accepted Candidate as the seed;
2. scans remaining accepted Candidates oldest first;
3. adds a Candidate only when it is pairwise compatible with every frozen
   member; and
4. freezes immediately when the scan ends or the member limit is reached.

It never waits for running Work Runs, uses a timer to grow the Batch, predicts
completion, or calls an LLM.

Eligibility requires the same Campaign and compatible:

- target and base identity, or a valid Clean Base Advance;
- check environment;
- Policy Witness digest;
- delivery identity;
- Assurance Requirement;
- protected surfaces; and
- pairwise Interaction Keys.

Strict Assurance is always a Singleton Batch. A repository-policy
classification of non-decomposable, high-coupling, or protected Interaction
Key is also Singleton. Other Candidates may batch only when all Interaction
Keys and protected surfaces are pairwise compatible.

### PatchIdentityV1 and Clean Base Advance

`PatchIdentityV1` is a repository-tree delta identity, not Git's heuristic
`git patch-id`. Define `LP(bytes)` as an unsigned 64-bit big-endian byte length
followed by those bytes. Its digest is:

```text
SHA-256(
  ASCII("gwo.patch-identity.v1\0")
  || LP(ASCII(repository_object_format))
  || CONCAT(LP(entry) FOR entry IN SORT_BYTEWISE(encoded_delta_entries))
)
```

Each `encoded_delta_entry` is the concatenation of length-prefixed:

1. old path as complete repository-relative Git path bytes with `/` separators,
   or empty when absent;
2. new path as complete repository-relative Git path bytes with `/` separators,
   or empty when absent;
3. canonical change kind;
4. old mode as six-digit ASCII octal, or empty when absent;
5. new mode as six-digit ASCII octal, or empty when absent;
6. old blob or gitlink object ID as raw object-ID bytes, or empty when absent;
   and
7. new blob or gitlink object ID as raw object-ID bytes, or empty when absent.

Unchanged entries are omitted. The canonical change kind is `add` when the old
entry is absent, `delete` when the new entry is absent, `type-change` when the
Git object or file type changes, and `modify` for every other object-ID or mode
change, including an executable-bit change. Entries are sorted bytewise by
their complete encoding. The algorithm compares Git trees, never worktree
text. Binary content uses exact object IDs. Rename/copy detection is disabled,
so a rename or copy is represented as delete plus add. Symlink and executable
modes participate. Gitlinks use their exact object IDs and are protected
Singleton work.

Missing objects, case-folding or path-normalization ambiguity, unsafe paths,
and merge ambiguity fail closed. BatchIntegrator computes and stores the
original `PatchIdentityV1` for `(base, Candidate)`.

For Clean Base Advance, BatchIntegrator applies each accepted Candidate alone
to the advanced target in an isolated Git tree. It recomputes
`PatchIdentityV1(advanced_target, advanced_member_tree)` and requires equality
with that member's original digest before any multi-member Batch composition.
It never compares one member with the whole composed Batch.

The Candidate and Review Evidence remain bound to the original Candidate and
Review Subject; Patch identity cannot authorize cross-SHA Review reuse. Batch
Evidence binds the algorithm version, original base and Candidate tree,
original patch digest, advanced target and advanced member tree, recomputed
patch digest, final Batch SHA, and the exact local and hosted Checks.

Clean Base Advance additionally requires the original base to remain an
ancestor of the current target, unchanged Candidate and Evidence, no protected
interaction with the target delta, and clean isolated composition. The final
Batch must still pass its complete local and hosted checks.

The same immutable Batch SHA must:

1. pass the repository-equivalent local suite;
2. be the pushed branch and pull-request head observed by GitHub; and
3. be the exact head observed by hosted CI.

Integration names that Batch SHA. The target branch may advance to a merge
commit rather than equal the Batch SHA, so target readback must prove the Batch
SHA is reachable as an ancestor and that GitHub's PR merge mapping connects the
PR head to the observed target head. Squash or rebase integration rewrites the
reviewed identity and therefore fails closed.

### Durable hosted result adoption

Every terminal hosted check produces one integrity-validated receipt keyed by
the stable delivery action, Batch SHA, check-suite identity, provider check ID,
terminal outcome, and observation digest.

Once that receipt is durably persisted, restart adopts it without another
hosted read. A receipt or provider observation whose Batch, suite, check ID, or
digest mismatches the stable delivery action returns
`DeliveryIdentityMismatch`. Ambiguous provider attribution returns
`DeliveryAttributionAmbiguous`.

Both outcomes preserve every Candidate and all Evidence. They are delivery
identity failures, not code-class failures, and permit neither Singleton Batch
Fallback nor Worker resume. Only an unambiguous receipt for the exact delivery
action can drive integration or code-class recovery.

Infrastructure failure retries the unchanged Batch SHA at most twice. A
composition, exact-local, or code-class hosted failure may dissolve one
multi-member Batch into Singleton Batches once. There is no recursive
bisection or LLM attribution. Only a failing Singleton can resume its parked
Worker; changed code re-enters CandidateGate.

## Persistence and liveness

ExecutionKernel is the only persisted state machine and next-action authority.
Every external effect has a stable action identity and is read back before
retry. No local transaction remains open during external I/O. GitHub remains
the durable business record; local storage is rebuildable control state.

Campaign Watchdog subscribes to Runtime and hosted-check events and owns
persisted `next_check_at` timers. Events are wake hints only; each wake invokes
`advance`, which performs authoritative readback. Restart reconstructs timers
and subscriptions from Campaign state. A due `next_check_at` wake invokes the
same `advance` path and recovers a lost callback without duplicating its
effect. Campaign Watchdog never restarts the Paseo daemon automatically.

Detailed admission, action, permission, Review, and delivery records are
module-private implementation facts. They are not public actors or vocabulary
that Coordinators and Workers must learn.

## Defaults

The current deterministic defaults are:

| Setting | Default | Configuration |
| --- | --- | --- |
| Worker Slots per Campaign | 4 | host-global, repository override |
| Batch member limit | 4, maximum 4 | host-global, repository override |
| Stale-binding deadline | 30 minutes | host-global, repository override |
| Interactive-wait grace | 3 minutes | host-global, repository override |
| Runtime availability/transport observations | initial plus at most 2 retries | fixed V8.0 policy |
| Distinct Candidate SHAs per Work Run | at most 3 | fixed V8.0 policy |
| Worker bindings per Work Run | initial plus at most one replacement | fixed V8.0 policy |

No default is inferred by an LLM or embedded as a Runtime assignment in
PlanSpec.

## Deliberate exclusions

V8.0 does not add:

- automatic difficulty or risk scoring;
- a model evaluator, price router, or learned scheduler;
- a resident Coordinator or periodic LLM monitor;
- a general Agent DAG;
- one Plan Revision per Ticket;
- Formal Review as a top-level workflow unit;
- repeated Review of an unchanged Review Subject;
- per-Ticket PR and hosted CI by default;
- cross-Campaign batching;
- recursive Batch optimization;
- cross-SHA Review approval reuse;
- a permanent GWO daemon or event bus;
- a long-lived shadow execution phase; or
- automatic authority expansion.

## Cutover

Activation is the durable writer-generation and Activation Receipt commit
point. Before Cutover Guard success, all V3-composition and V2-projection
adapters, callers, and write paths are absent or unreachable. V8 never
projects or reinterprets PlanSpec v2 as v3.

Active v2 work must finish through its original decoder before cutover or be
authoritatively quiescent and available only for read-only audit. V8 never
resumes, interprets, or writes v2. The Guard proves those facts, old-writer
quiescence, state compatibility, repository-global fence availability, and
required Runtime configuration before the activation commit point. Guard
failure leaves the V6.1 writer and all production state unchanged.

After activation, the root repository runs one real Campaign with four
independent Tickets and proves the public API, parallel Work Runs, exact
Runtime assignment, frozen authority, permission parking, CandidateGate,
bounded repair and replacement, restart, lost-callback recovery, the bounded
zero-LLM-readback-plus-one-diagnosis stale-binding path, one Campaign-scoped
Integration Batch, exact PR/CI identity, serial integration readback, and
cleanup. Passing makes V8 the default for new Campaigns in this repository
before downstream repositories adopt it.
