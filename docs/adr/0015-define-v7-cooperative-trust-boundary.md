---
status: amended by ADR-0034
---

# Define the cooperative single-host trust boundary

V6.1 and V8.0 assume cooperative Agents on one trusted host. Runtime and environment
identifiers provide attribution and protect against accidental misuse; they
are not attestation and do not resist a malicious local Agent. A genuine
capability broker or hostile-host design remains outside V8.0.
