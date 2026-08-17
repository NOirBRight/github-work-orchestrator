# GWO V8 GA Release Contract

Schema: `gwo-v8-ga-release-record.v1`

The committed record freezes `evidence_base_sha`, `canary_target_sha`, the
repository/campaign/activation/default-writer identity, the
Canary/Activation/default-writer receipt digests, and the exact metadata path
allow-list. It deliberately contains no tag-candidate SHA, final metadata commit SHA,
CI run ID, or pytest count. The pre-tag command obtains those dynamic values
only from the current `origin/main` and exact CI readback, then writes a
separate `ReleaseGateReceipt`.

Every pre-tag receipt input is canonicalized and its complete payload digest is
recomputed; a claimed `receipt_digest` is never accepted by itself. The gate
also binds all readbacks to the committed repository, campaign, activation,
and default-writer identity. It fails closed unless the exact CI run readback
has the same head as the current `origin/main`, the conclusion is `success`,
a pytest pass count is read from that run's log, both static SHAs are
ancestors of the candidate, and the post-Canary delta is exactly the metadata
allow-list. The renderer rejects dynamic SHA/CI fields at any nesting or alias
and publishes its three documents all-or-nothing. The post-release gate
checks existing package manifests before any regeneration, then archives the
tag into an isolated temporary source and installs both Skill packages into
temporary `.agents`, `.codex`, and `.claude` surfaces before smoking only the
public `start`, `advance`, and `inspect` operations.
