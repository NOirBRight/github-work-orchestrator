# GWO V8 Beta3 Production Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Every behavior change follows superpowers:test-driven-development.

**Goal:** Build the reviewed, one-off Beta3 production bootstrap that attests every Guard fact from explicit read-only sources, freezes the seven exact current-main readbacks, evaluates the exact Guard with zero external calls, and publishes only the canonical report/evidence pair.

**Architecture:** Keep the already audited fail-closed runner as the filesystem, preflight, Store, and exclusive-publication shell. Add a capability-separated in-process Attestor that performs two complete observations and returns a nonce-bound `AttestedCutoverBundle`; pass only frozen ports to exact-main `CutoverGuardSources` and `install_cutover_guard`. Retain source probes and local handles in a separate `BootstrapLease`, revalidate them around Guard and publication, and map collision/drift/unavailable/blocker outcomes without ever fabricating absence or activation authority.

**Tech Stack:** Python 3.13, stdlib dataclasses/typing/hashlib/secrets/sqlite3/subprocess/ctypes, existing `gwo_v8._canonical`, existing exact-main Cutover Guard types, pytest, Ruff, PowerShell on Windows, Git/GitHub CLI, and Paseo read commands. No new package dependency and no GitHub Actions.

## Global Constraints

- Repository: `NOirBRight/github-work-orchestrator`.
- Production checkout: `D:\Workstation\github-work-orchestrator`.
- Fixed source commit: `5de34bdaee45f0aba44077a8d1d3e3ed8293f237`.
- Fixed source tree: `104ee822dbfb494d33d56b8ccf54092d9d1d9c86`.
- Control branch: `gwo-control`; target branch: `main`.
- Source writer: `v6.1`; target writer: `v8`.
- Fresh Store generation: `store:v8:production:20260809T081500Z`.
- Fresh Store SHA-256: `afff1078e7a65fb8acccde28fee78fab3cf2278db9dd6548f5ef96a882076b98`.
- Fresh receipt SHA-256: `46814d166c857e3d7f847b7da6f3da5b39c394b42402b2f1d2cdd61d78ce7781`.
- Evidence root: `D:\gwo-release-evidence\2026-08-09-gwo-v8-beta3-production-cutover`.
- The only production output names are `beta3-live-guard-report.json` and `beta3-live-guard-evidence.json`.
- Any different repository, commit, tree, branch, generation, Store, receipt, package identity, installed surface, output directory, or runbook digest is a different subject and requires a new reviewed run.
- ADR-0015 is a cooperative single-host boundary. Do not add signatures, signing keys, certificates, or a capability broker.
- The Attestor is the only phase allowed to perform Legacy, control, Paseo, Runtime-registry, Store, config, or package source reads.
- Guard receives only exact frozen current-main types and performs zero external calls.
- Do not construct `ProductionPlanControlStartHost`, `ArtifactStore`, `RuntimeGateway`, a repository implementation, or a content client.
- Do not call `production_legacy_writer_control`, `_production_legacy_execution_readback`, `coordination_mutex`, `StoreV8OwnershipControl`, `install_github_plan_control_start`, or `ProductionPlanControlStartHost.start`.
- Missing or untrusted facts produce `UNAVAILABLE`, exit `3`; never substitute `[]`, `None`, `True`, a constant writer value, or a projection digest.
- Valid authoritative blockers produce `NO_GO`, exit `2`, and publish the canonical pair.
- Output collision or observed drift produces `REFUSED`, exit `1`, preserves every existing byte, and never adopts, repairs, overwrites, renames over, or deletes residue.
- Only seven passing checks plus a valid receipt may produce `GO`, exit `0`; the report must still say `activation_performed=false`, every mutation flag must be false, and V6.1 remains the writer.
- Beta3 evidence is not an activation token. This plan does not stop/restore V6.1, transfer writer authority, activate/admit V8, install packages, call a provider, publish a tag/release, or clean unrelated worktrees.
- GitHub Actions remain disabled. Verification is Local Verification Only.
- Never commit or push directly to `main`; never use `--no-verify`, force push, `git clean`, or worktree prune.
- All implementation and review subagents use `gpt-5.6-luna` with `reasoning=max`; at most five may be active concurrently.

## File Structure

| File | Responsibility |
|---|---|
| `scripts/run_beta3_live_guard.py` | Imported audited preflight/filesystem/Store/publication runner; later becomes the single CLI and orchestration façade. |
| `scripts/beta3_bootstrap_model.py` | Closed attempt/source/binding/attestation contracts, exact canonical digest rules, capability checks, component observation values, frozen ports, and `BootstrapLease`. |
| `scripts/beta3_legacy_attestor.py` | Complete GitHub Dispatch, Paseo Worker, cooperative-host process/lease, V2-vacuity, Legacy authority, and durable-observation derivation. |
| `scripts/beta3_control_ownership_attestor.py` | Fixed-OID control/fence/activation parsing, immutable Store ownership, Runtime registry/config, durable Store, compatibility, and package observations. |
| `scripts/beta3_replay_guard.py` | Exact `CutoverGuardSources` construction from frozen values, `install_cutover_guard`, report/bundle cross-validation, and zero-call audit surface. |
| `tests/test_beta3_live_guard_runner.py` | The imported 74-case runner regression suite plus end-to-end attestation/publication/outcome tests. |
| `tests/test_beta3_bootstrap_model.py` | Closed model, substitution, source binding, lease, and capability-surface tests. |
| `tests/test_beta3_legacy_attestor.py` | Legacy source completeness, double-read identity, active-work, vacuous decoder, and fail-closed producer tests. |
| `tests/test_beta3_control_ownership_attestor.py` | Control ledger, Store ownership, Runtime/config, compatibility/package, and drift tests. |
| `tests/test_beta3_replay_guard.py` | Exact-main GO/NO_GO replay, seven-read order, dynamic tripwires, and static call-surface tests. |
| `docs/superpowers/specs/2026-08-10-gwo-v8-beta3-production-bootstrap-readiness.md` | Final local verification commands, hashes, independent verdicts, open-finding count, and explicit production HOLD/READY state. |

## Execution Topology

```text
Task 1 (audited baseline import)
  -> Task 2 (shared contracts)
       -> Wave P: Task 3 || Task 4 || Task 5
       -> Task 6 (serial integration)
       -> Task 7 (parallel independent reviews, one consolidated fix wave)
```

- Tasks 3, 4, and 5 start from the reviewed Task 2 commit and have disjoint write sets.
- Each Wave P implementer commits only its assigned module and matching test file. The coordinator integrates commits in task-number order, then runs all three focused suites together.
- A task reviewer reads a generated review package and returns both spec and quality verdicts before the task is marked complete. Reviewers never edit code.
- Do not overlap Task 6 with Wave P; it owns the runner integration and cross-module tests.

---

### Task 1: Import the Audited Fail-Closed Runner Baseline

**Files:**
- Create: `scripts/run_beta3_live_guard.py`
- Create: `tests/test_beta3_live_guard_runner.py`

**Interfaces:**
- Consumes: the scratch files whose SHA-256 values are fixed below.
- Produces: importable `RunnerConfig`, `RunnerError`, `preflight(...)`, `run(...)`, `main(...)`, immutable Store read port, local input/publication leases, and exclusive two-file publication behavior with 74 green cases.

- [ ] **Step 1: Verify the two audited source hashes before copying**

Run:

