# GWO V8 Root Canary Acceptance

This document defines the read-only `local-only-v1` acceptance projection for
the Root Canary. It is a verifier contract and runbook; it does **not** claim
live GitHub, Hosted-CI, or Paseo execution.

## Acceptance boundary

`scripts/verify_v8_root_canary.py` accepts one canonical evidence bundle only
when all of these readbacks agree:

- exactly four authoritative `ready-for-agent` Ticket readbacks in
  `NOirBRight/github-work-orchestrator`;
- every Ticket readback carries the complete Task 1 contract (`number`, state,
  labels, body, comments, blockers, and blocker states) and its canonical
  contract digest;
- `alpha`, `beta`, and `gamma` are Standard and share one three-member `multi`
  Batch;
- the semantic `delta` Ticket key is Strict and is the sole member of a
  different `singleton` Batch; the verifier does not infer Strict from an
  Issue number;
- every Candidate receipt and Review Finding ledger is present, linked to the
  exact Ticket and receipt digest, and closed with no open Finding;
- each Batch has a passed local suite, a Batch ref/SHA readback, a serialized
  Integration Lease, CAS target integration, Batch ancestry, and final target
  readback; and
- local evidence contains no pull-request, Hosted-CI, workflow-run, or remote
  publication identity field;
- the read-only recovery proof records a four-slot peak and a complete refill,
  preserves every permission request on the same Runtime Binding through an
  authorization link, authorizes each stale diagnosis at most once, and
  separately authorizes any terminal-Evidence replacement binding (two
  bindings total per Ticket);
- semantic and external effect identities contain no duplicates and every
  effect ID has a complete effect record;
- Policy Witness, authority-root, Runtime Selector, and fault-journal evidence
  are present and cross-bound between the top-level bundle and proof; and
- the immutable `RootCanaryAcceptanceReceiptV1` digest covers complete
  authoritative Ticket, Candidate, accepted-Candidate, Review, Batch/target,
  recovery, effect, policy, authority, selector, and fault readbacks, not only
  their summary digests.

The verifier rejects malformed or incomplete evidence with a named diagnostic
and performs no workflow, Issue, PR, CI, target, or Paseo mutation.

## Inputs

The normal inputs are:

1. Task 1's `gwo-v8-root-canary-tickets.v2` manifest, including all four
   authoritative Issue readbacks;
2. a fresh-process public `inspect` JSON projection containing the acceptance
   bundle, a required complete diagnostics status, and recovery proof;
3. the two immutable Batch delivery readbacks, either inside that bundle or
   supplied with `--batch-receipt` twice; and
4. an optional operator-supplied exact remote target SHA via `--target-sha`.

The adapter also understands the current repository's nested `facts`/
`readback` projection and the current `CampaignProofReadback` field names. It
does not import or invoke a production host, transition, runner, ticket
provisioner, or GA metadata writer.

## Read-only CLI

```powershell
py -3.13 scripts/verify_v8_root_canary.py `
  --tickets tickets-readback.json `
  --diagnostics diagnostics.json `
  --output root-canary-receipt.json
```

When delivery receipts are separate, pass both explicitly:

```powershell
py -3.13 scripts/verify_v8_root_canary.py `
  --tickets tickets-readback.json `
  --diagnostics diagnostics.json `
  --batch-receipt multi-batch.json `
  --batch-receipt singleton-batch.json `
  --target-sha REMOTE_TARGET_SHA `
  --output root-canary-receipt.json
```

`--github-live` is an optional read-only `gh repo view` repository-identity
check. It is not required for local acceptance and does not turn supplied
evidence into a claim that a live GitHub or Paseo Campaign ran.

## Generated acceptance projection

`write_acceptance_document(path, receipt)` writes a Markdown document with
schema `gwo-v8-root-canary-acceptance.v2` and `acceptance_mode:
local-only-v1`, the receipt's canonical fields,
complete `authoritative_evidence`, and the final `receipt_digest`. The generated
document repeats the local/read-only limitation so it cannot be mistaken for
live GA evidence.

## Named fail-closed diagnostics

Examples include `ROOT_TICKET_READBACK_INVALID`,
`CANDIDATE_RECEIPT_INCOMPLETE`, `FINDING_LEDGER_INCOMPLETE`,
`BATCH_MEMBERS_INVALID`, `LOCAL_SUITE_SHA_MISMATCH`, `HOSTED_SHA_MISMATCH`,
`TARGET_SHA_MISMATCH`, `PERMISSION_BINDING_MISMATCH`,
`DIAGNOSTICS_STATUS_REQUIRED`, `DIAGNOSTICS_INVALID`,
`TICKET_READBACK_MISMATCH`, `TICKET_CONTRACT_DIGEST_MISMATCH`,
`PERMISSION_AUTHORIZATION_INCOMPLETE`,
`STALE_DIAGNOSIS_AUTHORIZATION_INCOMPLETE`,
`TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE`, `RECOVERY_BOUND_INVALID`,
`EFFECT_PROOF_INCOMPLETE`, `BATCH_BOUNDARY_COLLAPSED`, `DUPLICATE_EFFECT`,
`POLICY_EVIDENCE_INCOMPLETE`, and `ROOT_REPOSITORY_MISMATCH`.

## GA metadata bridge

The GA metadata renderer may project a separate, canonical bridge after this
local receipt has been accepted. The bridge keeps these identities explicit:

| Role | Authoritative identity |
| --- | --- |
| Local Root Canary | `campaign:fd16e735a23425ee5071e881`, `writer:local`, and the local receipt path |
| External Production Canary package | `NOirBRight/gwo-v8-canary`, package digest `2533a3e5f22cc0c5e8bf2e7cd7114f33f2895d394da3f0ab69a9742205069f30`, and its readback path |
| Production Activation | `activation:47895d07122a3d9827ecdf63`, transition `writer-transition:ce14291c00b0c5bfe7251729`, and `v8-generation-1` |
| Default-writer readback | the target repository's `default_v8` readback, bound to the activation and exact `v8-generation-1` |

The target repository is `NOirBRight/github-work-orchestrator`; the external
Canary package is not the target repository. The local Root Canary Campaign and
writer identities are therefore not copied into the Production Activation or
default-writer readback. The bridge binds the package evidence to the
activation's `canary_repository` and `canary_evidence_digest`, then binds that
activation ID and writer generation to the default-writer readback. It also
records the readback control ref
`origin/gwo-control@5d463d2ecd3e98644fa72dce01326bd553ecbb39`.

The renderer accepts the derived `gwo-v8-ga-evidence-bridge.v1` object (for
example, `D:\gwo-release-evidence\2026-08-19-gwo-v8-ga-production-cutover\ga-evidence-bridge.json`)
through `--evidence-bridge`. Its `bridge_digest` is recomputed over the
canonical payload before projection. The bridge has a role-specific allow-list
so unrecognized dynamic SHA/CI fields and unknown role fields are rejected;
repository, package, activation, generation, and default-writer mismatches
fail closed. Generated metadata retains a stable bridge-identity digest (the
bridge digest projection excludes the moving final `release_subject`), the
activation release subject, and the renderer's explicit `evidence_bridge_links`.

## Limitations

This implementation verifies the exact bytes and cross-links supplied to it;
it does not itself query GitHub, start a Campaign, call Paseo, or inspect a
remote repository unless the explicitly read-only `--github-live` identity
check is requested. A successful local verification is therefore local
evidence only and is not a live Canary or GA publication claim.
