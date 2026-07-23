# GitHub Work Orchestration

This context coordinates concurrent GitHub work while keeping planning,
execution, evidence, and integration authority explicit.

## Goals and coordination

**Repository Coordinator**:
The authoritative root Agent that makes semantic planning, diagnosis, and
integration decisions for one repository. A Goal outlives any one Coordinator
session.
_Avoid_: Father, Campaign Agent, permanent control loop

**Task Group**:
A label that groups bounded work toward one objective. It is not an Agent,
Workspace, or independent plan authority.
_Avoid_: Campaign room, Task Group Agent

**Task Group Goal**:
The stable objective and acceptance conditions associated with a Task Group.
It remains active across Coordinator turns and Plan Revisions until verified
complete or explicitly blocked.
_Avoid_: Coordinator turn, mutable task list

**Goal Driver**:
The host continuation mechanism that wakes Kernel Reconciliation until a Goal
is complete or blocked without keeping an Agent alive while waiting.
_Avoid_: GWO daemon, heartbeat loop, Coordinator Agent

**Coordinator Turn Observation**:
The compact control-plane reference from one Coordinator turn to its semantic
result, such as executable work, a Wait Condition, Decision Gate, replan,
integration decision, or completion proposal.
_Avoid_: Coordinator Outcome entity, GitHub turn receipt, progress log

**Semantic Input Digest**:
The digest of Goal facts that can change a Coordinator decision. Time, token
use, tool activity, liveness, and log growth are observation noise and do not
belong to it.
_Avoid_: heartbeat digest, transcript hash

**Wait Condition**:
An explicit observable external condition on which active work can sleep
without consuming an Agent turn.
_Avoid_: inactivity, unspecified waiting, progress timeout

**Blocked Goal**:
An unfinished Goal whose remaining path requires a named decision, external
input, runtime configuration, or other explicit unblock condition.
_Avoid_: active wait, failed Plan Node, silent Coordinator stop

**Decision Gate**:
A durable human decision required before scope, authority, budget, external
effects, or acceptance meaning may change.
_Avoid_: routine retry approval, chat-only consent

## Planning

**Plan Intent**:
A non-authoritative proposal of goals, decomposition, dependencies, capability
needs, risk, and uncertainty.
_Avoid_: executable plan, Plan Revision

**Plan Compiler**:
The deterministic authority that converts Plan Intent and policy into one
canonical Compiled Plan or rejects it.
_Avoid_: Semantic Planner, prompt generator

**Compiled Plan**:
The canonical PlanSpec bytes, digest, and Compilation Record returned by the
Plan Compiler.
_Avoid_: mutable plan draft, reserialized PlanSpec

**PlanSpec**:
The self-contained semantic authorization inside one immutable Plan Revision.
It contains intended work and proof requirements but no live execution facts.
_Avoid_: Runtime snapshot, Plan Intent, Compilation Record

**Plan Revision**:
An immutable, digest-addressed version of the authorized repository plan.
Replanning creates another revision instead of rewriting it.
_Avoid_: mutable plan, SQLite-only plan

**Plan Activation**:
The transition that makes one durably published Plan Revision authoritative
for new Admissions.
_Avoid_: Plan publication, Agent creation

**Activation Receipt**:
The durable GitHub fact that commits one Plan Activation. After it is read
back, recovery may only finish the activation or record a later compensating
action.
_Avoid_: local CAS result, pending activation

**Compilation Record**:
The audit record of source references, policy digests, and edge provenance
used to produce a Plan Revision.
_Avoid_: PlanSpec, duplicate execution graph

**Typed Plan Edge**:
The single authoritative, typed dependency relationship between two Plan
Nodes in PlanSpec.
_Avoid_: parallel GitHub and contract dependency maps

**Replan Hold**:
The pause on new Admissions for an affected Goal while a replacement Plan
Revision is being authorized.
_Avoid_: Attempt cancellation, repository-wide stop

## Work and execution

**Work Item**:
The durable outcome represented by one GitHub Issue. Several Plan Nodes and
Plan Revisions may contribute to it.
_Avoid_: Plan Node, Attempt, task row

**Ready Work Item**:
A Work Item whose tracker state is `ready-for-agent` and whose durable
behavioral contract is suitable input to Plan Intent.
_Avoid_: raw Issue, `needs-triage`, executable Plan Node

**Completed Work Item**:
A non-code Work Item whose required Results and Evidence are verified.
_Avoid_: implementation submitted, Agent finished

**Integrated Work Item**:
A code-producing Work Item whose verified candidate entered its target branch
and passed durable readback.
_Avoid_: PR opened, merge intended

**Plan Node**:
One immutable unit of authorized work linked to a Work Item and an explicit
output contract. V8.0 kinds are work, decision, and integration. Review is a
Candidate Evidence gate inside a Work Node's output contract, not a Plan Node.
_Avoid_: GitHub Issue, Agent, Attempt

**Node Key**:
The stable identity retained across Plan Revisions only while a Plan Node's
semantic inputs, dependencies, Effect Contract, and output contract remain
unchanged.
_Avoid_: display name, mutable node ID

