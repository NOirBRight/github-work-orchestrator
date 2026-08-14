# GWO V8 Release Subject Boundary Fix Wave Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the self-referential Beta3 production identity constants with an externally bound, held canonical release-subject manifest; separate the Git root-tree OID from the audited source digest; restore complete Task 2 RED/GREEN evidence; and make the exact-main Phase 4 rehearsal locally executable without activating V8.

Historical note: the initial draft named the manifest `gwo-v8-release-subject.v1`. The implementation described by this plan replaces it with the closed `gwo-v8-release-subject.v2`; v1 is retained only as historical context and is not a compatibility path.

**Architecture:** Add a focused `scripts/beta3_release_subject.py` deep module that owns the closed manifest schema, canonical-body digest, no-follow held-file loader, and exclusive-create generator seam. The live Guard builds one immutable `RunnerConfig` from that manifest and passes the same `ReleaseSubject` object to the control attestor. Git and existing source/Store/Runtime/control readbacks remain authoritative, while reports and evidence carry the manifest digest as a separate identity.

**Tech Stack:** Python 3.13, frozen dataclasses, canonical JSON, SHA-256, Windows no-follow/held-handle file identity, `subprocess` Git readback, pytest, Ruff, Python AST checks, existing Beta3 attestors and V8 cutover contracts.

## Global Constraints

- Use `gpt-5.6-luna` with reasoning `max` for every implementation or review subagent.
- Use SDD + TDD. Every behavior change has a recorded RED command, the named pre-fix failure, a minimal GREEN change, and a passing focused command.
- Use at most five parallel subagents, and never run two agents with overlapping write sets.
- GitHub CI is disabled. All checks in this plan run locally.
- V6.1 remains the only production writer until a separate Phase 5 owner authorization and durable Activation Receipt exist.
- The fixed production subject path is `EVIDENCE_ROOT / "gwo-v8-release-subject.json"`; no CLI path or subject-value override is permitted.
- The canonical manifest schema is exactly `gwo-v8-release-subject.v2`.
- The manifest digest is computed over the canonical body with `subject_digest` excluded; it is not a self-hash of the full manifest file.
- The manifest binds repository, canonical repository/evidence roots, `fresh_receipt_sha256`, `merged_main_sha`, `merged_main_git_tree`, `audited_source_tree_digest`, runner path/hash, the exact ordered four attestor paths/hashes/bundle, and reviewed-provenance path/hash.
- `fresh_receipt_sha256` is the SHA-256 of the exact raw canonical bytes at `EVIDENCE_ROOT / "fresh-store-exact-main-receipt.json"`; the runner binds that digest to the receipt's `source_main_sha == merged_main_sha` and `source_main_tree == merged_main_git_tree` checks.
- The runner carries the same subject-bound receipt/Git identities into its fixed configuration, passes the same typed subject to the control attestor, and uses the held runner/attestor/reviewed-provenance identities as the observer provenance boundary; the receipt digest does not replace observer provenance or either Git identity.
- The manifest is loaded and validated with no-follow/held-handle/byte identity before nonce creation, production dependency/source access, GitHub/Paseo/CIM, or output creation.
- Git readback remains authoritative. The manifest declares intended identity and never substitutes for `HEAD`, `HEAD^{tree}`, `origin/main`, Store, receipt, package, control, Runtime, legacy, or target readback.
- Production code no longer depends on self-naming `EXPECTED_HEAD`, `EXPECTED_TREE`, `PRODUCTION_SOURCE_COMMIT`, or `PRODUCTION_SOURCE_TREE`. Test injection remains available only through explicit non-production fixtures.
- `CutoverSubject.source_tree_digest` remains the 64-character audited source digest. `merged_main_git_tree` is the explicitly named 40-character Git-tree field used by Git and receipt validation; local checkout observations use `git_tree_oid`.
- The deterministic generator writes only the fixed subject file with exclusive-create semantics after exact canonical `main` is frozen. It does not create directories, staging files, SQLite, evidence reports, Git refs, provider actions, tags, or releases.
- Phase 4 report/evidence includes `release_subject_digest`. Phase 5 owner approval additionally names exact SHA, Git tree, run ID, evidence root, repository, target repository, and `v6.1 -> v8`.
- No activation, rollback, GitHub CI, provider action, tag, push, or release is executed in this fix wave.

---

## Write-set and execution topology

The first two tasks are sequential because Task 2 consumes Task 1 interfaces. After Task 2 is reviewed, Tasks 3 and 4 may run in parallel:

| Parallel lane | Write set | Dependency |
| --- | --- | --- |
| A | `scripts/run_beta3_live_guard.py`, `tests/test_beta3_live_guard_runner.py` | Task 2 |
| B | `scripts/beta3_control_ownership_attestor.py`, `tests/test_beta3_control_ownership_attestor.py` | Task 2 |
| C | `tests/test_v8_local_acceptance.py`, isolated TDD ledger | Task 2; no shared production file with A/B |
| D | review-only ignored reports | The code lane being reviewed |
| E | no production write; verification only | All code lanes complete |

Task 5 integrates lanes A and B and therefore is serial. Task 6 records the missing Task 2 RED/GREEN cycles using a detached `2feeaa6` worktree. Tasks 7 and 8 are serial; Tasks 9 and 10 then remain serial because they freeze the exact canonical `main`, generate the external manifest, and read it during the rehearsal.

---

### Task 1: Define the closed release-subject value and canonical digest

**Files:**
- Create: `scripts/beta3_release_subject.py`
- Create: `tests/test_beta3_release_subject.py`

**Interfaces:**
- Produces `ReleaseSubjectError(code: str, detail: str)`.
- Produces frozen `ReleaseFileIdentity(module: str, path: str, sha256: str)`.
- Produces frozen `ReviewedProvenanceIdentity(path: str, sha256: str)`.
- Produces frozen `ReleaseSubject` with fields `schema`, `repository`, `repository_root`, `evidence_root`, `fresh_receipt_sha256`, `merged_main_sha`, `merged_main_git_tree`, `audited_source_tree_digest`, `remote_ref`, `runner`, `attestors`, `attestor_bundle_sha256`, `reviewed_provenance`, and `subject_digest`.
- Produces `canonical_json_bytes(value: object) -> bytes`, `parse_release_subject(raw: bytes, expected_repository_root: Path, expected_evidence_root: Path) -> ReleaseSubject`, and `release_subject_digest(body: Mapping[str, object]) -> str`.

**Files and constants:**

```python
RELEASE_SUBJECT_SCHEMA = "gwo-v8-release-subject.v2"
RELEASE_SUBJECT_FILENAME = "gwo-v8-release-subject.json"
FRESH_RECEIPT_FILENAME = "fresh-store-exact-main-receipt.json"
REPOSITORY = "NOirBRight/github-work-orchestrator"
REMOTE_REF = "origin/main"
ATTESTOR_FILENAMES = (
    "beta3_bootstrap_model.py",
    "beta3_control_ownership_attestor.py",
    "beta3_legacy_attestor.py",
    "beta3_replay_guard.py",
)
```

These are filenames in the required order; each `attestors[*].module` uses the filename stem without `.py`, and each `attestors[*].path` uses the canonical path ending in that filename.

- [ ] **Step 1: Write the failing schema and digest tests.**

Add these exact test cases before implementing the module:

