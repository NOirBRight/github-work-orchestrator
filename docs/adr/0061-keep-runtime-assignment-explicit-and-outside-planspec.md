---
status: accepted
amends: ADR-0037, ADR-0042, ADR-0044, ADR-0055, ADR-0059
---

# Keep Runtime assignment explicit and outside PlanSpec

Successor PlanSpec v3 separates Runtime assignment from semantic planning. Runtime assignment
uses these exact selectors:

- Campaign-scoped `coordinator`;
- Ticket-scoped `worker`;
- Ticket-scoped `recovery_worker`;
- Ticket-scoped `review_primary`;
- Ticket-scoped `review_strong`; and
- Ticket-scoped `specialist:<policy-id>`.

The `coordinator` selector resolves Campaign-start Coordinator override,
repository `coordinator` role mapping, then host-global `coordinator` role
mapping. Every Ticket-scoped selector resolves exact Campaign-start
`(ticket_key, role)` override, repository role mapping, then host-global role
mapping. There is no Ticket-wide shorthand and no Ticket override for
Coordinator.

Each mapping names one required primary Runtime Profile and at most one
optional availability fallback. Permanent missing or invalid required
configuration before identity returns `Blocked(RuntimeConfigurationInvalid)`
without fallback or transport retry. Different selectors and primary/fallback
positions may intentionally resolve to the same Profile.

Campaign-start overrides are persisted with the Campaign. For each stable
Runtime action, RuntimeGateway records selector, configuration source, resolved
Profile digest, and whether the optional fallback was selected. Retries,
resume, readback, and same-binding recovery reuse that assignment.

For the initial Planning Pass, RuntimeGateway resolves only the Campaign-scoped
`coordinator` selector during a mechanically read-only preflight of the exact
pre-Plan `CampaignPlanningSubject`. PlanControl obtains the bound preflight
receipt after its immutable source snapshot and before a Ticket claim or
semantic action; neither the preflight nor assignment persistence creates an
Agent, session, workspace, provider action, or capacity reservation. The
subsequent Artifact-backed planning receipt is opaque to PlanControl, so the
persisted source/Profile/fallback facts remain RuntimeGateway-private.

The preflight method accepts only that semantic subject. Host composition
places an optional exact Campaign-start assertion in `RuntimeConfiguration`
under `(repository, campaign_key, campaign_handle)`. Absence on an existing
Campaign reuses the durable configuration; a present assertion must match it.
The assertion types and raw selector/mapping vocabulary remain host-module
details rather than package exports.

That preflight record is an exact durable compare-and-set binding of the
Campaign Planning subject, Campaign-start overrides, and resolved Coordinator
configuration. Reusing its stable action with a changed source snapshot,
Policy Witness, protocol/request Artifact, override, or configuration fails
closed rather than silently selecting a fresh assignment. No Work Run can use
the Planning preflight operation.

The preflight receipt digest includes both the resolved assignment digest and
the digest of the complete Campaign-start override value, including Ticket
overrides that do not select the Coordinator. Campaign, preflight, override,
and assignment records have closed schemas. Every journal load recomputes the
override, assignment, and receipt identities; changed fields, unknown fields,
duplicate Ticket override keys, or a valid-looking replacement Profile fail as
`RuntimeStoreInvalid` before Adapter readback.
The Campaign record separately cross-binds each planning stable action to the
same subject and complete override digest. Load requires every preflight to
have exactly one matching Campaign link, preventing an edit to either record
from silently rebinding the other.

Preflights and Work Run actions do not own separate stable-action namespaces.
One shared journal reservation binds each stable action to the kind and digest
of its complete canonical subject in the same transaction that commits the
preflight or assignment. Exact replay is valid; cross-kind or changed-subject
reuse fails before Adapter or provider activity. A schema-version-1 journal
without that map rebuilds it from all preflight and action records and rejects
any conflicting legacy identity.

Availability fallback is permitted only before any Agent identity may exist
for the stable action. After identity, RuntimeGateway recovers the same
binding. A replacement Worker requires terminal-binding Evidence and uses the
already resolved `recovery_worker` assignment.

Work Run callers do not supply selector strings. `WorkRunSubject` carries the
closed semantic `WorkRunPurpose`: implementation, terminal-recovery
implementation, Formal Review, invalid Review payload retry, or specialist
review with one policy ID. RuntimeGateway alone maps those purposes
to `worker`, `recovery_worker`, `review_primary`, `review_strong`, and
`specialist:<policy-id>`. Raw strings and subclasses fail closed. The immutable
provider-neutral `RuntimeProfile` value lives in a neutral module shared by
the successor gateway and predecessor compatibility code, so RuntimeGateway
does not import the legacy runtime implementation.
Its nested feature JSON is defensively copied and recursively immutable while
retaining predecessor canonical bytes and digest. The immutable feature
objects and arrays use composition rather than `dict` or `list` subclasses, so
builtin base-class mutators, subclass construction, and retained aliases
cannot bypass the snapshot. `dict(profile.features)` remains compatible for
V2 readers, while Runtime identity uses an explicit plain-JSON canonical
projection. Host composition reconstructs and freezes every Profile, selector,
mapping, nested repository mapping, and Campaign assertion rather than
retaining caller-owned values. RuntimeGateway rechecks those exact values and
their registry-key digests on every lookup or resolution before Adapter or
provider activity. The tuple-backed Profile, selector, mapping, and override
values reject explicit initializer re-entry and object-attribute mutation.
RuntimeGateway additionally pins and rechecks the digest of the whole composed
configuration, so replacing even one exposed registry with another
individually valid mapping fails before Adapter activity.

The Gateway-owned host Artifact Store returns an Artifact reference only after
it has exclusively staged, flushed, file-synced, atomically replaced,
directory-synced where supported, and finally reread the exact digest and
bytes. A failed put returns no reference and cannot produce a durable semantic
completion receipt that names the failed target.

All Runtime JSON identities share one closed canonical domain: JSON `null`,
exact strings and booleans, integers, finite floats, arrays, and objects whose
keys are exact strings. Encoding disables non-finite numbers and Python key
coercion. Artifact, journal, and provider ingress reject duplicate object
names, `NaN`/infinities, invalid UTF-8, and byte representations that are not
canonical, translating failure at the owning boundary rather than leaking a
raw JSON exception. The same validator rejects active-reference cycles, values
beyond its fixed nesting depth, and integers beyond its explicit digit bound,
normalizing recursion and interpreter integer-limit failures.

A durably selected fallback remains selected even if the primary later
recovers. Cached availability is advisory; live provider, configuration, and
transport behavior follows the canonical
[`Runtime failure taxonomy`](../design/gwo-v8-lean-architecture.md#runtime-failure-taxonomy).
Issue #111 persists the primary assignment, optional fallback candidate, and
initial `fallback_selected=false` record. Issue #112 owns authoritative native
availability classification and the one-time pre-identity mutation to that
candidate; a transport failure never selects it by inference.

PlanControl may declare factual Runtime capabilities such as required tools or
execution features. It cannot infer difficulty, assign a model, rank profiles,
or convert a semantic opinion into a stronger Runtime. Provider, model,
reasoning setting, CLI, selector, Profile, configuration source, and fallback
never enter PlanSpec.

V8 therefore supports different models for different roles and explicit
different models for selected Tickets without adding an LLM router, risk
score, difficulty tier, evaluation system, label convention, or dependency on
the external `/to-tickets` skill emitting GWO-specific fields.