**Admission**:
The atomic decision that reserves all required claims and authorizes
Materialization for one ready Plan Node.
_Avoid_: Attempt, Agent spawn

**Materialization**:
The idempotent convergence of an Admission into a read-backed Runtime Binding
with its initial Prompt accepted.
_Avoid_: Admission, blind spawn retry

**Materialization Ambiguity**:
An unresolved Admission for which readback cannot prove whether a partial
runtime began execution. Its claims remain protected until reconciled.
_Avoid_: failed Attempt, safe replacement

**Attempt**:
One execution of one Plan Node, permanently bound to its Plan Revision and
Runtime Binding. Resuming the same binding and performing one formal Repair
Round continue the same Attempt. A parent Agent may use Internal Subagents
without creating another Attempt.
_Avoid_: Plan Node, test run, mutable retry

**Internal Subagent**:
An Agent delegated by a GWO-managed parent Agent inside that parent's assigned
responsibility. It cannot exceed the parent's Effect Contract and has no
independent Plan Node, Admission, Attempt, Role Binding, or GWO capacity slot;
the parent remains accountable for authoritative lifecycle facts and Results.
Standards, Spec, and specialist review subagents are additionally read-only and
cannot delegate further.
_Avoid_: managed top-level Agent, hidden Plan Node, independent Result claimant

**Attempt Terminal Reason**:
The explicit reason an Attempt ended: rejected candidate, healthy execution
with no result, lost runtime, or authorized supersession.
_Avoid_: generic Failed Attempt

**Repair Round**:
One formal return of independently verified findings to the same Runtime
Binding after Candidate rejection.
_Avoid_: internal test retry, new Attempt

**Recovery Ladder**:
The authorized sequence of one primary Attempt and one fresh frontier Attempt,
each with at most one Repair Round.
_Avoid_: unbounded retry, incremental model ladder

**Failed Plan Node**:
A Plan Node whose semantic Recovery Ladder is exhausted without a verified
Result and without an unresolved external blocker.
_Avoid_: test failure, runtime outage, Rejected Candidate

**Work-Conserving Admission**:
The policy that admits every compatible ready Plan Node until a real
dependency, exclusive claim, configured pool, or observed Runtime limit is
reached.
_Avoid_: one-node wave, Agent-chosen capacity

**Resource Claim**:
A Plan Node's declared need for an operational resource that Admission must
reserve.
_Avoid_: live capacity snapshot, Runtime Binding

**Write Scope**:
The repository regions a Plan Node may modify under its Effect Contract.
Overlap is an advisory integration risk unless a resource is explicitly
exclusive.
_Avoid_: hard Hotset lock, Integration Lease

**Exclusive Resource**:
A resource whose concurrent use is explicitly unsafe, such as a target branch
or non-shareable external environment.
_Avoid_: ordinary file overlap, retained workspace

**Integration Lease**:
The repository-scoped exclusive right to mutate the target integration branch.
_Avoid_: repository-wide execution lock

**Active Turn Slot**:
One bounded unit of concurrent Agent reasoning or tool execution.
_Avoid_: Agent identity, retained session, workspace count

**Parked Attempt**:
An Attempt retaining its recoverable identity and necessary claims while it
waits on an observable external condition without holding an Active Turn Slot.
_Avoid_: failed Attempt, abandoned runtime

## Runtime and authority

**Runtime Adapter**:
An implementation of the runtime seam used to create, resume, observe, and
retire execution resources through stable identities.
_Avoid_: provider binding, model selector

**Runtime Capability**:
An ability truthfully advertised by a Runtime Adapter.
_Avoid_: capability inferred from provider name

**Runtime Requirements**:
The capabilities and logical execution level a Plan Node requires without
choosing a concrete runtime.
_Avoid_: Runtime Binding, model name

**Worker Tier**:
The logical Worker capability level: light, standard, heavy, or frontier.
It is not a provider or model identity.
_Avoid_: Risk, Runtime Profile

**Runtime Profile**:
A named operational configuration containing provider, model, reasoning,
mode, features, and optional fallback.
_Avoid_: Worker Tier, PlanSpec requirement

**Role Binding**:
The operational mapping from a managed runtime role, such as auto-created
Coordinator, to a Runtime Profile.
_Avoid_: extra Worker tier, Agent identity

**Review Profile**:
The host-local global or repository mapping from `standard_axis`,
`recovery_axis`, or `strict_specialist` to a Runtime Profile used for an
Internal Subagent. It is not a Role Binding or PlanSpec field.
_Avoid_: Reviewer Role Binding, Worker Tier, managed Reviewer identity

**Runtime Policy**:
The deterministic policy that resolves Runtime Requirements, Worker Tier,
role, recovery stage, configuration, and availability into an eligible
Runtime Profile.
_Avoid_: Semantic Planner, learned router

**Runtime Binding**:
The observed Adapter, Runtime Profile, Agent, session, and workspace used by
one Attempt.
_Avoid_: Runtime Requirements, desired profile

**Effect Contract**:
The explicit effects and authorities granted to one Plan Node. In V8.0 it is
a cooperative authorization and audit contract, not hostile-host enforcement.
_Avoid_: Skill instruction, security attestation, Capability Envelope

