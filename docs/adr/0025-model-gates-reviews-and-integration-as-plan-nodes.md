---
status: superseded by ADR-0055
---

# Model gates, reviews, and integration as Plan Nodes

Decision Gates are non-Agent Plan Nodes resolved by durable GitHub decisions.
Reviews are Plan Nodes bound to an exact candidate Artifact and acceptance
digest. V8 has no permanent Spec or Quality Reviewer identities: low risk uses
Coordinator inline review, standard risk runs one transient Code Review whose
Skill produces independent Standards and Spec axes, and strict risk adds the
specialist or human review required by the concrete risk. Integration is a
Kernel/Coordinator-only Plan Node. Cleanup remains a Kernel resource-lifecycle
responsibility rather than planner-controlled work. Integration retains one
repository lease and serial target-branch updates even when implementation and
verification run concurrently.

Independent implementation, review, hosted-check, and verification work may be
admitted concurrently. The Integration Lease serializes only target-branch
mutation; it is not a repository-wide barrier that waits for unrelated work.

Review remains bound to its exact candidate and acceptance digests. Reusing a
Result after the target branch advances requires refreshing base-sensitive
Evidence; if rebase, merge, or conflict resolution changes the candidate diff
digest, the affected review cannot be reused and must run again.
