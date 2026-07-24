---
status: superseded
superseded-by: 0007-adopt-v5-lightweight-orchestration.md
---

# Require acknowledged delivery for material Campaign Room events

Superseded by ADR 0007 for the V7 design: the acknowledged-delivery guarantee
moves into the gwo CLI (`inbox --ack-on-read`, Signal-ID idempotency,
idle-only wake). Operative until roadmap Phase 1 lands.

Treat a durable Room post and parent activation as separate facts. Every
explicitly addressed material event uses the GWO-owned transaction
`post-material → delivery-plan → idle-only signal wake → DELIVERY_WAKE →
recipient replay → DELIVERY_ACK`. Progress and Heartbeat remain visibility-only.

The sender never prompts a running Agent. While one delivery is pending it may
perform bounded Room waits and re-read only the exact recipient status; this is
not repository-wide polling. A recipient returning idle after a recorded wake
without ACK is protected and escalated rather than repeatedly prompted.

Wake and ACK records are deterministic, non-authoritative transport metadata.
They cannot poison their source business event, advance Issue/Campaign state,
authorize merge, or satisfy cleanup. Delivery state is accepted only with the
matching replayed Signal-ID and Room message UUID. The protocol is at-least-once across the
small native-send/receipt crash window, with source Signal-ID deduplication.

This closes the observed Worker→Campaign and Campaign→Coordinator scheduling
gap without a new Skill, daemon/sidecar, Paseo source change, or CodexHub
dependency. The guarantee applies to GWO-owned communication; direct external
Paseo operations remain outside the plugin boundary.
