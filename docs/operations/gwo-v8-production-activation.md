# GWO V8 production activation contract

This document defines the Phase 5 activation boundary implemented by
`D:\Workstation\gwo-work-orchestrator\scripts\run_v8_production_activation.py`.
The command is a composition boundary: it parses the exact activation bundle,
constructs the typed V8 inputs, asks a host-owned live factory for the durable
cutover controls, performs a zero-write preflight, and only then executes the
existing `WriterCutoverController` path when `--execute` is explicit.

## Owner approval identity

`ProductionActivationAuthorization` is one immutable identity. Every field is
required and is included in the authorization receipt digest:

| Field | Meaning |
| --- | --- |
| `repository` | Repository whose merged release subject is approved |
| `merged_main_sha` | Exact merged-main commit OID |
| `merged_main_git_tree` | Exact merged-main root-tree OID |
| `release_subject_digest` | Digest of the external release-subject manifest |
| `run_id` | One operator activation attempt |
| `evidence_root` | Exact evidence root for that attempt |
| `target_repository` | Repository whose writer authority is changed |
| `canary_repository` | Exact repository that owns the accepted Canary package and Evidence |
| `writer_transition` | Must equal the literal `v6.1 -> v8` |
| `target_writer_generation` | Exact V8 writer-generation identity |

`ProductionActivationAuthorizationReceipt` repeats those fields and adds
`approval_ref` and `receipt_digest`. The digest covers every field except
`receipt_digest`; a receipt with a changed repository, merged-main identity,
subject digest, run, evidence root, target, Canary source, or transition is
rejected before any cutover control is called.

`source_main_sha` and `source_main_tree` remain read-only compatibility aliases
for the merged-main fields. They are not emitted by the canonical contract.

## Activation bundle and wiring

The input JSON has this exact top-level schema:

```text
authorization
authorization_receipt
compiled_plan
canary
guard_subject
guard_receipt
worker_capacity
coordinator_capacity
```

The CLI constructs, in order, the following typed values:

```text
ProductionActivationAuthorization
CompiledPlan
CanaryAcceptance
CutoverSubject
CutoverGuardReceipt
ProductionActivationRequest
```

It then calls the required host-owned `ProductionActivationCompositionFactory`:

```python
def compose(
    *,
    authorization: ProductionActivationAuthorization,
    compiled_plan: CompiledPlan,
    canary: CanaryAcceptance,
    guard_subject: CutoverSubject,
    guard_receipt: CutoverGuardReceipt,
) -> ProductionActivationComposition
```

The returned `ProductionActivationComposition` must contain an exact
`WriterCutoverController` and durable Canary evidence readback operations. No
in-memory fallback or test double is selected by the CLI. A missing or wrongly
typed factory is a fail-closed composition error.

## Execution sequence

```text
closed input parsing
→ owner authorization/receipt identity check
→ typed Plan/Canary/Subject/Guard construction
→ live WriterCutoverController composition
→ ProductionActivationFacade.preflight()
→ [only with --execute] ProductionActivationFacade.execute()
→ Activation Receipt and default-writer readback
```

Without `--execute`, the command writes only its declared preflight report. With
`--execute`, the existing controller remains the sole writer-transition and
Activation Receipt authority. The CLI does not edit SQLite by hand, select V6.1
as a fallback, or implement rollback.

Example:

```powershell
py -3.13 scripts/run_v8_production_activation.py `
  --input <activation-bundle.json> `
  --composition-factory <live_module>:factory `
  --output <activation-report.json>

# Mutation is a separate, explicit invocation after the exact final approval:
py -3.13 scripts/run_v8_production_activation.py `
  --input <activation-bundle.json> `
  --composition-factory <live_module>:factory `
  --output <activation-report.json> `
  --execute
```

This slice supplies the typed contract and CLI boundary. The repository still
requires a concrete live composition factory that binds the production GitHub,
Store, V6.1 readback, Guard, and Canary controls before a real `--execute` can
be authorized. Until that factory and its live evidence are present, the
fail-closed result is intentional; no production mutation is attempted.

## Concrete Phase 5 factory boundary

`D:\Workstation\github-work-orchestrator\scripts\gwo_v8_production_factory.py`
is the bounded production composition implementation. Its
`ProductionCompositionConfig` is immutable and must explicitly provide the
activation `store_path`, `rollback_lineage`, and the package/install roots
needed by the existing resolver-backed live Guard host. The module-level
`factory` is intentionally unconfigured; an omitted configuration never
selects a Store or an in-memory test double.

`ProductionCompositionConfig.canary_repository` is the optional, host-owned
identity for the named Canary. When omitted, it defaults to
`target_repository`. When configured, `CanaryAcceptance.repository` must match
it exactly; for the Phase 5 external consumer this is
`NOirBRight/gwo-v8-canary`. The factory binds the durable Canary manifest
readback to that repository. An omitted configuration for an external Canary,
or any configured/input mismatch, fails closed with
`FACTORY_IDENTITY_DISJOINT`.

The authorization and receipt carry the same `canary_repository`, so the
external source is not merely a factory-local setting. Transition records also
persist that repository alongside the package digest, manifest reference, and
Evidence references. Pending, cut-over, replay, drain, and rollback readback
must preserve the exact value; a record from another Canary source cannot be
resumed or treated as the authorized activation.

This identity is independent of the writer target: `compiled_plan.repository`,
`CutoverSubject.repository`, and `CutoverGuardReceipt.repository` remain bound
to `target_repository` and are not mirrored or relaxed for the external
Canary. The Facade accepts the named Canary only when its package repository
and every typed Evidence readback match `CanaryAcceptance.repository`.

The factory keeps `store_generation` separate from
`target_writer_generation`. If the configured Store already has a
`v8_writer_generations` row for the target repository, its value must exactly
match the authorized `store_generation` from the configuration/Guard subject.
A matching provisioned Store-genesis row is accepted read-only; any other
writer identity stops composition with
`FACTORY_STORE_WRITER_IDENTITY_INVALID`. The factory does not reconstruct,
overwrite, or otherwise mutate the row. This specifically prevents a Store
identity such as `store:v8:production:20260817T205916Z` from being treated as
`v8-generation-1`; the cutover transition owns that later lineage step.

Composition validates the Store through an immutable SQLite read and binds the
real GitHub controls to one client and one unopened `LocalPlanPublication`.
It obtains the authoritative read-only Guard and legacy read through
`load_production_cutover_guard`; if the installed V3 resolver-backed host is
unavailable, composition raises `FACTORY_GUARD_LIVE_UNAVAILABLE` rather than
returning a synthetic GO validator. No activation, rollback, tag, or Store
write is performed by composition or by the zero-write CLI preflight.
