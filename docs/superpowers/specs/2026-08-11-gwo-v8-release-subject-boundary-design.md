# GWO V8 Release Subject Boundary Design

## Status and decision

This design records the approved **external canonical release-subject manifest** approach for the Phase 3/4 fix wave. It is a release-boundary correction, not a production activation. Until Phase 5 receives the separate owner authorization described below, V6.1 remains the only production writer.

The design closes the two load-bearing findings in the Task 6 four-axis review:

1. `scripts/run_beta3_live_guard.py` and `scripts/beta3_control_ownership_attestor.py` currently name the old production commit/tree inside the source that is supposed to attest the final merged source. Updating those constants after merge is a Git self-reference and cannot produce a stable exact-main subject.
2. The 40-character Git root-tree OID is currently conflated with the 64-character audited source digest carried by `CutoverSubject.source_tree_digest`. The default production subject therefore cannot satisfy the current runner/attestor contracts.

The smallest safe boundary is a canonical JSON object outside the tracked checkout, loaded through a held no-follow file boundary. It declares the intended release subject. Git, Store, control, Runtime, package, and legacy readbacks remain authoritative observations; the manifest never replaces them.

## Scope and non-goals

### In scope

- One fixed production manifest path: `EVIDENCE_ROOT / "gwo-v8-release-subject.json"`.
- A closed schema named `gwo-v8-release-subject.v1`.
- A non-self-referential subject digest over the canonical body.
- Exact binding of the repository, canonical roots, merged-main commit/tree, audited source digest, runner, four ordered attestors, attestor bundle, and reviewed provenance.
- A held-handle/byte-identity loader that runs before nonce creation, production dependency/source access, GitHub, Paseo, CIM, or output creation.
- A deterministic local generator that creates only the subject file with exclusive-create semantics after canonical `main` is frozen.
- Separate identity fields for Git readback and audited source scanning.
- Phase 4 report/evidence binding and the Phase 5 exact owner-approval tuple.
- TDD remediation evidence for the five Task 2 fix contracts using an isolated replay of commit `2feeaa6`.

### Out of scope

- Activation, rollback, writer transition, or any production SQLite mutation.
- GitHub CI, provider action, Paseo execution, tag, push, or release publication.
- A new signing or key-management subsystem. The current cooperative single-host trust boundary in ADR-0015 remains the threat model. If an existing trusted signer is available later, it may sign the owner authorization without changing this manifest schema.
- Changing the V8 public API or adding a second workflow driver.

## Normative trust boundary

The release operation has three identities that must not be collapsed:

| Identity | Meaning | Authoritative owner |
| --- | --- | --- |
| Release subject | The exact merged checkout and observer bytes that the owner intends to approve | External subject manifest plus Git/source readback |
| Observer provenance | The runner, attestors, and reviewed-provenance bytes actually used to observe the subject | Held file reads and the existing reviewed-provenance contract |
| Owner authorization | Permission for one named Phase 5 operation | A separate owner approval that names the manifest digest and exact operational inputs |

The manifest is therefore an input identity, not an attestation result. A manifest that says `merged_main_sha` is a given OID does not prove that `HEAD` has that OID. The runner must still read `HEAD`, `HEAD^{tree}`, and `origin/main`, and the control attestor must still produce its existing readbacks.

A failed manifest load or mismatch returns before the existing Guard preflight. It leaves V6.1 authoritative and creates no nonce, Agent/session, provider action, Store, gateway SQLite, artifact directory, report, or evidence file.

## Fixed paths and closed schema

The production constants remain fixed in the focused release-subject module:

```text
REPOSITORY_ROOT = Path(r"D:\Workstation\github-work-orchestrator").resolve()
EVIDENCE_ROOT = Path(r"D:\gwo-release-evidence\2026-08-09-gwo-v8-beta3-production-cutover").resolve()
RELEASE_SUBJECT_PATH = EVIDENCE_ROOT / "gwo-v8-release-subject.json"
REPOSITORY = "NOirBRight/github-work-orchestrator"
REMOTE_REF = "origin/main"
RELEASE_SUBJECT_SCHEMA = "gwo-v8-release-subject.v1"
```

The production loader does not accept a path argument. The production generator does not accept a root, path, or value argument. Test-only fixture functions may receive temporary paths explicitly, but they are not used by `run_beta3_live_guard.py` or the generator entry point.

