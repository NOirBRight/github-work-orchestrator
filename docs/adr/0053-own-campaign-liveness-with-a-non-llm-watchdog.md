---
status: amended by ADR-0054, ADR-0058, and ADR-0059
amends: ADR-0029, ADR-0050
---

# Own Campaign liveness with a non-LLM Watchdog

V8 owns one lightweight, non-LLM Campaign Watchdog. It is an execution host
component, not an Agent, Coordinator turn, Plan Node, or semantic authority.
It keeps active Campaigns converging when a native notification is delayed or
lost.

The Watchdog is event-first. Paseo Agent finish, error, permission, and Review
events, plus hosted-check events, call targeted `ExecutionKernel.advance`.
Every event is only a wake hint: ExecutionKernel reads authoritative Runtime,
GitHub, Workspace, and durable-plan state before changing lifecycle state.

Each ExecutionKernel Wait directive may also carry `next_check_at`. The
Watchdog owns the due-time queue and invokes the same idempotent advance when
the time arrives. This timer path recovers lost callbacks, drives Interactive
Wait Grace expiry, and performs targeted readback without sampling an LLM. On
Watchdog restart, active Campaign state reconstructs the outstanding event
subscriptions and timers.

RuntimeGateway and delivery-boundary calls must have bounded operation time. A
local Paseo, GitHub, or other CLI timeout is an operational observation
followed by identity readback and bounded retry; it is not Candidate rejection
or permission to create another Agent. The Watchdog never restarts the Paseo
daemon automatically because that can terminate unrelated live Agents.

The Watchdog does not read Worker conversations, infer progress from token or
log activity, approve unknown permissions, select models, guide implementation,
or invoke Coordinator turns on a schedule. Only a typed Kernel
`DecisionRequired` or separately accepted bounded stale/semantic trigger can
wake lazy Coordination. Healthy Campaigns therefore spend no LLM tokens on
supervision.
