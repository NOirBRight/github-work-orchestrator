---
status: superseded by 0008
---

# Adopt the V5.0 lightweight orchestration design

Replace the v4.x campaign protocol (Campaigns, rooms, Material Delivery,
dual-axis review, readback ceremonies) with the two-role design specified in
`docs/drafts/fable-v5-lightweight-orchestration.md`. Measured cost of v4.x: about
9,200 lines of protocol documents and scripts, and orchestration Agents that
spent an hour on ceremony before writing any code.

V5.0 keeps GitHub as the only durable state (`orch:*` labels plus structured
comments), authorizes Workers at creation, and replaces per-step handshakes
with one idempotent reconcile loop. Planning becomes a project-manager
concern: cluster related Issues, rank urgency, and schedule hotset-disjoint
waves as a pipeline. Provider and model selection resolves through three
user-configured difficulty tiers and fails closed. Acceptance is CI plus
graded review; merges are serialized in the Coordinator, making leases
unnecessary.

This supersedes ADRs 0001 through 0006, which document the replaced v4.x
mechanisms.

ADR 0008 is the proposed successor. It keeps the lightweight direction while
replacing the permanent Coordinator Agent, explicit review state, multi-Issue
clusters, and fixed provider binding with a workspace-scoped rolling model.