The exact top-level key set is:

```text
schema
repository
repository_root
evidence_root
merged_main_sha
merged_main_git_tree
audited_source_tree_digest
remote_ref
runner
attestors
attestor_bundle_sha256
reviewed_provenance
subject_digest
```

The exact nested shapes are:

```text
runner = {module, path, sha256}
attestors = [
  {module, path, sha256},
  {module, path, sha256},
  {module, path, sha256},
  {module, path, sha256}
]
reviewed_provenance = {path, sha256}
```

The attestor list is ordered and has exactly these four entries, matching the existing `_ATTESTOR_MODULE_NAMES` order. Reordering is invalid even when the bytes and bundle hash are unchanged:

```text
1. beta3_bootstrap_model.py
2. beta3_control_ownership_attestor.py
3. beta3_legacy_attestor.py
4. beta3_replay_guard.py
```

These values are filenames: each `attestors[*].module` is the filename stem without `.py`, and each `attestors[*].path` ends with the listed filename.

Field constraints are closed and exact:

| Field | Constraint and meaning |
| --- | --- |
| `schema` | Exact string `gwo-v8-release-subject.v1`. |
| `repository` | Exact string `NOirBRight/github-work-orchestrator`. |
| `repository_root` | Absolute canonical path equal to `D:\Workstation\github-work-orchestrator`. Every ancestor is a non-link, non-reparse directory. |
| `evidence_root` | Absolute canonical path equal to the fixed `EVIDENCE_ROOT`. Every ancestor is a non-link, non-reparse directory. |
| `merged_main_sha` | Lowercase hexadecimal Git commit OID matching `^[0-9a-f]{40}$`. It is used for `HEAD`, `origin/main`, and receipt `source_main_sha`. |
| `merged_main_git_tree` | Lowercase hexadecimal Git root-tree OID matching `^[0-9a-f]{40}$`. It is used for `HEAD^{tree}` and receipt `source_main_tree`. It is not an audited source digest. |
| `audited_source_tree_digest` | Lowercase SHA-256 matching `^[0-9a-f]{64}$`, returned by `gwo_v8.cutover_guard.source_tree_digest(REPOSITORY_ROOT)`. It remains the value of `CutoverSubject.source_tree_digest`. |
| `remote_ref` | Exact string `origin/main`; it prevents a moving or operator-selected ref from becoming the release subject. |
| `runner` | `module` is `run_beta3_live_guard`; `path` is the canonical repository `scripts/run_beta3_live_guard.py`; `sha256` is the hash of its raw held bytes. |
| `attestors` | The exact ordered module/path/hash entries above. Each hash is the hash of raw held bytes. |
| `attestor_bundle_sha256` | Existing ordered length-delimited SHA-256 over the four raw attestor files. The algorithm is: for each ordered filename, append its UTF-8 byte length as four big-endian bytes, the filename bytes, the file-content length as eight big-endian bytes, and the file bytes; hash the concatenation. |
| `reviewed_provenance` | `path` is the canonical repository `scripts/beta3_reviewed_provenance.json`; `sha256` is the hash of its raw held bytes. The JSON must still pass the existing `gwo-beta3-reviewed-provenance.v1` closed-schema and origin checks. |
| `subject_digest` | Lowercase SHA-256 over the canonical body defined below. |

No package, Store, control-ref, Runtime selector, or target repository value is duplicated into this manifest. Those remain the existing fixed configuration/readback contracts. Duplicating them would create a second source of truth without closing either reviewed finding.

## Canonical body and subject digest

The manifest file is canonical JSON with UTF-8 encoding, `ensure_ascii=False`, lexicographically sorted object keys, `separators=(",", ":")`, `allow_nan=False`, and one trailing LF. The loader rejects any other byte representation.

The digest rule is explicitly non-self-referential:

1. Parse the file as one JSON object.
2. Require the exact top-level key set above.
3. Remove only the `subject_digest` member.
4. Require the remaining object to contain every body field exactly once; no digest sentinel, omitted field, or additional field is allowed.
5. Serialize that body with the canonical JSON function, including its trailing LF.
6. Set `subject_digest = hashlib.sha256(canonical_body_bytes).hexdigest()`.
7. Serialize the full object, including `subject_digest`, as the manifest bytes.

