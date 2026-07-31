---
status: amended by ADR-0061 and ADR-0062
amends: ADR-0018, ADR-0024, ADR-0026, ADR-0044, ADR-0051, ADR-0052, ADR-0053, ADR-0055, ADR-0057
---

# Make RuntimeGateway the only Runtime boundary

RuntimeGateway is the only external semantic Runtime boundary for successor
PlanSpec v3 Campaigns. Successor PlanControl, ExecutionKernel, and
CandidateGate exchange typed Runtime requests and receipts with it; they never
construct Codex, Claude Code, Paseo, shell, Agent, session, or provider
commands directly. The schema-version-2 Kernel and `runtime.py` adapters are
predecessor compatibility for existing V2 state until Issue #118 Cutover Guard;
they are not successor composition inputs and this ADR does not rewrite them.

RuntimeGateway accepts a closed materialization-subject union. Its pre-Plan
`CampaignPlanningSubject` binds repository, Campaign key and handle, expected
previous Plan Revision digest or `null`, immutable snapshot Artifact digest,
Policy Witness digest, planning protocol/request Artifact digest, and stable
action. Its other member is a Plan-Revision Work Run subject. Planning never
fabricates a Plan Revision. Before PlanControl claims a Ticket or requests a
semantic action, the gateway performs a mechanically read-only Coordinator
configuration preflight over the exact planning subject and returns a bound
opaque receipt. Preflight creates no Agent, session, workspace, provider
action, claim, or capacity reservation.

The Work Run subject carries a closed semantic purpose, not a selector string:
implementation, terminal-recovery implementation, Formal Review, invalid
Review payload retry, or specialist review with one policy ID. The
purpose-to-selector mapping remains private to RuntimeGateway. The immutable
provider-neutral `RuntimeProfile` value is defined in a neutral module rather
than imported from predecessor runtime implementation code.

The caller surface is deliberately small: planning preflight, typed subject
progress (which accepts a wake cursor), and a typed closed-union transition
request by stable action. Subject progress owns all prepare/observe and
start-or-resume choreography, including the readback-first recovery loop for
Campaign Planning. Event cursors produce advisory wake hints only inside that
progress call; no caller can use an event as state or obtain a raw provider
operation. PlanControl consumes only opaque preflight and Artifact-backed
planning receipts, never assignment, adapter, CLI, Profile, session, Runtime
Binding, Agent, or workspace facts.

Progress validates its authoritative observation before polling those hints.
An Adapter event poll selects at most one fair-scan candidate without first
changing durable state. It captures the scan cursor, ordered eligible-set
digest, and selected stable action, then derives and validates the complete
Prepared or Bound observation and captures the exact reconciled action-record
digest. Only one final CAS that still matches all four identities may publish
the scan step, wake digest, event, and terminal marker together. A malformed
observation, including a Bound observation that reports `prepared`, or
malformed authoritative-absence evidence changes none of those values or the
Bound Workspace history. Exact transport, authoritative-absence,
binding-missing, and materialization-pending failures may consume one scan
position so a stale action cannot starve the bounded scan; they publish no
state event.

The exact public operations are `planning_preflight(subject)`,
`progress(subject, preflight=None, wake_cursor=None)`, and
`transition(stable_action_id, transition)`. Campaign-start configuration
assertions enter through host-composed `RuntimeConfiguration`, keyed by exact
Campaign identity, rather than by widening the semantic preflight signature.

Planning preflights and materialized actions share one global stable-action
identity reservation inside the same Gateway journal transaction. The
reservation binds subject kind plus the digest of the complete canonical
subject. Exact replay is allowed; cross-kind or changed-subject reuse fails
before Adapter readback, preparation, command, or any provider effect.
Schema-version-1 journals without the shared map rebuild it from every
preflight and action while holding the journal lock; any conflict within or
between those collections makes the store invalid rather than selecting one.
Only a completely validated rebuild is atomically published as schema version
2; a failed rebuild leaves the version-1 bytes untouched.
Campaign, preflight, override, and assignment records use closed schemas.
Every load recomputes the complete Campaign-override digest, assignment
digest, and preflight receipt digest. The opaque receipt binds the whole
Campaign-start override value, not only the Coordinator mapping it selected.
The Campaign record also binds each planning stable action to that exact
subject and override digest. Journal load requires a one-to-one match between
those Campaign links and preflight records, so editing either record alone
cannot produce a valid-looking replacement identity.

