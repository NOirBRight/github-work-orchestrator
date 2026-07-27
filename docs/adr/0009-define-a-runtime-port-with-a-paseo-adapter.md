---
status: superseded by ADR-0034
---

# Define a Runtime Port with a Paseo adapter

Confine every runtime dependency behind one narrow Runtime Port with five
operations: `spawn`, `status`, `deliver-prompt`, `worktree`, and `archive`.
The gwo kernel (mailbox, tasks, dispatches, review rounds, leases, guards) is
runtime-agnostic; only the adapter touches an Agent runtime. Paseo is the sole
adapter implemented now. Never hardcode a Provider or model in the kernel.

The port must serve two execution models as a first-class design constraint:

- **Resident-agent** (Paseo ACP container): Agents are long-lived with
  idle/running states; `deliver-prompt` targets an idle Agent.
- **Session-process** (headless CLIs such as `claude -p` and `codex exec`):
  one turn is one process; the Agent identity is a persistent session ID;
  `deliver-prompt` resumes the session in a new process; liveness is process
  supervision over a spawn-captured event journal, replacing HEARTBEAT.

Adapters inject `GWO_AGENT_ID` into the child environment at spawn — the
identity the gwo CLI enforces on every write — and compile the contract's
permission profile into spawn-time runtime flags, since a session-process
runtime cannot answer interactive permission prompts. `status` returns exactly
`running`, `stalled`, or `exited` with terminal evidence; silence still never
authorizes a destructive action.

ADR 0002 remains in force: cleanup authorization stays inside GWO, above the
port, and adapters only execute read-backed cleanup plans. The headless
adapter is specified in `docs/design/gwo-v7-architecture.md` as the port's
validation case; its implementation is deferred until there is a real need.