```powershell
$Scratch = 'D:\Workstation\github-work-orchestrator\.codex-tmp\gwo-beta3-live-guard-runner'
Get-FileHash "$Scratch\run_beta3_live_guard.py" -Algorithm SHA256
Get-FileHash "$Scratch\test_run_beta3_live_guard.py" -Algorithm SHA256
```

Expected:

```text
run_beta3_live_guard.py       8204626157F61388A1A9F44AF0C491EC9197C42FDEB5484F8CBEF7750888B724
test_run_beta3_live_guard.py  16139C9E44AEBFFA4D105EE0FC067BBBF13547FD996B4F243D09C8A568ABEC29
```

Stop with `BASELINE_HASH_MISMATCH` if either value differs.

- [ ] **Step 2: Copy the audited files without changing runner behavior**

Run:

```powershell
$Scratch = 'D:\Workstation\github-work-orchestrator\.codex-tmp\gwo-beta3-live-guard-runner'
Copy-Item -LiteralPath "$Scratch\run_beta3_live_guard.py" -Destination 'scripts\run_beta3_live_guard.py'
Copy-Item -LiteralPath "$Scratch\test_run_beta3_live_guard.py" -Destination 'tests\test_beta3_live_guard_runner.py'
```

- [ ] **Step 3: Bind the copied test to this worktree rather than the production checkout**

Change only the import roots at the top of `tests/test_beta3_live_guard_runner.py`:

```python
REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = REPO_ROOT / "scripts" / "run_beta3_live_guard.py"
EXACT_SCRIPTS = REPO_ROOT / "skills" / "orchestrator" / "scripts"
```

Keep the runner bytes unchanged in this task. This is provenance-preserving import of already audited behavior, not a new behavior slice.

- [ ] **Step 4: Verify collection still expands to 74 cases**

Run:

```powershell
py -3.13 -B -m pytest --collect-only -q -p no:cacheprovider tests\test_beta3_live_guard_runner.py
```

Expected: `74 tests collected`.

- [ ] **Step 5: Run the imported regression suite**

Run:

```powershell
py -3.13 -B -m pytest -q -p no:cacheprovider --basetemp 'C:\tmp\gwo-beta3-bootstrap-task1' tests\test_beta3_live_guard_runner.py
```

Expected: `74 passed` and no production path is opened because every execution case uses `_fixture_config(tmp_path)` and injected dependencies.

- [ ] **Step 6: Run syntax and lint checks**

Run:

```powershell
py -3.13 -B -c "import ast; from pathlib import Path; [ast.parse(Path(p).read_text(encoding='utf-8'), filename=p) for p in ['scripts/run_beta3_live_guard.py','tests/test_beta3_live_guard_runner.py']]; print('AST_OK')"
py -3.13 -B -m ruff check --no-cache scripts\run_beta3_live_guard.py tests\test_beta3_live_guard_runner.py
```

Expected: `AST_OK` and Ruff exits `0`.

- [ ] **Step 7: Commit the baseline**

```powershell
git add scripts/run_beta3_live_guard.py tests/test_beta3_live_guard_runner.py
git commit -m "test: import beta3 guard runner baseline"
```

---

### Task 2: Add Closed Attempt, Source-Binding, Bundle, and Lease Contracts

**Files:**
- Create: `scripts/beta3_bootstrap_model.py`
- Create: `tests/test_beta3_bootstrap_model.py`

**Interfaces:**
- Consumes: exact-main `CutoverSubject`, the seven readback classes, `CutoverReadbackBundle`, `canonical_bytes`, `digest_bytes`, and `digest_value`.
- Produces:

```python
class BootstrapError(RuntimeError):
    code: str
    detail: str

@dataclass(frozen=True)
class AttemptIdentity:
    run_id: str
    challenge_nonce: str
    repository: str
    evidence_root: str
    cutover_subject_digest: str
    runner_sha256: str
    attestor_sha256: str

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        repository: str,
        evidence_root: str,
        cutover_subject_digest: str,
        runner_sha256: str,
        attestor_sha256: str,
        nonce_factory: Callable[[int], str] = secrets.token_hex,
    ) -> "AttemptIdentity": ...

@dataclass(frozen=True)
class SourceRecord:
    role: str
    locator: str
    repository: str
    read_mode: str
    identity: tuple[tuple[str, str], ...]
    content_sha256: str
    readback_digest: str | None
    producer_sha256: str

    @property
    def digest(self) -> str: ...

    def canonical(self) -> dict[str, object]: ...

@dataclass(frozen=True)
class SourceObservation:
    record: SourceRecord
    canonical_payload: bytes
    complete: bool

@dataclass(frozen=True)
class FieldBinding:
    target: str
    source_record_digests: tuple[str, ...]
    derivation: str

@dataclass(frozen=True)
class WriterAuthorityObservation:
    writer_generation: str
    record_id: str
    authority_state: str
    activation_id: str | None
    legacy_stopped: bool
    source_record_digests: tuple[str, ...]

@dataclass(frozen=True)
class ComponentObservation:
    readbacks: tuple[tuple[str, object], ...]
    source_records: tuple[SourceRecord, ...]
    field_bindings: tuple[FieldBinding, ...]
    writer_authority: WriterAuthorityObservation | None = None

    def canonical(self) -> dict[str, object]: ...

@dataclass(frozen=True)
class AttestedCutoverBundle:
    schema: str
    attempt: AttemptIdentity
    subject: CutoverSubject
    legacy: LegacyReadback
    durable_state: DurableStateReadback
    writer_fence: WriterFenceReadback
    ownership: OwnershipReadback
    compatibility: CompatibilityPathReadback
    runtime: RuntimePreflightReadback
    packages: PackageReadback
    source_records: tuple[SourceRecord, ...]
    field_bindings: tuple[FieldBinding, ...]
    attestation_digest: str

    @classmethod
    def create(
        cls,
        *,
        attempt: AttemptIdentity,
        subject: CutoverSubject,
        components: tuple[ComponentObservation, ...],
    ) -> "AttestedCutoverBundle": ...

    def validate(self) -> None: ...
    def cutover_bundle(self) -> CutoverReadbackBundle: ...

class FrozenReadPort:
    def __init__(self, value: object, *, expected_args: tuple[object, ...]) -> None: ...
    def read(self, *args: object, **kwargs: object) -> object: ...

class BootstrapLease:
    def __init__(
        self,
        *,
        expected_records: tuple[SourceRecord, ...],
        probes: tuple[Callable[[], SourceRecord], ...],
        local_assertions: tuple[Callable[[], None], ...],
        closers: tuple[Callable[[], None], ...],
    ) -> None: ...
    def assert_stable(self) -> None: ...
    def close(self) -> None: ...
```

- [ ] **Step 1: Write RED tests for attempt identity and source records**

Add tests equivalent to:

```python
def test_attempt_identity_requires_nonce_subject_and_code_bindings():
    with pytest.raises(BootstrapError) as error:
        AttemptIdentity(
            run_id="beta3-prod-001",
            challenge_nonce="ab" * 15,
            repository="owner/repo",
            evidence_root=r"D:\evidence",
            cutover_subject_digest="1" * 64,
            runner_sha256="2" * 64,
            attestor_sha256="3" * 64,
        )
    assert error.value.code == "ATTEMPT_IDENTITY_INVALID"


def test_source_record_identity_is_closed_sorted_and_digest_bound():
    record = SourceRecord(
        role="control.writer",
        locator="github://owner/repo/gwo-control/.gwo-v8/writer-transition.json",
        repository="owner/repo",
        read_mode="GET_AT_OID",
        identity=(("blob_oid", "b" * 40), ("commit_oid", "a" * 40)),
        content_sha256="c" * 64,
        readback_digest=None,
        producer_sha256="d" * 64,
    )
    assert record.digest == digest_value(record.canonical())
```

