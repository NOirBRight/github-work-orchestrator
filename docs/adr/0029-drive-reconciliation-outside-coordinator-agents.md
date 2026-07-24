---
status: accepted
amends: 0003-use-an-event-driven-paseo-coordinator-loop.md
---

# Drive mechanical reconciliation outside Coordinator Agents

V8 does not rely on a Repository Coordinator Agent to wake and supervise its
own control loop. The GWO Kernel exposes a deterministic, idempotent
`reconcile --once` pass that observes recorded intent and runtime or GitHub
readback, executes all currently due mechanical actions, and exits.
Materialization is one such Kernel-owned convergence. Coordinator Agents retain
semantic planning, diagnosis, review, and integration judgment, but are
replaceable clients of the Kernel rather than the sole clock that keeps
coordination moving. SQLite remains rebuildable control-plane state and GitHub
remains durable business truth.

V8.0 does not add a GWO daemon, service manager, multi-primary Kernel, internal
event bus, or permanent reconciliation worker pool. A host-provided Goal Driver
such as a `/goal` continuation mechanism invokes `reconcile --once`, waits
without sampling an LLM, and invokes it again when an existing event source or
due time wakes the Goal. One pass may fan out a bounded batch of independent
Materialization calls before it exits. A host without Goal Driver support must
invoke the same pass manually or through its own external scheduler.

Each Task Group has a Goal whose objective and acceptance conditions outlive
individual Coordinator turns and Plan Revisions. A turn ending is not a Goal
outcome. The Coordinator may propose completion, but the Kernel accepts it only
after every in-scope Work Item is completed or integrated and all required
review, integration, and Decision Gates are satisfied. Resource cleanup is
Kernel-owned follow-up and does not hold the Goal open.

While the Goal remains active, a reconciliation pass returns a wait directive
without invoking an Agent when an Attempt, GitHub check, materialization retry,
or other explicit Wait Condition is outstanding. If completion is not yet
satisfied and no Wait Condition exists, it returns a continuation directive
and the Goal Driver invokes the Coordinator again. Every Wait Condition names
its observable wake source and may name a `next_check_at` for targeted readback
rather than relying on free-form prose. The Goal Driver waits until either
occurs; V8.0 has no separate global fallback scanner. A Goal becomes blocked
when its remaining frontier needs a human decision, external input, or runtime
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

Runtime events wake the Goal Driver but do not advance durable state without
readback. A lost callback therefore delays convergence at most; the next Kernel
pass, triggered by the Wait Condition's `next_check_at`, performs targeted
readback by stable Agent, session, Admission, and action identities.

Store recovery reconstructs the active Plan Revision, Goals, Work Items, and
Evidence Manifests from GitHub; runtime bindings and Prompt acceptance from
Adapter readback; candidates and workspaces from Git; and hosted-check state
from GitHub Checks. Uniquely matching resources are adopted automatically.
Ambiguity freezes only the affected Plan Node and cannot authorize a duplicate
runtime or stop unrelated repository work.