```python
def test_subject_digest_excludes_only_subject_digest(tmp_path: Path):
    repository_root = (tmp_path / "repository").resolve()
    evidence_root = (tmp_path / "evidence").resolve()
    body = {
        "schema": "gwo-v8-release-subject.v2",
        "repository": "NOirBRight/github-work-orchestrator",
        "repository_root": str(repository_root),
        "evidence_root": str(evidence_root),
        "fresh_receipt_sha256": "5" * 64,
        "merged_main_sha": "a" * 40,
        "merged_main_git_tree": "b" * 40,
        "audited_source_tree_digest": "c" * 64,
        "remote_ref": "origin/main",
        "runner": {
            "module": "run_beta3_live_guard",
            "path": str(repository_root / "scripts" / "run_beta3_live_guard.py"),
            "sha256": "d" * 64,
        },
        "attestors": [
            {
                "module": module_name.removesuffix(".py"),
                "path": str(repository_root / "scripts" / module_name),
                "sha256": digest,
            }
            for module_name, digest in zip(
                (
                    "beta3_bootstrap_model.py",
                    "beta3_control_ownership_attestor.py",
                    "beta3_legacy_attestor.py",
                    "beta3_replay_guard.py",
                ),
                ("e" * 64, "f" * 64, "1" * 64, "2" * 64),
                strict=True,
            )
        ],
        "attestor_bundle_sha256": "3" * 64,
        "reviewed_provenance": {
            "path": str(repository_root / "scripts" / "beta3_reviewed_provenance.json"),
            "sha256": "4" * 64,
        },
    }
    body_bytes = canonical_json_bytes(body)
    payload = {**body, "subject_digest": hashlib.sha256(body_bytes).hexdigest()}
    parsed = parse_release_subject(
        canonical_json_bytes(payload),
        expected_repository_root=repository_root,
        expected_evidence_root=evidence_root,
    )
    assert parsed.canonical_body() == body
    assert parsed.canonical() == payload


def test_subject_schema_rejects_extra_key_and_swapped_identity_domains(tmp_path: Path):
    payload = _canonical_fixture_payload(tmp_path)
    payload["unexpected"] = True
    with pytest.raises(ReleaseSubjectError) as extra:
        parse_release_subject(canonical_json_bytes(payload), tmp_path / "repository", tmp_path / "evidence")
    assert extra.value.code == "RELEASE_SUBJECT_SCHEMA_INVALID"

    swapped = _canonical_fixture_payload(tmp_path)
    swapped["merged_main_git_tree"], swapped["audited_source_tree_digest"] = (
        swapped["audited_source_tree_digest"],
        swapped["merged_main_git_tree"],
    )
    with pytest.raises(ReleaseSubjectError) as identity:
        parse_release_subject(canonical_json_bytes(swapped), tmp_path / "repository", tmp_path / "evidence")
    assert identity.value.code == "RELEASE_SUBJECT_SCHEMA_INVALID"
```

Define `_canonical_fixture_payload(tmp_path)` in the test file with the same thirteen body keys and the exact four attestor entries; it must not call production code or read a production path.

- [ ] **Step 2: Run the tests to prove RED.**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_release_subject.py
```

Expected: collection fails because `scripts/beta3_release_subject.py` does not exist. This is the missing-module RED, not a schema assertion failure.

- [ ] **Step 3: Write the minimal value implementation.**

Implement the frozen dataclasses with exact-type checks. `canonical_body()` returns exactly the thirteen body keys. `canonical()` returns the body plus `subject_digest`. `canonical_json_bytes()` uses `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)` followed by UTF-8 encoding and one LF. `release_subject_digest()` hashes the canonical bytes of the body. `parse_release_subject()` requires the exact top-level and nested key sets, lowercase 40/64-character digests, the ordered four attestors, exact `schema` and `remote_ref`, canonical paths equal to the two expected roots, and a matching body digest.

```python
def release_subject_digest(body: Mapping[str, object]) -> str:
    encoded = canonical_json_bytes(dict(body))
    return hashlib.sha256(encoded).hexdigest()


def parse_release_subject(
    raw: bytes,
    expected_repository_root: Path,
    expected_evidence_root: Path,
) -> ReleaseSubject:
    value = _decode_exact_canonical_object(raw)
    _validate_closed_shape(value, expected_repository_root, expected_evidence_root)
    body = dict(value)
    observed_digest = body.pop("subject_digest")
    if observed_digest != release_subject_digest(body):
        raise ReleaseSubjectError(
            "RELEASE_SUBJECT_DIGEST_MISMATCH",
            "subject_digest is not the digest of the canonical body",
        )
    return ReleaseSubject.from_canonical(value)
```

The helper names are private implementation details; the public functions and exact error codes above are the interface.

- [ ] **Step 4: Run the focused tests to prove GREEN.**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_release_subject.py
```

Expected: all schema, canonicalization, digest, length-domain, and closed-shape tests pass.

- [ ] **Step 5: Commit the value layer.**

```powershell
git add scripts/beta3_release_subject.py tests/test_beta3_release_subject.py
git commit -m "feat: define v8 release subject schema"
```

---
### Task 2: Add the held manifest loader and exclusive-create generator seam

**Files:**
- Modify: `scripts/beta3_release_subject.py`
- Create: `scripts/generate_beta3_release_subject.py`
- Modify: `tests/test_beta3_release_subject.py`
- Create: `tests/test_beta3_release_subject_generator.py`

**Interfaces:**
- Produces frozen `ReleaseSubjectBinding(subject: ReleaseSubject, manifest_path: Path, raw_bytes: bytes, identity: Mapping[str, object], handle: int)`.
- Produces `ReleaseSubjectBinding.assert_stable() -> None`, `close() -> None`, `__enter__() -> ReleaseSubjectBinding`, and `__exit__(exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None`.
- Produces `production_subject_path() -> Path` with no parameters.
- Produces `load_production_release_subject() -> ReleaseSubjectBinding` with no path parameters.
- Produces `load_release_subject_for_test(path: Path, expected_repository_root: Path, expected_evidence_root: Path, file_reader: Callable[[Path], tuple[bytes, Mapping[str, object]]] | None = None) -> ReleaseSubjectBinding`.
- Produces `generate_production_subject() -> ReleaseSubject` with no path or root parameters.
- Produces `write_production_subject_exclusive(subject: ReleaseSubject) -> ReleaseSubjectBinding` with no path parameter.
- Produces test-only `write_subject_for_test_exclusive(subject: ReleaseSubject, path: Path) -> ReleaseSubjectBinding`.
- `scripts/generate_beta3_release_subject.py` accepts no CLI options and invokes only `generate_production_subject()` followed by `write_production_subject_exclusive()`.

- [ ] **Step 1: Write failing loader, drift, and exclusive-create tests.**

Add tests with these exact behaviors:

```python
def test_production_loader_uses_one_fixed_path_and_rejects_absence(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(release_subject, "EVIDENCE_ROOT", Path(r"C:\tmp\gwo-subject-test-evidence"))
    with pytest.raises(ReleaseSubjectError) as error:
        release_subject.load_production_release_subject()
    assert error.value.code == "RELEASE_SUBJECT_UNAVAILABLE"


def test_binding_rejects_manifest_byte_replacement_after_first_read(tmp_path: Path):
    manifest = _write_valid_subject_fixture(tmp_path)
    binding = release_subject.load_release_subject_for_test(
        manifest,
        expected_repository_root=tmp_path / "repository",
        expected_evidence_root=tmp_path / "evidence",
    )
    original = manifest.read_bytes()
    manifest.write_bytes(
        original.replace(b'"' + b'a' * 40 + b'"', b'"' + b'b' * 40 + b'"', 1)
    )
    with pytest.raises(ReleaseSubjectError) as error:
        binding.assert_stable()
    assert error.value.code == "RELEASE_SUBJECT_DRIFT"


def test_exclusive_generator_does_not_replace_existing_subject(tmp_path: Path):
    subject = _valid_subject_value(tmp_path)
    path = tmp_path / "evidence" / "gwo-v8-release-subject.json"
    path.parent.mkdir()
    path.write_bytes(b"existing subject bytes\n")
    with pytest.raises(ReleaseSubjectError) as error:
        release_subject.write_subject_for_test_exclusive(subject, path)
    assert error.value.code == "RELEASE_SUBJECT_EXISTS"
    assert path.read_bytes() == b"existing subject bytes\n"
```