**Skill Reference**:
An optional logical name for Prompt guidance resolved when an Admission
compiles its Prompt.
_Avoid_: workflow command, locked dependency, execution authority

**Runtime Interrupt**:
The bounded stop of an active turn while preserving recoverable runtime
identity and context.
_Avoid_: Attempt replacement, retirement

**Runtime Retirement**:
The read-backed execution of Kernel-authorized cleanup after runtime ownership
has ended.
_Avoid_: interrupt, Adapter-authorized deletion

## Candidate, evidence, and result

**Artifact**:
A produced object such as a commit, pull request, report, or test output.
_Avoid_: Evidence, Result

**Result Claim**:
An Attempt's structured assertion that it produced a Candidate Artifact and
met its output contract.
_Avoid_: Result, Evidence, completion

**Candidate**:
An immutable local Artifact submitted by Result Claim for Verification.
Internal edits and test runs are not Candidates.
_Avoid_: working tree, intermediate SHA

**Candidate Rejected**:
A Verification decision that a submitted Candidate does not satisfy its
Evidence or acceptance contract.
_Avoid_: internal test failure, runtime outage, Failed Plan Node

**Typed Evidence**:
A schema-valid observation bound to an exact subject, observer, source
reference, and content digest.
_Avoid_: Worker self-report, natural-language done

**Check Evidence**:
An observed local command or hosted check result bound to its Candidate, check
definition, environment, input projection, outcome, and durable source.
_Avoid_: reported pass, mandatory rerun

**Review Evidence**:
A Runtime-Adapter-observed, exact-Candidate record containing separate
Standards and Spec axis observations, plus any required specialist observation.
Each axis retains its fixed input, runtime identity, output, and digest; the
parent cannot merge or rerank findings.
_Avoid_: Review Result, permanent Reviewer identity, Worker self-report

**Review Gate**:
The output-contract requirement that a Candidate carry the risk-required,
blocker-free Review Evidence before publication or Integration. It has no
Plan Node, Admission, Attempt, or lifecycle of its own.
_Avoid_: Review Plan Node, transient parent Reviewer, Coordinator approval

**Evidence Manifest**:
The compact durable index of required Evidence digests and source references.
_Avoid_: raw log archive, SQLite-only evidence

**Verification**:
The decision that accepts, rejects, or waits on a Result Claim by evaluating
its Evidence against the output contract.
_Avoid_: Review opinion, result submission

**Result**:
The one output accepted for a Plan Node after Verification.
_Avoid_: Attempt output, Candidate Artifact

**Result Adoption**:
A newer Plan Revision's reference to an existing verified Result for the same
unchanged Node Key and contract digest.
_Avoid_: Result copy, Attempt rebinding

**Publication Eligibility**:
The derived fact that one exact Candidate has all required valid local Check
and Review Evidence and no blocker, permitting its first push.
_Avoid_: lifecycle state, mutable candidate freeze

**Code Review**:
The Candidate-producing Work Attempt invokes `code-review` with a fixed,
history-free packet and runs independent read-only Standards and Spec Internal
Subagents in parallel. The Runtime Adapter observes their outputs and the
parent mechanically assembles Review Evidence without merging or reranking
findings. A missing or invalid axis can be recovered without repeating a valid
axis for the same Candidate.
_Avoid_: Review Plan Node, Review Result, permanent dual Reviewer pair

## Durability and transition

**Writer Generation**:
The durable identity of the one lifecycle writer currently authorized for a
repository.
_Avoid_: process ID, Coordinator epoch

**Coordinator Epoch**:
The fencing identity of the current Coordinator session for one Goal.
_Avoid_: writer generation, Agent liveness

**Store Generation**:
One isolated generation of rebuildable GWO control-plane state.
_Avoid_: in-place lifecycle migration

**Shadow Mode**:
A read-only evaluation that compiles plans and proposed Admissions from real
state without lifecycle, Runtime, or integration mutation.
_Avoid_: canary execution, dual writer

**Canary Goal**:
The first bounded low-risk V8 Goal with several independent nodes used to
prove real parallelism, waiting, review, and serial Integration.
_Avoid_: single trivial task, permanent low-concurrency mode

## Legacy V6.1 language

**Dispatch**:
The V6.1 record that combines assignment and execution identity. V8 separates
it into Admission, Attempt, and Runtime Binding.
_Avoid_: V8 execution identity

**Hotset**:
The V6.1 file claim used as an execution conflict lock. V8 replaces it with
Write Scope plus explicit Exclusive Resources.
_Avoid_: V8 hard file lock

**Coordinator Loop**:
The V6.1 Agent-driven cycle that owns both semantic judgment and mechanical
progress. V8 replaces its mechanical liveness with Goal Driver and Kernel
Reconciliation.
_Avoid_: V8 Kernel Reconciliation

**Kernel Reconciliation**:
One deterministic convergence pass from recorded intent and authoritative
readback to due control-plane actions and one continuation directive.
_Avoid_: Coordinator reasoning, daemon
