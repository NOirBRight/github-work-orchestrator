# GWO V8 GA Release Contract

Schema: `gwo-v8-ga-release-record.v1`

The committed record freezes `evidence_base_sha`, `canary_target_sha`, the
repository/campaign/activation/default-writer identity, the
Canary/Activation/default-writer receipt digests, and the exact metadata path
allow-list. It deliberately contains no tag-candidate SHA, final metadata commit SHA,
CI run ID, or pytest count. GitHub Actions acceptance is disabled and repository
release acceptance is Local Verification Only. The pre-tag command obtains the
dynamic tag-candidate SHA/tree and pytest count from the current `origin/main`
and a content-addressed local verification manifest, then writes a separate
`ReleaseGateReceipt`.

The canonical repository verification mode is `local-only-v1`. Its manifest
must explicitly read back `workflow_count: 0`, disabled Actions/workflows, a
successful full pytest suite, and the exact subject commit and tree. CI,
Hosted-CI, and pull-request fields are rejected recursively; product
Hosted-CI delivery is a separate concern and is not satisfied by repository
release verification.

Every pre-tag receipt input is strict canonical JSON: duplicate names,
non-canonical bytes, and `NaN`/`Infinity` are rejected. Its complete payload
digest is recomputed; a claimed `receipt_digest` is never accepted by itself.
The gate also binds all readbacks to the committed repository, campaign,
activation, and default-writer identity; the default-v8 readback may carry
`campaign_key: null` because it is not campaign-scoped. Git readback runs in
the requested canonical checkout, proves its `origin` remote is the requested
repository, and rereads `origin/main` before success. The local manifest must
prove Local Verification Only, zero workflows, a passing full pytest result,
and the exact candidate commit/tree; hosted CI fields are rejected. The
pre-tag receipt freezes that candidate commit and tree. The renderer rejects
dynamic SHA/CI fields at any nesting or alias, cross-binds its input
identities, and uses a durable staged publication journal with flushed files,
atomic replacement, directory sync, and exact final readback. The post-release
gate requires that the supplied pre-tag receipt is bound to the static record
and rechecks the pre-tag ancestry, metadata-delta, and commit/tree invariants.
It rejects a tag
whose peeled commit or tree differs, then archives by the captured immutable
commit SHA rather than the mutable tag name. Publication rejects symlink and
reparse output targets before any backup or replacement. It checks existing
package manifests before any regeneration, then installs both Skill packages
into temporary `.agents`, `.codex`, and `.claude` surfaces before smoking only
the public `start`, `advance`, and `inspect` operations.