The test-only writer may be named `write_subject_for_test_exclusive`; the production writer must have no path argument. Add a Windows junction/reparse ancestor test using the existing Beta3 held-handle fixture helpers. It must expect `RELEASE_SUBJECT_PATH_INVALID` before JSON decoding.

- [ ] **Step 2: Run the loader tests to prove RED.**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_release_subject.py tests/test_beta3_release_subject_generator.py
```

Expected: collection or attribute failures identify the missing binding, fixed production loader, held-file stability, and exclusive-create interfaces. The current Guard has no external subject loader, so no current behavior can satisfy these tests.

- [ ] **Step 3: Implement the held boundary by reusing the existing safe primitives.**

Move or share the existing `_open_bound_handle`, `_read_held_bytes`, identity comparison, directory-component no-follow, and regular-file checks from `run_beta3_live_guard.py` into the focused module without weakening their Windows and non-Windows branches. The loader must retain the manifest descriptor in `ReleaseSubjectBinding`. It must validate raw bytes, not a second path read. `assert_stable()` must compare the held identity and raw bytes to a fresh no-follow observation. Use `O_CREAT|O_EXCL`/`CREATE_NEW` for the writer, flush and `fsync`, and leave an existing file untouched.

```python
def production_subject_path() -> Path:
    return EVIDENCE_ROOT / RELEASE_SUBJECT_FILENAME


def load_production_release_subject() -> ReleaseSubjectBinding:
    path = production_subject_path()
    raw, identity, handle = _read_held_regular_file(path, "RELEASE_SUBJECT_UNAVAILABLE")
    subject = _validate_manifest_and_observer_bytes(
        raw, identity, path, REPOSITORY_ROOT, EVIDENCE_ROOT
    )
    return ReleaseSubjectBinding(subject, path, raw, identity, handle)
```

`_validate_manifest_and_observer_bytes()` must hash the runner, all four attestors, and reviewed provenance through held canonical paths; it must compare their exact paths, modules, raw hashes, ordered bundle, and existing reviewed-provenance JSON. It must not call Git, read Store/receipt/package/Runtime inputs, create a nonce, or create an output.

- [ ] **Step 4: Implement the no-option deterministic generator.**

`generate_production_subject()` reads only fixed `REPOSITORY_ROOT` and `EVIDENCE_ROOT`, reads and hashes the fixed fresh receipt as canonical JSON, requires `origin/main == HEAD`, computes `HEAD^{tree}`, computes `audited_source_tree_digest = source_tree_digest(REPOSITORY_ROOT)`, hashes the ordered observer files, and constructs the subject. It re-reads the receipt and all repository/observer inputs before construction and refuses any byte or identity drift. It refuses a dirty status other than the existing `.codex-tmp` allowance. `write_production_subject_exclusive()` writes only the fixed subject path and validates it through the runtime loader.

```python
def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if arguments:
        return 1
    subject = generate_production_subject()
    binding = write_production_subject_exclusive(subject)
    try:
        print(subject.subject_digest)
        binding.assert_stable()
    finally:
        binding.close()
    return 0
```

- [ ] **Step 5: Run focused loader and generator tests to prove GREEN.**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_release_subject.py tests/test_beta3_release_subject_generator.py
```

Expected: the fixed path, canonical JSON, body digest, path/reparse rejection, observer hash/bundle checks, held-byte drift, exclusive-create, and no-option entry-point tests pass.

- [ ] **Step 6: Commit the held boundary.**

```powershell
git add scripts/beta3_release_subject.py scripts/generate_beta3_release_subject.py tests/test_beta3_release_subject.py tests/test_beta3_release_subject_generator.py
git commit -m "feat: add held external release subject"
```

---

### Task 3: Replace runner self-identity with the bound subject

**Files:**
- Modify: `scripts/run_beta3_live_guard.py`
- Modify: `tests/test_beta3_live_guard_runner.py`

**Interfaces:**
- `RunnerConfig` now has `merged_main_sha: str`, `merged_main_git_tree: str`, `audited_source_tree_digest: str`, `release_subject_digest: str`, and production-bound `expected_fresh_receipt_sha256: str`.
- `GitRunner` is a callable with signature `(args: list[str], *, cwd: Path, env: Mapping[str, str]) -> subprocess.CompletedProcess[str]`; `GuardFactory` is `(config: RunnerConfig, subject: object) -> object`; `ControlReader` is `() -> object`; and `PackageReader` is `(config: RunnerConfig) -> object`. These aliases match the existing runner call sites without creating a CLI surface.
- `ProductionBootstrapAttestor.__init__(*, control_ownership_attestor: object, legacy_attestor: object, subject_factory: Callable[[RunnerConfig, ReleaseSubject], CutoverSubject] | None = None) -> None`; the factory is accepted only by fixture construction.
- `ProductionBootstrapAttestor.attest(config: RunnerConfig, attempt: AttemptIdentity, release_subject: ReleaseSubject) -> tuple[AttestedCutoverBundle, BootstrapLease, dict[str, object]]`.
- `_default_subject_factory(config: RunnerConfig, release_subject: ReleaseSubject) -> CutoverSubject`.
- `run(config: RunnerConfig | None = None, *, execute: bool, run_id: str | None = None, git_runner: GitRunner = _default_git_runner, dependencies: ExecutionDependencies | None = None, guard_factory: GuardFactory | None = None, control_reader: ControlReader | None = None, package_reader: PackageReader | None = None) -> dict[str, object]`; `config=None` is the only production default and loads the fixed manifest.
- `main(argv: Sequence[str] | None = None, *, config: RunnerConfig | None = None, git_runner: GitRunner = _default_git_runner, dependencies: ExecutionDependencies | None = None, guard_factory: GuardFactory | None = None, control_reader: ControlReader | None = None, package_reader: PackageReader | None = None, stdout: TextIO | None = None) -> int` retains only `--execute` and `--run-id` CLI inputs.

- [ ] **Step 1: Write the production-order and identity-domain RED tests.**

Add these tests before changing the runner:

```python
def test_production_run_loads_subject_before_git_and_nonce(monkeypatch: pytest.MonkeyPatch):
    events: list[str] = []

    def missing_subject():
        events.append("subject")
        raise RunnerError("RELEASE_SUBJECT_UNAVAILABLE", "test manifest is absent")

    monkeypatch.setattr(run_guard, "load_production_release_subject", missing_subject)
    monkeypatch.setattr(run_guard, "_default_git_runner", lambda *args, **kwargs: events.append("git"))
    monkeypatch.setattr(run_guard.secrets, "token_hex", lambda *args: events.append("nonce"))
    result = run_guard.run(execute=True, run_id="subject-order-red")
    assert result["code"] == "RELEASE_SUBJECT_UNAVAILABLE"
    assert events == ["subject"]


def test_default_subject_keeps_git_tree_and_audited_digest_separate():
    config = _fixture_config(merged_main_sha="a" * 40, merged_main_git_tree="b" * 40, audited_source_tree_digest="c" * 64)
    manifest = _fixture_subject(merged_main_sha="a" * 40, merged_main_git_tree="b" * 40, audited_source_tree_digest="c" * 64)
    subject = run_guard._default_subject_factory(config, manifest)
    assert subject.source_commit == "a" * 40
    assert subject.source_tree_digest == "c" * 64
    assert config.merged_main_git_tree == "b" * 40
```