- [ ] **Step 2: Run the attempt/source tests and verify RED**

Run:

```powershell
py -3.13 -B -m pytest -q -p no:cacheprovider tests\test_beta3_bootstrap_model.py -k 'attempt_identity or source_record'
```

Expected: collection/import fails because `beta3_bootstrap_model.py` does not exist.

- [ ] **Step 3: Implement the minimal closed scalar contracts**

Use exact-type checks (`type(value) is ...`), lowercase 64-hex validation, at least 32 lowercase hexadecimal nonce characters, unique sorted identity keys, exact tuple storage, and fixed-key `canonical()` projections. `AttemptIdentity.create(...)` must call an injected `nonce_factory(16)` that defaults to `secrets.token_hex`; it must not accept a caller-supplied nonce in production.

Use these digest rules:

```python
SourceRecord.digest = digest_value(SourceRecord.canonical())
AttemptIdentity.digest = digest_value(AttemptIdentity.canonical())
```

- [ ] **Step 4: Run the attempt/source tests and verify GREEN**

Run the command from Step 2. Expected: selected tests pass.

- [ ] **Step 5: Write RED tests for complete field binding and substitution resistance**

Build a valid exact-main fixture bundle and assert all of these independently fail with `ATTESTATION_INVALID`:

```python
@pytest.mark.parametrize(
    "substitution",
    ("run_id", "challenge_nonce", "repository", "evidence_root", "subject"),
)
def test_attested_bundle_rejects_attempt_or_subject_substitution(valid_bundle, substitution):
    forged = substitute(valid_bundle, substitution)
    with pytest.raises(BootstrapError) as error:
        forged.validate()
    assert error.value.code == "ATTESTATION_INVALID"


def test_attested_bundle_rejects_missing_or_unknown_field_binding(valid_bundle):
    forged = replace(valid_bundle, field_bindings=valid_bundle.field_bindings[:-1])
    with pytest.raises(BootstrapError) as error:
        forged.validate()
    assert error.value.code == "ATTESTATION_INVALID"
```

The exact binding targets are every top-level canonical field of `subject` and every top-level canonical field of all seven readbacks, including each `readback_digest`. Nested package identities and Runtime selectors are covered by the `packages.source_packages`, `packages.installed_packages`, and `runtime.selectors` targets. No unknown target is accepted.

- [ ] **Step 6: Run the attested-bundle tests and verify RED**

Run:

```powershell
py -3.13 -B -m pytest -q -p no:cacheprovider tests\test_beta3_bootstrap_model.py -k 'attested_bundle'
```

Expected: fails because `AttestedCutoverBundle.create(...)` and validation are absent.

- [ ] **Step 7: Implement `ComponentObservation` and `AttestedCutoverBundle`**

Implement `AttestedCutoverBundle.create(...)` so it:

1. requires exact current-main types for subject and all seven values;
2. verifies every inner `readback_digest` against the canonical body without that member;
3. requires `attempt.repository == subject.repository` and `attempt.cutover_subject_digest == digest_value(subject.canonical())`;
4. requires ordered, unique `SourceRecord.digest` values and bindings that refer only to those records;
5. requires exactly the complete binding-target set described in Step 5;
6. requires every `SourceRecord.producer_sha256 == attempt.attestor_sha256`;
7. computes `attestation_digest = digest_value(canonical_without_attestation_digest())`.

Its `cutover_bundle()` method returns one exact `CutoverReadbackBundle(schema="gwo.cutover-readback-bundle.v1", ...)` and never serializes an intermediate production file.

- [ ] **Step 8: Run bundle tests and verify GREEN**

Run the command from Step 6. Expected: selected tests pass.

- [ ] **Step 9: Write RED tests for frozen ports, capability rejection, and lease drift**

Add:

```python
def test_frozen_port_returns_one_exact_value_and_rejects_wrong_arguments(valid_legacy):
    port = FrozenReadPort(valid_legacy, expected_args=("owner/repo",))
    assert port.read("owner/repo") is valid_legacy
    assert port.read("owner/repo") is valid_legacy
    with pytest.raises(BootstrapError):
        port.read("other/repo")


def test_capability_check_rejects_any_mutator_surface():
    class Unsafe:
        def read(self):
            return object()
        def compare_and_swap(self):
            raise AssertionError("must not be called")
    with pytest.raises(BootstrapError) as error:
        require_read_only_surface(Unsafe(), required_method="read")
    assert error.value.code == "UNSAFE_SOURCE_CAPABILITY"


def test_bootstrap_lease_maps_changed_source_record_to_input_drift(valid_record):
    lease = BootstrapLease(
        expected_records=(valid_record,),
        probes=(lambda: replace(valid_record, content_sha256="e" * 64),),
        local_assertions=(),
        closers=(),
    )
    with pytest.raises(BootstrapError) as error:
        lease.assert_stable()
    assert error.value.code == "LIVE_INPUT_DRIFT"
```

The forbidden surface set is exactly:

```python
{
    "start", "stop", "restore", "drain", "write", "publish",
    "compare_and_swap", "compare_and_swap_ref", "activate", "advance",
    "install", "prepare", "command", "events", "put", "delete", "unlink",
}
```

- [ ] **Step 10: Implement ports and lease, then verify GREEN**

`FrozenReadPort` stores only the exact value and expected positional arguments, defines only `read`, and rejects keyword arguments. `BootstrapLease.assert_stable()` compares complete `SourceRecord.canonical()` values and executes local assertions; `close()` runs closers once in reverse order. It never deletes or repairs a path.

Run:

```powershell
py -3.13 -B -m pytest -q -p no:cacheprovider tests\test_beta3_bootstrap_model.py
```

Expected: all model tests pass.

- [ ] **Step 11: Commit the shared contract**

```powershell
git add scripts/beta3_bootstrap_model.py tests/test_beta3_bootstrap_model.py
git commit -m "feat: bind beta3 bootstrap attestations"
```

---

### Task 3: Implement the Authoritative Legacy Observation Lane

**Files:**
- Create: `scripts/beta3_legacy_attestor.py`
- Create: `tests/test_beta3_legacy_attestor.py`

**Interfaces:**
- Consumes: `AttemptIdentity`, `WriterAuthorityObservation`, `SourceRecord`, `FieldBinding`, `ComponentObservation`, exact `LegacyReadback`, and narrow sources exposing only `read(...)`.
- Produces:

```python
@dataclass(frozen=True)
class LegacySourceSet:
    dispatches: object
    workers: object
    processes: object
    decoder: object | None

class LegacyAttestor:
    def __init__(self, sources: LegacySourceSet) -> None: ...

    def observe(
        self,
        *,
        subject: CutoverSubject,
        attempt: AttemptIdentity,
        writer: WriterAuthorityObservation,
    ) -> ComponentObservation: ...

def production_legacy_sources(
    *,
    command_runner: Callable[[tuple[str, ...]], bytes],
    producer_sha256: str,
) -> LegacySourceSet: ...
```

- [ ] **Step 1: Write RED tests for complete source authority**

Use independent fake readers for Dispatch, Worker, and process/lease observations. Add:

