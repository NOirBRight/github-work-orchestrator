---
status: amended by ADR-0035
---

# Publish Plan Revisions as durable facts

Every immutable Plan Revision digest and activation decision is published to
GitHub. The local GWO store may cache the active revision and execution state,
but cannot be the only record of what plan was authorized; this preserves
GitHub as durable truth while still allowing atomic local admission. Activation
therefore canonicalizes and digests PlanSpec, publishes and reads it back from
GitHub, then compare-and-swaps the expected prior digest in the store. A failed
step cannot admit work from the proposed revision; existing Attempts remain
bound to their prior revisions.

ADR-0035 refines the cross-system commit protocol: the Store first reserves a
non-executable pending digest, the GitHub Activation Receipt is the commit
point, and Store activation is finalized only after receipt readback.