The first test monkeypatches `load_production_release_subject`, `_default_git_runner`, and `secrets.token_hex` exactly as shown. Before the fix, the current runner calls `_git_snapshot()` during preflight and creates its `AttemptIdentity` later, so the expected `events == ["subject"]` assertion fails with a Git call or the subject loader attribute is absent. The second test fails because the current `_default_subject_factory()` has no manifest argument and derives a fresh 64-character digest instead of the bound fixture digest.

- [ ] **Step 2: Run the runner RED tests.**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_live_guard_runner.py -k "production_run_loads_subject_before_git_and_nonce or default_subject_keeps_git_tree_and_audited_digest_separate"
```

Expected: RED for the missing loader-first boundary and the missing `merged_main_git_tree`/`audited_source_tree_digest` separation. Record the exact failure output in the fix-wave SDD ledger; do not replace it with a synthetic failure.

- [ ] **Step 3: Remove code-local production subject constants.**

Delete `EXPECTED_HEAD` and `EXPECTED_TREE` from the production identity path. Remove their use from `DEFAULT_CONFIG` and from `_is_fixed_production_subject()`/`_is_production_subject_gate()`; `DEFAULT_CONFIG` is no longer a production default and fixture execution uses the explicit `run_fixture()` path in Task 5. Preserve static Store/package/output validation and explicit fixture-only construction. Replace every production identity reference as follows:

```text
config.expected_head       -> config.merged_main_sha
config.expected_tree       -> config.merged_main_git_tree
subject.source_tree_digest -> release_subject.audited_source_tree_digest
```

Do not add a replacement commit/tree constant to either tracked observer module.

- [ ] **Step 4: Bind the manifest before any production effect.**

Implement the production `run(config=None, execute=True)` path so its first operational action is `load_production_release_subject()`. Build the immutable effective config from the subject, retain the returned binding, and close it only after report/evidence validation. The execution order must be:

```python
binding = load_production_release_subject()
try:
    binding.assert_stable()
    config = bind_runner_config_from_subject(binding.subject)
    preflight_result = preflight(config, git_runner=git_runner, authoritative_sources=not execute)
    binding.assert_stable()
    if execute:
        run_attested_guard_with_subject(config, binding.subject, binding, run_id)
finally:
    binding.close()
```

`run_attested_guard_with_subject()` must call `binding.assert_stable()` immediately before nonce creation, before `_production_dependencies`, before each attestation/replay boundary, and before creating the report/evidence descriptors. Dependency injection is rejected for the production bound config before source reads. Fixture tests may call a test-only runner helper with temporary config and injected readers; the production `main()` cannot select that helper.

- [ ] **Step 5: Update Git, receipt, and CutoverSubject construction.**

Use `merged_main_sha` for `HEAD`, `origin/main`, and receipt `source_main_sha`. Use `merged_main_git_tree` for `HEAD^{tree}` and receipt `source_main_tree`. Bind `expected_fresh_receipt_sha256` from `release_subject.fresh_receipt_sha256` and compare it with the exact receipt bytes. Pass `audited_source_tree_digest` to `CutoverSubject.source_tree_digest`. Add `release_subject_digest` to the preflight/result context.

```python
def _default_subject_factory(
    config: RunnerConfig,
    release_subject: ReleaseSubject,
) -> CutoverSubject:
    return CutoverSubject(
        repository=config.repository,
        control_branch=config.control_branch,
        target_branch=config.target_branch,
        source_writer_generation=config.source_writer_generation,
        target_writer_generation=config.target_writer_generation,
        store_generation=config.store_generation,
        source_commit=release_subject.merged_main_sha,
        source_tree_digest=release_subject.audited_source_tree_digest,
        production_entry_refs=PRODUCTION_ENTRY_REFS,
    )
```

- [ ] **Step 6: Run the runner GREEN tests and focused existing tests.**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_live_guard_runner.py -k "production_run_loads_subject_before_git_and_nonce or default_subject_keeps_git_tree_and_audited_digest_separate"
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_live_guard_runner.py
```

Expected: the new boundary tests and the existing runner suite pass, including dependency refusal before source/nonce, `git_runner` validation, lease drift, canonical provenance, and no activation flags.

- [ ] **Step 7: Commit the runner binding.**

```powershell
git add scripts/run_beta3_live_guard.py tests/test_beta3_live_guard_runner.py
git commit -m "fix: bind guard runner to external release subject"
```

---
### Task 4: Make the control attestor consume the same subject and separate Git-tree readback

**Files:**
- Modify: `scripts/beta3_control_ownership_attestor.py`
- Modify: `tests/test_beta3_control_ownership_attestor.py`

**Interfaces:**
- `ControlOwnershipAttestor.observe(*, config: object, subject: CutoverSubject, attempt: AttemptIdentity, release_subject: ReleaseSubject) -> ComponentObservation`.
- `_validate_config_subject(config: object, subject: CutoverSubject, release_subject: ReleaseSubject) -> None`.
- Local checkout canonical payload and identity use `git_tree_oid`, not `tree_digest`.
- `CompatibilityPathReadback.source_tree_digest` remains the 64-character audited source digest.

- [ ] **Step 1: Write the type-separation RED tests.**

Add exact tests for the existing production mismatch and the swapped domains:

```python
def test_default_subject_accepts_separate_git_tree_and_audited_digest():
    subject = _subject_with_source_tree_digest("c" * 64)
    config = _config_with_identity(
        merged_main_sha=subject.source_commit,
        merged_main_git_tree="b" * 40,
        audited_source_tree_digest="c" * 64,
    )
    release_subject = _release_subject(
        merged_main_sha=subject.source_commit,
        merged_main_git_tree="b" * 40,
        audited_source_tree_digest="c" * 64,
    )
    _validate_config_subject(config, subject, release_subject)


def test_swapping_git_tree_and_audited_source_digest_fails_before_readers():
    subject = _subject_with_source_tree_digest("b" * 40)
    config = _config_with_identity(
        merged_main_sha=subject.source_commit,
        merged_main_git_tree="c" * 64,
        audited_source_tree_digest="b" * 40,
    )
    release_subject = _release_subject(
        merged_main_sha=subject.source_commit,
        merged_main_git_tree="c" * 64,
        audited_source_tree_digest="b" * 40,
    )
    with pytest.raises(BootstrapError) as error:
        _validate_config_subject(config, subject, release_subject)
    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"
```

The first test is intentionally the real default-path contract: the current code compares `config.expected_tree` to `CutoverSubject.source_tree_digest` and requires `_HEX40`, so the 64-character audited digest produces the recorded `STATIC_INPUT_SOURCE_UNAVAILABLE` mismatch before any source reader. The second test proves the corrected validator rejects domain swapping rather than truncating or coercing values.

- [ ] **Step 2: Run the attestor RED tests.**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_control_ownership_attestor.py -k "default_subject_accepts_separate_git_tree_and_audited_digest or swapping_git_tree_and_audited_source_digest"
```

Expected: the first test fails because `_validate_config_subject()` does not accept `release_subject` and the current default path aliases the fields; the second test fails because the current fixture contract uses the 40-character `source_tree_digest` domain. Record the actual failure output.

- [ ] **Step 3: Remove production constants and add the shared subject parameter.**

Delete `PRODUCTION_SOURCE_COMMIT` and `PRODUCTION_SOURCE_TREE`. Update `ProductionBootstrapAttestor._observe_pair()` to pass the same `ReleaseSubject` to `ControlOwnershipAttestor.observe()`. Update `_validate_config_subject()` to compare the three exact identity lines:

```python
if config.merged_main_sha != release_subject.merged_main_sha:
    _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", "config merged_main_sha is not manifest-bound")
