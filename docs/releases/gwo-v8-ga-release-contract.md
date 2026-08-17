# GWO V8 GA Release Contract

Schema: `gwo-v8-ga-release-record.v1`

The committed record freezes `evidence_base_sha`, `canary_target_sha`, the
repository/campaign/activation/default-writer identity, the
Canary/Activation/default-writer receipt digests, and the exact metadata path
allow-list. It deliberately contains no tag-candidate SHA, final metadata commit SHA,
CI run ID, or pytest count. The pre-tag command obtains those dynamic values
only from the current `origin/main` and exact CI readback, then writes a
separate `ReleaseGateReceipt`.

Every pre-tag receipt input is strict canonical JSON: duplicate names,
non-canonical bytes, and `NaN`/`Infinity` are rejected. Its complete payload
digest is recomputed; a claimed `receipt_digest` is never accepted by itself.
The gate also binds all readbacks to the committed repository, campaign,
activation, and default-writer identity; the default-v8 readback may carry
`campaign_key: null` because it is not campaign-scoped. Git readback runs in
the requested canonical checkout, proves its `origin` remote is the requested
repository, and rereads `origin/main` before success. The pre-tag receipt
freezes the candidate commit and tree. The renderer rejects dynamic SHA/CI
fields at any nesting or alias, cross-binds its input identities, and uses a
durable staged publication journal with flushed files, atomic replacement,
directory sync, and exact final readback. The post-release gate requires that
pre-tag receipt and rejects a tag whose peeled commit or tree differs before
archiving it into an isolated temporary source. It checks existing package
manifests before any regeneration, then installs both Skill packages into
temporary `.agents`, `.codex`, and `.claude` surfaces before smoking only the
public `start`, `advance`, and `inspect` operations.
