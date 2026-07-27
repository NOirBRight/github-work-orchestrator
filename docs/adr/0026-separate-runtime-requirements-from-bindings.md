---
status: amended by ADR-0037, ADR-0039, ADR-0042, ADR-0044, and ADR-0059
---

# Separate Runtime requirements from Runtime Bindings

PlanSpec records only factual capabilities required by a selected Ticket. It
does not contain a provider, model, reasoning setting, CLI, Runtime selector,
Profile, fallback, or fabricated Agent identity.

RuntimeGateway resolves the exact role selector through persisted
Campaign-start overrides, repository role configuration, and host-global role
configuration. It records the selector, configuration source, resolved Profile
digest, fallback selection, and authoritative Agent, session, workspace, and
Runtime Binding identity as Runtime facts outside PlanSpec.

Capability requirements and assignment are independent. PlanControl may reject
a Profile that lacks a required capability, but neither semantic planning nor
Assurance Requirement may rank Profiles or choose a stronger model. Replacing
a terminal Worker uses the already resolved `recovery_worker` assignment and
does not rewrite PlanSpec.
