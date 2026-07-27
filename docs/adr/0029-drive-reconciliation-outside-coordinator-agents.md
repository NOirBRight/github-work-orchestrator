---
status: amended by ADR-0050, ADR-0053, ADR-0054, ADR-0056, and ADR-0058
amends: 0003-use-an-event-driven-paseo-coordinator-loop.md
---

# Drive mechanical reconciliation outside Coordinator Agents

V8 does not rely on a Repository Coordinator Agent to wake and supervise its
own control loop. ExecutionKernel exposes a deterministic, idempotent
`advance` operation that observes recorded intent and Runtime or GitHub
readback, executes all currently due bounded mechanical actions, and returns a
typed continuation state.
Materialization is one such Kernel-owned convergence. Coordinator Agents retain
the required Campaign Planning Pass and explicit semantic diagnosis or
Decision judgment, but are replaceable clients of the Kernel rather than the
sole clock that keeps coordination moving. Formal Review and Integration are
Kernel-owned gates. SQLite remains rebuildable control-plane state and GitHub
remains durable business truth.

V8.0 does not add a GWO daemon, service manager, multi-primary Kernel, internal
event bus, or permanent reconciliation worker pool. Campaign Watchdog invokes
`ExecutionKernel.advance`, waits without sampling an LLM, and invokes it again
when an existing event source or due time wakes the Goal. One call may fan out
a bounded batch of independent Materialization actions before it returns. A
host without Watchdog support may invoke the same operation manually.

Each Task Group has a Goal whose objective and acceptance conditions outlive
individual Coordinator turns and Plan Revisions. A turn ending is not a Goal
outcome. The Coordinator may propose completion, but the Kernel accepts it only
after every in-scope Work Item is completed or integrated and all required
review, integration, and Decision Gates are satisfied. Resource cleanup is
Kernel-owned follow-up and does not hold the Goal open.

While the Goal remains active, an ExecutionKernel advance returns a wait directive
without invoking an Agent when an Attempt, GitHub check, materialization retry,
or other explicit Wait Condition is outstanding. If deterministic work is due,
ExecutionKernel performs it; it requests a Coordinator only through a typed
semantic action. Every Wait Condition names its observable wake source and may
name a `next_check_at` for targeted readback rather than relying on free-form
prose. Campaign Watchdog waits until either occurs. A Goal becomes blocked when
its remaining frontier needs a human decision, external input, or Runtime
configuration, and reactivates when that input arrives.

Continuation is keyed by a semantic-input digest rather than time, activity, or
token consumption. One zero-outcome turn may receive one compact corrective
delta in the same session; a second zero-outcome turn for the unchanged digest
creates an explicit Decision Gate rather than sampling indefinitely. A
manually created Coordinator remains preferred. Only a missing or
irrecoverable session is replaced from the configured auto-Coordinator Role
Binding and a compact Goal snapshot. Within the existing Goal, authority, and
effect boundary, discovered work may enter a new Plan Revision automatically;
expansion requires a Decision Gate.

Runtime events wake Campaign Watchdog but do not advance durable state without
readback. A lost callback therefore delays convergence at most; the next
ExecutionKernel advance, triggered by the Wait Condition's `next_check_at`,
performs targeted readback by stable Agent, session, Admission, and action
identities.

Store recovery reconstructs the active Plan Revision, Goals, Work Items, and
Evidence Manifests from GitHub; runtime bindings and Prompt acceptance from
Adapter readback; candidates and workspaces from Git; and hosted-check state
from GitHub Checks. Uniquely matching resources are adopted automatically.
Ambiguity freezes only the affected Plan Node and cannot authorize a duplicate
runtime or stop unrelated repository work.
