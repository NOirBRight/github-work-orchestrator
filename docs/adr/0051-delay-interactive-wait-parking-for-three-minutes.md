---
status: amended by ADR-0059
amends: ADR-0045, ADR-0050
---

# Delay interactive-wait parking for three minutes

A permission request or Worker request for Coordinator guidance begins an
interactive wait. The Work Run retains its existing Campaign Worker Slot
during a three-minute grace period. Host-global configuration may override the
default, and repository configuration may override the host value.

Resolution during the grace period continues the same Runtime Binding,
session, workspace, binding, and Slot without parking or reacquisition. Grace
expiry is only a scheduling event: it cannot approve or deny permission,
declare failure, consume a Candidate submission, invoke availability fallback,
or prove that the Runtime is stalled.

After grace expiry, ExecutionKernel asks RuntimeGateway to interrupt or
otherwise park the Runtime Binding and reads back proof that it cannot resume
execution before releasing the Worker Slot. If parking is ambiguous, the Work
Run keeps the Slot until the binding is reconciled. A permission or Coordinator
decision received after parking is recorded durably; ExecutionKernel
reacquires a Worker Slot before RuntimeGateway delivers the decision and
resumes the same binding.

Candidate acceptance into the Integration Batch queue still releases the Slot
immediately. Candidate Checks, Formal Review Internal Subagents, and Repair
retain the Work Run's existing Slot. This grace applies only to short interactive
waits whose answer may arrive promptly, avoiding release/reacquire churn and
preventing a directly approved Runtime from executing outside GWO capacity.