if config.merged_main_git_tree != release_subject.merged_main_git_tree:
    _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", "config merged_main_git_tree is not manifest-bound")
if config.audited_source_tree_digest != release_subject.audited_source_tree_digest:
    _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", "config audited source digest is not manifest-bound")
if subject.source_commit != release_subject.merged_main_sha:
    _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", "CutoverSubject source commit is not manifest-bound")
if subject.source_tree_digest != release_subject.audited_source_tree_digest:
    _fail("STATIC_INPUT_SOURCE_UNAVAILABLE", "CutoverSubject audited source digest is not manifest-bound")
```

Validate `_HEX40` for `merged_main_sha` and `merged_main_git_tree`; validate `_HEX64` for `audited_source_tree_digest` and `subject.source_tree_digest`.

- [ ] **Step 4: Rename local checkout tree fields and receipt comparisons.**

In `_LocalInputsSource`, emit:

```python
value = {
    "repository_root": str(root),
    "commit_oid": commit,
    "git_tree_oid": tree,
    "git_status_sha256": digest_bytes(status),
    "files": files,
}
```

Update `_validate_checkout_observation()` to compare `git_tree_oid` with `config.merged_main_git_tree`. Keep the compatibility scanner's `source_tree_digest` and all `CutoverSubject` fields bound to the 64-character audited digest. Update the fresh receipt validator to compare `source_main_tree` with `config.merged_main_git_tree`. Update test fixtures and assertions by field name, not by length coercion.

- [ ] **Step 5: Run the attestor GREEN and existing suites.**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_control_ownership_attestor.py -k "default_subject_accepts_separate_git_tree_and_audited_digest or swapping_git_tree_and_audited_source_digest"
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_control_ownership_attestor.py
```

Expected: the domain tests pass, the local checkout observation contains `git_tree_oid`, the compatibility readback contains the 64-character `source_tree_digest`, and all existing held-handle/reparse/capability/source-readback tests remain green.

- [ ] **Step 6: Commit the attestor boundary.**

```powershell
git add scripts/beta3_control_ownership_attestor.py tests/test_beta3_control_ownership_attestor.py
git commit -m "fix: separate git tree and audited source digest"
```

---

### Task 5: Bind provenance, leases, report/evidence, and CLI behavior

**Files:**
- Modify: `scripts/run_beta3_live_guard.py`
- Modify: `tests/test_beta3_live_guard_runner.py`

**Interfaces:**
- `_attested_report()` and `_attested_evidence()` include `release_subject_digest` and `release_subject_path`.
- `_validate_attested_report_value()` and `_validate_attested_evidence_value()` compare those fields to the held `ReleaseSubjectBinding`.
- `_lease_input_paths()` includes the fixed subject path.
- `build_parser()` accepts only `--execute` and `--run-id`.
- Test-only `run_fixture(config: RunnerConfig, binding: ReleaseSubjectBinding, *, execute: bool, run_id: str) -> dict[str, object]` is the only injected execution helper.

The held observer provenance is the runner raw hash, the ordered four attestor raw hashes/bundle, and the reviewed-provenance raw hash. `fresh_receipt_sha256` is a separate evidence-input identity: the production runner and control attestor must retain the same subject-bound digest and validate the receipt's raw bytes and source-main SHA/tree, without treating the receipt as observer provenance.

- [ ] **Step 1: Write the report, drift, and CLI RED tests.**

```python
def test_cli_has_no_subject_or_identity_override():
    parser = run_guard.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--subject", r"C:\tmp\other-subject.json"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--expected-head", "a" * 40])


def test_subject_drift_is_refused_before_report_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    binding = _fixture_binding(tmp_path)
    config = _fixture_config_for_binding(binding)
    report = config.report_path
    binding.assert_stable()
    binding.replace_for_test()
    result = run_guard.run_fixture(config, binding=binding, execute=True, run_id="drift-before-output")
    assert result["code"] == "RELEASE_SUBJECT_DRIFT"
    assert not report.exists()


def test_report_and_evidence_carry_external_subject_digest(tmp_path: Path):
    record = _run_fixture_guard_to_completion(tmp_path, run_id="report-subject-digest")
    assert record["report"]["release_subject_digest"] == record["subject"]["release_subject_digest"]
    assert record["evidence"]["release_subject_digest"] == record["subject"]["release_subject_digest"]
```

The current parser already rejects the two new flags, but the complete test also verifies that no alternate path/value is added while changing the runner. Before the fix, the subject drift test cannot load a binding and the report/evidence dictionaries have no `release_subject_digest`, so both assertions are RED for the intended reason.

- [ ] **Step 2: Run the RED tests and record the failures.**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_live_guard_runner.py -k "cli_has_no_subject_or_identity_override or subject_drift_is_refused_before_report_creation or report_and_evidence_carry_external_subject_digest"
```

Expected: the CLI rejection checks pass, while the binding/report tests fail because the current runner has no release-subject binding and no external digest fields. The mixed result is still a valid RED cycle; record the two failing node IDs and their messages.

- [ ] **Step 3: Add the external subject to the input lease and output validators.**

Include the fixed manifest in `_local_input_files()` and `_lease_input_paths()`. Keep the held descriptor alive for the entire execute path. Assert the binding immediately before output parent acquisition and again before each exclusive report/evidence write. Add these exact report/evidence fields without renaming the existing `subject_digest`:

```python
"release_subject_digest": release_subject.subject_digest,
"release_subject_path": str(release_subject_binding.manifest_path),
"merged_main_sha": release_subject.merged_main_sha,
"merged_main_git_tree": release_subject.merged_main_git_tree,
```

The output validators require the values from the same held object. They also require `activation_performed is False`, `default_writer_changed is False`, and the existing all-false mutation flags.

- [ ] **Step 4: Preserve injection only in fixture helpers.**

Move current dependency-injected execution tests to an explicit `run_fixture()` helper or equivalent private fixture path. Production `run(config=None, execute=True)` must reject a caller-provided dependency, `git_runner`, legacy snapshot, or reader after the subject load and before any source access. The production path must not infer “fixed production” from equality with a mutable `DEFAULT_CONFIG`.

- [ ] **Step 5: Run GREEN and the complete live-guard module.**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_live_guard_runner.py -k "cli_has_no_subject_or_identity_override or subject_drift_is_refused_before_report_creation or report_and_evidence_carry_external_subject_digest"
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_live_guard_runner.py
```

Expected: the new tests and all existing provenance, lease, injection, output, replay, and no-mutation tests pass.

- [ ] **Step 6: Commit the report/evidence binding.**

```powershell
git add scripts/run_beta3_live_guard.py tests/test_beta3_live_guard_runner.py
git commit -m "fix: bind guard evidence to release subject"
```

---
### Task 6: Restore Task 2's five missing RED/GREEN contracts without backdating evidence

**Files:**
- Modify: `tests/test_v8_local_acceptance.py`
- Create ignored ledger: `.superpowers/sdd/2026-08-11-gwo-v8-phase1-5-production/task-6-fixwave-task2-tdd.md`
- Create temporary test replay: `C:\tmp\gwo-task6-task2-contracts.py`
- Create temporary detached worktree: `C:\tmp\gwo-task6-task2-red`

**Interfaces:**
- The five tests call `run_local_acceptance(root=tmp_path, run_id="task2-root", scenario="root")`.
- They observe the public acceptance record, independent Git readback, traced `gwo_v8.advance`, final slot/resource diagnostics, and canonical output. They do not call `Kernel.reconcile_once()`.