```python
def test_legacy_rejects_forged_typed_readback_without_source_records(subject, attempt, writer):
    attestor = LegacyAttestor(LegacySourceSet(
        dispatches=FakeSource(records=()),
        workers=FakeSource(records=()),
        processes=FakeSource(records=()),
    ))
    with pytest.raises(BootstrapError) as error:
        attestor.observe(subject=subject, attempt=attempt, writer=writer)
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("source", ("dispatches", "workers", "processes"))
def test_legacy_requires_complete_enumeration_and_identity(source, valid_sources, subject, attempt, writer):
    broken = replace_source(valid_sources, source, complete=False)
    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(broken).observe(subject=subject, attempt=attempt, writer=writer)
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"
```

- [ ] **Step 2: Run the source-authority tests and verify RED**

```powershell
py -3.13 -B -m pytest -q -p no:cacheprovider tests\test_beta3_legacy_attestor.py -k 'forged or complete_enumeration'
```

Expected: import/collection fails because the module is absent.

- [ ] **Step 3: Implement the three narrow production readers**

Each class exposes only `read` and returns one immutable observation plus one `SourceRecord`:

1. `GitHubDispatchSnapshotReader` runs the existing read-only GraphQL snapshot query, requires all Issue/PR `pageInfo.hasNextPage is False`, rejects a missing connection/base OID, normalizes every Dispatch record, and retains the canonical full response digest. It never calls a GitHub mutation or a moving-content write helper.
2. `PaseoWorkerInventoryReader` runs exactly `paseo ls --global --all --label orch.repository=<repo> --label orch.role=worker --json`, requires one non-empty unique identity per row, and runs exactly `paseo inspect <id> --json` for every returned identity. It rejects incomplete, duplicate, uninspectable, or repository/role-mismatched rows.
3. `CooperativeHostProcessReader` runs the fixed read-only PowerShell/CIM inventory query for `ProcessId`, `ParentProcessId`, `CreationDate`, `ExecutablePath`, and `CommandLine`; it captures the complete canonical response and identifies matching V6.1 integration/delivery processes by exact repository path and command tokens. Missing fields or an unparseable complete inventory is unavailable.

For live sources without an immutable cursor, set `read_mode="COMPLETE_DOUBLE_READ"` and use the full canonical observation digest in `identity=(("observation_digest", digest),)`. The runner, not this lane, will require observation A and B equality.

- [ ] **Step 4: Write RED tests for derivation semantics**

Add tests that assert:

```python
def test_empty_complete_legacy_observation_is_authoritative_and_decoder_is_vacuous(
    subject, attempt, writer, valid_empty_sources
):
    observed = LegacyAttestor(valid_empty_sources).observe(
        subject=subject, attempt=attempt, writer=writer
    )
    readback = dict(observed.readbacks)["legacy"]
    assert readback.authority_state == "authoritative_quiescent"
    assert readback.v2_execution_refs == ()
    assert readback.v2_execution_state == "none"
    assert readback.original_decoder_readable is True


def test_active_legacy_fact_is_not_silently_erased(
    subject, attempt, writer, sources_with_decoded_active_dispatch
):
    observed = LegacyAttestor(sources_with_decoded_active_dispatch).observe(
        subject=subject, attempt=attempt, writer=writer
    )
    readback = dict(observed.readbacks)["legacy"]
    assert readback.authority_state == "active"
    assert readback.active_dispatches == ("dispatch:17",)


def test_nonempty_v2_refs_without_original_decoder_proof_are_unavailable(
    subject, attempt, writer, sources_with_undecoded_active_dispatch
):
    with pytest.raises(BootstrapError) as error:
        LegacyAttestor(sources_with_undecoded_active_dispatch).observe(
            subject=subject, attempt=attempt, writer=writer
        )
    assert error.value.code == "LEGACY_SOURCE_UNAVAILABLE"
```

Use separate fixtures for the second and third tests: the honest active-fact projection test supplies a valid original-decoder proof; the unavailable test omits it.

- [ ] **Step 5: Implement exact Legacy derivation**

Implement these rules without fallback values:

```text
legacy_stopped == true
    -> authority_state = stopped
else any active dispatch, active Worker, or integration lease owner
    -> authority_state = active
else writer_generation == v6.1 and all complete observations are empty
    -> authority_state = authoritative_quiescent
else
    -> LEGACY_SOURCE_UNAVAILABLE
```

- `active_dispatches` is the unique sorted set of current executable, non-terminal V2 Dispatch references. Unknown status is unavailable; no row is omitted by default.
- `active_workers` is the unique sorted set of non-archived/non-closed Worker identities after exact inspect readback.
- `integration_lease_owner` is the one exact matching integration process identity; zero matching rows yields `None` only because the complete process observation proves absence; multiple owners are contradictory and unavailable.
- Empty V2 refs produce `v2_execution_state="none"` and `original_decoder_readable=True` as a field binding with derivation `vacuous_empty_reference_set`.
- Non-empty refs require a supplied original read-only decoder proof for every ref. Missing, partial, mismatched, or effectful decoder proof is unavailable.
- `durable_state_digest` is `digest_value(...)` over the complete canonical Legacy authority envelope containing the writer/fence source digests, full normalized Dispatch snapshot, full inspected Worker inventory, complete process/lease observation, V2 refs/states, and decoder proof. It is never a digest of `LegacyReadback.canonical()`.
- `readback_digest` is the exact-main digest of the typed body without `readback_digest`.

Create a `FieldBinding` for every Legacy canonical field. Bind derived values to the exact underlying record digests; bind `readback_digest` with derivation `canonical_digest`.

- [ ] **Step 6: Write and pass identity/substitution/capability tests**

Add parameterized cases for changed GitHub response digest, Paseo inspect identity, process creation identity, writer/fence record digest, equal projected values from different records, and an injected source exposing any forbidden method. The expected errors are `LIVE_INPUT_DRIFT` when comparing two complete observations and `UNSAFE_SOURCE_CAPABILITY` at construction.

Run:

```powershell
py -3.13 -B -m pytest -q -p no:cacheprovider tests\test_beta3_legacy_attestor.py
```

Expected: all Legacy tests pass and every fake mutation counter remains zero.

- [ ] **Step 7: Commit the Legacy lane**

```powershell
git add scripts/beta3_legacy_attestor.py tests/test_beta3_legacy_attestor.py
git commit -m "feat: attest beta3 legacy authority"
```

---

### Task 4: Implement Control, Ownership, Runtime, and Static Input Attestation

**Files:**
- Create: `scripts/beta3_control_ownership_attestor.py`
- Create: `tests/test_beta3_control_ownership_attestor.py`

**Interfaces:**
- Consumes: Task 2 contracts, exact current-main readback classes, existing pure `ProductionPathScanner`, `RuntimeConfigurationReader`, `ReadOnlyPackageValidator`, and fixed runner configuration/receipt values.
- Produces:

```python
@dataclass(frozen=True)
class ControlOwnershipSourceSet:
    control: object
    runtime_registry: object
    runtime_config: object
    local_inputs: object

class ControlOwnershipAttestor:
    def __init__(self, sources: ControlOwnershipSourceSet) -> None: ...

    def observe(
        self,
        *,
        config: object,
        subject: CutoverSubject,
        attempt: AttemptIdentity,
    ) -> ComponentObservation: ...

def production_control_ownership_sources(
    *,
    command_runner: Callable[[tuple[str, ...]], bytes],
    producer_sha256: str,
) -> ControlOwnershipSourceSet: ...
```

The returned component contains `durable_state`, `writer_fence`, `ownership`, `compatibility`, `runtime`, and `packages`, plus one non-null `WriterAuthorityObservation` for Task 3.

- [ ] **Step 1: Write RED tests for exact fixed-OID control reads**

