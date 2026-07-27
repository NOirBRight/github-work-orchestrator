---
status: amended by ADR-0035 and ADR-0055
---

# Publish Plan Revisions as durable facts

Every immutable Plan Revision digest and activation decision is published to
GitHub. The local GWO store may cache the active revision and execution state,
but cannot be the only record of what plan was authorized; this preserves
GitHub as durable truth while still allowing atomic local admission.

Activation canonicalizes and digests PlanSpec, publishes and reads it back,
then compare-and-swaps `(repository, campaign_key)` against
`expected_previous_revision_digest`, null for an initial revision. One
Campaign has exactly one active revision, while disjoint Campaigns may coexist
in the repository.

The store first reserves a non-executable pending digest. A durable Activation
Receipt is the commit point and records repository, Campaign key, activated
revision digest, expected previous revision digest, and repository writer
generation. Store activation is finalized only after receipt readback. A
failed step cannot admit a Work Run from the proposed revision; existing Work
Runs remain bound to their prior revision.