The subject digest is therefore not `sha256(manifest_file_bytes)`, and the file hash is not interchangeable with the subject digest. The rule works because the digest field is excluded before hashing and because the manifest is outside the source tree being identified.

The focused module exposes exact pure operations:

```python
@dataclass(frozen=True)
class ReleaseSubject:
    schema: str
    repository: str
    repository_root: str
    evidence_root: str
    merged_main_sha: str
    merged_main_git_tree: str
    audited_source_tree_digest: str
    remote_ref: str
    runner: ReleaseFileIdentity
    attestors: tuple[ReleaseFileIdentity, ReleaseFileIdentity, ReleaseFileIdentity, ReleaseFileIdentity]
    attestor_bundle_sha256: str
    reviewed_provenance: ReviewedProvenanceIdentity
    subject_digest: str

    canonical_body() -> dict[str, object]
    canonical() -> dict[str, object]

@dataclass(frozen=True)
class ReleaseFileIdentity:
    module: str
    path: str
    sha256: str

@dataclass(frozen=True)
class ReviewedProvenanceIdentity:
    path: str
    sha256: str
```

The dataclass method declarations above are interface notation. The implementation plan gives concrete method behavior and tests; no interface is permitted to return an open-ended mapping or accept unknown keys.

`ReleaseSubject.canonical_body()` returns the exact 12 body keys in canonical projection. `ReleaseSubject.canonical()` returns those keys plus `subject_digest`. `parse_release_subject(raw, expected_repository_root, expected_evidence_root)` recomputes the digest and returns an immutable value or raises `ReleaseSubjectError` with a stable code.

## Held manifest loading protocol

`beta3_release_subject.py` owns the manifest boundary so the 4,000-line live Guard does not grow another parser or a second path-safety implementation. Its production loader uses the same no-follow/held-handle semantics already present in `run_beta3_live_guard.py`:

1. Resolve the fixed `RELEASE_SUBJECT_PATH` without following a link. Open every directory component with identity checks and reject a symlink or Windows reparse ancestor.
2. Open the final regular file with no-follow semantics. Retain the descriptor/handle and capture the file identity, size, and raw bytes from that handle.
3. Decode UTF-8, parse canonical JSON, enforce the exact closed schema, recompute `subject_digest`, and validate every path and digest shape.
4. Read the runner, four attestors, and reviewed provenance through held canonical paths. Compare raw byte hashes and the ordered bundle. Check that the reviewed-provenance entries agree with all manifest observer identities.
5. Return `ReleaseSubjectBinding`, which owns the held manifest identity and raw bytes. `assert_stable()` rereads the same fixed path with no-follow/held identity and requires the identity and bytes to match. The runner retains this binding through nonce creation, preflight, double attestation, replay, and the two output writes.
6. On any failure, close the handle and raise `RELEASE_SUBJECT_UNAVAILABLE`, `RELEASE_SUBJECT_SCHEMA_INVALID`, `RELEASE_SUBJECT_DIGEST_MISMATCH`, `RELEASE_SUBJECT_PATH_INVALID`, `RELEASE_SUBJECT_PROVENANCE_MISMATCH`, or `RELEASE_SUBJECT_DRIFT` as appropriate. No fallback loads a path supplied by a caller.

The loader is run before:

- `secrets.token_hex` or any other nonce factory;
- `_production_dependencies`, `git_runner`, control readers, package readers, legacy readers, or injected providers;
- `_git_snapshot` and all Store/receipt/package/source reads;
- GitHub, Paseo, Runtime/CIM, or any external/provider command;
- `_PublicationLease`, output creation, gateway SQLite creation, or artifact-root creation.

A test-only `load_release_subject_for_test(path, expected_repository_root, expected_evidence_root)` may use a temporary fixture and a supplied held-reader seam. It must be impossible for the production function to reach that seam through the CLI or `run()` default.

## Runner and control-attestor binding

### Runner configuration

The production path removes `EXPECTED_HEAD`, `EXPECTED_TREE`, and the equality-to-`DEFAULT_CONFIG` subject gate. It uses these explicit `RunnerConfig` identity fields instead:

```text
merged_main_sha: str
merged_main_git_tree: str
audited_source_tree_digest: str
release_subject_digest: str
```