Add tests that require the reader to:

```python
def test_control_reads_every_blob_at_one_fixed_oid(
    control_fixture, config, subject, attempt
):
    observation = ControlOwnershipAttestor(control_fixture.sources).observe(
        config=config, subject=subject, attempt=attempt
    )
    assert observation.writer_authority is not None
    assert control_fixture.calls == [
        ("read_ref", subject.repository, "gwo-control"),
        ("read_at_oid", subject.repository, control_fixture.oid, ".gwo-v8/writer-transition.json"),
        ("read_at_oid", subject.repository, control_fixture.oid, ".gwo/v8/active-plan.json"),
        ("read_at_oid", subject.repository, control_fixture.oid, ".gwo-v8/legacy-writer-fence.json"),
    ]


def test_control_rejects_missing_record_instead_of_initial_writer_fallback(
    control_fixture, config, subject, attempt
):
    control_fixture.writer_bytes = None
    with pytest.raises(BootstrapError) as error:
        ControlOwnershipAttestor(control_fixture.sources).observe(
            config=config, subject=subject, attempt=attempt
        )
    assert error.value.code == "WRITER_FENCE_SOURCE_UNAVAILABLE"
```

Also parameterize noncanonical bytes, unknown/missing keys, repository mismatch, blob/OID mismatch, duplicate records, bad record IDs, fork/cycle/orphan lineage, current-pointer mismatch, wrong generation, non-null activation, and missing predecessor binding.

- [ ] **Step 2: Run control tests and verify RED**

```powershell
py -3.13 -B -m pytest -q -p no:cacheprovider tests\test_beta3_control_ownership_attestor.py -k 'control or writer_fence'
```

Expected: import/collection fails because the module is absent.

- [ ] **Step 3: Implement the narrow GitHub control reader and ledger validator**

The reader issues only GitHub API reads: capture the exact `gwo-control` head OID, then fetch the three paths at that OID. It validates canonical bytes and retains repository, branch, commit OID, path, blob OID, byte SHA-256, and selected record identities in `SourceRecord`s.

Reuse the existing closed transition machine semantics from `gwo_v8.plan_control_github` without constructing `GitHubPlanRepository` or a content client. Under the current ledger schema, an authoritative pre-activation V6.1 selection must be an explicit current record whose legal lineage resolves to `kind="rollback"`, `status="rolled_back"`, `writer_generation="v6.1"`, and `activation_id is None`; `record_id="initial-writer"` and a missing file are unavailable. Validate `.gwo/v8/active-plan.json` receipt/predecessor lineage and require the selected record's plan binding to match the authoritative active plan lineage.

Construct:

```python
WriterFenceReadback(
    repository=subject.repository,
    writer_generation="v6.1",
    authority_state="authoritative",
    record_id=selected.record_id,
    activation_id=None,
    control_ref_digest=digest_value(control_binding),
    readback_digest=digest_value(body_without_digest),
)
```

Validate the legacy fence's exact schema/events/final stopped state and place `legacy_stopped` plus all control record digests into `WriterAuthorityObservation`.

- [ ] **Step 4: Write RED tests for immutable ownership and honest blockers**

Add:

```python
@pytest.mark.parametrize(
    ("table", "expected"),
    (
        ("v8_admissions", ("admission:1",)),
        ("v8_attempts", ("attempt:1",)),
        ("v8_integration_leases", "lease-owner"),
        ("v8_resource_claims", ("claim:resource:1",)),
        ("runtime_registry", ("runtime:agent:1",)),
    ),
)
def test_ownership_reports_active_facts_instead_of_marking_them_unavailable(
    ownership_fixture, table, expected
):
    observed = ownership_fixture.observe_with(table)
    readback = dict(observed.readbacks)["ownership"]
    assert expected in readback.canonical().values()


def test_ownership_uses_only_immutable_read_only_sqlite(ownership_fixture):
    observed = ownership_fixture.observe()
    assert observed is not None
    assert ownership_fixture.sqlite_calls == [
        (("mode", "ro"), ("immutable", "1"))
    ]
    assert ownership_fixture.write_tripwire.calls == []
```

Parameterize wrong Store path/hash/schema/receipt/generation, new sidecar, duplicate/cross-linked rows, absent Runtime-registry provenance, and registry identity drift as unavailable or `LIVE_INPUT_DRIFT` as appropriate.

- [ ] **Step 5: Implement one coherent Store ownership observation**

Open the fixed Store through a URI equivalent to:

```python
sqlite3.connect(f"file:{quoted_path}?mode=ro&immutable=1", uri=True)
```

After the existing receipt/schema/integrity/generation/file-identity/sidecar gates, issue one read transaction and enumerate:

- `v8_admissions` whose state is not `consumed` or `abandoned`;
- `v8_attempts` whose state is not `verified` or `terminal`;
- the exact `holder` from `v8_integration_leases`;
- every `v8_resource_claims` row with its `resource_key`, admission, and attempt links.

Read the separate complete Runtime registry and append stable `runtime:<identity>` references. Encode Store claims as `claim:<resource_key>` references so the existing `writer_and_lease` check blocks. Do not convert active facts into unavailable results. Compute the exact typed digest and bind every field to Store and Runtime source records.

- [ ] **Step 6: Write RED tests for Runtime config and static readers**

Test the exact path `C:\Users\noirb\.orch\config.json`, raw-byte/file identity binding, all five selectors in this order:

```python
("coordinator", "worker", "recovery_worker", "review_primary", "review_strong")
```

Reject missing/malformed/noncanonical config, mapping/profile digest mismatch, path replacement, and source hash drift. Test that compatibility and package reads are bound to the fixed commit/tree and all `.agents`, `.codex`, `.claude` package files and that no install/copy/replace function is called.

- [ ] **Step 7: Implement the remaining exact readbacks and source records**

- Construct `DurableStateReadback` with the runner-owned immutable Store logic and validated receipt; do not accept an injected preformed durable port.
- Construct `RuntimeConfiguration` only from the exact raw config, then use `RuntimeConfigurationReader.read(...)`; retain raw hash, file identity, resolved Profile digests, and configuration digest.
- Use `ProductionPathScanner(config.repository_root).read(subject)` only after fixed commit/tree validation and bind every scanned module file to the local lease.
- Use `ReadOnlyPackageValidator(config.repository_root, surfaces).read(subject)` and bind source plus all installed manifest/content files; do not install or synchronize anything.

- [ ] **Step 8: Run the full control/ownership suite and verify GREEN**

```powershell
py -3.13 -B -m pytest -q -p no:cacheprovider --basetemp 'C:\tmp\gwo-beta3-bootstrap-task4' tests\test_beta3_control_ownership_attestor.py
```

Expected: all tests pass, all mutation counters remain zero, active-state fixtures return exact typed readbacks, and missing provenance remains unavailable.

- [ ] **Step 9: Commit the control/ownership lane**

```powershell
git add scripts/beta3_control_ownership_attestor.py tests/test_beta3_control_ownership_attestor.py
git commit -m "feat: attest beta3 control ownership"
```

---

### Task 5: Implement Replay-Only Exact-Main Guard Evaluation

**Files:**
- Create: `scripts/beta3_replay_guard.py`
- Create: `tests/test_beta3_replay_guard.py`

**Interfaces:**
- Consumes: one validated `AttestedCutoverBundle`, `FrozenReadPort`, exact current-main `CutoverGuardSources`, and exact `install_cutover_guard`.
- Produces:

