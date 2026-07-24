---
status: accepted
amends: ADR-0017, ADR-0023
---

# Use a durable Activation Receipt as the commit point

SQLite and GitHub cannot share one transaction, so activation first reserves a
pending expected digest, publishes and reads back the immutable Plan record,
CAS-updates the dedicated GitHub control record, reads back an Activation
Receipt, and only then finalizes the active digest in the Store. No Admission
is allowed while activation is pending or unfinalized.

Before the receipt, proven unchanged GitHub state permits clearing the pending
reservation. After the receipt, recovery may only roll forward; rollback is a
new durable compensating action and never erases the receipt.