The static Store, package, branch, writer-generation, and output settings remain the existing fixed contracts. `run()` loads the fixed external subject and builds an effective immutable `RunnerConfig` from it. A caller cannot replace any of those four identity fields in production. Existing fixture tests retain explicit non-production configuration injection through a test-only construction path and never pass that path to the production CLI.

The production runner changes the existing functions as follows:

- `_git_snapshot()` compares `HEAD` and `origin/main` to `config.merged_main_sha`, and `HEAD^{tree}` to `config.merged_main_git_tree`.
- `_validate_receipt()` compares `source_main_sha` to `config.merged_main_sha` and `source_main_tree` to `config.merged_main_git_tree`.
- `_default_subject_factory()` constructs `CutoverSubject.source_commit` from `merged_main_sha` and `CutoverSubject.source_tree_digest` from `audited_source_tree_digest`; it never places the Git tree OID into `source_tree_digest`.
- `ProductionBootstrapAttestor.attest()` receives the same immutable `ReleaseSubject` object held by the runner. It does not construct a subject from code-local expected values.
- `_local_input_files()` and the input lease include the external manifest path and its held identity. A change after preflight returns `LIVE_INPUT_DRIFT`/`RELEASE_SUBJECT_DRIFT` before nonce or output effects.
- Reports and evidence retain the existing `subject_digest` for the `CutoverSubject` digest and add an explicit `release_subject_digest` field for the external manifest digest. The latter is required in both the Phase 4 report and evidence and is checked during output readback.

The CLI remains limited to `--execute` and `--run-id`. `--subject`, `--subject-path`, `--expected-head`, `--expected-tree`, `--repository-root`, and `--evidence-root` are not accepted. `--run-id` identifies the operation; it never selects identity values.

### Control ownership attestor

The control attestor removes `PRODUCTION_SOURCE_COMMIT` and `PRODUCTION_SOURCE_TREE`. Its production `observe()` receives the same `ReleaseSubject` value as the runner and verifies:

```text
config.merged_main_sha == release_subject.merged_main_sha == subject.source_commit
config.merged_main_git_tree == release_subject.merged_main_git_tree
config.audited_source_tree_digest == release_subject.audited_source_tree_digest == subject.source_tree_digest
```

The first and second lines are 40-character Git identity checks. The third line is a 64-character audited-source check. No comparison aliases `merged_main_git_tree` to `source_tree_digest`.

The local checkout `SourceObservation` has an explicitly named `git_tree_oid` field in its canonical payload and identity. Its `commit_oid` remains the 40-character commit OID. `CompatibilityPathReadback.source_tree_digest` remains the 64-character audited source digest. This is the only new Git-tree name in the control/source observation domain; `CutoverSubject.source_tree_digest` is not renamed or widened.

All fixture sources and readback validators are updated to use the correct domain. A test that swaps the 40-character Git tree and 64-character audited digest must fail before any control/ref, Runtime, package, or CIM read.

## Deterministic subject generator

The tracked entry point is:

```text
D:\Workstation\github-work-orchestrator\scripts\generate_beta3_release_subject.py
```

It accepts no CLI options. It performs this exact sequence:

1. Require the fixed canonical repository and evidence-root directories to exist and be non-reparse.
2. Require the fixed subject path to be absent. An existing file produces `RELEASE_SUBJECT_EXISTS`; it is never replaced, truncated, or deleted.
3. Read `HEAD`, `HEAD^{tree}`, and `origin/main` from the canonical checkout and require `origin/main == HEAD`. Require the Git status rule used by the Guard: no status other than the explicitly permitted `.codex-tmp` untracked subtree.
4. Compute `audited_source_tree_digest` from the exact audited source function.
5. Read and hash the runner, four attestors, and reviewed-provenance file through held no-follow handles. Require the reviewed-provenance cross-check and existing canonical origins.
6. Construct the closed `ReleaseSubject`, compute its body digest, and serialize its full canonical bytes.
7. Open `EVIDENCE_ROOT / "gwo-v8-release-subject.json"` with exclusive-create semantics (`O_CREAT|O_EXCL`, or `CREATE_NEW` on Windows), write only those bytes, flush and `fsync` the descriptor, then validate the written identity and bytes through the runtime loader.
8. Print the canonical subject digest and exit zero. It does not create a directory, temporary file, SQLite database, staging file, report, evidence file, Git ref, provider action, or tag.