```python
@dataclass(frozen=True)
class ReplayResult:
    report: CutoverGuardReport
    subject: CutoverSubject
    readback_bundle: CutoverReadbackBundle
    attestation_digest: str

def evaluate_attested_bundle(bundle: AttestedCutoverBundle) -> ReplayResult: ...
```

- [ ] **Step 1: Write RED GO and NO_GO replay tests**

Add:

```python
def test_replay_go_uses_exact_bundle_and_zero_external_calls(valid_attested_bundle, tripwires):
    result = evaluate_attested_bundle(valid_attested_bundle)
    assert type(result.report) is CutoverGuardReport
    assert result.report.decision == "GO"
    assert tuple(check.check_id for check in result.report.checks) == EXPECTED_CHECK_IDS
    assert result.report.receipt is not None
    assert tripwires.external_calls == []


def test_replay_no_go_collects_blocker_and_still_has_zero_external_calls(active_bundle, tripwires):
    result = evaluate_attested_bundle(active_bundle)
    assert result.report.decision == "NO_GO"
    assert result.report.receipt is None
    assert {blocker.code for blocker in result.report.blockers} == {"CUTOVER_V2_ACTIVE"}
    assert tripwires.external_calls == []
```

Tripwire `subprocess.run/Popen`, `socket.socket/create_connection`, `sqlite3.connect`, GitHub/Paseo readers, Runtime/provider methods, Gateway/Artifact construction or writes, install/copy/replace, transition/CAS, and activation methods.

- [ ] **Step 2: Run replay tests and verify RED**

```powershell
py -3.13 -B -m pytest -q -p no:cacheprovider tests\test_beta3_replay_guard.py -k 'replay_go or replay_no_go'
```

Expected: import/collection fails because the module is absent.

- [ ] **Step 3: Implement exact frozen source composition**

Construct ports with the exact expected arguments:

```python
sources = CutoverGuardSources(
    legacy=FrozenReadPort(bundle.legacy, expected_args=(bundle.subject.repository,)),
    durable_state=FrozenReadPort(
        bundle.durable_state, expected_args=(bundle.subject.repository,)
    ),
    writer_fence=FrozenReadPort(
        bundle.writer_fence, expected_args=(bundle.subject.repository,)
    ),
    ownership=FrozenReadPort(
        bundle.ownership, expected_args=(bundle.subject.repository,)
    ),
    compatibility=FrozenReadPort(
        bundle.compatibility, expected_args=(bundle.subject,)
    ),
    runtime=FrozenReadPort(
        bundle.runtime,
        expected_args=(
            bundle.subject.repository,
            bundle.subject.required_runtime_selectors,
        ),
    ),
    packages=FrozenReadPort(bundle.packages, expected_args=(bundle.subject,)),
)
host = install_cutover_guard(sources=sources)
report = host.check(bundle.subject)
```

Call `bundle.validate()` before composition. Do not call direct live resolvers, `JsonCutoverReadPorts`, `CutoverGuard` directly, `validate_activation`, or any source probe.

- [ ] **Step 4: Cross-validate exact Guard output**

Require:

- `type(report) is CutoverGuardReport`;
- exact schema `gwo.cutover-guard.v1`;
- exact repository and `subject_digest`;
- exact seven check IDs and observed digests corresponding to the attested readbacks;
- exact `readback_digest` over all seven canonical readbacks in Guard order;
- receipt present only for `GO` and absent for `NO_GO`;
- receipt digests and source-writer generation match exact current-main semantics.

Any mismatch raises `BootstrapError("LIVE_GUARD_INVALID", ...)` and cannot be published.

- [ ] **Step 5: Add and pass the static call-surface test**

Parse `scripts/beta3_replay_guard.py` with `ast` and fail on imports/calls containing:

```text
subprocess, socket, sqlite3, requests, urllib, github, paseo, provider,
RuntimeGateway, ProductionPlanControlStartHost, ArtifactStore,
install_github_plan_control_start, transition, activation,
compare_and_swap, publish, put, write, start, stop, restore, drain
```

Allow only stdlib dataclass/typing, `beta3_bootstrap_model`, `gwo_v8.cutover_guard` type imports, and `gwo_v8.plan_control_host.install_cutover_guard`.

Run:

```powershell
py -3.13 -B -m pytest -q -p no:cacheprovider tests\test_beta3_replay_guard.py
```

Expected: all replay and static tests pass with zero external calls.

- [ ] **Step 6: Commit replay evaluation**

```powershell
git add scripts/beta3_replay_guard.py tests/test_beta3_replay_guard.py
git commit -m "feat: replay beta3 guard from frozen inputs"
```

---

### Task 6: Integrate Double Attestation, Leases, Outcomes, and Two-File Publication

**Files:**
- Modify: `scripts/run_beta3_live_guard.py`
- Modify: `tests/test_beta3_live_guard_runner.py`
- Modify only if integration exposes a real contract defect: `scripts/beta3_bootstrap_model.py`
- Modify only if integration exposes a real contract defect: `tests/test_beta3_bootstrap_model.py`

**Interfaces:**
- Consumes: `LegacyAttestor`, `ControlOwnershipAttestor`, `AttestedCutoverBundle.create`, `BootstrapLease`, and `evaluate_attested_bundle`.
- Produces:

```python
@dataclass(frozen=True)
class ExecutionDependencies:
    control_ownership_attestor: object
    legacy_attestor: object
    replay_guard: Callable[[AttestedCutoverBundle], ReplayResult]

class ProductionBootstrapAttestor:
    def attest(
        self,
        config: RunnerConfig,
        attempt: AttemptIdentity,
    ) -> tuple[AttestedCutoverBundle, BootstrapLease, dict[str, object]]: ...
```

- [ ] **Step 1: Write RED tests for CLI attempt creation and zero-write preflight**

Add:

```python
def test_execute_requires_operator_run_id_but_preflight_does_not(tmp_path):
    config = _fixture_config(tmp_path)
    assert runner.main([], config=config, git_runner=_git_runner_factory(config), stdout=io.StringIO()) == 0
    assert runner.main(["--execute"], config=config, git_runner=_git_runner_factory(config), stdout=io.StringIO()) == 1


def test_attempt_is_created_after_preflight_and_nonce_is_os_generated(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(runner.secrets, "token_hex", lambda count: events.append(("nonce", count)) or "ab" * count)
    result = runner.run(
        config=config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=fake_dependencies(events),
    )
    assert events.index(("preflight",)) < events.index(("nonce", 16)) < events.index(("attest",))
    assert result["status"] == "GO"
```

`--run-id` accepts one exact non-empty operator-visible string; `--execute` without it is refused before any production source read. Preflight never generates a nonce or creates an artifact.

- [ ] **Step 2: Replace arbitrary live Guard dependencies with attestation-aware dependencies**

Remove the production use of `ProductionReaders`, `_OperatorLegacyReadPort`, caller-provided preformed readbacks, `ProductionPlanControlStartHost`, and `ProductionCutoverReadAdapterResolver`. Preserve fixture injection only through exact `ExecutionDependencies` whose two attestors return complete `ComponentObservation` values and whose replay function receives a validated attested bundle.

`_production_dependencies(config, producer_sha256)` constructs only `production_control_ownership_sources(...)`, `production_legacy_sources(...)`, the two Attestors, and `evaluate_attested_bundle`. It does not instantiate a content client, Gateway, Artifact store, repository, RuntimeGateway, or transition control.

