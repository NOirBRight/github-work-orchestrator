---
status: superseded by ADR-0034
---

# Compile V7 DAGs before V8 activation

V8 may accept a V7 DAG v1 document as migration input, but never executes that
document directly. It must compile successfully into PlanSpec v2 and pass the
same publication and activation gates as a native V8 plan, preventing a legacy
format from bypassing new invariants.

The compiler does not copy V7's live capacity snapshot into immutable
PlanSpec. V7 concurrency limits are reconciled with repository Runtime Policy,
current `global_active_agents` and group `active` counters are discarded, and
the resulting Plan Nodes retain only their resource claims. Live occupancy is
read again by the Kernel at Admission.

V7 `github_dependencies`, `contract_dependencies`, and `edges` are compiler
inputs rather than three independently authoritative execution graphs. The
compiler cross-checks their meaning, rejects unresolved disagreement, and
emits one typed edge collection in PlanSpec v2. The compilation record retains
source references and digests so the origin of each compiled relationship can
be audited without scheduling from stale duplicate maps.

The compiler also does not translate a V7 file Hotset directly into a V8 hard
admission lock. It becomes a Write Scope and overlap-risk hint. Only an
explicitly non-shareable resource or policy declaration becomes an Exclusive
Resource, preventing legacy conservative locking from serializing otherwise
isolated worktrees.

Before cutover, the same compilation path runs in shadow mode against real
repository state and produces proposed Admissions without lifecycle writes or
Runtime creation.