- [ ] **Step 1: Add the five focused tests to the current test file.**

Use these exact test names and independent assertions:

```python
def test_root_candidate_readback_uses_real_git_commit_tree_and_diff(tmp_path: Path):
    runner = _load_runner()
    record = runner.run_local_acceptance(root=tmp_path, run_id="task2-git-readback", scenario="root")
    repository = tmp_path / "repository"
    candidates = record["facts"]["git_readback"]["candidate_objects"]
    assert len(candidates) == 4
    for candidate in candidates:
        commit = subprocess.run(
            ["git", "rev-parse", f"{candidate['reference']}^{{commit}}"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "rev-parse", f"{candidate['reference']}^{{tree}}"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert (commit, tree) == (candidate["commit_sha"], candidate["tree_sha"])
        assert candidate["diff_record_digest"] in {
            diff["record_digest"] for diff in record["facts"]["readback"]["candidate_diffs"]
        }


def test_root_batch_delivery_uses_real_batch_integrator_and_git_readback(tmp_path: Path):
    runner = _load_runner()
    record = runner.run_local_acceptance(root=tmp_path, run_id="task2-batch-readback", scenario="root")
    repository = tmp_path / "repository"
    batches = record["facts"]["git_readback"]["batches"]
    assert [batch["member_ticket_keys"] for batch in batches] == [
        ["issue:101", "issue:102", "issue:103"],
        ["issue:104"],
    ]
    assert all(batch["batch_ref_sha"] == batch["batch_sha"] for batch in batches)
    assert all(batch["target_contains_batch_sha"] for batch in batches)
    for batch in batches:
        ancestry = subprocess.run(
            ["git", "merge-base", "--is-ancestor", batch["batch_sha"], batch["target_head_sha"]],
            cwd=repository,
            check=False,
        )
        assert ancestry.returncode == 0


def test_root_watchdog_callback_lost_wake_duplicate_and_restart_are_public_advance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runner = _load_runner()
    calls: list[str | None] = []
    original = runner.gwo_v8.advance

    def traced(handle, wake_ref=None, **kwargs):
        calls.append(wake_ref)
        return original(handle, wake_ref, **kwargs)

    monkeypatch.setattr(runner.gwo_v8, "advance", traced)
    record = runner.run_local_acceptance(root=tmp_path, run_id="task2-watchdog", scenario="root")
    replay = record["replay"]
    assert replay["watchdog_progressed"] is True
    assert replay["lost_wake"]["status"] == "Complete"
    assert replay["duplicate_callback"]["status"] == "Complete"
    assert replay["restart_advance"]["status"] == "Complete"
    assert any(wake == replay["callback_emitted"] for wake in calls)
    assert replay["idempotent_effects"] is True


def test_root_worker_slots_release_and_strict_resource_is_exclusive(tmp_path: Path):
    runner = _load_runner()
    record = runner.run_local_acceptance(root=tmp_path, run_id="task2-resources", scenario="root")
    concurrency = record["facts"]["concurrency"]
    resources = record["facts"]["exclusive_resources"]
    assert concurrency["worker_slot_limit"] == 4
    assert concurrency["max_held"] == 4
    assert concurrency["final_held"] == 0
    assert concurrency["final_available"] == 4
    assert resources["issue:101"] == []
    assert resources["issue:102"] == []
    assert resources["issue:103"] == []
    assert resources["issue:104"] == ["repository.target.v1"]


def test_root_acceptance_is_canonical_across_independent_roots(tmp_path: Path):
    runner = _load_runner()
    first = runner.run_local_acceptance(
        root=tmp_path / "first-root", run_id="task2-deterministic", scenario="root"
    )
    second = runner.run_local_acceptance(
        root=tmp_path / "second-root", run_id="task2-deterministic", scenario="root"
    )
    assert first["record_digest"] == second["record_digest"]
    assert runner.canonical_json(first) == runner.canonical_json(second)
```

- [ ] **Step 2: Create the isolated pre-fix replay without changing historical reports.**

Run these commands from the current feature worktree:

```powershell
$redRoot = 'C:\tmp\gwo-task6-task2-red'
if (Test-Path -LiteralPath $redRoot) { throw "red replay path already exists" }
git worktree add --detach $redRoot 2feeaa6
$source = Get-Content -LiteralPath 'tests\test_v8_local_acceptance.py' -Raw
$loader = @'
def _load_runner():
    root = Path(os.environ["GWO_LOCAL_ACCEPTANCE_RUNNER_ROOT"])
    path = root / "scripts" / "run_v8_local_acceptance.py"
    spec = importlib.util.spec_from_file_location("run_v8_local_acceptance_replay", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
'@
$replay = [regex]::Replace($source, '(?s)def _load_runner\(\):.*?return module', $loader, 1)
if ($replay -eq $source) { throw 'replay loader replacement did not match' }
$replay | Set-Content -LiteralPath 'C:\tmp\gwo-task6-task2-contracts.py' -Encoding utf8
$env:GWO_LOCAL_ACCEPTANCE_RUNNER_ROOT = $redRoot
$env:PYTHONDONTWRITEBYTECODE = '1'
py -3.13 -B -m pytest -q C:\tmp\gwo-task6-task2-contracts.py --basetemp C:\tmp\gwo-task6-task2-red-pytest
```

The standalone replay contains the current test module with only `_load_runner()` replaced; pytest `-k` selects the five test names from Step 1. That loader reads `scripts/run_v8_local_acceptance.py` from `GWO_LOCAL_ACCEPTANCE_RUNNER_ROOT`, not from the current checkout. The five RED failures must identify the old synthetic Candidate/Git, synthetic Batch delivery, missing public Watchdog callback/readback, missing final resource proof, or non-canonical root output. Record the actual node IDs, messages, exit code, and current UTC time in `task-6-fixwave-task2-tdd.md` under `red_run`; do not claim the run happened when `2feeaa6` was authored.

- [ ] **Step 3: Run the same five tests against the final fix-wave source.**

Unset the old-root variable and run:

```powershell
Remove-Item Env:GWO_LOCAL_ACCEPTANCE_RUNNER_ROOT -ErrorAction SilentlyContinue
$env:PYTHONDONTWRITEBYTECODE = '1'
py -3.13 -B -m pytest -q tests/test_v8_local_acceptance.py -k "root_candidate_readback_uses_real_git_commit_tree_and_diff or root_batch_delivery_uses_real_batch_integrator_and_git_readback or root_watchdog_callback_lost_wake_duplicate_and_restart_are_public_advance or root_worker_slots_release_and_strict_resource_is_exclusive or root_acceptance_is_canonical_across_independent_roots"
```

Expected: five GREEN tests. Record the actual final commit, UTC time, command, exit code, and output under `green_run`. The ledger must explicitly state that RED was a contemporaneous reproduction against `2feeaa6`, not a historical timestamp.