Only the host configuration assembler reads immutable Runtime Profile
provider/model facts and supplies the composed `RuntimeConfiguration`; that
host-only composition data is not a PlanSpec or semantic-workflow input.
`RuntimeProfile` recursively snapshots feature objects and arrays into
composition-only immutable views, never mutable `dict`/`list` subclasses,
without changing their canonical bytes or digest. Its explicit plain-JSON
projection owns identity and `dict(profile.features)` remains compatible with
V2 readers. `RuntimeConfiguration` reconstructs and freezes every Profile,
selector, mapping, nested repository mapping, and Campaign assertion rather
than retaining caller-owned values. Every lookup or resolution revalidates the
exact value and registry-key digest before an Adapter operation or provider
effect, so drift or alias mutation fails as permanent configuration
invalidity. The tuple-backed public Runtime values also reject explicit
initializer re-entry and object-attribute writes or deletion. RuntimeGateway
pins the canonical digest of the complete composed configuration and rechecks
it at each public operation and resolution boundary.
PlanControl, ExecutionKernel, CandidateGate, and other semantic workflow
callers receive neither those facts nor a vendor command surface. Host
composition uses provider-neutral `build_runtime_gateway` and
`RuntimeRepositoryContext`; its default production transport is created inside
the module. The factory accepts the composed configuration but has no direct
provider, CLI command, transport, raw-adapter, or binding parameter.

Every production adapter and the deterministic in-memory adapter implements
the same module-private provider-neutral interface. Its request, receipt,
observation, event, permission, and failure types are private implementation
types; they are neither package exports nor caller contracts:

```text
prepare(RuntimeActionSpec) -> PrepareReceipt | RuntimeFailure
observe(stable_action_id) -> PreparedObservation | BoundObservation | RuntimeFailure
command(stable_action_id, RuntimeTransition) -> CommandReceipt | RuntimeFailure
events(after_cursor) -> RuntimeEventPage
```

`_reconcile_observation` and its sealed `ObservationRead` are adapter-private
implementation details, not Protocol operations. They bind the requested and
selected action, complete subject/Profile/Prompt/input/spec and
Workspace/binding identity, one exact observation or failure, Artifact
evidence, and a causal record token at the reconciliation linearization point.
The adapter validates that sealed read before its public private-seam
`observe` projection returns only a Prepared or Bound observation (or a typed
failure). RuntimeGateway then independently validates that projection against
its durable subject and identity and proves every governed Artifact, including
completed output; it never trusts an Adapter's Artifact acceptance as semantic
evidence.

One pure total validator owns exact sealed-read type and field schemas,
including nested permission/completion evidence, closed failure
classification, and causal-token consistency. Its closed kinds are
`prepared`, `bound`, `authoritative_absence`, `fairness_advance`, `failure`,
and `invalid`. The protocol alone assigns `fairness_advance` to an exact
transport, same-action binding-missing, or same-action
materialization-pending failure; event callers do not inspect raw failure
codes. Prepare/command receipts, failures, and event pages likewise require
exact classes and closed fields; subclasses, missing/extra fields, and
cross-action values are protocol invalid. Every scalar is checked as its
exact built-in type before equality, hashing, membership, or conversion, and
the classifiers are total for hostile provider objects.
Every populated failure action ID must equal the selected action, including
identityless reads. Action-bound absence, binding-missing,
materialization-pending, acknowledgement-loss, and effect-ambiguity codes
require that ID. Only same-action `RUNTIME_PREPARE_ACK_LOST` and
`RUNTIME_EFFECT_AMBIGUOUS` permit prepare follow-up readback recovery;
permanent, configuration, protocol, unknown, and transport failures retain
their original result.

