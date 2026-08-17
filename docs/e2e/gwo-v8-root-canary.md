# GWO V8 Root Canary Acceptance

This document defines the read-only acceptance projection for Root Canary Task
4. It is a verifier contract and runbook; it does **not** claim live GitHub or
Paseo execution.

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
- each Batch has a passed local suite, the same Batch SHA at its PR head and
  hosted-CI readback, a serialized Integration Lease, merge integration, and a
  target readback proving Batch ancestry, PR identity, and the PR-to-merge
  target mapping;
- the two Batch PR numbers and hosted run identities are distinct;
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

1. Task 1's `gwo-v8-root-canary-tickets.v1` manifest, including all four
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
schema `gwo-v8-root-canary-acceptance.v1`, the receipt's canonical fields,
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

## Limitations

This implementation verifies the exact bytes and cross-links supplied to it;
it does not itself query GitHub, start a Campaign, call Paseo, or inspect a
remote repository unless the explicitly read-only `--github-live` identity
check is requested. A successful local verification is therefore local
evidence only and is not a live Canary or GA publication claim.
