# GWO V8 GA Release Contract

Schema: `gwo-v8-ga-release-record.v1`

The committed record freezes `evidence_base_sha`, `canary_target_sha`, the
Canary/Activation/default-writer receipt digests, and the exact metadata path
allow-list. It deliberately contains no tag-candidate SHA, final metadata commit SHA,
CI run ID, or pytest count. The pre-tag command obtains those
dynamic values only from exact merged-main and CI readback, then writes a
separate `ReleaseGateReceipt`.

The pre-tag gate fails closed unless the CI head SHA equals the tag-candidate
SHA, the CI conclusion is `success`, a pytest pass count is read from the
exact CI log, both static SHAs are ancestors of the candidate, and the
post-Canary delta is exactly the metadata allow-list. The post-release gate
archives the tag into an isolated temporary source and installs both Skill
packages into temporary `.agents`, `.codex`, and `.claude` surfaces before
smoking only the public `start`, `advance`, and `inspect` operations.
