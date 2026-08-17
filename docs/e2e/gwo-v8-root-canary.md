# GWO V8 Root Canary Acceptance

This document defines the read-only acceptance projection for Root Canary Task
4. It is a verifier contract and runbook; it does **not** claim live GitHub or
Paseo execution.

## Acceptance boundary

`scripts/verify_v8_root_canary.py` accepts one canonical evidence bundle only
when all of these readbacks agree:

- exactly four authoritative `ready-for-agent` Ticket readbacks in
  `NOirBRight/github-work-orchestrator`;
- `alpha`, `beta`, and `gamma` are Standard and share one three-member `multi`
  Batch;
- `delta` is Strict and is the sole member of a different `singleton` Batch;
- every Candidate receipt and Review Finding ledger is present, linked to the
  exact Ticket, and closed with no open Finding;
- each Batch has a passed local suite, the same Batch SHA at its PR head and
  hosted-CI readback, a serialized Integration Lease, merge integration, and a
  target readback proving Batch ancestry;
- the two Batch PR numbers and hosted run identities are distinct;
- the read-only recovery proof records a four-slot peak and a complete refill,
  preserves permission requests on the same Runtime Binding, diagnoses each
  stale binding at most once, and permits no more than one terminal-Evidence
  replacement binding (two bindings total per Ticket);
- semantic and external effect identities contain no duplicates;
- Policy Witness, authority-root, Runtime Selector, and fault-journal evidence
  are present and internally cross-bound; and
- the immutable `RootCanaryAcceptanceReceiptV1` digest covers the identity,
  Candidate, Review, Batch, recovery, and evidence digests.

The verifier rejects malformed or incomplete evidence with a named diagnostic
and performs no workflow, Issue, PR, CI, target, or Paseo mutation.

## Inputs

The normal inputs are:

1. Task 1's `gwo-v8-root-canary-tickets.v1` manifest, including all four
   authoritative Issue readbacks;
2. a fresh-process public `inspect` JSON projection containing the acceptance
   bundle and recovery proof;
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
schema `gwo-v8-root-canary-acceptance.v1`, the receipt's canonical fields, and
the final `receipt_digest`. The generated document repeats the local/read-only
limitation so it cannot be mistaken for live GA evidence.

## Named fail-closed diagnostics

Examples include `ROOT_TICKET_READBACK_INVALID`,
`CANDIDATE_RECEIPT_INCOMPLETE`, `FINDING_LEDGER_INCOMPLETE`,
`BATCH_MEMBERS_INVALID`, `LOCAL_SUITE_SHA_MISMATCH`, `HOSTED_SHA_MISMATCH`,
`TARGET_SHA_MISMATCH`, `PERMISSION_BINDING_MISMATCH`,
`RECOVERY_BOUND_INVALID`, `DUPLICATE_EFFECT`,
`POLICY_EVIDENCE_INCOMPLETE`, and `ROOT_REPOSITORY_MISMATCH`.

## Limitations

This implementation verifies the exact bytes and cross-links supplied to it;
it does not itself query GitHub, start a Campaign, call Paseo, or inspect a
remote repository unless the explicitly read-only `--github-live` identity
check is requested. A successful local verification is therefore local
evidence only and is not a live Canary or GA publication claim.
