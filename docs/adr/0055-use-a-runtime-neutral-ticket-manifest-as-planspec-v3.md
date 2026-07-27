---
status: amended by ADR-0056 and ADR-0059
supersedes: ADR-0020, ADR-0025, ADR-0030
amends: ADR-0038, ADR-0039, ADR-0040, ADR-0042, ADR-0043, ADR-0050
---

# Use a Runtime-neutral Ticket Manifest as PlanSpec v3

V8 is a concurrent GitHub Ticket execution engine, not a general Agent DAG
engine. Upstream `/to-tickets` owns semantic decomposition into independently
executable Tickets and canonical blocker relationships. PlanSpec v3 freezes
that accepted handoff as one immutable, Runtime-neutral Ticket Manifest.

The canonical top level is:

```yaml
schema_version: 3
repository: owner/repo
target_branch: main
campaign:
  key: campaign-key
  source: {ref: source-ref, digest: source-digest}
  contract: optional-frozen-parent-contract
policy: {ref: policy-ref, digest: policy-digest}
work:
  - key: issue:101
    source: {ref: issue-ref, digest: ticket-contract-digest}
    contract: complete-frozen-ticket-contract
    depends_on: []
    exclusive_resources: []
    capabilities: [git, local_check]
```

There is exactly one WorkSpec per selected Ticket. `depends_on` contains
canonical Ticket blocker identities plus Compiler-accepted dependency
additions from the required Campaign Planning Pass; their provenance remains
in the Compilation Record. `exclusive_resources` contains only resources whose
concurrent use is genuinely unsafe. `capabilities` expresses Runtime needs
without selecting a provider or model. The complete frozen Ticket contract,
target branch, and readable policy witness make an old Plan Revision
deterministic after mutable tracker or configuration state changes.

PlanSpec v3 has no generic `nodes`, node kinds, synthetic `edges`, Goal list,
`parent_plan_digest`, proposed `file_changes`, path/content patch, risk,
difficulty, recovery policy, Check list, Review requirement, Decision Node, or
Integration Node. Activation metadata, not Plan content, records the expected
previous Plan for compare-and-swap.

Review, human/semantic Decisions, permission waits, Attempts, Recovery,
Candidate assurance, Checks, Batch construction, hosted CI, Integration, and
cleanup are fixed Kernel lifecycle and runtime Artifacts. They are not
Planner-authored graph nodes. Exact Candidate readback produces the actual
Diff Manifest; the frozen policy witness then derives Assurance, Checks,
protected surfaces, and Interaction Keys. V8.0 Worker authority defaults to
the isolated repository workspace with uncontrolled external effects denied;
an authority expansion requires a Decision and replacement Plan Revision.

Provider, model, reasoning, CLI, Prompt rendering, Agent, session, workspace,
capacity, permission policy, timeout, and fallback selection are forbidden in
PlanSpec. RuntimeGateway renders the same Work Contract and authority digest
for Codex, Claude Code, Paseo, or another compatible internal Adapter.
Tickets, blockers, Candidate SHAs, and typed Evidence remain portable; a live
Agent session does not.

`/implement-gwo` snapshots the complete selected set once. PlanControl obtains
one typed Plan Intent from a Campaign-level Coordinator Planning Pass, then
performs one deterministic compilation, publication, and Activation. After
Activation, the Kernel fills the dependency-eligible frontier without
Coordinator scheduling. Exception-path Coordination may propose a successor
Ticket Manifest or changed authority, but only the deterministic Compiler can
produce PlanSpec bytes.

This intentionally gives up arbitrary internal Agent workflows. Work that
needs multiple semantic steps must be decomposed into Tickets and blockers
upstream. In return, normal execution no longer asks an LLM to reproduce
decomposition, predict implementation files, or author boilerplate lifecycle
nodes.

PlanSpec v2 records are never reinterpreted as v3. New V8 Campaigns write only
v3. Cutover requires active v2 work to finish under its original decoder or be
quiescent; v2 Runtime or Review identity is not silently adopted into v3.