`prepare` may stage only an action-owned Workspace and every governed
Artifact-backed input, including the Prompt;
it cannot create an Agent, session, Runtime Binding, or semantic execution.
Only an authoritative typed absence result permits it. Transport failure,
malformed native return, ambiguity, or any other failure stops progression and
cannot be treated as absence. A Prepared observation proves the exact subject,
Profile, authority, Workspace, Prompt, and boolean fence state while
Agent/session/binding are explicitly absent. Prepared provenance additionally
proves that the fixed action result path is absent before prepare commits,
before every Prepared readback, and before the `start` claim or effect. A
planted result is rejected even when its JSON and semantic binding are valid.
`start` is allowed only from that state. It may atomically create and start an
Agent, but acknowledgement loss
must immediately read back the stable-action label and accept only an exact
Bound observation. A Bound observation proves the complete binding and Prompt
receipt—including repository, Campaign, Plan Revision, Work Run, stable
action, selected Profile, Agent, session, workspace, Runtime Binding,
lifecycle, normalized exact permission requests, and boolean fence state. Its
lifecycle is closed to `running`, `parked`, `completed`, or `retired`;
`prepared` belongs only to the unbound Prepared observation.
`resume` requires an exact parked Bound observation. The other allowed
commands are `park`, `interrupt`, `PermissionResponse(request_id, allow|deny)`,
`fence`, and `retire`; a permission response names exactly one request ID and
can never use an open-ended provider allow. Production and in-memory
implementations pass the same contract suite; Profile and permission policy
remain in RuntimeGateway.
`PermissionResponse` requires exact non-empty string fields before command
gating or mutation. `None` is the sole event-cursor origin. Concrete cursors
are canonical ASCII `[1-9][0-9]{0,18}` in `1..2^63-1`; zero and
leading-zero aliases, booleans, integers, subclasses, Unicode digits,
overflows, and coercible objects fail without advancing the scan or
publishing a wake. Returned events are strictly newer than the request, a
non-empty page returns exactly its last event cursor, and an empty page echoes
the request. Paseo accepts only a bounded consecutive ring of at most 64
events with its exact next counter; it never truncates or normalizes corrupt
durable input. After publishing cursor `2^63-1`, a later required publication
returns `RUNTIME_EVENT_CURSOR_EXHAUSTED` with no scan, event, or terminal-state
mutation.

Every accepted command receipt, including acknowledgement-loss recovery, must
be followed by authoritative Bound readback proving its own effect: `start`
and `resume` are running or completed, `park` and `interrupt` are parked,
`fence` is exactly true, `retire` is retired, and a `PermissionResponse` first
has an exact same-decision provider receipt and then removes its exact request.
Absence without that receipt is ambiguous and cannot recover an acknowledged
permission decision. Paseo verifies the native receipt name against the
provider-namespaced operation, then retains the normalized operation ID in
`name`; ingestion, restart, and readback all require
`receipt.name == request.operation_id`. A fenced Bound action cannot resume. Production
wake cursors persist a closed snapshot of stable action, subject digest,
lifecycle, exact pending permissions, fence state, and output digest; they
remain advisory and never replace authoritative readback. A successful
private-seam `observe` opens one ephemeral, adapter-private, one-shot command
gate from its sealed read. `command` accepts no caller-supplied token and
consumes that gate before it can dispatch. A command without a fresh observe,
after an event-only read, after an earlier consumed command, or after adapter
restart therefore has zero effect. Both production and in-memory Adapters
revalidate the complete identity and selected-record digest before dispatch.
A concurrent rebind, retire, or reconciliation invalidates a stale gate before
provider state can change. Paseo repeats the check inside the durable
effect-claim transaction itself; in-memory validates the complete current
sealed read inside the same re-entrant lock as mutation.

Each wake poll captures one fair-scan selection and performs at most one
detached action readback before changing durable event state. After a valid
complete observation, or a protocol-owned `authoritative_absence` or
`fairness_advance` verdict,
one final CAS rechecks the captured cursor, eligible set, selected action, and
post-readback action record before advancing the scan position and publishing
any wake state. A CAS miss advances and publishes nothing; a later poll
reselects and re-reads. An allowed failed readback is an isolated missed hint,
not an event-stream failure or a reason to retry the same action immediately.
The event-page protocol owns its separate `page`, `transient_failure`,
`failure`, and `invalid` union, so Gateway also handles transport unavailability
without reading a failure code.
Terminal state is stored and pageable once; that action is then omitted from
future scans until a state-changing fence or retire claim atomically re-arms
it. Proven non-dispatch restores the previous terminal marker.

RuntimeGateway hides:

- deterministic Runtime Profile resolution from host-global configuration and
  repository override;
- internal Adapters for each supported execution model and CLI;
- Worker and Coordinator materialization, resume, observation, checkpoint,
  fencing, and retirement;
- Review and specialist Internal Subagent launch, including selection of a CLI
  different from the parent Worker;
- stable Agent, session, action, Prompt, and Workspace identity readback;
- bounded, digest-verified Artifact-backed Prompt and output transport without
  silent truncation, missing input, or canonical-binding drift;
- structured permission request readback and exact authorization enforcement;
- the durable primary Profile plus optional fallback candidate seam, and
  post-identity same-binding recovery;
  and
- bounded command duration, transport retry, and typed Runtime errors.

Provider Adapters remain replaceable implementation plugins behind this
boundary. They advertise truthful capabilities, but no semantic workflow
caller branches on a provider name. The same frozen Ticket contract,
authority-subtree digest, and action key can therefore be rendered for Codex,
Claude Code, Paseo, or another compatible execution model without changing
PlanSpec or ExecutionKernel.

