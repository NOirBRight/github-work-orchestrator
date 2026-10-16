---
status: superseded by ADR-0055
---

# Define the minimal PlanSpec v2 contract

PlanSpec v2 is the self-contained immutable semantic authorization for one Plan
Revision. It records what may be done and what proves completion, while
excluding execution facts such as Attempt state, concrete Agent, provider,
model, session, workspace, live capacity, token use, and elapsed time.

Its canonical top level is limited to `schema_version`, `repository`,
`parent_plan_digest`, `goals`, `work_items`, `nodes`, and `edges`. Goal and Work
Item entries carry stable identities plus the normalized objective, acceptance,
or outcome-contract snapshot used by this revision and its semantic digest.
This does not move ownership of those durable entities into PlanSpec; it makes
an old revision readable after its GitHub source changes without introducing
separate Goal Revision or Work Item Revision entities.

A Plan Node records its stable Node Key, kind, Goal and Work Item relationship,
inputs and dependencies, output contract and required Evidence, Effect
Contract and resource claims, Runtime Requirements, difficulty, risk, and recovery policy,
and optional Skill Reference. It never records a concrete execution binding.
V8.0 node kinds are `work`, `review`, `decision`, and `integration`. Planning is
the process that proposes the plan, checks produce Evidence, Wait Conditions
are runtime state, and cleanup is a Kernel resource responsibility. A
standalone verification activity may still be represented by a generic `work`
node when it is itself authorized work.

The Plan Revision digest covers versioned canonical serialization of the full
PlanSpec, including `parent_plan_digest`, and excludes publication metadata,
timestamps, authorship, URLs, activation state, and all runtime state. A Plan
Node's separate contract digest exists only for unchanged-contract Result
Adoption and is not a second Plan Revision identity.

PlanSpec contains one typed edge collection produced by the compiler. Runtime
policy, current resource occupancy, compilation provenance, GitHub publication,
and activation records remain outside this canonical semantic document.
