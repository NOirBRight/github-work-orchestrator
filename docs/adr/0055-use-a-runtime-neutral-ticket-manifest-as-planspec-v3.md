---
status: amended by ADR-0056, ADR-0059, ADR-0061, and ADR-0062
supersedes: ADR-0020, ADR-0023, ADR-0025, ADR-0030
amends: ADR-0017, ADR-0035, ADR-0038, ADR-0039, ADR-0040, ADR-0042, ADR-0043, ADR-0050
---

# Use a Runtime-neutral Ticket Manifest as PlanSpec v3

V8 is a concurrent GitHub Ticket execution engine, not a general Agent DAG
engine. Upstream `/to-tickets` owns semantic decomposition into independently
executable Tickets and canonical blocker relationships. PlanSpec v3 freezes
the selected Ticket set as one immutable, Runtime-neutral Ticket Manifest.

The canonical PlanSpec contains repository and target identity, Campaign
identity and source, a frozen Policy Witness, and one work entry per selected
Ticket. Each work entry contains its complete Ticket contract, dependencies,
genuine Exclusive Resources, factual Runtime capability requirements, and
frozen authority:

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
  - key: issue:101
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

The Campaign Authority Grant permits only read-only Coordinator planning and
Decision scope. Every work entry contains isolated-workspace Authority Grants
for `worker` and `recovery_worker` and read-only grants for Review Internal
Subagents. Operation and resource identifiers are versioned repository-policy
identifiers, not provider permission strings. PlanControl compiles the grants
deterministically from the Policy Witness; neither semantic planning nor
Campaign-start options can add authority.

The canonical PlanSpec digest is the authority root. The relevant authority
subtree digest is persisted in Work Run admission, Prompt acceptance, Runtime
Binding, Candidate receipt, Review Evidence, and accepted-Candidate receipt.
The frozen Ticket contract, target branch, and Policy Witness keep an old Plan
Revision deterministic after mutable tracker or configuration state changes.

PlanSpec v3 has no generic nodes or edges, lifecycle actions, predicted paths,
check or Review instructions, recovery ladder, risk, difficulty, model,
provider, CLI, Runtime selector or binding, capacity, timeout, permission
decision, or integration instruction. Review, Decisions, permission waits,
bindings, recovery, Candidate assurance, Checks, Batch construction, hosted
CI, integration, and cleanup are fixed module behavior. Deterministic
PlanControl and BatchIntegrator service authority remains repository policy,
not semantic Runtime authority.

Any new or broader operation or resource, or a changed authority root, requires
an explicitly recorded human Decision, deterministic recompilation, and a
successor Plan Revision. A semantic Coordinator Decision can never expand
authority. Plan activation compare-and-swaps `(repository, campaign_key)`
against the exact expected previous revision digest, null initially. One
Campaign has one active revision; disjoint Campaigns may coexist. The stable
Campaign handle does not change across successor revisions.

Every Activation Receipt records repository, Campaign key, activated revision
digest, expected previous revision digest, and repository writer generation.
The repository-global writer generation prevents simultaneous production
writers. The repository-global Integration Lease serializes target mutation;
neither fence changes the Campaign scope of a Plan Revision or Integration
Batch.

PlanControl obtains one typed planning output from the Campaign Planning Pass,
then performs deterministic compilation, publication, activation, and
readback. The planning output and compilation record are private to
PlanControl. Only canonical PlanSpec bytes and durable receipts cross the
module boundary.

PlanSpec v2 records are never projected or reinterpreted as v3. Before cutover,
active v2 work finishes under its original decoder or is authoritatively
quiescent and available only for read-only audit. V8 never resumes, interprets,
or writes v2 and never adopts v2 Runtime or Review identity into v3. The
complete fence is defined by
[`Cutover`](../design/gwo-v8-lean-architecture.md#cutover).