Complete Ticket contracts, the planning protocol/request, Review Subjects, and
Review Finding context use bounded Artifact references or files. The
Gateway-owned Artifact Store reads every governed input and completed output
by digest, bounds byte length, requires canonical JSON where the protocol is
JSON, and verifies the exact subject, stable action, authority, and payload
binding before the provider sees it or PlanControl consumes it. Paseo's short
bootstrap references the verified Workspace Prompt file and may use an output
schema, but a completed receipt is authoritative only when the Agent atomically
writes the action-owned result Artifact. Logs and events are wake hints, never
output. No provider adapter may put complete payloads in a short command
argument: it must account for underlying CLI and Paseo command-length limits
and fail closed on unavailable bounded transport. An ambiguous prepare
acknowledgement, callback, or restart first observes the stable action; it
never creates a second Agent, workspace, Prompt, or Planning Pass.
The completed-output proof is one shared operation with a closed
`gwo.runtime.output.v1` object schema. It verifies bounded canonical bytes,
digest, schema version, subject, stable action, and authority, and rejects
missing, corrupt, cross-action, or extra-field output before reconciliation,
Gateway journal update, or receipt emission.

The shared canonical layer accepts only JSON `null`, exact strings and
booleans, integers, finite floats, arrays, and objects with exact string keys.
It disables non-finite encoding and Python key coercion. GWO-owned Artifact,
journal, schema, and authoritative-output loaders require exact canonical
bytes. A native Paseo CLI stdout or stderr JSON envelope is bounded external
transport rather than a GWO Artifact or authoritative identity: strict UTF-8
and JSON decoding creates a fresh closed JSON value before the private provider
seam, and the raw vendor bytes never cross it. That ingress still rejects
duplicate names, `NaN`/infinities, invalid UTF-8, non-domain values, excess
depth, and excessive integers, but accepts ordinary pretty or unsorted JSON.
Any later journal, Artifact, schema, or identity operation creates and verifies
its own canonical bytes from the parsed value. Non-empty whitespace-only
success output is malformed. Exact empty stdout is allowed only for a concrete
mutating command whose effect has a required authoritative readback; identity,
list, create-receipt, and permission-receipt paths require their declared
response shapes. Strings and object keys must contain Unicode scalar values:
lone high or low surrogate code points fail at every shared ingress, while
supplementary characters and valid JSON surrogate pairs decoded to one scalar
remain accepted.

Putting bytes into that host Artifact Store uses a unique exclusively-created
temporary file, complete write plus flush and file `fsync`, atomic replacement,
directory `fsync` where the platform supports it, and bounded final readback
that must match both digest and exact bytes. An existing digest target is
verified before adoption, and concurrent writers of the same bytes are
idempotent. Replacement or final-verification failure returns no
`ArtifactRef`; cleanup may remove only the temporary file created by that
attempt. These host-store durability rules are distinct from the Runtime
Workspace helper's explicitly non-racing link/reparse threat model.

For the current Paseo transport, stable-action labels are proved by
`ls --global --label` before `inspect`; `inspect` is compared exactly on Agent
ID, provider, model, thinking, mode, current working directory, and status.
All documented Paseo Agent and Workspace compatibility aliases pass through
one exact decoder: absent values remain missing, equal populated aliases are
accepted, and conflicting populated aliases fail as ambiguous identity rather
than choosing the first spelling. This applies to inspect, Agent-list,
Workspace-list, and Workspace-create receipt identity and path readback.
The inspected working directory then joins one exact recorded Workspace ID,
name, and worktree isolation. Workspace-registry selection is target-scoped:
only the action-owned worktree slug and its exact durable Workspace ID/path
may participate, while unrelated registry rows cannot create ambiguity.
Conflicting target candidates fail closed, and untrusted durable network paths
are rejected without resolution. Its resolved path must differ from the
resolved source checkout; private bounded Git readback then proves their shared
repository common directory. The first durable Workspace intent resolves and
pins one exact base commit, creates from that commit, and a Prepared Workspace
proves its `HEAD` still equals that pinned commit without re-resolving the
mutable base ref. Pinned equality applies only while Prepared. An exact Bound
prepare replay re-proves its Agent/label binding, repository common directory,
Workspace ownership marker and staged Artifacts, then requires `HEAD` to remain
on a monotonic descendant chain from the pinned base and the last observed
Bound head. Worker descendant commits are valid; unrelated history or rewind
is ambiguous. Paseo exposes no provider session ID,
so the bound session reference is explicitly adapter-derived as
`paseo-agent:<agent-id>`. Non-empty Profile features fail closed until the CLI
can both set and read them.

