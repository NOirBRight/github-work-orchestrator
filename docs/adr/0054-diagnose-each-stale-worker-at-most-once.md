---
status: accepted
amends: ADR-0029, ADR-0050, ADR-0053
---

# Diagnose each stale Worker at most once

The Campaign Watchdog maintains a stale-turn deadline for each active Worker
binding. The default is thirty minutes without a trusted state change, with
host-global configuration and repository override. Trusted state changes
include an authoritative Runtime lifecycle transition, normalized permission
event, a durably persisted Candidate receipt, or another independently
observed Kernel-owned state transition. Only a Candidate receipt whose exact
Candidate SHA/tree came from authoritative readback and whose complete diff
digest was constructed and revalidated over the exact base and Candidate
objects, then durably persisted, is trusted Candidate liveness progress. A
Candidate-reference report, notification, Worker checkpoint text, workspace
head, raw log, or unread-back completion statement does not reset the deadline.

When the deadline becomes due, the Watchdog first performs zero-LLM targeted
Runtime, process, Workspace, and Kernel readback. A terminal, idle, permission,
Candidate-receipt-backed, or otherwise mechanically classified state follows
its existing deterministic path.

Only when the same binding remains `running` and the readback cannot classify
it may the Kernel request one Coordinator stale-turn diagnostic for that
binding. The Coordinator receives a bounded packet containing the frozen
Ticket contract and Authority Grant references, binding and Candidate
counters, lifecycle and process facts, workspace/diff summary, Check state,
and bounded transcript tail. It does not receive every Worker conversation.

The typed diagnostic result is limited to `continue`, `guide_same_worker`,
`recover_same_binding`, or `decision`. It cannot approve the implementation,
produce Formal Review Evidence, expand authority, select a different Runtime,
or create another Agent. A replacement Worker still requires terminal-binding
Evidence and the unused replacement-binding allowance.

The diagnostic is allowed at most once for each initial or replacement
binding. A
`continue` result suppresses further stale-turn Coordinator calls for that
binding even if more time passes. Timeout alone never interrupts a Worker or
declares semantic failure. Healthy bindings therefore pay no supervision LLM
cost, and an ambiguous stale binding pays at most one bounded diagnostic.
