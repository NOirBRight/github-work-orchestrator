---
status: amended by ADR-0036, ADR-0039, ADR-0041, and ADR-0043
---

# Require verified evidence before completion

A Worker may report only a Candidate reference. The report and its
notification are wake hints and do not complete the Work Run. RuntimeGateway
transports them without adopting their content. CandidateGate authoritatively
reads back the exact Candidate commit/tree and complete diff identity and
produces a private Candidate receipt; ExecutionKernel changes persisted state
only after persisting that receipt.

A Candidate is an Artifact, not Evidence or a Result. Independently observed
Evidence and the fixed acceptance and delivery conditions determine
completion. A code-producing Result exists only after the exact accepted
Candidate is integrated and target readback proves that integration. Objective
Evidence checks precede any Formal Review.

Result adoption across Plan Revisions requires the same Ticket identity,
unchanged contract digest, unchanged authority-subtree digest, and applicable
Candidate identity. Evidence that depends on the target branch must be
refreshed before the adopted Result contributes to completion or integration.

Typed Evidence uses one common envelope containing its kind, subject,
observer, observation time, source reference, payload, and content digest.
V8.0 defines six evidence kinds: `runtime`, `candidate`, `check`, `review`,
`integration`, and `decision`. Local commands and hosted CI are both `check`
Evidence with different sources. `candidate` Evidence is an independent
observation about a Candidate; the Candidate Artifact itself is not Evidence.
A Worker report is only a Candidate reference and wake hint, not
self-authenticating Evidence. ExecutionKernel, CandidateGate, RuntimeGateway,
GitHub/CI, or a durable human Decision must observe the supporting fact owned
by its boundary.

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
Candidate. Without that capability, a command mentioned in raw Worker output
is non-authoritative log text and another source such as hosted CI must supply
the Evidence.

A failed CandidateGate verification returns one consolidated repair request
while a Candidate submission remains. Unavailable CI or another missing
Evidence source yields a named Wait rather than a Candidate verdict. Runtime
loss alone cannot make the Ticket contract semantically failed.