- [ ] **Step 4: Run the complete local acceptance module.**

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
py -3.13 -B -m pytest -q tests/test_v8_local_acceptance.py
```

Expected: the existing single and root acceptance tests plus the five new contracts pass. No GitHub/Paseo/Hosted CI path is involved.

- [ ] **Step 5: Commit the five test contracts.**

```powershell
git add tests/test_v8_local_acceptance.py
git commit -m "test: restore task2 root canary red green evidence"
```

Keep the TDD ledger ignored under `.superpowers`; never stage it as a product or release source file.

---

### Task 7: Integrate the subject digest into the Phase 3 release-candidate package

**Files:**
- Modify: `scripts/run_beta3_live_guard.py`
- Modify: `tests/test_beta3_live_guard_runner.py`
- Modify: `docs/superpowers/specs/2026-08-11-gwo-v8-phase3-release-candidate.md`
- Create ignored reports under `.superpowers/sdd/2026-08-11-gwo-v8-phase1-5-production/`

**Interfaces:**
- The RC document records `release_subject_digest` separately from the `CutoverSubject` digest.
- The four-axis review package consumes the fix-wave ledger and never appends a second historical Task 7 fix wave to the old ledger.

- [ ] **Step 1: Run the focused integration RED check.**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_live_guard_runner.py tests/test_beta3_control_ownership_attestor.py tests/test_beta3_release_subject.py tests/test_beta3_release_subject_generator.py tests/test_v8_local_acceptance.py
```

Expected before the integration edits: the new subject-loader tests are GREEN from Tasks 1/2, while runner/attestor integration tests fail where the old names or old function signatures remain. The retained failure list must contain only the current fix-wave write sets.

- [ ] **Step 2: Regenerate the reviewed-provenance fixture only after all observer code is final for this branch.**

Use the existing ordered generator and verify the raw runner/attestor hashes. Do not put the external subject into `scripts/beta3_reviewed_provenance.json`; that file remains the in-checkout observer provenance manifest. Do not alter its schema.

- [ ] **Step 3: Update the RC record and independent review package.**

Record the five RED/GREEN Task 2 cycles, subject schema version, digest rule, fixed path, separate identity fields, no-CLI rule, held-manifest ordering, and the no-activation scope. Keep the four review verdicts pending until all reviewers inspect the final combined diff.

- [ ] **Step 4: Run the integration GREEN check.**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_live_guard_runner.py tests/test_beta3_control_ownership_attestor.py tests/test_beta3_release_subject.py tests/test_beta3_release_subject_generator.py tests/test_v8_local_acceptance.py
```

Expected: all subject, runner, attestor, and local single/root acceptance tests pass with the corrected identity domains and report/evidence fields.

- [ ] **Step 5: Commit the RC documentation update.**

```powershell
git add docs/superpowers/specs/2026-08-11-gwo-v8-phase3-release-candidate.md
git commit -m "docs: record release subject fix wave evidence"
```

---

### Task 8: Run the complete local verification and four-axis review gate

**Files:**
- Read-only verification over all modified source/tests.
- Create ignored review reports under `.superpowers/sdd/2026-08-11-gwo-v8-phase1-5-production/`.

**Interfaces:**
- Required independent verdicts are `SPEC GO`, `QUALITY GO`, `TDD VALID`, and `OPEN 0`.
- No reviewer may run Guard `--execute`, activation, writer transition, tag, push, or release.

- [ ] **Step 1: Run the five Beta3 suites and local acceptance.**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_beta3_bootstrap_model.py tests/test_beta3_control_ownership_attestor.py tests/test_beta3_legacy_attestor.py tests/test_beta3_replay_guard.py tests/test_beta3_live_guard_runner.py tests/test_beta3_release_subject.py tests/test_beta3_release_subject_generator.py tests/test_v8_local_acceptance.py
```

Expected: PASS with no dependency injection in the production path and no missing release-subject field.

- [ ] **Step 2: Run Ruff, AST, forbidden graph, and diff checks.**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; ruff check --no-cache scripts/beta3_release_subject.py scripts/generate_beta3_release_subject.py scripts/beta3_bootstrap_model.py scripts/beta3_control_ownership_attestor.py scripts/beta3_legacy_attestor.py scripts/beta3_replay_guard.py scripts/run_beta3_live_guard.py tests/test_beta3_release_subject.py tests/test_beta3_release_subject_generator.py tests/test_beta3_bootstrap_model.py tests/test_beta3_control_ownership_attestor.py tests/test_beta3_legacy_attestor.py tests/test_beta3_replay_guard.py tests/test_beta3_live_guard_runner.py tests/test_v8_local_acceptance.py
py -3.13 -B -c "import ast; from pathlib import Path; files = [Path(p) for p in 'scripts/beta3_release_subject.py scripts/generate_beta3_release_subject.py scripts/beta3_bootstrap_model.py scripts/beta3_control_ownership_attestor.py scripts/beta3_legacy_attestor.py scripts/beta3_replay_guard.py scripts/run_beta3_live_guard.py tests/test_beta3_release_subject.py tests/test_beta3_release_subject_generator.py tests/test_beta3_bootstrap_model.py tests/test_beta3_control_ownership_attestor.py tests/test_beta3_legacy_attestor.py tests/test_beta3_replay_guard.py tests/test_beta3_live_guard_runner.py tests/test_v8_local_acceptance.py'.split()]; [ast.parse(path.read_bytes(), filename=str(path)) for path in files]; print(f'AST_OK ({len(files)} files)')"
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B -m pytest -q tests/test_v8_cutover_guard_static.py
git diff --check
```

Expected: Ruff passes, AST prints `AST_OK (15 files)`, the forbidden call-graph suite passes, and `git diff --check` is clean.

- [ ] **Step 3: Run the full repository suite locally.**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:GWO_CONVERGENCE_ARCHIVE_ROOT='D:\gwo-convergence-archive\20260804T185544Z'
py -3.13 -B -m pytest -q -p no:cacheprovider --basetemp 'C:\tmp\gwo-phase3-subject-fix-full-20260811'
```

Expected: exit code zero with a retained pytest summary and log. Do not claim a partial foreground run as evidence.

- [ ] **Step 4: Dispatch four independent Luna Max reviews.**

Each reviewer reads the exact final diff, the design, the plan, all Task 6 axis reports, the source-identity review, the actual RED/GREEN ledger, and the retained command logs. The review write sets are separate ignored report files:

```text
.superpowers/sdd/2026-08-11-gwo-v8-phase1-5-production/task-8-four-axis-spec.md
.superpowers/sdd/2026-08-11-gwo-v8-phase1-5-production/task-8-four-axis-quality.md
.superpowers/sdd/2026-08-11-gwo-v8-phase1-5-production/task-8-four-axis-tdd.md
.superpowers/sdd/2026-08-11-gwo-v8-phase1-5-production/task-8-four-axis-open.md
```

The reviewers must specifically inspect the manifest body digest rule, fixed path/no CLI override, held-handle order, exact ordered attestor bundle, 40/64 domain separation, no self-naming constants, output binding, and Task 2 contemporaneous RED evidence.

- [ ] **Step 5: Stop if any review gate is not exact.**

Do not proceed to canonical-main convergence unless the four verdicts are exactly `SPEC GO`, `QUALITY GO`, `TDD VALID`, and `OPEN 0`. A review that says mechanically green but retains a release-boundary concern is a HOLD, not a GO.

---
### Task 9: Workspace convergence, exact-main freeze, and local subject generation

**Files:**
- Canonical checkout: `D:\Workstation\github-work-orchestrator`
- Candidate branch/worktree: `D:\Workstation\github-work-orchestrator\.codex-tmp\ga-phase1-5-fix`
- Tracked provenance: `scripts/beta3_reviewed_provenance.json`
- Generator: `scripts/generate_beta3_release_subject.py`
- Ignored inventory/evidence under `.superpowers` and the fixed external evidence root

**Interfaces:**
- The exact canonical checkout is the only final `repository_root` in the subject.
- Unknown user data is preserved until explicitly classified.
- The generator is run once after the final canonical `main` tree is frozen.

- [ ] **Step 1: Produce and review the workspace inventory before cleanup.**

Run read-only commands:

