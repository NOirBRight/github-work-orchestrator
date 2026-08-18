# GWO V8 Local-Only Root Canary Contract

**Date:** 2026-08-18  
**Status:** Approved implementation design for the #119 acceptance boundary

## Goal

Convert the #119 root-Canary acceptance from a Hosted-CI/PR contract to an
explicit local-only contract that can be executed without GitHub Actions,
while preserving the public V8 workflow and the four-Ticket concurrency,
Candidate, Review, repair, restart, permission, deduplication, Batch, and
target-integration behaviors.

This contract is acceptance evidence for the V8 workflow. It is not a
production Activation Receipt and it does not authorize a writer transition.

## Normative boundary

The canonical acceptance mode is exactly:

```text
local-only-v1
```

The local-only root acceptance:

- reads the authoritative `gwo-v8-root-canary-tickets.v2` manifest;
- requires exactly the real root Tickets #195, #196, #197, and #198 in the
  current manifest, in the explicit `alpha`, `beta`, `gamma`, `delta` order;
- keeps the three Standard Tickets in one `multi` Batch and the Strict Ticket
  in a separate `singleton` Batch;
- uses only an isolated temporary Git repository, local suite receipts, local
  Batch composition, a durable local Integration Lease, CAS ref updates, and
  local target readback;
- rejects any PR, Hosted-CI, remote-publication, workflow-run, or Hosted-CI
  identity field in the local-only evidence; and
- remains read-only with respect to GitHub, Paseo, production SQLite, the
  production target, Activation Receipts, and writer configuration.

The repository release gate is independently local-only. Product Hosted-CI
support remains a separate production BatchIntegrator concern and cannot be
represented, simulated, or satisfied by this root-Canary record.

## Ticket manifest adapter

`scripts/provision_v8_root_canary.py` remains the authoritative readback
adapter. Its read-only output is the input to both the verifier and the local
runner. The implementation must not infer role from an Issue number. The role
mapping is the manifest order and the existing ticket contract:

| semantic key | authoritative issue | assurance | batch |
| --- | ---: | --- | --- |
| `alpha` | #195 | standard | multi |
| `beta` | #196 | standard | multi |
| `gamma` | #197 | standard | multi |
| `delta` | #198 | strict | singleton |

The adapter must preserve the complete contract, labels, comments, native
blockers, `ready_refs`, and source/contract digests. Missing, duplicate,
reordered, stale, or tampered manifest entries fail closed. A fixture may be
used in tests, but it must contain the exact authoritative #195–#198 readback
shape and digest rules.

## Local acceptance evidence

The producer writes `gwo.v8.local-root-acceptance.v1` and the top-level record
contains `acceptance_mode: "local-only-v1"`. All fields below are canonical
JSON and digest-covered.

### Local Batch proof

Each Batch has a `local_batch_proof.v1` containing only:

- repository, Campaign, Plan Revision, Batch ID, Batch SHA, Batch ref and ref
  readback;
- ordered member Ticket keys and Candidate/accepted-Candidate/diff digests;
- local suite definition and successful local suite receipt;
- local Integration Lease identity, acquisition/release readback, and stable
  action identity;
- target branch, target-before and target-after commit/tree readback;
- CAS update result, Batch ancestry proof, and final target readback; and
- the proof digest.

It contains no PR number/head, PR merge mapping, Hosted-CI suite/run/check,
publication receipt, workflow URL, remote target, or nullable placeholder for
any of those concepts.

### Root evidence

The root evidence retains:

- public `start`, `advance`, and `inspect` transcript;
- four Work Runs with four-slot peak and final slot release;
- independent semantic Runtime selectors and frozen authority digests;
- CandidateGate transitions for accepted, repair-required/repaired, and
  ordinary-rejected/replacement Candidates;
- Standard multi-Batch and Strict singleton Batch proofs;
- permission parking/resume, stale diagnosis bounds, lost wake, duplicate
  callback, restart, and idempotent local effects; and
- exact Ticket, Plan, Candidate, Review, Policy Witness, authority-root,
  Runtime Selector, and fault-journal readbacks.

The producer may use deterministic internal test doubles for Runtime, Candidate
Review, and local Git operations. Their output must be projected into the
local-only schema; internal test-double implementation names and Hosted-CI
fields must not leak into the acceptance evidence.

## Verifier behavior

`scripts/verify_v8_root_canary.py` must:

1. require the explicit `acceptance_mode == "local-only-v1"`;
2. validate the #195–#198 manifest and preserve the full v2 objects;
3. validate local suite, local Batch, local Lease, and local target proof;
4. reject Hosted-CI/PR fields recursively in the selected local evidence;
5. bind the mode and complete local evidence into the immutable acceptance
   receipt digest; and
6. continue to perform no workflow, tracker, provider, or target mutation.

The local verifier must never make a missing Hosted-CI field pass by substituting
a digest or a synthetic `not-applicable` Hosted-CI record. It must use a
separate local proof schema instead.

The acceptance receipt remains a local evidence receipt. Its schema is bumped
to `gwo-v8-root-canary-acceptance.v2` because the digest-covered batch contract
has changed and the old PR/Hosted-CI receipt must not silently round-trip as a
new local receipt.

## Release and production separation

The local root receipt can satisfy the local #119 acceptance gate only. It does
not provide:

- `Activation Receipt`;
- `writer transition receipt`;
- default-writer readback;
- permission to execute production `--execute`; or
- permission to create the `v8.0.0` tag.

Those artifacts remain frozen behind the separately approved Phase 5
production mutation and GA gate.

## Failure codes

The implementation uses named fail-closed diagnostics, including:

```text
ROOT_ACCEPTANCE_MODE_REQUIRED
ROOT_ACCEPTANCE_MODE_INVALID
ROOT_TICKET_MANIFEST_INVALID
ROOT_TICKET_REAL_ISSUES_REQUIRED
LOCAL_BATCH_PROOF_INCOMPLETE
LOCAL_BATCH_HOSTED_FIELD_FORBIDDEN
LOCAL_BATCH_SHA_MISMATCH
LOCAL_INTEGRATION_LEASE_INVALID
LOCAL_TARGET_READBACK_INVALID
LOCAL_TARGET_ANCESTRY_INVALID
```

Existing Candidate, Review, recovery, identity, and effect diagnostics remain
unchanged unless a new local-only boundary is the reason for the rejection.

