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
authority. Before Guard success, every compatibility adapter, caller, and write
path that could compose V3 state from or project V2 state is absent or
unreachable. V8 never projects V2 into V3. An active V2 execution either
finishes through its original decoder or is proven quiescent/read-only; V8
never resumes, interprets, or writes it.

Guard failure leaves the V6.1 writer generation authoritative and changes no
production state. Only the durable writer-generation and Activation Receipt
commit may transfer authority. V6.1 and V8 are never simultaneous writers.
The complete contract is
[`Cutover`](../design/gwo-v8-lean-architecture.md#cutover). Earlier V7
transition ADRs remain as superseded history.