Add `test_fixed_production_subject_rejects_dependency_injection`: when every fixed production identity/path/hash in `RunnerConfig` matches `DEFAULT_CONFIG`, passing non-null `dependencies`, `guard_factory`, `control_reader`, or `package_reader` returns `UNAVAILABLE` with `DEPENDENCY_INJECTION_FORBIDDEN` before source access. Temporary fixture configurations remain injectable for deterministic unit tests; the production CLI has no dependency-injection option.

- [ ] **Step 3: Write RED tests for observation A/B equality and source unavailability**

Add parameterized tests over every source role:

```python
@pytest.mark.parametrize("role", ALL_SOURCE_ROLES)
def test_each_source_identity_drift_refuses_before_guard_or_publication(tmp_path, role):
    dependencies, calls = attested_dependencies(drift_on_second_observation=role)
    result = runner.run(
        config=config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )
    assert result["status"] == "REFUSED"
    assert result["exit_code"] == 1
    assert result["code"] == "LIVE_INPUT_DRIFT"
    assert "guard" not in calls
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


@pytest.mark.parametrize("role", ALL_SOURCE_ROLES)
def test_each_unavailable_source_is_exit_three_and_never_go(tmp_path, role):
    dependencies, calls = attested_dependencies(unavailable_role=role)
    result = runner.run(
        config=config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )
    assert result["status"] == "UNAVAILABLE"
    assert result["exit_code"] == 3
    assert "guard" not in calls
    assert not config.report_path.exists()
```

Include same projected readbacks with changed source identity; comparison is over complete component canonical observations, not only typed fields.

- [ ] **Step 4: Implement `ProductionBootstrapAttestor.attest`**

For observation A and B independently:

1. call control/ownership observation;
2. pass its exact `WriterAuthorityObservation` to Legacy observation;
3. merge the exact six plus one readbacks, ordered source records, and field bindings;
4. compare complete A/B canonical component observations;
5. create one `AttestedCutoverBundle` only after equality;
6. build `BootstrapLease` with remote source probes and local assertions/closers.

The lease must cover fresh/rollback/prior Stores, receipt, source/runbook/attestor modules, production source tree, package manifests/content, all installed package files, Runtime config, any local process/registry bytes, output-parent components, control OID/blobs, complete GitHub snapshot identity, complete Paseo identities, and Runtime registry identity. Extend the imported `_InputLease` to retain the local files and reject reparse points; use source probes for remote/live observations.

- [ ] **Step 5: Write RED tests for Guard and publication boundary revalidation**

Parameterize drift at:

```text
before Guard
immediately after Guard
before report create
before evidence create
after both outputs
```

Expected at every boundary: `REFUSED`, exit `1`, no GO. If report was already exclusively created when later drift appears, preserve that report as failed-attempt residue and do not create/adopt evidence.

Add `test_guard_report_and_attested_bundle_mismatch_is_unavailable` and require exit `3`, no publication.

- [ ] **Step 6: Integrate replay under the combined leases**

Use this serial order inside `run(...)`:

```text
preflight
-> AttemptIdentity.create
-> attestation A
-> attestation B
-> AttestedCutoverBundle freeze
-> enter publication/local/source leases
-> assert stable
-> evaluate_attested_bundle
-> assert stable
-> validate ReplayResult against attestation
-> assert stable
-> exclusive report
-> assert stable
-> exclusive evidence
-> assert stable and revalidate both owned outputs
```

Do not call any production source from `beta3_replay_guard.py`; all revalidation calls stay in the runner/lease phase outside Guard.

- [ ] **Step 7: Embed the complete attestation in both canonical outputs**

The report adds exact keys for:

```text
attempt_identity
attestation
attestation_digest
readback_bundle
source_records
field_bindings
activation_performed=false
mutation flags all false
```

The evidence adds the same attempt/attestation identity, exact report digest and file identity, before/after complete source observations, fixed subject, all retained input identities, safety flags, decision, and exit result. Continue using newline-terminated canonical outer JSON. The inner exact-main values retain exact-main compact canonical semantics.

Do not create an attestation file, bundle file, marker, journal, staging file, manifest, temporary SQLite database, Gateway store, or Artifact directory in the production evidence root.

- [ ] **Step 8: Preserve the four distinct outcome paths**

Implement this mapping in one place:

```python
if error.code in {"OUTPUT_COLLISION", "LIVE_INPUT_DRIFT"}:
    return _result("REFUSED", 1, code=error.code, detail=error.detail)
if isinstance(error, BootstrapError):
    return _result("UNAVAILABLE", 3, code=error.code, detail=error.detail)
if replay.report.decision == "NO_GO":
    publish_pair()
    return _result(
        "NO_GO",
        2,
        decision="NO_GO",
        report_path=_path_text(config.report_path),
        evidence_path=_path_text(config.evidence_path),
    )
publish_pair()
return _result(
    "GO",
    0,
    decision="GO",
    report_path=_path_text(config.report_path),
    evidence_path=_path_text(config.evidence_path),
)
```

No generic exception path may return `GO`. Existing output checks remain before dependency construction; `_resume_existing_outputs` stays unreachable and no recovery/adoption helper is added.

- [ ] **Step 9: Update the imported 74 tests to the attested dependency seam**

Replace `_stable_dependencies(...)` with a helper that builds exact component observations, a valid outer attestation, stable lease probes, and real replay evaluation. Preserve all 74 behavioral assertions. Change old operator-snapshot acceptance expectations to `UNAVAILABLE`; retain the fail-closed collision and no-resume assertions.

Run:

```powershell
py -3.13 -B -m pytest --collect-only -q -p no:cacheprovider tests\test_beta3_live_guard_runner.py
py -3.13 -B -m pytest -q -p no:cacheprovider --basetemp 'C:\tmp\gwo-beta3-bootstrap-task6-runner' tests\test_beta3_live_guard_runner.py
```

Expected: at least the original 74 cases plus the new integration cases; all pass.

- [ ] **Step 10: Run all five focused suites together**

```powershell
py -3.13 -B -m pytest -q -p no:cacheprovider --basetemp 'C:\tmp\gwo-beta3-bootstrap-focused' `
  tests\test_beta3_bootstrap_model.py `
  tests\test_beta3_legacy_attestor.py `
  tests\test_beta3_control_ownership_attestor.py `
  tests\test_beta3_replay_guard.py `
  tests\test_beta3_live_guard_runner.py