```powershell
git -C 'D:\Workstation\github-work-orchestrator' worktree list --porcelain
Get-ChildItem -LiteralPath 'D:\Workstation\github-work-orchestrator' -Force
Get-ChildItem -LiteralPath 'D:\Workstation\github-work-orchestrator\.codex-tmp' -Force -ErrorAction SilentlyContinue
Get-ChildItem -LiteralPath 'D:\Workstation\gwo-worktrees' -Force -ErrorAction SilentlyContinue
```

Compare the output to the existing `workspace-inventory.md`. Preserve unclassified `docs/research`, historical evidence, old worktrees, and user-listed directories. Do not use recursive deletion, `git clean`, or worktree removal in this task.

- [ ] **Step 2: Integrate the reviewed commits without a direct main commit.**

Use a dedicated integration branch and fast-forward the canonical local `main` only after the candidate commits and review gates are identified. Do not create a new commit on `main`, do not push `main`, and do not overwrite unknown files. Verify:

```powershell
git -C 'D:\Workstation\github-work-orchestrator' rev-parse HEAD
git -C 'D:\Workstation\github-work-orchestrator' rev-parse 'HEAD^{tree}'
git -C 'D:\Workstation\github-work-orchestrator' status --short --branch
git -C 'D:\Workstation\github-work-orchestrator' log -1 --oneline
```

- [ ] **Step 3: Regenerate canonical reviewed provenance after the final merge.**

Run the existing local provenance generator against the canonical checkout. Verify every path is under `D:\Workstation\github-work-orchestrator\scripts`, every observer hash is computed from raw bytes, and the four attestors remain in the required order. Commit that tracked manifest on the integration branch before the subject freeze. Any later tracked source change invalidates the subject and requires a fresh provenance regeneration.

- [ ] **Step 4: Freeze and generate the external subject.**

After canonical `main` is clean and `origin/main == HEAD`, run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B 'D:\Workstation\github-work-orchestrator\scripts\generate_beta3_release_subject.py'
```

Expected: one canonical file at `D:\gwo-release-evidence\2026-08-09-gwo-v8-beta3-production-cutover\gwo-v8-release-subject.json`, no other new file, and stdout containing the 64-character `subject_digest`. If the subject file exists or any exact-main readback differs, stop without replacing it.

- [ ] **Step 5: Validate the frozen subject independently.**

Run a local read-only validation that loads the fixed file through `load_production_release_subject()`, checks `subject_digest`, verifies `fresh_receipt_sha256` against the fixed receipt bytes, verifies `merged_main_sha`, `merged_main_git_tree`, `audited_source_tree_digest`, runner hash, exact attestor list/bundle, and reviewed-provenance hash, then closes the binding. The receipt readback must also show `source_main_sha == merged_main_sha` and `source_main_tree == merged_main_git_tree`. This validation must not call the Guard, GitHub, Paseo, CIM, activation, or provider code.

---

### Task 10: Phase 4 read-only rehearsal and final safe stop

**Files:**
- `scripts/run_beta3_live_guard.py`
- Fixed external report/evidence pair under `D:\gwo-release-evidence\2026-08-09-gwo-v8-beta3-production-cutover`
- Ignored Phase 4 rehearsal report under `.superpowers/sdd/2026-08-11-gwo-v8-phase1-5-production/`

**Interfaces:**
- No activation or writer transition is called.
- Report/evidence include `release_subject_digest`, `merged_main_sha`, `merged_main_git_tree`, and `release_subject_path`.
- `BETA3_CUTOVER_REHEARSAL_GO` is recorded only when all readbacks match the held subject.

- [ ] **Step 1: Run the zero-write Guard preflight.**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B 'D:\Workstation\github-work-orchestrator\scripts\run_beta3_live_guard.py'
```

Expected: canonical `PREFLIGHT_OK` output, no report/evidence file, no gateway SQLite, no artifact root, no nonce, and no writer mutation. A subject mismatch stops before the existing Git/Store/Runtime reads.

- [ ] **Step 2: Run the declared read-only rehearsal only after the preflight is green.**

Use the exact local rehearsal identity `gwo-v8-phase4-rehearsal-20260811`:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3.13 -B 'D:\Workstation\github-work-orchestrator\scripts\run_beta3_live_guard.py' --execute --run-id 'gwo-v8-phase4-rehearsal-20260811'
```

This command is permitted to create only the declared report/evidence pair. It must not call `transition.py`, `WriterCutoverController.cutover()`, an Activation Receipt writer, GitHub CI, a provider action, a tag, or a release. V6.1 remains authoritative.

- [ ] **Step 3: Independently validate the report/evidence binding.**

Read both canonical output files and the external subject. Require:

```text
report.release_subject_digest == subject.subject_digest
evidence.release_subject_digest == subject.subject_digest
report.merged_main_sha == subject.merged_main_sha
evidence.merged_main_git_tree == subject.merged_main_git_tree
report.activation_performed is False
evidence.activation_performed is False
report.default_writer_changed is False
evidence.default_writer_changed is False
report.mutation_flags is all false
evidence.mutation_flags is all false
```

Also compare Git `HEAD`, `HEAD^{tree}`, `origin/main`, compatibility `source_tree_digest`, the receipt digest and its source-main SHA/tree, runner/attestor hashes, reviewed provenance, Store/receipt/package readbacks, and legacy quiescence to the same subject. If any field differs, the gate is HOLD and the evidence pair is not repaired in place.

- [ ] **Step 4: Record the Phase 4 gate and stop before Phase 5.**

Write the ignored rehearsal record with `BETA3_CUTOVER_REHEARSAL_GO` only after the independent checks pass. The record names the exact subject digest, merged SHA/tree, fixed evidence root, run ID, and local verification logs. It explicitly states that no Activation Receipt, writer transition, rollback, tag, push, or release occurred.

- [ ] **Step 5: Do not perform Phase 5 mutation in this fix wave.**

The next action requires a fresh owner approval naming repository, exact merged-main SHA, exact Git tree, `release_subject_digest`, exact run ID, evidence root, target repository, and `writer_transition = "v6.1 -> v8"`. Without that approval, stop with V6.1 as the only writer.

---

## Final verification checklist

Before declaring this fix wave ready for Phase 4/Phase 5 authorization, verify every item below on one exact final commit:

```text
release-subject schema is gwo-v8-release-subject.v2; v1 is superseded and not accepted
subject path is fixed and external
subject digest hashes canonical body without subject_digest
fresh_receipt_sha256 hashes the exact raw canonical fresh receipt bytes
fresh receipt digest, source_main_sha/source_main_tree, and merged_main_sha/merged_main_git_tree agree
held no-follow load precedes nonce, source, provider, CIM, and output
runner/attestor/reviewed-provenance paths and raw hashes match
attestor order and bundle match the existing length-delimited algorithm
merged_main_sha is 40-character Git commit identity
merged_main_git_tree is 40-character Git root-tree identity
audited_source_tree_digest is 64-character source audit identity
CutoverSubject.source_tree_digest remains the audited 64-character digest
local checkout uses git_tree_oid for Git tree readback
EXPECTED_HEAD/EXPECTED_TREE are absent from production identity logic
PRODUCTION_SOURCE_COMMIT/PRODUCTION_SOURCE_TREE are absent from production identity logic
no raw subject/path/value CLI override exists
report/evidence include release_subject_digest
Task 2 has five contemporaneous RED/GREEN records against 2feeaa6 and final code
SPEC GO
QUALITY GO
TDD VALID
OPEN 0
full local pytest green
Phase 4 rehearsal is read-only and still leaves V6.1 authoritative
```

The implementation is not complete merely because the focused tests pass. A changed subject, changed canonical-main tree, stale provenance, missing RED evidence, or any nonzero review finding keeps the release candidate on HOLD.
