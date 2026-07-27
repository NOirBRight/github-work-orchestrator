---
status: amended by ADR-0039, ADR-0042, ADR-0044, ADR-0045, and ADR-0061
amends: ADR-0026, ADR-0031
---

# Separate Runtime capabilities from role Profiles

PlanSpec may state factual Runtime capabilities but never assigns a provider,
model, CLI, reasoning setting, selector, Profile, or fallback. RuntimeGateway
resolves one Campaign-scoped `coordinator` selector and the Ticket-scoped
`worker`, `recovery_worker`, `review_primary`, `review_strong`, and
`specialist:<policy-id>` selectors from deterministic user configuration.

Every mapping has one required primary Runtime Profile and at most one optional
availability fallback. RuntimeGateway persists the selector, configuration
source, resolved Profile digest, and fallback selection for each stable action.
Changing configuration never rewrites an existing assignment or Plan Revision.

Formal Review Internal Subagents consume no Campaign Worker Slot and may use a
different selector from their parent Worker. They remain read-only under the
work entry's Authority Grants and Policy Witness. Runtime capability never
grants semantic or external-effect authority.
