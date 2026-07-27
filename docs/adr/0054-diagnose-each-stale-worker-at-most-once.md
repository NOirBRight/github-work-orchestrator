---
status: accepted
amends: ADR-0029, ADR-0050, ADR-0053
---

# Diagnose each stale Worker at most once

The Campaign Watchdog maintains a stale-turn deadline for each active Worker
Attempt. The default is thirty minutes without a trusted state change, with
host-global configuration and repository override. Trusted state changes
include an authoritative Runtime lifecycle transition, typed Worker
checkpoint or attention event, Workspace head or diff-manifest digest change,
permission event, Result Claim, or other Kernel-owned state transition. Token
growth and arbitrary log activity are not trusted progress.

When the deadline becomes due, the Watchdog first performs zero-LLM targeted
Runtime, process, Workspace, and Kernel readback. A terminal, idle, permission,
Result, or otherwise mechanically classified state follows its existing
deterministic path.

Only when the same binding remains `running` and the readback cannot classify
it may the Kernel request one Coordinator stale-turn diagnostic for that
Attempt. The Coordinator receives a bounded packet containing the frozen Work
Contract reference, Attempt and Candidate counters, lifecycle and process
facts, Workspace/diff summary, Check state, and bounded transcript tail. It
does not receive every Worker conversation.

The typed diagnostic result is limited to `continue`, `guide_same_worker`,
`recover_same_binding`, or `decision`. It cannot approve the implementation,
produce Formal Review Evidence, expand authority, select a different Runtime,
or create another Agent. A replacement Worker still requires a Terminal
Binding Receipt and the remaining Worker Attempt Budget.

The diagnostic is allowed at most once for the complete Worker Attempt. A
`continue` result suppresses further stale-turn Coordinator calls for that
Attempt even if more time passes. Timeout alone never interrupts a Worker or
declares semantic failure. Healthy Attempts therefore pay no supervision LLM
cost, and an ambiguous stale Attempt pays at most one bounded diagnostic.
