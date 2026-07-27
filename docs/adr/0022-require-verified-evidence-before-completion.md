---
status: amended by ADR-0036, ADR-0039, ADR-0041, and ADR-0043
---

# Require verified evidence before completion

A Work Run submitting a typed Result Claim has not completed. Independently
observed Evidence and the fixed acceptance and delivery conditions determine
completion. Worker self-report never collapses those states. Artifacts are
produced objects; Evidence is independently observed. Objective Evidence
checks precede any Formal Review.

Result adoption across Plan Revisions requires the same Ticket identity,
unchanged contract digest, unchanged authority-subtree digest, and applicable
Candidate identity. Evidence that depends on the target branch must be
refreshed before the adopted Result contributes to completion or integration.

Typed Evidence uses one common envelope containing its kind, subject,
observer, observation time, source reference, payload, and content digest.
V8.0 defines six evidence kinds: `runtime`, `candidate`, `check`, `review`,
`integration`, and `decision`. Local commands and hosted CI are both `check`
Evidence with different sources. An Agent's submission is a Result Claim and
Artifact reference, not self-authenticating Evidence; ExecutionKernel,
RuntimeGateway, GitHub/CI, or a durable human decision must observe the
supporting fact.

The Ticket contract, Authority Grants, Policy Witness, and Assurance
Requirement determine required Evidence. An Agent, Skill, or Campaign Planning
Pass cannot remove it. Evidence binds to the relevant Candidate, base,
acceptance, authority-subtree, and Policy Witness digests. A changed base
expires only base-sensitive Evidence, while a changed Candidate or acceptance
digest invalidates the Evidence and Review that depended on it.

GitHub stores a compact Evidence Manifest containing required digests and
durable source references. Large logs and Artifacts remain in CI, artifact, or
runtime storage; an acceptance-critical reference cannot point only to an
ephemeral local file.

Check Evidence is validated rather than repeated by default. A reusable check
binds its exact Candidate SHA or tree digest, canonical command or hosted check
definition, execution-environment identity, timestamps, outcome, observer, and
durable log digest or reference. RuntimeGateway may directly observe a local
command; GitHub readback observes a hosted check. Assurance Policy determines
the required check set and acceptable sources, not the number of executions of
the same valid check. Rerun occurs only when the subject changed, provenance or
readback is missing, or the check or environment does not meet the contract.

RuntimeGateway may produce local Check Evidence only when it directly
observes the executed tool or process, its outcome, and its binding to the
Candidate. Without that capability, an Agent's reported command remains a
Result Claim and another source such as hosted CI must supply the Evidence.

A failed CandidateGate verification returns one consolidated repair request
while a Candidate submission remains. Unavailable CI or another missing
Evidence source yields a named Wait rather than a Candidate verdict. Runtime
loss alone cannot make the Ticket contract semantically failed.
