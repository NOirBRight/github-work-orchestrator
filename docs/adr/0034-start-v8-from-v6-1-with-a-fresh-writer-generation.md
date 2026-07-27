---
status: amended by ADR-0046
supersedes: ADR-0014, ADR-0016, ADR-0027, ADR-0032, ADR-0033, 0007-move-invariants-into-a-stateful-gwo-cli.md, 0008-flatten-supervision-into-a-dynamic-task-dag.md, 0009-define-a-runtime-port-with-a-paseo-adapter.md
amends: ADR-0015
---

# Start V8 from V6.1 with a fresh writer generation

V6.1 is the actual production writer and V7 was never adopted, so V8 starts
from a fenced V6.1 repository rather than completing, freezing, compiling, or
migrating V7. Cutover reuses durable GitHub, Git, Runtime, and CI facts,
creates a fresh store generation, and publishes one repository-global writer
generation. It does not import V6.1 execution or store identities.

The activation Cutover Guard proves the prior writer quiescent before changing
authority. V6.1 and V8 are never simultaneous writers. Earlier V7 transition
ADRs remain as superseded history.
