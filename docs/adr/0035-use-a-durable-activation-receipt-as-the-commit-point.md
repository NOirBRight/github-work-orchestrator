---
status: amended by ADR-0055
amends: ADR-0017, ADR-0023
---

# Use a durable Activation Receipt as the commit point

SQLite and GitHub cannot share one transaction, so activation first reserves a
non-executable pending revision digest, publishes and reads back the immutable
Plan record, compare-and-swaps `(repository, campaign_key)` against the exact
expected previous revision digest, then reads back an Activation Receipt. The
expected previous digest is null for an initial revision.

The receipt records repository, Campaign key, activated Plan Revision digest,
expected previous revision digest, and repository writer generation. For V8
cutover, the read-only Cutover Guard must already have succeeded and the
durable writer-generation publication plus read-backed Activation Receipt is
the sole authority-transfer commit point. Only then does the store finalize
the active revision. No Work Run is admitted while activation is pending or
unfinalized.

Before the receipt, proven unchanged GitHub state permits clearing the pending
reservation. After the receipt, recovery only rolls forward; rollback is a new
durable action and never erases the receipt. A Campaign handle remains stable
across successor revisions.

No compatibility path may project V2 into V3. Before the cutover Guard can
succeed, every V3-composition or V2-projection adapter, caller, and write path
must be absent or unreachable. A failed Guard preserves V6.1 writer authority.
The integrated rules are
[`Cutover`](../design/gwo-v8-lean-architecture.md#cutover).