The pinned base commit must not contain a casefold-equivalent `.gwo` top-level
entry, including `.GWO` or a tracked link. The durable Workspace intent also
contains an unpredictable
ownership nonce and layout version before provider creation. Once exact
Workspace registry and Git identity are proved, the adapter creates or
crash-recovers a nonce-bound marker and the fixed
`runtime-artifacts`, `runtime-schemas`, and `runtime-results` directories.
Recorded paths are evidence only: restart derives each artifact, schema,
result, and resume path again and requires exact equality. Before every
governed create, read, or replace it rejects links, Windows reparse points,
non-directory parents, non-regular or multiply linked leaves, and resolved
paths outside the verified Workspace. Atomic writes use unique exclusive
temporary files, flush before replacement, and verify the final bytes.
Ownership-marker creation uses one deterministic nonce-owned temporary name.
Restart removes or rebuilds that orphan only after proving containment,
regular-file type, no reparse point, and a single link.
This protects the stated non-racing link/reparse threat. It intentionally does
not claim descriptor-grade protection against a local attacker racing between
portable path checks and the subsequent open or replacement.

An unrecorded Workspace may require read-only Agent or Workspace registry
discovery before its local path is known. Those readbacks are not provider
effects. Once the path is known, unsafe local state fails closed before any
Workspace create, run, send, permit, stop, label update, or archive effect.

Before the provider may create a Workspace, start an Agent, resume an Agent,
answer a permission, park, fence, or retire an Agent, the adapter validates all
local files and complete provider arguments, then durably records the exact
pending effect. A missing label or
Workspace after an acknowledgement loss remains `RuntimeMaterializationPending`;
it cannot authorize a second create, run, send, permit, stop, update, or
archive. Only provider-process creation failure proves non-dispatch and permits
an exact CAS restoration of the claim. For Workspace create, that proof first
restores `create_pending` to the complete `recorded` intent before any registry
readback; an independently failing registry cannot strand the action pending.
Timeout, bounded-output overflow,
malformed protocol, native error, and receipt-verification failure occur after
the dispatch boundary and retain ambiguity evidence. Workspace adoption requires
one exact slug/isolation/cwd readback and repository identity proof. The entire
Workspace registry is safely decoded before filtering, but only action-target
candidates for the durable worktree slug, Workspace ID, and path participate
in uniqueness or ambiguity. Unrelated duplicate rows therefore do not block
adoption; duplicate or conflicting action-target candidates fail closed. Fence
failure likewise retains its unique claim and records quiescence without
guessing that the label effect did or did not occur.

Verified action-bound output is stronger than every non-retired provider
lifecycle, including idle, running, and busy, or stale park/resume bookkeeping.
Adopting it atomically clears all mutually exclusive
park/resume/stop flags and returns `completed`; a still-running provider with
output is therefore completed. Completed and retired bindings never send a
new permission response or regress lifecycle. An exact same-request,
same-decision replay is accepted only when the completed effect evidence is
already durable, both digests recompute, the stable action, subject, and
binding match, and the request is absent from outstanding permissions; every
other terminal permission request fails as unknown.
The in-memory adapter follows the same ordering: `start` first establishes the
binding and `running` lifecycle, then publishes output. Publication failure
therefore remains one recoverable Bound/running action; later observation
retries completion without creating another Agent. The same rule applies
after the final permission response.

Issue #111's permission descriptor is internal to RuntimeGateway. It joins
the provider's exact request identity with canonical, bounded,
provider-namespaced operation/resource digests and preserves the Runtime
Binding and authority-subtree digest; it does not infer authority from a
provider name or description and does not auto-allow. Issue #112 owns the
policy decision: it may automatically allow an individual exact request only
when both exact canonical identifiers are covered by the frozen Authority
Grant and referenced Policy Witness; otherwise it returns `PermissionRequired`
to ExecutionKernel. No layer may use an open-ended `--all` grant.

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

Issue #111 pins the primary Profile, optional fallback candidate, and durable
`fallback_selected=false` initial assignment, but does not classify a native
provider result as `unavailable` or `capacity_exhausted`. Issue #112 owns that
authoritative availability classification, the one-time pre-identity update to
the pinned fallback, and its bounded retry episode; #111's adapter seam must
therefore fail closed rather than infer that selection from a transport error.

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