```

Expected: all tests pass; production outputs remain absent because tests use only temporary fixture roots.

- [ ] **Step 11: Run static safety checks**

Run:

```powershell
py -3.13 -B -c "import ast; from pathlib import Path; files=list(Path('scripts').glob('beta3_*.py'))+[Path('scripts/run_beta3_live_guard.py')]; [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in files]; print('AST_OK')"
py -3.13 -B -m ruff check --no-cache scripts\run_beta3_live_guard.py scripts\beta3_*.py tests\test_beta3_*.py
git grep -n -E 'production_legacy_writer_control|_production_legacy_execution_readback|coordination_mutex|StoreV8OwnershipControl|install_github_plan_control_start|ProductionPlanControlStartHost|ArtifactStore|RuntimeGateway' -- scripts\beta3_*.py scripts\run_beta3_live_guard.py
```

Expected: `AST_OK`, Ruff exits `0`, and grep finds only explicit forbidden-name constants/tests or explanatory error strings; no executable call/import edge exists.

- [ ] **Step 12: Commit the integrated bootstrap**

```powershell
git add scripts/run_beta3_live_guard.py scripts/beta3_bootstrap_model.py tests/test_beta3_live_guard_runner.py tests/test_beta3_bootstrap_model.py
git commit -m "feat: integrate beta3 production bootstrap"
```

---

### Task 7: Independent Review, Local Verification, and Readiness Hash Freeze

**Files:**
- Create: `docs/superpowers/specs/2026-08-10-gwo-v8-beta3-production-bootstrap-readiness.md`
- Modify only through the one permitted consolidated fix wave: files identified by independent findings.

**Interfaces:**
- Consumes: the complete branch diff from merge base `5de34bdaee45f0aba44077a8d1d3e3ed8293f237`, the approved design, this plan, TDD reports, focused test logs, and the SDD ledger.
- Produces: independent verdicts `SPEC GO`, `QUALITY GO`, `TDD VALID`, `OPEN 0`; final file hashes; and an explicit statement that production remains HOLD until exact run identity approval and zero-write preflight.

- [ ] **Step 1: Run focused verification from a clean branch state**

```powershell
git status --short --branch
py -3.13 -B -m pytest -q -p no:cacheprovider --basetemp 'C:\tmp\gwo-beta3-bootstrap-final-focused' tests\test_beta3_*.py
py -3.13 -B -m ruff check --no-cache scripts\run_beta3_live_guard.py scripts\beta3_*.py tests\test_beta3_*.py
py -3.13 -B -c "import ast; from pathlib import Path; files=[Path('scripts/run_beta3_live_guard.py'),*Path('scripts').glob('beta3_*.py'),*Path('tests').glob('test_beta3_*.py')]; [ast.parse(p.read_text(encoding='utf-8'), filename=str(p)) for p in files]; print('AST_OK')"
```

Expected: clean except intended tracked changes before commit, all focused tests pass, Ruff clean, and `AST_OK`.

- [ ] **Step 2: Run the repository-local suite with the required convergence archive root**

```powershell
$env:GWO_CONVERGENCE_ARCHIVE_ROOT = 'D:\gwo-convergence-archive\20260804T185544Z'
py -3.13 -B -m pytest -q -p no:cacheprovider --basetemp 'C:\tmp\gwo-beta3-bootstrap-final-full'
```

Expected baseline: no regression relative to `1962 passed, 1 skipped`; the previously observed convergence-receipt case passes with the environment variable set. Any new failure caused by this branch blocks readiness.

- [ ] **Step 3: Dispatch four independent Luna Max review axes in parallel**

Use `gpt-5.6-luna`, `reasoning=max`, one read-only reviewer per axis:

1. **Spec reviewer:** map every approved design acceptance criterion and non-goal to code/tests; return only `SPEC GO` or explicit findings.
2. **Quality/security reviewer:** inspect capability separation, complete source binding, TOCTOU, output crash/collision behavior, and forbidden call graph; return only `QUALITY GO` or severity-ranked findings.
3. **TDD auditor:** inspect commit order, RED outputs, GREEN outputs, covering test names, and report files for every new behavior; return only `TDD VALID` or exact missing evidence.
4. **Open-findings adjudicator:** compare all prior bootstrap/security/fail-closed findings and the three current reviews; return `OPEN 0` only if no load-bearing item remains.

Each reviewer receives the generated review package, approved design, implementation plan, and SDD ledger. Reviewers do not access production systems and do not edit files.

- [ ] **Step 4: Apply at most one consolidated final fix wave if reviews find issues**

Dispatch one Luna Max implementer with the complete finding list and disjoint test requirements. Require a failing regression test for every behavior fix, rerun focused suites, commit once, then dispatch one scoped read-only re-review. Residual load-bearing findings keep production HOLD; do not perform a second unbounded fix wave.

- [ ] **Step 5: Freeze reviewed hashes and write the readiness record**

Run:

```powershell
Get-FileHash scripts\run_beta3_live_guard.py -Algorithm SHA256
Get-FileHash scripts\beta3_bootstrap_model.py -Algorithm SHA256
Get-FileHash scripts\beta3_legacy_attestor.py -Algorithm SHA256
Get-FileHash scripts\beta3_control_ownership_attestor.py -Algorithm SHA256
Get-FileHash scripts\beta3_replay_guard.py -Algorithm SHA256
Get-FileHash tests\test_beta3_live_guard_runner.py -Algorithm SHA256
Get-FileHash tests\test_beta3_bootstrap_model.py -Algorithm SHA256
Get-FileHash tests\test_beta3_legacy_attestor.py -Algorithm SHA256
Get-FileHash tests\test_beta3_control_ownership_attestor.py -Algorithm SHA256
Get-FileHash tests\test_beta3_replay_guard.py -Algorithm SHA256
git rev-parse HEAD
git rev-parse HEAD^{tree}
```

Write the exact commands, outputs, review-package SHA-256, file hashes, branch HEAD/tree, verdicts, and `OPEN 0` into `docs/superpowers/specs/2026-08-10-gwo-v8-beta3-production-bootstrap-readiness.md`. End the record with:

```text
Implementation gate: READY only if SPEC GO / QUALITY GO / TDD VALID / OPEN 0.
Production execution: HOLD pending owner approval of the exact run_id and evidence root, followed by zero-write preflight.
Activation: NOT AUTHORIZED; V6.1 remains writer.
```

- [ ] **Step 6: Commit readiness evidence**

```powershell
git add docs/superpowers/specs/2026-08-10-gwo-v8-beta3-production-bootstrap-readiness.md
git commit -m "docs: record beta3 bootstrap readiness"
```

- [ ] **Step 7: Stop before production execution**

Do not run either of these inside implementation SDD:

```powershell
py -3.13 -B scripts\run_beta3_live_guard.py
py -3.13 -B scripts\run_beta3_live_guard.py --execute --run-id 'example-not-authorized'
```

The first command touches fixed production inputs even though it is zero-write; the second performs the one production evidence publication. Present the final hashes and a concrete proposed `run_id` to the owner. Only a subsequent exact approval may authorize zero-write production preflight, followed by a separately checked single `--execute` when both final outputs are absent.

---

## Final Acceptance Checklist

- [ ] Every Guard fact has at least one valid `FieldBinding` to an included `SourceRecord`.
- [ ] All seven values are exact current-main immutable types and all intrinsic digests verify.
- [ ] Observation A and B compare complete source identities and canonical payloads.
- [ ] Missing/truncated/untrusted Legacy, control, ownership, Runtime, Store, config, package, or path facts return exit `3` without output.
- [ ] Honest active work remains an exact readback and produces Guard `NO_GO`, exit `2`, with both files.
- [ ] Replay uses exact `CutoverGuardSources` plus `install_cutover_guard` and performs zero external calls.
- [ ] Local handles and remote identities are checked before/after Guard, around both publications, and at final validation.
- [ ] Only the report and evidence can be created in the production evidence directory.
- [ ] Existing or racing output residue is preserved and returns `OUTPUT_COLLISION`/exit `1`.
- [ ] GO/NO_GO/REFUSED/UNAVAILABLE remain distinct and no exception becomes GO.
- [ ] Both outputs bind attempt, nonce, subject, source records, field bindings, attestation, report identity, and mutation-false flags.
- [ ] `activation_performed=false`; no V6.1 stop/restore, writer transition, V8 activation/admission, provider action, package installation, tag, or release occurs.
- [ ] Focused tests, AST, Ruff, full local pytest, and independent Luna Max review are clean.
- [ ] Final verdicts are exactly `SPEC GO`, `QUALITY GO`, `TDD VALID`, and `OPEN 0`.
- [ ] Production remains HOLD until the owner approves the exact run identity and the zero-write production preflight passes.
