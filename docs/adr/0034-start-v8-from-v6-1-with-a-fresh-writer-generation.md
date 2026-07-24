---
status: accepted
supersedes: ADR-0014, ADR-0016, ADR-0027, ADR-0032, ADR-0033
---

# Start V8 from V6.1 with a fresh writer generation

V6.1 is the actual production writer and V7 was never adopted, so V8 starts
from a fenced V6.1 repository rather than completing, freezing, compiling, or
migrating V7. Cutover reuses durable GitHub, Git, Runtime, and CI facts, creates
a fresh Store generation, and publishes one durable writer generation; it does
not import V6.1 Dispatch, Attempt, or Store identities.

This preserves the useful single-writer, shadow, canary, and fresh-generation
decisions while deleting a compatibility path that had no production state to
protect. Earlier V7 transition ADRs remain as superseded history.