The generator's pure test function may receive a temporary repository and evidence root. The production entry point cannot receive those values, so a typo or CLI override cannot select a different subject.

## Phase 4 and Phase 5 evidence binding

Every Phase 4 report and evidence object includes:

```text
release_subject_digest
release_subject_path = EVIDENCE_ROOT / "gwo-v8-release-subject.json"
merged_main_sha
merged_main_git_tree
```

The existing Cutover Guard readbacks continue to prove the observed Git and audited source values. The report/evidence validator checks that the four fields agree with the held manifest and with the authoritative readbacks. A changed manifest invalidates the rehearsal; evidence is not repaired in place.

Before Phase 5 mutation, the owner approval must name all of these exact values:

```text
repository = NOirBRight/github-work-orchestrator
merged_main_sha = the exact 40-character merged-main commit OID
merged_main_git_tree = the exact 40-character Git root-tree OID
release_subject_digest = the external manifest body digest
run_id = the exact operator run identity
evidence_root = the fixed evidence root
target_repository = the exact repository being transitioned
writer_transition = "v6.1 -> v8"
```

The approval is separate from the subject file, report/evidence, and durable Activation Receipt. It is not a new signing system. `WriterCutoverController.cutover()` and the durable Activation Receipt remain the only authority-transfer mechanism, and this fix wave never calls them.

## TDD remediation for Task 2

The previous Task 2 fix changed five production behaviors without retaining a pre-fix RED run for each behavior. The fix wave adds five focused contracts and replays them against an isolated checkout at commit `2feeaa6`:

1. `test_root_candidate_readback_uses_real_git_commit_tree_and_diff`
2. `test_root_batch_delivery_uses_real_batch_integrator_and_git_readback`
3. `test_root_watchdog_callback_lost_wake_duplicate_and_restart_are_public_advance`
4. `test_root_worker_slots_release_and_strict_resource_is_exclusive`
5. `test_root_acceptance_is_canonical_across_independent_roots`

The RED replay is captured at the time it is run with `pre_fix_commit=2feeaa6`, the actual UTC timestamp, the exact command, exit code, and complete output. It does not edit or backdate `task-2-fix-round-1-report.md`. The same test bodies then run against the final fix-wave checkout and record GREEN with its actual commit and timestamp. The contracts use independent observations: Git commands against the temporary repository, BatchIntegrator refs and target ancestry, traced public `gwo_v8.advance` calls, final slot/resource diagnostics, and canonical output comparison. Existing green assertions are not relabeled as historical RED evidence.

## Review gates and safe stop

The fix wave is complete only after the following are independently reviewed on the final code:

```text
SPEC GO
QUALITY GO
TDD VALID
OPEN 0
```

The Phase 4 sequence then freezes the canonical merged `main`, regenerates `scripts/beta3_reviewed_provenance.json`, generates the external subject exactly once, runs local focused/full verification, and performs the read-only Beta3 rehearsal. The rehearsal may create only its declared report/evidence pair; it does not activate V8 or change V6.1 authority. `BETA3_CUTOVER_REHEARSAL_GO` is not recorded until the report/evidence `release_subject_digest`, Git readbacks, source digest, runner/attestor hashes, and local verification all agree.

No failure path in this design performs rollback, deletes an existing subject/receipt, changes a writer, contacts GitHub CI, executes a provider action, creates a tag, or publishes a release.

## References and rationale

This boundary follows the normative hierarchy in `docs/agents/domain.md`, the ubiquitous language in `CONTEXT.md`, and the current mechanics in `docs/design/gwo-v8-lean-architecture.md`. The cutover behavior is owned by ADR-0015, ADR-0034, ADR-0035, and ADR-0046. The one-driver, runtime-boundary, watchdog, candidate/evidence, and delivery constraints come from ADR-0022, ADR-0053, ADR-0058, ADR-0059, ADR-0060, and ADR-0063.

The design directly disposes the findings in `task-6-four-axis-spec.md`, `task-6-four-axis-quality.md`, `task-6-four-axis-tdd.md`, `task-6-four-axis-open.md`, and `task-7-source-identity-boundary-review.md`. It preserves the accepted cooperative-host trust model rather than inventing hostile-host attestation, keeps Git readback authoritative, and removes the unsatisfiable self-naming identity constants without adding a second production driver.
