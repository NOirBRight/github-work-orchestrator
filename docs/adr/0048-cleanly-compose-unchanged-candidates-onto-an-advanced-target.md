---
status: amended by ADR-0049 and ADR-0060
amends: ADR-0036, ADR-0040, ADR-0041, ADR-0047
---

# Cleanly compose unchanged Candidates onto an advanced target

V8 retains immediate micro-batching: when the Integration Lease is free,
BatchIntegrator freezes up to the configured number of oldest compatible
Candidates from one Campaign eligible at that moment. It does not wait for
running Work Runs, use a timer, or predict future completion merely to enlarge
a Batch.

An unchanged Candidate does not become semantically stale merely because
another Batch advanced the target branch. BatchIntegrator may use Clean Base
Advance when the Candidate's original base remains an ancestor of the exact
current target, Candidate identity and Evidence are unchanged, the target delta
shares no protected Interaction Key with the Candidate, and Git composes the
histories without manual resolution.

For each proposed member, BatchIntegrator computes and retains the original
[`PatchIdentityV1`](../design/gwo-v8-lean-architecture.md#patchidentityv1-and-clean-base-advance)
over `(original base, Candidate tree)`. It then applies that member alone to the
exact advanced target in an isolated tree, recomputes
`PatchIdentityV1(advanced target, advanced member tree)`, and requires exact
equality before any multi-member composition. It never compares a member with
the whole Batch, a cumulative member tree, or a final Batch tree.

`PatchIdentityV1` is the versioned SHA-256 identity of a canonical,
provider-independent Git-tree delta. It uses the repository object format and
sorted length-prefixed entries containing old/new paths, change kind, old/new
modes, and exact old/new blob or gitlink object IDs. Rename and copy detection
is disabled; those changes are delete plus add. Binary content, symlinks,
executable modes, and gitlinks retain exact Git identities. Missing objects,
path or case ambiguity, merge ambiguity, or any non-canonical tree observation
fails closed. Gitlinks are protected and require a Singleton Batch.

Only after every member passes that proof may BatchIntegrator compose them.
The resulting exact Batch SHA must pass its repository-equivalent local suite
and hosted CI.

Clean Base Advance reuses the original Candidate and Review Subject; it does
not pretend that a different Candidate SHA was reviewed. Batch Evidence binds
the algorithm version, each member's original base and Candidate tree, original
patch digest, exact advanced target and isolated advanced-member tree,
recomputed digest, final composed Batch SHA, and the local and hosted Checks
that observed that composition. Review Evidence remains attached to the
original Candidate and is never reusable across Candidate SHAs.

If a proven, attributable ancestry, identity, Interaction Key,
clean-composition, or exact-Batch Check condition requires changed code, only
the affected work forms a new Candidate. A changed Candidate creates a new
Review Subject and remains subject to the fixed limit of at most three
distinct Candidate SHAs for its Work Run. Missing-object, path, case, merge, or
attribution ambiguity instead fails closed and preserves all Evidence; it
authorizes neither a new Candidate nor Worker resume. Unrelated Candidates
retain their Evidence.
