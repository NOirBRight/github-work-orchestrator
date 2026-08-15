from __future__ import annotations

from dataclasses import dataclass, replace
import base64
import gc
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
for candidate in (ROOT / "skills" / "orchestrator" / "scripts", ROOT / "scripts"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from gwo_v8.cutover_guard import CutoverSubject  # noqa: E402
from gwo_v8._canonical import (  # noqa: E402
    canonical_bytes,
    digest_bytes,
    digest_value,
    load_canonical_json,
)
from gwo_v8.cutover_guard import (  # noqa: E402
    CompatibilityPathReadback,
    DurableStateReadback,
    PackageReadback,
    ReadOnlyPackageValidator,
)
from gwo_v8.plan_control import PlanControlError  # noqa: E402
from gwo_v8.transition import WriterTransitionRecord  # noqa: E402
import beta3_control_ownership_attestor as attestor_module  # noqa: E402
from beta3_release_subject import ReleaseSubject, release_subject_digest  # noqa: E402
from beta3_bootstrap_model import (  # noqa: E402
    AttemptIdentity,
    BootstrapError,
    SourceObservation,
    SourceRecord,
)
from beta3_control_ownership_attestor import (  # noqa: E402
    ControlOwnershipAttestor,
    ControlOwnershipSourceSet,
)


@dataclass
class _Blob:
    content: bytes
    blob_sha: str


@dataclass
class _ExactBlob:
    repository: str
    ref: str
    commit_oid: str
    path: str
    content: bytes
    blob_sha: str
    object_type: str = "file"
    encoding: str = "base64"
    size: int | None = None


@dataclass
class _Ref:
    repository: str
    ref: str
    commit_oid: str
    object_type: str = "commit"


class _ControlFixture:
    oid = "1" * 40

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.writer_bytes: bytes | None = b"{}"
        self.active_plan_bytes = b"{}"
        self.legacy_fence_bytes = b"{}"

    def read_ref(self, repository: str, branch: str) -> _Ref:
        self.calls.append(("read_ref", repository, branch))
        return _Ref(repository, f"refs/heads/{branch}", self.oid)

    def read_at_oid(self, repository: str, oid: str, path: str) -> _ExactBlob | None:
        self.calls.append(("read_at_oid", repository, oid, path))
        content = {
            ".gwo-v8/writer-transition.json": self.writer_bytes,
            ".gwo/v8/active-plan.json": self.active_plan_bytes,
            ".gwo-v8/legacy-writer-fence.json": self.legacy_fence_bytes,
        }[path]
        if content is None:
            return None
        blob_sha = hashlib.sha1(
            f"blob {len(content)}\0".encode("ascii") + content
        ).hexdigest()
        return _ExactBlob(
            repository=repository,
            ref=oid,
            commit_oid=oid,
            path=path,
            content=content,
            blob_sha=blob_sha,
            size=len(content),
        )


class _ExactControlFixture(_ControlFixture):
    def read_ref(self, repository: str, branch: str) -> _Ref:
        self.calls.append(("read_ref", repository, branch))
        return _Ref(repository, f"refs/heads/{branch}", self.oid)

    def read_at_oid(
        self,
        repository: str,
        oid: str,
        path: str,
    ) -> _ExactBlob | None:
        self.calls.append(("read_at_oid", repository, oid, path))
        content = {
            ".gwo-v8/writer-transition.json": self.writer_bytes,
            ".gwo/v8/active-plan.json": self.active_plan_bytes,
            ".gwo-v8/legacy-writer-fence.json": self.legacy_fence_bytes,
        }[path]
        if content is None:
            return None
        blob_sha = hashlib.sha1(
            f"blob {len(content)}\0".encode("ascii") + content
        ).hexdigest()
        return _ExactBlob(
            repository=repository,
            ref=oid,
            commit_oid=oid,
            path=path,
            content=content,
            blob_sha=blob_sha,
            size=len(content),
        )


def _subject() -> CutoverSubject:
    return CutoverSubject(
        repository="owner/repo",
        control_branch="gwo-control",
        target_branch="main",
        source_writer_generation="v6.1",
        target_writer_generation="v8",
        store_generation="store:v8:test",
        source_commit="a" * 40,
        source_tree_digest="b" * 64,
        production_entry_refs=("entry:main",),
    )


def _subject_with_source_tree_digest(source_tree_digest: str) -> CutoverSubject:
    return replace(
        _subject(),
        repository="NOirBRight/github-work-orchestrator",
        source_tree_digest=source_tree_digest,
        store_generation="store:v8:production:20260809T081500Z",
    )


def _release_subject(
    *,
    merged_main_sha: str,
    merged_main_git_tree: str,
    audited_source_tree_digest: str,
) -> ReleaseSubject:
    repository_root = ROOT.resolve()
    body = {
        "schema": "gwo-v8-release-subject.v2",
        "repository": "NOirBRight/github-work-orchestrator",
        "repository_root": str(repository_root),
        "evidence_root": str((repository_root / ".codex-tmp" / "evidence").resolve()),
        "fresh_receipt_sha256": "8" * 64,
        "merged_main_sha": merged_main_sha,
        "merged_main_git_tree": merged_main_git_tree,
        "audited_source_tree_digest": audited_source_tree_digest,
        "remote_ref": "origin/main",
        "runner": {
            "module": "run_beta3_live_guard",
            "path": str(repository_root / "scripts" / "run_beta3_live_guard.py"),
            "sha256": "1" * 64,
        },
        "attestors": [
            {
                "module": name.removesuffix(".py"),
                "path": str(repository_root / "scripts" / name),
                "sha256": digest,
            }
            for name, digest in zip(
                (
                    "beta3_bootstrap_model.py",
                    "beta3_control_ownership_attestor.py",
                    "beta3_legacy_attestor.py",
                    "beta3_replay_guard.py",
                ),
                ("2" * 64, "3" * 64, "4" * 64, "5" * 64),
                strict=True,
            )
        ],
        "attestor_bundle_sha256": "6" * 64,
        "reviewed_provenance": {
            "path": str(repository_root / "scripts" / "beta3_reviewed_provenance.json"),
            "sha256": "7" * 64,
        },
    }
    return ReleaseSubject.from_canonical(
        {**body, "subject_digest": release_subject_digest(body)}
    )


def _release_subject_for(subject: CutoverSubject) -> ReleaseSubject:
    if subject.repository == "NOirBRight/github-work-orchestrator":
        return _release_subject(
            merged_main_sha=subject.source_commit,
            merged_main_git_tree="104ee822dbfb494d33d56b8ccf54092d9d1d9c86",
            audited_source_tree_digest=subject.source_tree_digest,
        )
    value = object.__new__(ReleaseSubject)
    object.__setattr__(value, "repository", subject.repository)
    object.__setattr__(value, "merged_main_sha", subject.source_commit)
    object.__setattr__(value, "merged_main_git_tree", "b" * 40)
    object.__setattr__(value, "audited_source_tree_digest", subject.source_tree_digest)
    return value


def _attempt(subject: CutoverSubject) -> AttemptIdentity:
    from gwo_v8._canonical import digest_value

    return AttemptIdentity(
        run_id="beta3-test",
        challenge_nonce="ab" * 16,
        repository=subject.repository,
        evidence_root="C:/evidence",
        cutover_subject_digest=digest_value(subject.canonical()),
        runner_sha256="c" * 64,
        attestor_sha256="d" * 64,
    )


@pytest.fixture
def control_fixture() -> _ControlFixture:
    return _ControlFixture()


class _Registry:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def read(self, repository: str) -> SourceObservation:
        self.calls.append(repository)
        payload = canonical_bytes({"runtimes": []})
        return SourceObservation(
            record=SourceRecord(
                role="runtime.registry",
                locator=f"runtime-registry://{repository}",
                repository=repository,
                read_mode="COMPLETE_OBSERVATION",
                identity=(("observation_digest", digest_bytes(payload)),),
                content_sha256=digest_bytes(payload),
                readback_digest=None,
                producer_sha256="d" * 64,
            ),
            canonical_payload=payload,
            complete=True,
        )


class _RuntimeConfig:
    def read(self, path: Path | None = None) -> object:
        profile = {
            "provider": "provider",
            "settings": {
                "model": "model",
                "thinkingOptionId": "high",
                "modeId": "write",
                "features": {},
            },
        }
        value = {
            "schema_version": 1,
            "global": {"default_tier": "standard"},
            "tiers": {"standard": profile},
            "role_profiles": {
                "coordinator_auto": profile,
                "reviewer_recovery": profile,
                "reviewer_standard": profile,
                "reviewer_strict": profile,
            },
        }
        payload = canonical_bytes(value)
        config_path = Path(path or r"C:\fixture\.orch\config.json").resolve()
        record = SourceRecord(
            role="runtime.config",
            locator=str(config_path),
            repository="owner/repo",
            read_mode="EXACT_FILE",
            identity=(
                ("byte_sha256", digest_bytes(payload)),
                ("inode", "1"),
                ("mtime_ns", "2"),
                ("path", str(config_path.resolve())),
                ("size", str(len(payload))),
            ),
            content_sha256=digest_bytes(payload),
            readback_digest=None,
            producer_sha256="d" * 64,
        )
        return SourceObservation(record=record, canonical_payload=payload, complete=True)


class _LocalInputs:
    def __init__(
        self,
        *,
        commit: str | None = None,
        tree: str | None = None,
        root: Path | None = None,
    ) -> None:
        self._commit = commit
        self._tree = tree
        self._root = root
        self.calls: list[tuple[object, CutoverSubject]] = []

    def read(self, config: object, subject: CutoverSubject) -> SourceObservation:
        self.calls.append((config, subject))
        root = Path(self._root or config.repository_root).resolve()
        files = []
        if all((root / "skills" / name).is_dir() for name in subject.package_names):
            files = [
                {
                    "relative_path": path.relative_to(root).as_posix(),
                    "byte_sha256": digest_bytes(path.read_bytes()),
                }
                for path in attestor_module._checkout_source_files(root, subject)
            ]
        value = {
            "repository_root": str(root),
            "commit_oid": self._commit or subject.source_commit,
            "git_tree_oid": self._tree or config.merged_main_git_tree,
            "git_status_sha256": digest_bytes(b""),
            "files": files,
        }
        payload = canonical_bytes(value)
        return SourceObservation(
            record=SourceRecord(
                role="local.inputs",
                locator=f"local-checkout://{root}",
                repository=subject.repository,
                read_mode="EXACT_GIT_SNAPSHOT",
                identity=tuple(
                    sorted(
                        {
                            "repository_root": value["repository_root"],
                            "commit_oid": value["commit_oid"],
                            "git_tree_oid": value["git_tree_oid"],
                            "git_status_sha256": value["git_status_sha256"],
                            "file_set_digest": digest_value(files),
                            "observation_digest": digest_bytes(payload),
                        }.items()
                    )
                ),
                content_sha256=digest_bytes(payload),
                readback_digest=None,
                producer_sha256="d" * 64,
            ),
            canonical_payload=payload,
            complete=True,
        )


def _record(role: str, repository: str, producer: str) -> SourceRecord:
    payload = canonical_bytes({"role": role})
    return SourceRecord(
        role=role,
        locator=f"test://{role}",
        repository=repository,
        read_mode="TEST",
        identity=(("role", role),),
        content_sha256=digest_bytes(payload),
        readback_digest=None,
        producer_sha256=producer,
    )


def _readback(value):
    body = value.canonical()
    body.pop("readback_digest")
    return replace(value, readback_digest=digest_value(body))


def _writer_record(**values: object) -> WriterTransitionRecord:
    identity = {key: value for key, value in values.items() if key != "created_at"}
    record_id = f"writer-transition:{digest_value(identity)[:24]}"
    return WriterTransitionRecord(record_id=record_id, **values)


def _control_bytes(subject: CutoverSubject) -> tuple[bytes, bytes, bytes]:
    plan = "1" * 64
    pending = _writer_record(
        repository=subject.repository,
        kind="cutover_pending",
        status="pending",
        previous_writer_generation="v6.1",
        writer_generation="v8",
        activation_id=None,
        plan_digest=plan,
        canary_evidence_digest="2" * 64,
        canary_evidence_refs=("canary:1",),
        canary_manifest_ref="manifest:1",
        worker_capacity=0,
        coordinator_capacity=0,
        reason=None,
        created_at="2026-08-10T00:00:00Z",
    )
    draining = _writer_record(
        repository=subject.repository,
        kind="drain",
        status="draining",
        previous_writer_generation="v8",
        writer_generation="v8",
        activation_id=None,
        plan_digest=plan,
        canary_evidence_digest="2" * 64,
        canary_evidence_refs=("canary:1",),
        canary_manifest_ref="manifest:1",
        worker_capacity=0,
        coordinator_capacity=0,
        reason="drain",
        created_at="2026-08-10T00:00:01Z",
    )
    rollback = _writer_record(
        repository=subject.repository,
        kind="rollback",
        status="rolled_back",
        previous_writer_generation="v8",
        writer_generation="v6.1",
        activation_id=None,
        plan_digest=plan,
        canary_evidence_digest=None,
        canary_evidence_refs=("canary:1",),
        canary_manifest_ref="manifest:1",
        worker_capacity=0,
        coordinator_capacity=0,
        reason="drain",
        created_at="2026-08-10T00:00:02Z",
    )
    writer = {
        "schema_version": 1,
        "current": {
            "repository": subject.repository,
            "writer_generation": "v6.1",
            "record_id": rollback.record_id,
        },
        "records": [
            {
                **pending.__dict__,
                "canary_evidence_refs": list(pending.canary_evidence_refs),
            },
            {
                **draining.__dict__,
                "canary_evidence_refs": list(draining.canary_evidence_refs),
            },
            {
                **rollback.__dict__,
                "canary_evidence_refs": list(rollback.canary_evidence_refs),
            },
        ],
    }
    active = {
        "schema_version": 1,
        "repository": subject.repository,
        "active_plan_digest": plan,
        "receipts": [
            {
                "schema_version": 1,
                "repository": subject.repository,
                "writer_generation": "v8",
                "activation_id": "activation:1",
                "plan_digest": plan,
                "expected_previous_digest": None,
                "plan_record_ref": "plan:1",
                "created_at": "2026-08-10T00:00:00Z",
            }
        ],
    }
    legacy = {
        "schema_version": 1,
        "repository": subject.repository,
        "stopped": True,
        "events": [{"action_key": "stop:1", "operation": "stop"}],
    }
    return canonical_bytes(writer), canonical_bytes(active), canonical_bytes(legacy)


def _sources(control: _ControlFixture) -> ControlOwnershipSourceSet:
    subject = _subject()
    control.writer_bytes, control.active_plan_bytes, control.legacy_fence_bytes = _control_bytes(subject)
    return ControlOwnershipSourceSet(
        control=control,
        runtime_registry=_Registry(),
        runtime_config=_RuntimeConfig(),
        local_inputs=_LocalInputs(),
    )


def _config(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        repository="owner/repo",
        control_branch="gwo-control",
        target_branch="main",
        source_writer_generation="v6.1",
        target_writer_generation="v8",
        merged_main_sha="a" * 40,
        merged_main_git_tree="b" * 40,
        audited_source_tree_digest="b" * 64,
        repository_root=tmp_path,
        install_roots=(tmp_path / ".agents", tmp_path / ".codex", tmp_path / ".claude"),
        package_names=("implement-gwo", "orchestrator"),
        expected_package_version="8.0.0",
        expected_package_content_digests=(
            ("implement-gwo", "1" * 64),
            ("orchestrator", "2" * 64),
        ),
        fresh_store=tmp_path / "store.sqlite3",
        fresh_receipt=tmp_path / "receipt.json",
        expected_fresh_store_sha256="e" * 64,
        expected_fresh_receipt_sha256="f" * 64,
        expected_fresh_receipt_runbook_sha256="3" * 64,
        expected_fresh_receipt_schema_digest="4" * 64,
        expected_fresh_receipt_generation_rows=(("owner/repo", "store:v8:test"),),
        expected_fresh_receipt_row_counts=(),
        rollback_store=tmp_path / "rollback.sqlite3",
        expected_rollback_store_sha256="5" * 64,
        prior_store=tmp_path / "prior.sqlite3",
        expected_prior_store_sha256="6" * 64,
        runtime_config_path=tmp_path / "runtime-config.json",
        store_generation="store:v8:test",
        expected_store_tables=(),
    )


def _config_with_identity(
    *,
    merged_main_sha: str,
    merged_main_git_tree: str,
    audited_source_tree_digest: str,
) -> SimpleNamespace:
    _, config = _production_subject_and_config(
        ROOT / ".codex-tmp" / "control-attestor-fixture"
    )
    config.merged_main_sha = merged_main_sha
    config.merged_main_git_tree = merged_main_git_tree
    config.audited_source_tree_digest = audited_source_tree_digest
    return config


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

    attestor_module._validate_config_subject(config, subject, release_subject)


def test_swapping_git_tree_and_audited_source_digest_fails_before_readers():
    subject = _subject_with_source_tree_digest("b" * 40)
    config = _config_with_identity(
        merged_main_sha=subject.source_commit,
        merged_main_git_tree="c" * 64,
        audited_source_tree_digest="b" * 40,
    )
    release_subject = _release_subject(
        merged_main_sha=subject.source_commit,
        merged_main_git_tree="c" * 40,
        audited_source_tree_digest="b" * 64,
    )

    with pytest.raises(BootstrapError) as error:
        attestor_module._validate_config_subject(config, subject, release_subject)
    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    "field",
    (
        "repository",
        "control_branch",
        "target_branch",
        "source_writer_generation",
        "target_writer_generation",
        "merged_main_sha",
        "merged_main_git_tree",
        "audited_source_tree_digest",
        "repository_root",
        "fresh_store",
        "expected_fresh_store_sha256",
        "fresh_receipt",
        "expected_fresh_receipt_sha256",
        "expected_fresh_receipt_runbook_sha256",
        "expected_fresh_receipt_schema_digest",
        "expected_fresh_receipt_generation_rows",
        "expected_fresh_receipt_row_counts",
        "rollback_store",
        "expected_rollback_store_sha256",
        "prior_store",
        "expected_prior_store_sha256",
        "runtime_config_path",
        "store_generation",
        "expected_store_tables",
        "install_roots",
        "package_names",
        "expected_package_version",
        "expected_package_content_digests",
    ),
)
def test_config_requires_every_fixed_identity(tmp_path, field):
    config = _config(tmp_path)
    delattr(config, field)

    with pytest.raises(BootstrapError) as error:
        attestor_module._validate_config_subject(
            config,
            _subject(),
            _release_subject_for(_subject()),
        )

    assert error.value.code in {
        "STATIC_INPUT_SOURCE_UNAVAILABLE",
        "STORE_SOURCE_UNAVAILABLE",
        "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE",
        "PACKAGE_SOURCE_UNAVAILABLE",
    }


def _production_subject_and_config(tmp_path):
    subject = replace(
        _subject(),
        repository="NOirBRight/github-work-orchestrator",
        source_commit="5de34bdaee45f0aba44077a8d1d3e3ed8293f237",
        source_tree_digest="c" * 64,
        store_generation="store:v8:production:20260809T081500Z",
    )
    config = _config(tmp_path)
    config.repository = subject.repository
    config.merged_main_sha = subject.source_commit
    config.merged_main_git_tree = "104ee822dbfb494d33d56b8ccf54092d9d1d9c86"
    config.audited_source_tree_digest = subject.source_tree_digest
    config.repository_root = Path(r"D:\Workstation\github-work-orchestrator")
    config.fresh_store = Path(
        r"C:\Users\noirb\.orch\v8\NOirBRight__github-work-orchestrator"
        r"\store-20260809T081500Z.sqlite3"
    )
    config.store_generation = subject.store_generation
    config.expected_fresh_store_sha256 = (
        "afff1078e7a65fb8acccde28fee78fab3cf2278db9dd6548f5ef96a882076b98"
    )
    config.fresh_receipt = Path(
        r"D:\gwo-release-evidence\2026-08-09-gwo-v8-beta3-production-cutover"
        r"\fresh-store-exact-main-receipt.json"
    )
    config.expected_fresh_receipt_sha256 = "8" * 64
    config.rollback_store = Path(
        r"C:\Users\noirb\.orch\v8\NOirBRight__github-work-orchestrator\store.sqlite3"
    )
    config.expected_rollback_store_sha256 = (
        "1cc3f304044032fdab9569f8561b28220ecfd93e4efc35cf6bb2e492c1ca72b8"
    )
    config.prior_store = Path(
        r"C:\Users\noirb\.orch\v8\NOirBRight__github-work-orchestrator"
        r"\store-20260809T023000Z.sqlite3"
    )
    config.expected_prior_store_sha256 = (
        "df2341d76eb2ab54110ac3e70ff137a93d05ffbb02352c61b654321dba188ed7"
    )
    config.expected_fresh_receipt_runbook_sha256 = (
        "329bade311df03d0b52a344ce7062c7c7984e2fa35b3d0fa9cbb5386a88e0c6c"
    )
    config.expected_fresh_receipt_schema_digest = (
        "69ac6babce5db564fcc60fc5dd97feb0635911e07955234098210ddd97a93aed"
    )
    config.expected_fresh_receipt_generation_rows = (
        (subject.repository, subject.store_generation),
    )
    config.expected_store_tables = attestor_module._fixed_store_contract()[0]
    config.expected_fresh_receipt_row_counts = tuple(
        (
            table,
            1 if table == "v8_writer_generations" else 0,
        )
        for table in config.expected_store_tables
    )
    config.runtime_config_path = Path(r"C:\Users\noirb\.orch\config.json")
    config.install_roots = tuple(
        Path(rf"C:\Users\noirb\{surface}\skills")
        for surface in (".agents", ".codex", ".claude")
    )
    config.expected_package_content_digests = (
        ("implement-gwo", "fcafa60645a2ea18408ec97369fdf5a01402a950b90e701fa2305624a1bfeaa9"),
        ("orchestrator", "aebe7ea61bd41e848dd4c84f68569e4f359a6b2eaba5293507d637d5c47a9c24"),
    )
    return subject, config


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("repository_root", Path(r"D:\different")),
        ("fresh_store", Path(r"C:\different\store.sqlite3")),
        ("fresh_receipt", Path(r"D:\different\receipt.json")),
        ("expected_fresh_receipt_sha256", "0" * 64),
        ("rollback_store", Path(r"C:\different\rollback.sqlite3")),
        ("expected_rollback_store_sha256", "0" * 64),
        ("prior_store", Path(r"C:\different\prior.sqlite3")),
        ("expected_prior_store_sha256", "0" * 64),
        ("expected_fresh_receipt_runbook_sha256", "0" * 64),
        ("expected_fresh_receipt_schema_digest", "0" * 64),
        ("expected_store_tables", ("unexpected",)),
        ("expected_fresh_receipt_generation_rows", (("other/repo", "store:v8:other"),)),
        ("expected_fresh_receipt_row_counts", (("unexpected", 1),)),
        ("runtime_config_path", Path(r"C:\different\config.json")),
        ("install_roots", (Path(r"C:\different"),) * 3),
        ("expected_package_content_digests", (("implement-gwo", "0" * 64), ("orchestrator", "0" * 64))),
    ),
)
def test_production_configuration_requires_every_global_fixed_identity(
    tmp_path,
    field,
    replacement,
):
    subject, config = _production_subject_and_config(tmp_path)
    setattr(config, field, replacement)

    with pytest.raises(BootstrapError):
        attestor_module._validate_config_subject(config, subject, _release_subject_for(subject))


def test_production_configuration_accepts_exact_global_fixed_identities(tmp_path):
    subject, config = _production_subject_and_config(tmp_path)

    attestor_module._validate_config_subject(config, subject, _release_subject_for(subject))


def test_production_package_digest_constants_match_source_manifests():
    observed = {
        package.name: json.loads(
            (package / ".skill-package.json").read_text(encoding="utf-8")
        )["content_sha256"]
        for package in (
            ROOT / "skills" / "implement-gwo",
            ROOT / "skills" / "orchestrator",
        )
    }

    assert dict(attestor_module.PRODUCTION_PACKAGE_CONTENT_DIGESTS) == observed


def test_production_configuration_accepts_subject_bound_fresh_store_identity(tmp_path):
    subject, config = _production_subject_and_config(tmp_path)
    subject = replace(subject, store_generation="store:v8:production:20260815T120000Z")
    config.fresh_store = attestor_module.PRODUCTION_STORE.parent / "store-20260815T120000Z.sqlite3"
    config.store_generation = subject.store_generation
    config.expected_fresh_store_sha256 = "9" * 64
    config.expected_fresh_receipt_generation_rows = (
        (subject.repository, config.store_generation),
    )

    attestor_module._validate_config_subject(config, subject, _release_subject_for(subject))


@pytest.mark.parametrize("existing_store", ("prior_store", "rollback_store"))
def test_production_configuration_rejects_fresh_store_aliasing_existing_store(
    tmp_path,
    existing_store,
):
    subject, config = _production_subject_and_config(tmp_path)
    config.fresh_store = getattr(config, existing_store)

    with pytest.raises(BootstrapError) as error:
        attestor_module._validate_config_subject(config, subject, _release_subject_for(subject))

    assert error.value.code == "STORE_SOURCE_UNAVAILABLE"


def test_production_configuration_accepts_nonlegacy_receipt_digest_bound_by_v2_subject(
    tmp_path,
):
    subject, config = _production_subject_and_config(tmp_path)
    release_subject = _release_subject_for(subject)
    config.expected_fresh_receipt_sha256 = release_subject.fresh_receipt_sha256

    attestor_module._validate_config_subject(config, subject, release_subject)


def test_production_configuration_rejects_receipt_digest_not_bound_to_v2_subject(tmp_path):
    subject, config = _production_subject_and_config(tmp_path)
    release_subject = _release_subject_for(subject)
    config.expected_fresh_receipt_sha256 = "f" * 64

    with pytest.raises(BootstrapError) as error:
        attestor_module._validate_config_subject(config, subject, release_subject)

    assert error.value.code == "STORE_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("invalid_digest", (None, "not-a-sha256"))
def test_production_configuration_rejects_invalid_v2_subject_receipt_digest(
    tmp_path,
    invalid_digest,
):
    subject, config = _production_subject_and_config(tmp_path)
    release_subject = _release_subject_for(subject)
    object.__setattr__(release_subject, "fresh_receipt_sha256", invalid_digest)

    with pytest.raises(BootstrapError) as error:
        attestor_module._validate_config_subject(config, subject, release_subject)

    assert error.value.code == "STORE_SOURCE_UNAVAILABLE"


def test_production_configuration_rejects_missing_v2_subject_receipt_digest(tmp_path):
    subject, config = _production_subject_and_config(tmp_path)
    release_subject = _release_subject_for(subject)
    object.__delattr__(release_subject, "fresh_receipt_sha256")

    with pytest.raises(BootstrapError) as error:
        attestor_module._validate_config_subject(config, subject, release_subject)

    assert error.value.code == "STORE_SOURCE_UNAVAILABLE"


def test_production_configuration_rejects_runtime_config_path_alias(tmp_path):
    subject, config = _production_subject_and_config(tmp_path)
    config.runtime_config_path = Path(
        r"C:\Users\noirb\.orch\nested\..\config.json"
    )

    with pytest.raises(BootstrapError) as error:
        attestor_module._validate_config_subject(config, subject, _release_subject_for(subject))

    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("role", ("control", "runtime_registry", "runtime_config", "local_inputs"))
@pytest.mark.parametrize("extra_method", ("write_file", "synchronize", "unexpected_read"))
def test_sources_expose_only_the_exact_read_only_surface(
    control_fixture,
    role,
    extra_method,
):
    sources = _sources(control_fixture)
    original = getattr(sources, role)
    unsafe_type = type(
        f"Unsafe{role.title()}",
        (type(original),),
        {extra_method: lambda self: None},
    )
    unsafe = unsafe_type() if role != "control" else unsafe_type()

    with pytest.raises(BootstrapError) as error:
        ControlOwnershipAttestor(replace(sources, **{role: unsafe}))

    assert error.value.code == "UNSAFE_SOURCE_CAPABILITY"


def test_check_source_rejects_instance_dir_hiding_public_callable():
    class Hiding:
        def read(self):
            return object()

        def publish(self):
            return object()

        def __dir__(self):
            return ["read"]

    with pytest.raises(BootstrapError) as error:
        ControlOwnershipAttestor._check_source(Hiding(), ("read",))

    assert error.value.code == "UNSAFE_SOURCE_CAPABILITY"


def test_check_source_rejects_instance_dynamic_getattr_callable():
    class Dynamic:
        def read(self):
            return object()

        def __getattr__(self, name):
            if name == "publish":
                return lambda: object()
            raise AttributeError(name)

    with pytest.raises(BootstrapError) as error:
        ControlOwnershipAttestor._check_source(Dynamic(), ("read",))

    assert error.value.code == "UNSAFE_SOURCE_CAPABILITY"


def test_check_source_rejects_instance_dynamic_getattribute_callable():
    class Dynamic:
        def read(self):
            return object()

        def __getattribute__(self, name):
            if name == "publish":
                return lambda: object()
            return object.__getattribute__(self, name)

    with pytest.raises(BootstrapError) as error:
        ControlOwnershipAttestor._check_source(Dynamic(), ("read",))

    assert error.value.code == "UNSAFE_SOURCE_CAPABILITY"


def test_check_source_rejects_custom_metaclass_even_with_exact_declared_surface():
    class CustomMeta(type):
        pass

    class Source(metaclass=CustomMeta):
        def read(self):
            return object()

    with pytest.raises(BootstrapError) as error:
        ControlOwnershipAttestor._check_source(Source(), ("read",))

    assert error.value.code == "UNSAFE_SOURCE_CAPABILITY"


def test_control_rejects_shadowed_gwo_v8_dependency(control_fixture, monkeypatch, tmp_path):
    shadow_root = tmp_path / "shadow-gwo-v8"
    shadow_root.mkdir()
    shadow_package = SimpleNamespace(
        __file__=str(shadow_root / "__init__.py"),
        __path__=[str(shadow_root)],
        __spec__=SimpleNamespace(origin=str(shadow_root / "__init__.py")),
    )
    monkeypatch.setitem(sys.modules, "gwo_v8", shadow_package)

    with pytest.raises(BootstrapError) as error:
        ControlOwnershipAttestor(_sources(control_fixture))

    assert error.value.code == "UNSAFE_SOURCE_CAPABILITY"


def test_control_reads_every_blob_at_one_fixed_oid(control_fixture, tmp_path, monkeypatch):
    subject = _subject()
    store_record = _record("store.sqlite", subject.repository, _attempt(subject).attestor_sha256)
    receipt_record = _record("store.receipt", subject.repository, _attempt(subject).attestor_sha256)
    durable = _readback(
        DurableStateReadback(
            repository=subject.repository,
            generation_id=subject.store_generation,
            state_schema="gwo.v8.store.v1",
            compatible=True,
            active_plan_digests=(),
            pending_activation_ids=(),
            predecessor_identity_refs=(),
            readback_digest="",
        )
    )
    store = SimpleNamespace(
        store_record=store_record,
        receipt_record=receipt_record,
        durable=durable,
        active_admissions=(),
        active_attempts=(),
        integration_lease_owner=None,
        resource_claims=(),
    )
    compatibility = _readback(
        CompatibilityPathReadback(
            repository=subject.repository,
            source_commit=subject.source_commit,
            source_tree_digest=subject.source_tree_digest,
            audit_version="test",
            reachable_v2_projection_refs=(),
            reachable_v3_compatibility_refs=(),
            reachable_legacy_writer_refs=(),
                proven_unreachable_refs=tuple(sorted(subject.forbidden_production_refs)),
            readback_digest="",
        )
    )
    packages = _readback(PackageReadback(source_packages=(), installed_packages=(), drift=(), readback_digest=""))
    static_record = _record("compatibility.module", subject.repository, _attempt(subject).attestor_sha256)
    package_record = _record("package.file", subject.repository, _attempt(subject).attestor_sha256)
    monkeypatch.setattr(attestor_module, "_read_store", lambda *_args: store)
    monkeypatch.setattr(attestor_module, "_validate_package_identity_config", lambda *_args: None)
    monkeypatch.setattr(
        attestor_module,
        "_validate_checkout_file_bindings",
        lambda *_args: None,
    )
    monkeypatch.setattr(attestor_module, "_static_records", lambda *_args, **_kwargs: [static_record])
    monkeypatch.setattr(attestor_module, "_package_records", lambda *_args, **_kwargs: [package_record])
    monkeypatch.setattr(
        attestor_module,
        "_read_stable_static_inputs",
        lambda *_args, **_kwargs: (compatibility, [static_record]),
    )
    monkeypatch.setattr(
        attestor_module,
        "_read_stable_package_inputs",
        lambda *_args, **_kwargs: (packages, [package_record]),
    )
    monkeypatch.setattr(attestor_module, "ProductionPathScanner", lambda _root: SimpleNamespace(read=lambda _subject: compatibility))
    monkeypatch.setattr(attestor_module, "ReadOnlyPackageValidator", lambda *_args: SimpleNamespace(read=lambda _subject: packages))
    config = _config(tmp_path)
    observation = ControlOwnershipAttestor(_sources(control_fixture)).observe(
        config=config,
        subject=subject,
        attempt=_attempt(subject),
        release_subject=_release_subject_for(subject),
    )
    assert observation.writer_authority is not None
    assert control_fixture.calls == [
        ("read_ref", subject.repository, "gwo-control"),
        ("read_at_oid", subject.repository, control_fixture.oid, ".gwo-v8/writer-transition.json"),
        ("read_at_oid", subject.repository, control_fixture.oid, ".gwo/v8/active-plan.json"),
        ("read_at_oid", subject.repository, control_fixture.oid, ".gwo-v8/legacy-writer-fence.json"),
    ]


def test_control_source_records_retain_each_blob_byte_digest(
    control_fixture, tmp_path, monkeypatch
):
    subject = _subject()
    attempt = _attempt(subject)
    store_record = _record("store.sqlite", subject.repository, attempt.attestor_sha256)
    receipt_record = _record("store.receipt", subject.repository, attempt.attestor_sha256)
    durable = _readback(
        DurableStateReadback(
            repository=subject.repository,
            generation_id=subject.store_generation,
            state_schema="gwo.v8.store.v1",
            compatible=True,
            active_plan_digests=(),
            pending_activation_ids=(),
            predecessor_identity_refs=(),
            readback_digest="",
        )
    )
    store = SimpleNamespace(
        store_record=store_record,
        receipt_record=receipt_record,
        durable=durable,
        active_admissions=(),
        active_attempts=(),
        integration_lease_owner=None,
        resource_claims=(),
    )
    compatibility = _readback(
        CompatibilityPathReadback(
            repository=subject.repository,
            source_commit=subject.source_commit,
            source_tree_digest=subject.source_tree_digest,
            audit_version="test",
            reachable_v2_projection_refs=(),
            reachable_v3_compatibility_refs=(),
            reachable_legacy_writer_refs=(),
            proven_unreachable_refs=tuple(sorted(subject.forbidden_production_refs)),
            readback_digest="",
        )
    )
    packages = _readback(
        PackageReadback(source_packages=(), installed_packages=(), drift=(), readback_digest="")
    )
    static_record = _record("compatibility.module", subject.repository, attempt.attestor_sha256)
    package_record = _record("package.file", subject.repository, attempt.attestor_sha256)
    monkeypatch.setattr(attestor_module, "_read_store", lambda *_args: store)
    monkeypatch.setattr(attestor_module, "_validate_package_identity_config", lambda *_args: None)
    monkeypatch.setattr(
        attestor_module,
        "_validate_checkout_file_bindings",
        lambda *_args: None,
    )
    monkeypatch.setattr(attestor_module, "_static_records", lambda *_args, **_kwargs: [static_record])
    monkeypatch.setattr(attestor_module, "_package_records", lambda *_args, **_kwargs: [package_record])
    monkeypatch.setattr(
        attestor_module,
        "_read_stable_static_inputs",
        lambda *_args, **_kwargs: (compatibility, [static_record]),
    )
    monkeypatch.setattr(
        attestor_module,
        "_read_stable_package_inputs",
        lambda *_args, **_kwargs: (packages, [package_record]),
    )
    monkeypatch.setattr(
        attestor_module,
        "ProductionPathScanner",
        lambda _root: SimpleNamespace(read=lambda _subject: compatibility),
    )
    monkeypatch.setattr(
        attestor_module,
        "ReadOnlyPackageValidator",
        lambda *_args: SimpleNamespace(read=lambda _subject: packages),
    )

    observation = ControlOwnershipAttestor(_sources(control_fixture)).observe(
        config=_config(tmp_path),
        subject=subject,
        attempt=attempt,
        release_subject=_release_subject_for(subject),
    )

    control_records = {
        record.role: dict(record.identity)
        for record in observation.source_records
        if record.role.startswith("control.")
    }
    assert control_records["control.writer"]["byte_sha256"] == digest_bytes(
        control_fixture.writer_bytes
    )
    assert control_records["control.active_plan"]["byte_sha256"] == digest_bytes(
        control_fixture.active_plan_bytes
    )
    assert control_records["control.legacy_fence"]["byte_sha256"] == digest_bytes(
        control_fixture.legacy_fence_bytes
    )


    checkout_digest = next(
        record.digest
        for record in observation.source_records
        if record.role == "local.inputs"
    )
    for readback_name in ("compatibility", "packages"):
        bindings = [
            binding
            for binding in observation.field_bindings
            if binding.target.startswith(f"{readback_name}.")
        ]
        assert bindings
        assert all(
            checkout_digest in binding.source_record_digests
            for binding in bindings
        )


def test_control_rejects_missing_record_instead_of_initial_writer_fallback(
    control_fixture,
    tmp_path,
):
    subject = _subject()
    sources = _sources(control_fixture)
    control_fixture.writer_bytes = None
    with pytest.raises(Exception) as error:
        ControlOwnershipAttestor(sources).observe(
            config=_config(tmp_path),
            subject=subject,
            attempt=_attempt(subject),
            release_subject=_release_subject_for(subject),
        )
    assert getattr(error.value, "code", None) == "WRITER_FENCE_SOURCE_UNAVAILABLE"


def test_control_rejects_blob_identity_mismatch(control_fixture, tmp_path):
    subject = _subject()
    sources = _sources(control_fixture)

    class MismatchedBlob(_ControlFixture):
        def read_ref(self, repository: str, branch: str) -> _Ref:
            return control_fixture.read_ref(repository, branch)

        def read_at_oid(self, repository: str, oid: str, path: str) -> _ExactBlob | None:
            value = control_fixture.read_at_oid(repository, oid, path)
            if value is None:
                return None
            return replace(value, blob_sha="0" * 40)

    with pytest.raises(Exception) as error:
        ControlOwnershipAttestor(
            replace(sources, control=MismatchedBlob())
        ).observe(
            config=_config(tmp_path),
            subject=subject,
            attempt=_attempt(subject),
            release_subject=_release_subject_for(subject),
        )
    assert getattr(error.value, "code", None) == "WRITER_FENCE_SOURCE_UNAVAILABLE"


def test_control_rejects_unsafe_source_capability(control_fixture):
    class UnsafeControl(_ControlFixture):
        def compare_and_swap(self):
            raise AssertionError("must not be called")

    with pytest.raises(Exception) as error:
        ControlOwnershipAttestor(
            replace(_sources(control_fixture), control=UnsafeControl())
        )
    assert getattr(error.value, "code", None) == "UNSAFE_SOURCE_CAPABILITY"


def test_runtime_config_rejects_replaced_exact_path(control_fixture, tmp_path, monkeypatch):
    subject = _subject()
    attempt = _attempt(subject)
    sources = _sources(control_fixture)
    config_path = _config(tmp_path).runtime_config_path.resolve()
    payload = _RuntimeConfig().read(config_path).canonical_payload
    record = SourceRecord(
        role="runtime.config",
        locator=str(Path("C:/replaced/config.json")),
        repository=subject.repository,
        read_mode="EXACT_FILE",
        identity=(
            ("byte_sha256", digest_bytes(payload)),
            ("inode", "1"),
            ("mtime_ns", "2"),
            ("path", str(config_path)),
            ("size", str(len(payload))),
        ),
        content_sha256=digest_bytes(payload),
        readback_digest=None,
        producer_sha256=attempt.attestor_sha256,
    )
    replaced = SourceObservation(record=record, canonical_payload=payload, complete=True)
    sources = replace(sources, runtime_config=SimpleNamespace(read=lambda _path: replaced))
    store = SimpleNamespace(
        store_record=_record("store.sqlite", subject.repository, attempt.attestor_sha256),
        receipt_record=_record("store.receipt", subject.repository, attempt.attestor_sha256),
        durable=_readback(
            DurableStateReadback(
                repository=subject.repository,
                generation_id=subject.store_generation,
                state_schema="gwo.v8.store.v1",
                compatible=True,
                active_plan_digests=(),
                pending_activation_ids=(),
                predecessor_identity_refs=(),
                readback_digest="",
            )
        ),
        active_admissions=(),
        active_attempts=(),
        integration_lease_owner=None,
        resource_claims=(),
    )
    compatibility = _readback(
        CompatibilityPathReadback(
            repository=subject.repository,
            source_commit=subject.source_commit,
            source_tree_digest=subject.source_tree_digest,
            audit_version="test",
            reachable_v2_projection_refs=(),
            reachable_v3_compatibility_refs=(),
            reachable_legacy_writer_refs=(),
            proven_unreachable_refs=tuple(sorted(subject.forbidden_production_refs)),
            readback_digest="",
        )
    )
    packages = _readback(
        PackageReadback(source_packages=(), installed_packages=(), drift=(), readback_digest="")
    )
    monkeypatch.setattr(attestor_module, "_read_store", lambda *_args: store)
    monkeypatch.setattr(
        attestor_module,
        "ProductionPathScanner",
        lambda _root: SimpleNamespace(read=lambda _subject: compatibility),
    )
    monkeypatch.setattr(
        attestor_module,
        "ReadOnlyPackageValidator",
        lambda *_args: SimpleNamespace(read=lambda _subject: packages),
    )
    with pytest.raises(Exception) as error:
        ControlOwnershipAttestor(sources).observe(
            config=_config(tmp_path),
            subject=subject,
            attempt=attempt,
            release_subject=_release_subject_for(subject),
        )
    assert getattr(error.value, "code", None) == "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE"


def test_runtime_config_accepts_current_optional_configuration_keys():
    valid = load_canonical_json(_RuntimeConfig().read().canonical_payload)
    extended = {
        **valid,
        "schema_version": 1,
        "global": {**valid["global"], "execution_slots": 3},
        "reviewer_tiers": {"standard": "standard", "strict": "heavy"},
        "repositories": {"owner/repo": {"default_tier": "standard"}},
    }
    _configuration, runtime = attestor_module._runtime_config_value(
        canonical_bytes(extended), "owner/repo"
    )
    assert tuple(item.selector for item in runtime.selectors) == attestor_module.RUNTIME_SELECTORS


def test_source_observation_rejects_content_hash_drift():
    subject = _subject()
    attempt = _attempt(subject)
    payload = canonical_bytes({"observed": "bytes"})
    record = SourceRecord(
        role="runtime.registry",
        locator="runtime-registry://owner/repo",
        repository=subject.repository,
        read_mode="COMPLETE_OBSERVATION",
        identity=(("observation_digest", digest_bytes(payload)),),
        content_sha256=digest_bytes(payload),
        readback_digest=None,
        producer_sha256=attempt.attestor_sha256,
    )
    object.__setattr__(record, "content_sha256", digest_bytes(b"different"))
    observation = object.__new__(SourceObservation)
    object.__setattr__(observation, "record", record)
    object.__setattr__(observation, "canonical_payload", payload)
    object.__setattr__(observation, "complete", True)

    with pytest.raises(BootstrapError) as error:
        attestor_module._source_observation(
            observation,
            role="runtime.registry",
            repository=subject.repository,
            producer_sha256=attempt.attestor_sha256,
            default_locator="runtime-registry://owner/repo",
            default_read_mode="COMPLETE_OBSERVATION",
        )
    assert error.value.code == "RUNTIME_REGISTRY_SOURCE_UNAVAILABLE"


def test_local_file_snapshot_rejects_reparse_parent(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    source = target / "source.json"
    source.write_bytes(b"{}")
    redirected = tmp_path / "redirected"
    try:
        redirected.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("directory reparse/symlink creation is unavailable")

    with pytest.raises(BootstrapError) as error:
        attestor_module._read_file_snapshot(
            redirected / source.name,
            "STATIC_INPUT_SOURCE_UNAVAILABLE",
        )

    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


def test_local_checkout_source_rejects_reparse_path_before_read(tmp_path, monkeypatch):
    target = _write_static_fixture(tmp_path)
    redirected = tmp_path / "redirected"
    try:
        redirected.symlink_to(target.parent, target_is_directory=True)
    except OSError:
        pytest.skip("directory reparse/symlink creation is unavailable")

    monkeypatch.setattr(
        attestor_module,
        "_checkout_source_files",
        lambda *_args: (redirected / target.name,),
    )
    config = _config(tmp_path)
    subject = _subject()
    responses = iter(
        (
            subject.source_commit.encode("ascii"),
            config.merged_main_git_tree.encode("ascii"),
            b"",
        )
    )

    with pytest.raises(BootstrapError) as error:
        attestor_module._LocalInputsSource(
            lambda _command: next(responses), "d" * 64
        ).read(config, subject)

    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


def test_local_tree_rejects_parent_replacement_during_scan(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "source.py").write_bytes(b"original")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "source.py").write_bytes(b"replacement")
    original_scandir = os.scandir
    replaced = False

    def replacing_scandir(path):
        nonlocal replaced
        if Path(path) == root and not replaced:
            original = tmp_path / "original-root"
            root.rename(original)
            root.symlink_to(replacement, target_is_directory=True)
            replaced = True
        return original_scandir(path)

    def replacing_enumeration(path, _held, _code):
        return replacing_scandir(path)

    monkeypatch.setattr(attestor_module, "_enumerate_held_directory", replacing_enumeration)

    with pytest.raises(BootstrapError) as error:
        attestor_module._local_tree_files(root, "STATIC_INPUT_SOURCE_UNAVAILABLE")

    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


def test_local_tree_rejects_reparse_ancestor(tmp_path):
    real_root = tmp_path / "real-root"
    scanned = real_root / "tree"
    scanned.mkdir(parents=True)
    (scanned / "source.py").write_bytes(b"source")
    redirected = tmp_path / "redirected"
    try:
        redirected.symlink_to(real_root, target_is_directory=True)
    except OSError:
        pytest.skip("directory reparse/symlink creation is unavailable")

    with pytest.raises(BootstrapError) as error:
        attestor_module._local_tree_files(
            redirected / "tree",
            "STATIC_INPUT_SOURCE_UNAVAILABLE",
        )

    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


def test_local_tree_reads_normal_path_without_reparse(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    source = root / "source.py"
    source.write_bytes(b"source")

    assert attestor_module._local_tree_files(
        root,
        "STATIC_INPUT_SOURCE_UNAVAILABLE",
    ) == (source.resolve(),)


@pytest.mark.skipif(os.name != "nt", reason="Windows file-ID root identity contract")
def test_local_tree_rejects_same_metadata_root_replacement_before_first_scan(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "root"
    root.mkdir()
    source = root / "source.py"
    source.write_bytes(b"original")
    replaced = False

    original_identity = attestor_module._held_directory_identity

    def replacing_identity(path, code):
        nonlocal replaced
        observed = original_identity(path, code)
        if Path(path) == root and not replaced:
            original = tmp_path / "original-root"
            root.rename(original)
            replacement = tmp_path / "replacement-root"
            replacement.mkdir()
            (replacement / source.name).write_bytes(b"changed!")
            os.utime(
                replacement,
                ns=(observed["st_mtime_ns"], observed["st_mtime_ns"]),
            )
            replacement.rename(root)
            os.utime(root, ns=(observed["st_mtime_ns"], observed["st_mtime_ns"]))
            replacement_stat = root.stat()
            if replacement_stat.st_size != observed["st_size"]:
                pytest.skip("directory metadata cannot be preserved on this filesystem")
            replaced = True
        return observed

    monkeypatch.setattr(attestor_module, "_held_directory_identity", replacing_identity)

    with pytest.raises(BootstrapError) as error:
        attestor_module._local_tree_files(root, "STATIC_INPUT_SOURCE_UNAVAILABLE")

    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


def test_local_tree_rejects_scan_replacement_to_empty_subset_after_held_assertion(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "root"
    root.mkdir()
    (root / "source.py").write_bytes(b"original")
    original_scandir = os.scandir
    replaced = False

    class Scanner:
        def __enter__(self):
            return self

        def __iter__(self):
            nonlocal replaced
            original = tmp_path / "original-root"
            replacement = tmp_path / "replacement-root"
            root.rename(original)
            replacement.mkdir()
            replacement_scanner = original_scandir(replacement)
            replaced = True
            try:
                yield from replacement_scanner
            finally:
                replacement_scanner.close()
                replacement.rename(tmp_path / "empty-replacement-root")
                original.rename(root)

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    def replacing_scandir(path):
        if Path(path) == root and not replaced:
            return Scanner()
        return original_scandir(path)

    def replacing_enumeration(path, _held, _code):
        return replacing_scandir(path)

    monkeypatch.setattr(attestor_module, "_enumerate_held_directory", replacing_enumeration)

    with pytest.raises(BootstrapError) as error:
        attestor_module._local_tree_files(root, "STATIC_INPUT_SOURCE_UNAVAILABLE")

    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


@pytest.mark.skipif(os.name != "nt", reason="Windows file-ID identity contract")
def test_windows_entry_identity_uses_file_id_not_metadata(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    source = root / "source.py"
    source.write_bytes(b"source")

    from run_beta3_live_guard import (
        _close_descriptors,
        _open_directory_components,
        _open_path_handle,
        _windows_handle_identity,
    )

    with os.scandir(root) as scanner:
        entry = next(scanner)
        entry_stat = entry.stat(follow_symlinks=False)
        entry_inode = entry.inode()
    descriptors, _identities = _open_directory_components(root, "TEST")
    descriptor = None
    try:
        descriptor = _open_path_handle(
            Path(source.name),
            "TEST",
            directory=False,
            parent=descriptors[-1],
        )
        opened_identity = _windows_handle_identity(descriptor, "TEST", directory=False)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        _close_descriptors(descriptors)

    assert type(entry_inode) is int
    assert opened_identity["file_id"].startswith(
        entry_inode.to_bytes(8, "little", signed=False).hex()
    )
    assert attestor_module._entry_identity_matches(entry, entry_stat, opened_identity)
    replaced_identity = dict(opened_identity)
    replaced_identity["file_id"] = "0" * len(opened_identity["file_id"])
    assert not attestor_module._entry_identity_matches(
        entry,
        entry_stat,
        replaced_identity,
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows file-ID identity contract")
def test_windows_entry_identity_tolerates_directory_metadata_refresh(tmp_path):
    entry = SimpleNamespace(inode=lambda: 0x1234)
    entry_stat = SimpleNamespace(
        st_mode=0o40755,
        st_size=0,
        st_mtime_ns=100,
    )
    opened_identity = {
        "volume_id": 7,
        "file_id": (0x1234).to_bytes(8, "little").hex() + "00" * 8,
        "st_mode": 0o40755,
        "st_size": 4096,
        "st_mtime_ns": 200,
    }

    assert attestor_module._entry_identity_matches(
        entry,
        entry_stat,
        opened_identity,
    )


@pytest.mark.parametrize("changed_field", ("st_size", "st_mtime_ns"))
def test_windows_entry_identity_rejects_ordinary_file_metadata_change(
    monkeypatch,
    changed_field,
):
    monkeypatch.setattr(attestor_module.os, "name", "nt")
    entry = SimpleNamespace(inode=lambda: 0x1234)
    entry_stat = SimpleNamespace(
        st_mode=0o100644,
        st_size=6,
        st_mtime_ns=100,
    )
    opened_identity = {
        "volume_id": 7,
        "file_id": (0x1234).to_bytes(8, "little").hex() + "00" * 8,
        "st_mode": 0o100644,
        "st_size": 6,
        "st_mtime_ns": 100,
    }
    opened_identity[changed_field] = 7 if changed_field == "st_size" else 200

    assert not attestor_module._entry_identity_matches(
        entry,
        entry_stat,
        opened_identity,
    )


def test_local_tree_rejects_ordinary_parent_replacement_after_scan_assertion(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "root"
    root.mkdir()
    source = root / "source.py"
    source.write_bytes(b"original")
    original_scandir = os.scandir
    replaced = False

    class Entry:
        def __init__(self, entry):
            self._entry = entry
            self.name = entry.name
            self.path = entry.path

        def stat(self, *, follow_symlinks=True):
            nonlocal replaced
            observed = self._entry.stat(follow_symlinks=follow_symlinks)
            if not replaced:
                original = tmp_path / "original-root"
                root.rename(original)
                root.mkdir()
                (root / source.name).write_bytes(b"replacement")
                replaced = True
            return observed

    class Scanner:
        def __init__(self, scanner):
            self._scanner = scanner

        def __enter__(self):
            self._scanner.__enter__()
            return (Entry(entry) for entry in self._scanner)

        def __exit__(self, exc_type, exc_value, traceback):
            return self._scanner.__exit__(exc_type, exc_value, traceback)

    def replacing_scandir(path):
        scanner = original_scandir(path)
        if Path(path) == root:
            return Scanner(scanner)
        return scanner

    def replacing_enumeration(path, _held, _code):
        return replacing_scandir(path)

    monkeypatch.setattr(attestor_module, "_enumerate_held_directory", replacing_enumeration)

    with pytest.raises(BootstrapError) as error:
        attestor_module._local_tree_files(root, "STATIC_INPUT_SOURCE_UNAVAILABLE")

    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


def test_local_tree_rejects_child_file_replacement_after_entry_stat(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    source = root / "source.py"
    source.write_bytes(b"original")
    original_scandir = os.scandir
    replaced = False

    class Entry:
        def __init__(self, entry):
            self._entry = entry
            self.name = entry.name
            self.path = entry.path
            self._inode = entry.inode()

        def stat(self, *, follow_symlinks=True):
            nonlocal replaced
            observed = self._entry.stat(follow_symlinks=follow_symlinks)
            if self.name == source.name and not replaced:
                original = tmp_path / "original-source.py"
                source.rename(original)
                source.write_bytes(b"changed!")
                os.utime(source, ns=(observed.st_atime_ns, observed.st_mtime_ns))
                replaced = True
            return observed

        def inode(self):
            return self._inode

    class Scanner:
        def __init__(self, scanner):
            self._scanner = scanner

        def __enter__(self):
            self._scanner.__enter__()
            return (Entry(entry) for entry in self._scanner)

        def __exit__(self, exc_type, exc_value, traceback):
            return self._scanner.__exit__(exc_type, exc_value, traceback)

    def replacing_scandir(path):
        scanner = original_scandir(path)
        if Path(path) == root:
            return Scanner(scanner)
        return scanner

    def replacing_enumeration(path, _held, _code):
        return replacing_scandir(path)

    monkeypatch.setattr(attestor_module, "_enumerate_held_directory", replacing_enumeration)

    with pytest.raises(BootstrapError) as error:
        attestor_module._local_tree_files(root, "STATIC_INPUT_SOURCE_UNAVAILABLE")

    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


def test_local_tree_rejects_child_directory_replacement_after_entry_stat(tmp_path, monkeypatch):
    root = tmp_path / "root"
    child = root / "child"
    child.mkdir(parents=True)
    (child / "source.py").write_bytes(b"original")
    original_scandir = os.scandir
    replaced = False

    class Entry:
        def __init__(self, entry):
            self._entry = entry
            self.name = entry.name
            self.path = entry.path
            self._inode = entry.inode()

        def stat(self, *, follow_symlinks=True):
            nonlocal replaced
            observed = self._entry.stat(follow_symlinks=follow_symlinks)
            if self.name == child.name and not replaced:
                original = tmp_path / "original-child"
                child.rename(original)
                child.mkdir()
                (child / "source.py").write_bytes(b"changed!")
                os.utime(child, ns=(observed.st_atime_ns, observed.st_mtime_ns))
                replaced = True
            return observed

        def inode(self):
            return self._inode

    class Scanner:
        def __init__(self, scanner):
            self._scanner = scanner

        def __enter__(self):
            self._scanner.__enter__()
            return (Entry(entry) for entry in self._scanner)

        def __exit__(self, exc_type, exc_value, traceback):
            return self._scanner.__exit__(exc_type, exc_value, traceback)

    def replacing_scandir(path):
        scanner = original_scandir(path)
        if Path(path) == root:
            return Scanner(scanner)
        return scanner

    def replacing_enumeration(path, _held, _code):
        return replacing_scandir(path)

    monkeypatch.setattr(attestor_module, "_enumerate_held_directory", replacing_enumeration)

    with pytest.raises(BootstrapError) as error:
        attestor_module._local_tree_files(root, "STATIC_INPUT_SOURCE_UNAVAILABLE")

    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


def test_local_tree_rejects_dangling_reparse_ancestor_when_allow_missing(tmp_path):
    redirected = tmp_path / "redirected"
    try:
        redirected.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    except OSError:
        pytest.skip("directory reparse/symlink creation is unavailable")

    with pytest.raises(BootstrapError) as error:
        attestor_module._local_tree_files(
            redirected / "tree",
            "STATIC_INPUT_SOURCE_UNAVAILABLE",
            allow_missing=True,
        )

    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


def test_dynamic_sidecars_reject_dangling_reparse_parent(tmp_path):
    redirected = tmp_path / "redirected"
    try:
        redirected.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    except OSError:
        pytest.skip("directory reparse/symlink creation is unavailable")

    with pytest.raises(BootstrapError) as error:
        attestor_module._dynamic_sidecars(redirected / "store.sqlite3")

    assert error.value.code == "STORE_SOURCE_UNAVAILABLE"


def test_source_observation_rejects_role_or_read_mode_substitution():
    subject = _subject()
    attempt = _attempt(subject)
    payload = canonical_bytes({"observed": "bytes"})
    record = SourceRecord(
        role="substituted.role",
        locator="fixture://source",
        repository=subject.repository,
        read_mode="FIXTURE",
        identity=(("observation_digest", digest_bytes(payload)),),
        content_sha256=digest_bytes(payload),
        readback_digest=None,
        producer_sha256=attempt.attestor_sha256,
    )
    with pytest.raises(BootstrapError) as error:
        attestor_module._source_observation(
            SourceObservation(record=record, canonical_payload=payload, complete=True),
            role="runtime.registry",
            repository=subject.repository,
            producer_sha256=attempt.attestor_sha256,
            default_locator="runtime-registry://owner/repo",
            default_read_mode="COMPLETE_OBSERVATION",
        )
    assert error.value.code == "RUNTIME_REGISTRY_SOURCE_UNAVAILABLE"


def test_runtime_registry_rejects_locator_provenance_substitution():
    subject = _subject()
    observed = _Registry().read(subject.repository)
    replaced_observation = SourceObservation(
        record=replace(observed.record, locator="runtime-registry://other/repo"),
        canonical_payload=observed.canonical_payload,
        complete=True,
    )

    with pytest.raises(BootstrapError) as error:
        attestor_module._source_observation(
            replaced_observation,
            role="runtime.registry",
            repository=subject.repository,
            producer_sha256="d" * 64,
            default_locator=f"runtime-registry://{subject.repository}",
            default_read_mode="COMPLETE_OBSERVATION",
        )

    assert error.value.code == "RUNTIME_REGISTRY_SOURCE_UNAVAILABLE"


def test_runtime_registry_rejects_unknown_mapping_shape():
    with pytest.raises(BootstrapError) as error:
        attestor_module._registry_refs({"epoch": "registry:1"})
    assert error.value.code == "RUNTIME_REGISTRY_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    "value",
    (
        {"runtimes": [], "epoch": "extra"},
        {"runtimes": ["agent:1"]},
        {"runtimes": [{"identity": "agent:1", "state": "extra"}]},
        {"runtimes": [{"runtime_id": "agent:1"}]},
        {"runtimes": [{"identity": ""}]},
        {"runtimes": [{"identity": "agent:1"}, {"identity": "agent:1"}]},
    ),
)
def test_runtime_registry_requires_one_exact_complete_shape(value):
    with pytest.raises(BootstrapError) as error:
        attestor_module._registry_refs(value)

    assert error.value.code == "RUNTIME_REGISTRY_SOURCE_UNAVAILABLE"


def test_runtime_registry_exact_shape_returns_stable_identity_refs():
    assert attestor_module._registry_refs(
        {"runtimes": [{"identity": "agent:2"}, {"identity": "agent:1"}]}
    ) == ("runtime:agent:1", "runtime:agent:2")


def test_bindings_do_not_fallback_to_unrelated_source_records():
    subject = _subject()
    readback = _readback(
        DurableStateReadback(
            repository=subject.repository,
            generation_id=subject.store_generation,
            state_schema="gwo.v8.store.v1",
            compatible=True,
            active_plan_digests=(),
            pending_activation_ids=(),
            predecessor_identity_refs=(),
            readback_digest="",
        )
    )
    record = _record("unrelated", subject.repository, _attempt(subject).attestor_sha256)
    with pytest.raises(BootstrapError) as error:
        attestor_module._bindings(
            {"durable_state": readback},
            (record,),
            {"durable_state": ()},
        )
    assert error.value.code == "COMPONENT_INVALID"


def test_runtime_config_requires_complete_file_identity():
    path = Path(r"C:\fixture\.orch\config.json")
    observation = _RuntimeConfig().read(path)
    record = replace(
        observation.record,
        identity=tuple(
            pair for pair in observation.record.identity if pair[0] != "inode"
        ),
    )
    with pytest.raises(BootstrapError) as error:
        attestor_module._validate_runtime_config_source(
            SourceObservation(
                record=record,
                canonical_payload=observation.canonical_payload,
                complete=True,
            ),
            path,
        )
    assert error.value.code == "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE"


def test_package_records_reject_missing_package_provenance(tmp_path):
    subject = _subject()
    with pytest.raises(BootstrapError) as error:
        attestor_module._package_records(
            tmp_path,
            {surface: tmp_path / surface for surface in subject.install_surfaces},
            subject,
            producer_sha256="d" * 64,
            readback_digest="c" * 64,
        )
    assert error.value.code == "PACKAGE_SOURCE_UNAVAILABLE"


def test_runtime_config_requires_schema_version():
    value = load_canonical_json(_RuntimeConfig().read().canonical_payload)
    value.pop("schema_version", None)

    with pytest.raises(BootstrapError) as error:
        attestor_module._runtime_config_value(canonical_bytes(value), "owner/repo")
    assert error.value.code == "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE"


def test_runtime_config_resolves_repository_tier_and_role_overrides():
    value = load_canonical_json(_RuntimeConfig().read().canonical_payload)
    override = {
        "provider": "override-provider",
        "settings": {
            "model": "override-model",
            "thinkingOptionId": "override-thinking",
            "modeId": "override-mode",
            "features": {},
        },
    }
    value["repositories"] = {
        "owner/repo": {
            "default_tier": "standard",
            "tiers": {"standard": override},
            "role_profiles": {"coordinator_auto": override},
        }
    }

    _configuration, runtime = attestor_module._runtime_config_value(
        canonical_bytes(value), "owner/repo"
    )
    selectors = {item.selector: item for item in runtime.selectors}
    expected_digest = attestor_module.RuntimeProfile(
        name="standard",
        provider="override-provider",
        model="override-model",
        thinking="override-thinking",
        mode="override-mode",
        features={},
    ).digest
    assert selectors["worker"].profile_digest == expected_digest
    assert selectors["worker"].configuration_source == "repository"
    assert selectors["coordinator"].profile_digest != selectors["worker"].profile_digest
    assert selectors["coordinator"].configuration_source == "repository"


def test_runtime_config_uses_current_main_empty_features_default():
    value = load_canonical_json(_RuntimeConfig().read().canonical_payload)
    for profile in (
        *value["tiers"].values(),
        *value["role_profiles"].values(),
    ):
        profile["settings"].pop("features")
    _configuration, runtime = attestor_module._runtime_config_value(
        canonical_bytes(value), "owner/repo"
    )
    assert tuple(item.selector for item in runtime.selectors) == attestor_module.RUNTIME_SELECTORS


@pytest.mark.parametrize("location", ("top", "global", "repository"))
def test_runtime_config_rejects_unknown_mapping_keys(location):
    value = load_canonical_json(_RuntimeConfig().read().canonical_payload)
    if location == "top":
        value["unknown"] = True
    elif location == "global":
        value["global"]["unknown"] = True
    else:
        value["repositories"] = {"owner/repo": {"unknown": True}}

    with pytest.raises(BootstrapError) as error:
        attestor_module._runtime_config_value(canonical_bytes(value), "owner/repo")

    assert error.value.code == "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    "case",
    (
        "reviewer_tier",
        "unused_global_profile",
        "unselected_repository_profile",
        "unselected_repository_identity",
    ),
)
def test_runtime_config_rejects_malformed_unselected_mappings(case):
    value = load_canonical_json(_RuntimeConfig().read().canonical_payload)
    invalid_profile = {
        "provider": "provider",
        "settings": {
            "model": "model",
            "thinkingOptionId": "high",
            "modeId": "write",
            "unknown": True,
        },
    }
    if case == "reviewer_tier":
        value["reviewer_tiers"] = {"standard": "unknown-tier"}
    elif case == "unused_global_profile":
        value["tiers"]["light"] = invalid_profile
    elif case == "unselected_repository_profile":
        value["repositories"] = {
            "other/repo": {"tiers": {"light": invalid_profile}}
        }
    else:
        value["repositories"] = {"other-repo": {}}

    with pytest.raises(BootstrapError) as error:
        attestor_module._runtime_config_value(canonical_bytes(value), "owner/repo")

    assert error.value.code == "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE"


def test_runtime_config_rejects_noncanonical_exact_bytes():
    payload = _RuntimeConfig().read().canonical_payload + b"\n"

    with pytest.raises(BootstrapError) as error:
        attestor_module._runtime_config_value(payload, "owner/repo")

    assert error.value.code == "RUNTIME_CONFIGURATION_SOURCE_UNAVAILABLE"


def test_runtime_config_binds_profile_mapping_and_configuration_digests():
    configuration, readback = attestor_module._runtime_config_value(
        _RuntimeConfig().read().canonical_payload,
        "owner/repo",
    )

    _, repeated = attestor_module._runtime_config_value(
        _RuntimeConfig().read().canonical_payload,
        "owner/repo",
    )
    assert readback.configuration_digest == repeated.configuration_digest
    assert len(readback.configuration_digest) == 64
    assert tuple(item.selector for item in readback.selectors) == attestor_module.RUNTIME_SELECTORS
    assert all(item.profile_digest in configuration.profiles for item in readback.selectors)


def test_static_records_do_not_fallback_to_empty_provenance(tmp_path):
    with pytest.raises(BootstrapError) as error:
        attestor_module._static_records(
            tmp_path,
            repository="owner/repo",
            producer_sha256="d" * 64,
            role="compatibility.module",
            source_commit="a" * 40,
            source_tree_digest="b" * 64,
            readback_digest="c" * 64,
        )
    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    ("override", "value"),
    (
        ("commit", "c" * 40),
        ("tree", "c" * 40),
        ("root", Path(r"C:\different-checkout")),
    ),
)
def test_checkout_observation_rejects_identity_drift(tmp_path, override, value):
    subject = _subject()
    config = _config(tmp_path)
    source = _LocalInputs(**{override: value})
    observation = source.read(config, subject)

    with pytest.raises(BootstrapError) as error:
        attestor_module._validate_checkout_observation(observation, config, subject)

    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


def test_checkout_observation_accepts_exact_commit_tree_and_root(tmp_path):
    subject = _subject()
    config = _config(tmp_path)
    observation = _LocalInputs().read(config, subject)

    record = attestor_module._validate_checkout_observation(observation, config, subject)

    assert dict(record.identity) == {
        "commit_oid": subject.source_commit,
        "file_set_digest": digest_value([]),
        "git_status_sha256": digest_bytes(b""),
        "observation_digest": digest_bytes(observation.canonical_payload),
        "repository_root": str(tmp_path.resolve()),
        "git_tree_oid": config.merged_main_git_tree,
    }


def test_local_checkout_source_reads_authoritative_head_and_git_tree(tmp_path):
    subject = _subject()
    config = _config(tmp_path)
    _write_static_fixture(tmp_path)
    responses = (
        subject.source_commit.encode("ascii"),
        config.merged_main_git_tree.encode("ascii"),
        b"?? .codex-tmp/local-evidence.txt\0",
    )
    calls: list[tuple[str, ...]] = []

    def command_runner(command):
        calls.append(command)
        return responses[len(calls) - 1]

    observed = attestor_module._LocalInputsSource(
        command_runner,
        "d" * 64,
    ).read(config, subject)

    assert calls == [
        ("git", "-C", str(tmp_path.resolve()), "rev-parse", "--verify", "HEAD"),
        ("git", "-C", str(tmp_path.resolve()), "rev-parse", "--verify", "HEAD^{tree}"),
        (
            "git",
            "-C",
            str(tmp_path.resolve()),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ),
    ]
    assert dict(observed.record.identity)["commit_oid"] == subject.source_commit
    assert dict(observed.record.identity)["git_tree_oid"] == config.merged_main_git_tree
    assert load_canonical_json(observed.canonical_payload)["files"]


def test_local_checkout_source_accepts_codex_tmp_status_and_binds_digest(tmp_path):
    subject = _subject()
    config = _config(tmp_path)
    _write_static_fixture(tmp_path)
    status = b"?? .codex-tmp\0?? .codex-tmp/evidence/subject.json\0"
    responses = iter(
        (
            subject.source_commit.encode("ascii"),
            config.merged_main_git_tree.encode("ascii"),
            status,
        )
    )

    observed = attestor_module._LocalInputsSource(
        lambda _command: next(responses),
        "d" * 64,
    ).read(config, subject)

    expected_digest = digest_bytes(status)
    value = load_canonical_json(observed.canonical_payload)
    assert value["git_status_sha256"] == expected_digest
    assert dict(observed.record.identity)["git_status_sha256"] == expected_digest
    attestor_module._validate_checkout_observation(observed, config, subject)


def test_local_checkout_source_rejects_untracked_path_outside_codex_tmp(tmp_path):
    subject = _subject()
    config = _config(tmp_path)
    responses = iter(
        (
            subject.source_commit.encode("ascii"),
            config.merged_main_git_tree.encode("ascii"),
            b"?? docs/research/temporary.txt\0",
        )
    )
    source = attestor_module._LocalInputsSource(lambda _command: next(responses), "d" * 64)

    with pytest.raises(BootstrapError) as error:
        source.read(config, subject)

    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


@pytest.mark.skipif(os.name == "nt", reason="POSIX literal backslash name contract")
def test_local_checkout_source_rejects_untracked_backslash_name_outside_codex_tmp(tmp_path, monkeypatch):
    subject = _subject()
    config = _config(tmp_path)
    _write_static_fixture(tmp_path)
    responses = iter(
        (
            subject.source_commit.encode("ascii"),
            config.merged_main_git_tree.encode("ascii"),
            b"?? .codex-tmp\\outside.txt\0",
        )
    )
    monkeypatch.setattr(attestor_module.os, "name", "posix")
    source = attestor_module._LocalInputsSource(lambda _command: next(responses), "d" * 64)

    with pytest.raises(BootstrapError) as error:
        source.read(config, subject)

    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


def test_local_checkout_source_rejects_dirty_worktree(tmp_path):
    subject = _subject()
    config = _config(tmp_path)
    responses = iter(
        (
            subject.source_commit.encode("ascii"),
            config.merged_main_git_tree.encode("ascii"),
            b" M changed.py\0",
        )
    )
    source = attestor_module._LocalInputsSource(lambda _command: next(responses), "d" * 64)

    with pytest.raises(BootstrapError) as error:
        source.read(config, subject)

    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("bare", ({"runtimes": []}, canonical_bytes({"runtimes": []})))
def test_runtime_registry_rejects_bare_values_without_provenance(bare):
    subject = _subject()
    attempt = _attempt(subject)

    with pytest.raises(BootstrapError) as error:
        attestor_module._source_observation(
            bare,
            role="runtime.registry",
            repository=subject.repository,
            producer_sha256=attempt.attestor_sha256,
            default_locator="runtime-registry://owner/repo",
            default_read_mode="COMPLETE_OBSERVATION",
        )

    assert error.value.code == "RUNTIME_REGISTRY_SOURCE_UNAVAILABLE"


def test_complete_observation_comparison_rejects_changed_registry_identity():
    first = _Registry().read("owner/repo")
    second_record = replace(
        first.record,
        identity=(
            ("observation_digest", digest_bytes(first.canonical_payload)),
            ("registry_epoch", "epoch:2"),
        ),
    )
    second = SourceObservation(
        record=second_record,
        canonical_payload=first.canonical_payload,
        complete=True,
    )

    with pytest.raises(BootstrapError) as error:
        attestor_module.compare_complete_observations(first, second)

    assert error.value.code == "LIVE_INPUT_DRIFT"


def test_live_registry_source_records_one_complete_observation():
    calls: list[tuple[str, ...]] = []
    payload = canonical_bytes({"runtimes": []})

    def command_runner(command: tuple[str, ...]) -> bytes:
        calls.append(command)
        return payload

    observed = attestor_module._RuntimeRegistrySource(
        command_runner,
        "d" * 64,
    ).read("owner/repo")

    assert calls == [
        ("paseo", "runtime", "registry", "--repository", "owner/repo", "--json")
    ]
    assert observed.record.read_mode == "COMPLETE_OBSERVATION"


def test_runtime_config_source_reads_only_explicit_fixture_path(tmp_path):
    path = tmp_path / "explicit-runtime-config.json"
    path.write_bytes(canonical_bytes({"fixture": True}))

    observed = attestor_module._RuntimeConfigSource("d" * 64, "owner/repo").read(path)

    assert observed.record.locator == str(path.resolve())
    assert dict(observed.record.identity)["path"] == str(path.resolve())


def test_runtime_config_validation_uses_explicit_path_not_home(tmp_path, monkeypatch):
    expected = tmp_path / "config.json"
    observed = _RuntimeConfig().read(expected)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(r"C:\wrong-home")))

    attestor_module._validate_runtime_config_source(observed, expected)


def test_control_accepts_only_complete_returned_response_identity():
    subject = _subject()
    source = _ExactControlFixture()
    source.writer_bytes, source.active_plan_bytes, source.legacy_fence_bytes = _control_bytes(subject)

    fence, authority, records = attestor_module._read_control(
        source,
        subject=subject,
        attempt=_attempt(subject),
    )

    assert fence.repository == subject.repository
    assert authority.record_id == fence.record_id
    assert len(records) == 4


def test_control_accepts_historical_plan_activation_writer_record():
    subject = replace(_subject(), target_writer_generation="v8-generation-1")
    plan_one = "1" * 64
    plan_two = "3" * 64
    plan_three = "4" * 64
    pending = _writer_record(
        repository=subject.repository,
        kind="cutover_pending",
        status="pending",
        previous_writer_generation="v6.1",
        writer_generation="v8-generation-1",
        activation_id=None,
        plan_digest=plan_one,
        canary_evidence_digest="2" * 64,
        canary_evidence_refs=("canary:1",),
        canary_manifest_ref="manifest:1",
        worker_capacity=0,
        coordinator_capacity=0,
        reason=None,
        created_at="2026-08-10T00:00:00Z",
    )
    cutover = _writer_record(
        repository=subject.repository,
        kind="cutover",
        status="cut_over",
        previous_writer_generation="v8-generation-1",
        writer_generation="v8-generation-1",
        activation_id="activation:1",
        plan_digest=plan_one,
        canary_evidence_digest="2" * 64,
        canary_evidence_refs=("canary:1",),
        canary_manifest_ref="manifest:1",
        worker_capacity=8,
        coordinator_capacity=1,
        reason=None,
        created_at="2026-08-10T00:00:01Z",
    )
    plan_activation = _writer_record(
        repository=subject.repository,
        kind="plan_activation",
        status="cut_over",
        previous_writer_generation="v8-generation-1",
        writer_generation="v8-generation-1",
        activation_id="activation:2",
        plan_digest=plan_two,
        canary_evidence_digest="2" * 64,
        canary_evidence_refs=("canary:1",),
        canary_manifest_ref="manifest:1",
        worker_capacity=8,
        coordinator_capacity=1,
        reason=None,
        created_at="2026-08-10T00:00:02Z",
    )
    draining = _writer_record(
        repository=subject.repository,
        kind="drain",
        status="draining",
        previous_writer_generation="v8-generation-1",
        writer_generation="v8-generation-1",
        activation_id="activation:2",
        plan_digest=plan_two,
        canary_evidence_digest="2" * 64,
        canary_evidence_refs=("canary:1",),
        canary_manifest_ref="manifest:1",
        worker_capacity=0,
        coordinator_capacity=0,
        reason="drain",
        created_at="2026-08-10T00:00:03Z",
    )
    corrective_draining = _writer_record(
        repository=subject.repository,
        kind="drain",
        status="draining",
        previous_writer_generation="v8-generation-1",
        writer_generation="v8-generation-1",
        activation_id="activation:3",
        plan_digest=plan_three,
        canary_evidence_digest="2" * 64,
        canary_evidence_refs=("canary:1",),
        canary_manifest_ref="manifest:1",
        worker_capacity=0,
        coordinator_capacity=0,
        reason="corrective drain",
        created_at="2026-08-10T00:00:04Z",
    )
    rollback = _writer_record(
        repository=subject.repository,
        kind="rollback",
        status="rolled_back",
        previous_writer_generation="v8-generation-1",
        writer_generation="v6.1",
        activation_id="activation:3",
        plan_digest=plan_three,
        canary_evidence_digest=None,
        canary_evidence_refs=("canary:1",),
        canary_manifest_ref="manifest:1",
        worker_capacity=0,
        coordinator_capacity=0,
        reason="rollback after correction",
        created_at="2026-08-10T00:00:05Z",
    )
    source = _ExactControlFixture()
    source.writer_bytes = canonical_bytes(
        {
            "schema_version": 1,
            "current": {
                "repository": subject.repository,
                "writer_generation": "v6.1",
                "record_id": rollback.record_id,
            },
            "records": [
                {
                    **record.__dict__,
                    "canary_evidence_refs": list(record.canary_evidence_refs),
                }
                for record in (
                    pending,
                    cutover,
                    plan_activation,
                    draining,
                    corrective_draining,
                    rollback,
                )
            ],
        }
    )
    source.active_plan_bytes = canonical_bytes(
        {
            "schema_version": 1,
            "repository": subject.repository,
            "active_plan_digest": plan_three,
            "receipts": [
                {
                    "schema_version": 1,
                    "repository": subject.repository,
                    "writer_generation": "v8-generation-1",
                    "activation_id": "activation:1",
                    "plan_digest": plan_one,
                    "expected_previous_digest": None,
                    "plan_record_ref": "plan:1",
                    "created_at": "2026-08-10T00:00:01Z",
                },
                {
                    "schema_version": 1,
                    "repository": subject.repository,
                    "writer_generation": "v8-generation-1",
                    "activation_id": "activation:2",
                    "plan_digest": plan_two,
                    "expected_previous_digest": plan_one,
                    "plan_record_ref": "plan:2",
                    "created_at": "2026-08-10T00:00:02Z",
                },
                {
                    "schema_version": 1,
                    "repository": subject.repository,
                    "writer_generation": "v8-generation-1",
                    "activation_id": "activation:3",
                    "plan_digest": plan_three,
                    "expected_previous_digest": plan_two,
                    "plan_record_ref": "plan:3",
                    "created_at": "2026-08-10T00:00:04Z",
                },
            ],
        }
    )
    source.legacy_fence_bytes = canonical_bytes(
        {
            "schema_version": 1,
            "repository": subject.repository,
            "stopped": True,
            "events": [{"action_key": "stop:1", "operation": "stop"}],
        }
    )

    fence, authority, _records = attestor_module._read_control(
        source,
        subject=subject,
        attempt=_attempt(subject),
    )

    assert fence.record_id == rollback.record_id
    assert authority.record_id == rollback.record_id


def _historical_cutover_edge():
    plan_one = "1" * 64
    plan_two = "3" * 64
    prior = _writer_record(
        repository="owner/repo",
        kind="cutover",
        status="cut_over",
        previous_writer_generation="v8",
        writer_generation="v8",
        activation_id="activation:1",
        plan_digest=plan_one,
        canary_evidence_digest="2" * 64,
        canary_evidence_refs=("canary:1",),
        canary_manifest_ref="manifest:1",
        worker_capacity=8,
        coordinator_capacity=1,
        reason=None,
        created_at="2026-08-10T00:00:01Z",
    )
    successor = _writer_record(
        repository="owner/repo",
        kind="plan_activation",
        status="cut_over",
        previous_writer_generation="v8",
        writer_generation="v8",
        activation_id="activation:2",
        plan_digest=plan_two,
        canary_evidence_digest="2" * 64,
        canary_evidence_refs=("canary:1",),
        canary_manifest_ref="manifest:1",
        worker_capacity=8,
        coordinator_capacity=1,
        reason=None,
        created_at="2026-08-10T00:00:02Z",
    )
    receipts = {
        "activation:1": {
            "writer_generation": "v8",
            "plan_digest": plan_one,
            "expected_previous_digest": None,
        },
        "activation:2": {
            "writer_generation": "v8",
            "plan_digest": plan_two,
            "expected_previous_digest": plan_one,
        },
    }
    return prior, successor, receipts


@pytest.mark.parametrize(
    ("field", "replacement"),
    (("canary_manifest_ref", "manifest:changed"), ("worker_capacity", 7)),
)
def test_control_rejects_changed_historical_cutover_invariant(field, replacement):
    prior, successor, receipts = _historical_cutover_edge()
    tampered = replace(successor, **{field: replacement})

    with pytest.raises(PlanControlError):
        attestor_module._WriterLedgerValidator("owner/repo", "v8")._validate_writer_edge(
            prior,
            tampered,
            receipts,
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    (("activation_id", "activation:1"), ("plan_digest", "1" * 64)),
)
def test_control_rejects_duplicate_historical_cutover_authority(field, replacement):
    prior, successor, receipts = _historical_cutover_edge()
    tampered = replace(successor, **{field: replacement})

    with pytest.raises(PlanControlError):
        attestor_module._WriterLedgerValidator("owner/repo", "v8")._validate_writer_edge(
            prior,
            tampered,
            receipts,
        )


def test_control_rejects_non_descendant_historical_cutover_receipt():
    prior, successor, receipts = _historical_cutover_edge()
    receipts["activation:2"] = {
        **receipts["activation:2"],
        "expected_previous_digest": "9" * 64,
    }

    with pytest.raises(PlanControlError):
        attestor_module._WriterLedgerValidator("owner/repo", "v8")._validate_writer_edge(
            prior,
            successor,
            receipts,
        )


def test_github_ref_adapter_retains_response_repository_ref_oid_and_type():
    oid = "1" * 40
    response = {
        "ref": "refs/heads/gwo-control",
        "url": "https://api.github.com/repos/owner/repo/git/refs/heads/gwo-control",
        "object": {
            "type": "commit",
            "sha": oid,
            "url": f"https://api.github.com/repos/owner/repo/git/commits/{oid}",
        },
    }
    source = attestor_module._GitHubControlSource(
        lambda _command: json.dumps(response).encode("utf-8")
    )

    observed = source.read_ref("owner/repo", "gwo-control")

    assert observed.repository == "owner/repo"
    assert observed.ref == "refs/heads/gwo-control"
    assert observed.commit_oid == oid
    assert observed.object_type == "commit"


def test_github_blob_adapter_retains_every_returned_identity():
    oid = "1" * 40
    path = ".gwo-v8/writer-transition.json"
    content = b"exact bytes"
    blob_oid = hashlib.sha1(
        f"blob {len(content)}\0".encode("ascii") + content
    ).hexdigest()
    response = {
        "name": "writer-transition.json",
        "path": path,
        "sha": blob_oid,
        "size": len(content),
        "url": f"https://api.github.com/repos/owner/repo/contents/{path}?ref={oid}",
        "git_url": f"https://api.github.com/repos/owner/repo/git/blobs/{blob_oid}",
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode(content).decode("ascii"),
    }
    source = attestor_module._GitHubControlSource(
        lambda _command: json.dumps(response).encode("utf-8")
    )

    observed = source.read_at_oid("owner/repo", oid, path)

    assert observed is not None
    assert observed.repository == "owner/repo"
    assert observed.ref == oid
    assert observed.commit_oid == oid
    assert observed.path == path
    assert observed.blob_sha == blob_oid
    assert observed.object_type == "file"
    assert observed.encoding == "base64"
    assert observed.size == len(content)
    assert observed.content == content


@pytest.mark.parametrize(
    ("kind", "field", "replacement", "code"),
    (
        ("ref", "repository", "other/repo", "CONTROL_REF_UNAVAILABLE"),
        ("ref", "ref", "refs/heads/main", "CONTROL_REF_UNAVAILABLE"),
        ("ref", "commit_oid", "not-an-oid", "CONTROL_REF_UNAVAILABLE"),
        ("ref", "object_type", "tag", "CONTROL_REF_UNAVAILABLE"),
        ("blob", "repository", "other/repo", "WRITER_FENCE_SOURCE_UNAVAILABLE"),
        ("blob", "ref", "2" * 40, "WRITER_FENCE_SOURCE_UNAVAILABLE"),
        ("blob", "commit_oid", "2" * 40, "WRITER_FENCE_SOURCE_UNAVAILABLE"),
        ("blob", "path", ".gwo-v8/other.json", "WRITER_FENCE_SOURCE_UNAVAILABLE"),
        ("blob", "blob_sha", "2" * 40, "WRITER_FENCE_SOURCE_UNAVAILABLE"),
        ("blob", "object_type", "dir", "WRITER_FENCE_SOURCE_UNAVAILABLE"),
        ("blob", "encoding", "utf-8", "WRITER_FENCE_SOURCE_UNAVAILABLE"),
        ("blob", "size", 0, "WRITER_FENCE_SOURCE_UNAVAILABLE"),
        ("blob", "content", b"replaced", "WRITER_FENCE_SOURCE_UNAVAILABLE"),
    ),
)
def test_control_rejects_every_returned_identity_substitution(
    kind,
    field,
    replacement,
    code,
):
    subject = _subject()
    source = _ExactControlFixture()
    source.writer_bytes, source.active_plan_bytes, source.legacy_fence_bytes = _control_bytes(subject)

    class Substituted:
        def read_ref(self, repository: str, branch: str):
            observed = source.read_ref(repository, branch)
            return replace(observed, **{field: replacement}) if kind == "ref" else observed

        def read_at_oid(self, repository: str, oid: str, path: str):
            observed = source.read_at_oid(repository, oid, path)
            if observed is not None and kind == "blob" and path == attestor_module.WRITER_PATH:
                return replace(observed, **{field: replacement})
            return observed

    with pytest.raises(BootstrapError) as error:
        attestor_module._read_control(
            Substituted(),
            subject=subject,
            attempt=_attempt(subject),
        )

    assert error.value.code == code


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("ref", "refs/heads/main"),
        ("url", "https://api.github.com/repos/other/repo/git/refs/heads/gwo-control"),
        ("object.type", "tag"),
        ("object.sha", "2" * 40),
        ("object.url", "https://api.github.com/repos/other/repo/git/commits/" + "1" * 40),
    ),
)
def test_github_ref_adapter_rejects_response_identity_mismatch(field, replacement):
    oid = "1" * 40
    response = {
        "ref": "refs/heads/gwo-control",
        "url": "https://api.github.com/repos/owner/repo/git/refs/heads/gwo-control",
        "object": {
            "type": "commit",
            "sha": oid,
            "url": f"https://api.github.com/repos/owner/repo/git/commits/{oid}",
        },
    }
    if field.startswith("object."):
        response["object"][field.split(".", 1)[1]] = replacement
    else:
        response[field] = replacement
    source = attestor_module._GitHubControlSource(
        lambda _command: json.dumps(response).encode("utf-8")
    )

    with pytest.raises(BootstrapError) as error:
        source.read_ref("owner/repo", "gwo-control")

    assert error.value.code == "CONTROL_REF_UNAVAILABLE"


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("path", ".gwo-v8/other.json"),
        ("type", "dir"),
        ("encoding", "utf-8"),
        ("size", 0),
        ("url", "https://api.github.com/repos/other/repo/contents/file?ref=" + "1" * 40),
        ("git_url", "https://api.github.com/repos/other/repo/git/blobs/" + "2" * 40),
        ("sha", "2" * 40),
        ("content", base64.b64encode(b"replaced").decode("ascii")),
    ),
)
def test_github_blob_adapter_rejects_response_identity_mismatch(field, replacement):
    oid = "1" * 40
    path = ".gwo-v8/writer-transition.json"
    content = b"exact bytes"
    blob_oid = hashlib.sha1(
        f"blob {len(content)}\0".encode("ascii") + content
    ).hexdigest()
    response = {
        "path": path,
        "sha": blob_oid,
        "size": len(content),
        "url": f"https://api.github.com/repos/owner/repo/contents/{path}?ref={oid}",
        "git_url": f"https://api.github.com/repos/owner/repo/git/blobs/{blob_oid}",
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode(content).decode("ascii"),
    }
    response[field] = replacement
    source = attestor_module._GitHubControlSource(
        lambda _command: json.dumps(response).encode("utf-8")
    )

    with pytest.raises(BootstrapError) as error:
        source.read_at_oid("owner/repo", oid, path)

    assert error.value.code == "CONTROL_BLOB_UNAVAILABLE"


def _mutated_control_source(subject, mutation):
    source = _ExactControlFixture()
    writer_bytes, active_bytes, legacy_bytes = _control_bytes(subject)
    writer = load_canonical_json(writer_bytes)
    active = load_canonical_json(active_bytes)
    legacy = load_canonical_json(legacy_bytes)
    mutation(writer, active, legacy)
    source.writer_bytes = canonical_bytes(writer)
    source.active_plan_bytes = canonical_bytes(active)
    source.legacy_fence_bytes = canonical_bytes(legacy)
    return source


@pytest.mark.parametrize("target", ("writer", "active", "legacy"))
def test_control_rejects_noncanonical_bytes_for_every_blob(target):
    subject = _subject()
    source = _mutated_control_source(subject, lambda *_values: None)
    name = {
        "writer": "writer_bytes",
        "active": "active_plan_bytes",
        "legacy": "legacy_fence_bytes",
    }[target]
    setattr(source, name, getattr(source, name) + b"\n")

    with pytest.raises(BootstrapError) as error:
        attestor_module._read_control(source, subject=subject, attempt=_attempt(subject))

    assert error.value.code == "WRITER_FENCE_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("target", ("writer", "writer_record", "active", "receipt", "legacy", "event"))
@pytest.mark.parametrize("change", ("missing", "unknown"))
def test_control_rejects_unknown_or_missing_keys(target, change):
    subject = _subject()

    def mutate(writer, active, legacy):
        values = {
            "writer": (writer, "current"),
            "writer_record": (writer["records"][0], "reason"),
            "active": (active, "receipts"),
            "receipt": (active["receipts"][0], "plan_record_ref"),
            "legacy": (legacy, "events"),
            "event": (legacy["events"][0], "operation"),
        }
        value, required = values[target]
        if change == "missing":
            value.pop(required)
        else:
            value["unknown"] = True

    source = _mutated_control_source(subject, mutate)

    with pytest.raises(BootstrapError) as error:
        attestor_module._read_control(source, subject=subject, attempt=_attempt(subject))

    assert error.value.code == "WRITER_FENCE_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("target", ("writer", "active", "legacy"))
def test_control_rejects_repository_mismatch_in_every_blob(target):
    subject = _subject()

    def mutate(writer, active, legacy):
        if target == "writer":
            writer["current"]["repository"] = "other/repo"
        elif target == "active":
            active["repository"] = "other/repo"
        else:
            legacy["repository"] = "other/repo"

    source = _mutated_control_source(subject, mutate)

    with pytest.raises(BootstrapError) as error:
        attestor_module._read_control(source, subject=subject, attempt=_attempt(subject))

    assert error.value.code == "WRITER_FENCE_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    "case",
    (
        "duplicate_record",
        "bad_record_id",
        "current_pointer",
        "wrong_generation",
        "non_null_activation",
        "missing_predecessor",
        "duplicate_receipt",
        "activation_generation",
        "lineage_fork",
        "lineage_cycle",
        "lineage_orphan",
    ),
)
def test_control_rejects_record_and_activation_lineage_failures(case):
    subject = _subject()

    def mutate(writer, active, _legacy):
        if case == "duplicate_record":
            writer["records"].append(dict(writer["records"][0]))
        elif case == "bad_record_id":
            writer["records"][-1]["record_id"] = "bad-record"
            writer["current"]["record_id"] = "bad-record"
        elif case == "current_pointer":
            writer["current"]["record_id"] = writer["records"][0]["record_id"]
        elif case == "wrong_generation":
            writer["current"]["writer_generation"] = "v8"
        elif case == "non_null_activation":
            writer["records"][-1]["activation_id"] = "activation:1"
        elif case == "missing_predecessor":
            active["receipts"][0]["expected_previous_digest"] = "9" * 64
        elif case == "duplicate_receipt":
            active["receipts"].append(dict(active["receipts"][0]))
        elif case == "activation_generation":
            active["receipts"][0]["writer_generation"] = "v6.1"
        else:
            root = active["receipts"][0]
            second = {
                **root,
                "activation_id": "activation:2",
                "plan_digest": "3" * 64,
                "expected_previous_digest": None,
                "plan_record_ref": "plan:2",
                "created_at": "2026-08-10T00:00:01Z",
            }
            if case == "lineage_fork":
                second["expected_previous_digest"] = root["plan_digest"]
                third = {
                    **second,
                    "activation_id": "activation:3",
                    "plan_digest": "4" * 64,
                    "plan_record_ref": "plan:3",
                }
                active["receipts"].extend((second, third))
                active["active_plan_digest"] = third["plan_digest"]
            elif case == "lineage_cycle":
                root["expected_previous_digest"] = second["plan_digest"]
                second["expected_previous_digest"] = root["plan_digest"]
                active["receipts"].append(second)
            else:
                active["receipts"].append(second)

    source = _mutated_control_source(subject, mutate)

    with pytest.raises(BootstrapError) as error:
        attestor_module._read_control(source, subject=subject, attempt=_attempt(subject))

    assert error.value.code == "WRITER_FENCE_SOURCE_UNAVAILABLE"


def _write_static_fixture(root: Path) -> Path:
    target = root / "skills" / "orchestrator" / "scripts" / "gwo_v8" / "entry.py"
    target.parent.mkdir(parents=True)
    target.write_text("def start():\n    return None\n", encoding="utf-8")
    implement = root / "skills" / "implement-gwo" / "SKILL.md"
    implement.parent.mkdir(parents=True)
    implement.write_text("# implement-gwo\n", encoding="utf-8")
    orchestrator = root / "skills" / "orchestrator" / "SKILL.md"
    orchestrator.write_text("# orchestrator\n", encoding="utf-8")
    return target


def _package_digest(path: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        (
            child
            for child in path.rglob("*")
            if child.is_file()
            and child.name != ".skill-package.json"
            and "__pycache__" not in child.parts
            and child.suffix != ".pyc"
        ),
        key=lambda child: child.relative_to(path).as_posix(),
    )
    for child in files:
        relative = child.relative_to(path).as_posix().encode("utf-8")
        content = child.read_bytes()
        if child.suffix.lower() in {".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"}:
            content = content.replace(b"\r\n", b"\n")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _write_package(path: Path, name: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    manifest = {
        "content_sha256": _package_digest(path),
        "schema_version": 1,
        "skill": name,
        "version": "8.0.0",
    }
    (path / ".skill-package.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_package_fixture(root: Path, install_roots: dict[str, Path]) -> None:
    for name in ("implement-gwo", "orchestrator"):
        _write_package(root / "skills" / name, name)
        for install_root in install_roots.values():
            _write_package(install_root / name, name)


def _compatibility(subject: CutoverSubject) -> CompatibilityPathReadback:
    return _readback(
        CompatibilityPathReadback(
            repository=subject.repository,
            source_commit=subject.source_commit,
            source_tree_digest=subject.source_tree_digest,
            audit_version="fixture",
            reachable_v2_projection_refs=(),
            reachable_v3_compatibility_refs=(),
            reachable_legacy_writer_refs=(),
            proven_unreachable_refs=tuple(sorted(subject.forbidden_production_refs)),
            readback_digest="",
        )
    )


def test_static_scan_rejects_file_replacement_between_validation_and_recording(
    tmp_path,
    monkeypatch,
):
    target = _write_static_fixture(tmp_path)
    subject = _subject()

    class ReplacingScanner:
        def read(self, scan_subject):
            replacement = target.with_suffix(".replacement")
            replacement.write_bytes(target.read_bytes())
            os.replace(replacement, target)
            return _compatibility(scan_subject)

    monkeypatch.setattr(attestor_module, "ProductionPathScanner", lambda _root: ReplacingScanner())

    with pytest.raises(BootstrapError) as error:
        attestor_module._read_stable_static_inputs(
            tmp_path,
            subject,
            producer_sha256="d" * 64,
        )

    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


def test_static_scan_binds_every_scanned_file_snapshot(tmp_path, monkeypatch):
    _write_static_fixture(tmp_path)
    subject = _subject()
    monkeypatch.setattr(
        attestor_module,
        "ProductionPathScanner",
        lambda _root: SimpleNamespace(read=lambda scan_subject: _compatibility(scan_subject)),
    )

    readback, records = attestor_module._read_stable_static_inputs(
        tmp_path,
        subject,
        producer_sha256="d" * 64,
    )

    assert readback.source_tree_digest == subject.source_tree_digest
    assert {dict(record.identity)["relative_path"] for record in records} == {
        "skills/implement-gwo/SKILL.md",
        "skills/orchestrator/SKILL.md",
        "skills/orchestrator/scripts/gwo_v8/entry.py",
    }


def test_package_validation_rejects_installed_file_replacement(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    install_roots = {
        surface: tmp_path / surface / "skills"
        for surface in (".agents", ".codex", ".claude")
    }
    _write_package_fixture(root, install_roots)
    subject = _subject()
    readback = ReadOnlyPackageValidator(root, install_roots).read(subject)
    target = install_roots[".codex"] / "orchestrator" / "SKILL.md"

    class ReplacingValidator:
        def read(self, _subject):
            replacement = target.with_suffix(".replacement")
            replacement.write_bytes(target.read_bytes())
            os.replace(replacement, target)
            return readback

    monkeypatch.setattr(
        attestor_module,
        "ReadOnlyPackageValidator",
        lambda *_args: ReplacingValidator(),
    )

    with pytest.raises(BootstrapError) as error:
        attestor_module._read_stable_package_inputs(
            root,
            install_roots,
            subject,
            producer_sha256="d" * 64,
        )

    assert error.value.code == "PACKAGE_SOURCE_UNAVAILABLE"


def test_package_validation_binds_every_source_and_installed_file(tmp_path):
    root = tmp_path / "repo"
    install_roots = {
        surface: tmp_path / surface / "skills"
        for surface in (".agents", ".codex", ".claude")
    }
    _write_package_fixture(root, install_roots)

    readback, records = attestor_module._read_stable_package_inputs(
        root,
        install_roots,
        _subject(),
        producer_sha256="d" * 64,
    )

    assert readback.drift == ()
    assert len(records) == 16
    assert {dict(record.identity)["package"] for record in records} == {
        f"{surface}:{name}"
        for surface in ("source", ".agents", ".codex", ".claude")
        for name in ("implement-gwo", "orchestrator")
    }
    assert {dict(record.identity)["relative_path"] for record in records} == {
        ".skill-package.json",
        "SKILL.md",
    }


def _checkout_binding_fixture(tmp_path):
    root = tmp_path / "repo"
    _write_static_fixture(root)
    install_roots = {
        surface: tmp_path / surface / "skills"
        for surface in (".agents", ".codex", ".claude")
    }
    _write_package_fixture(root, install_roots)
    subject = _subject()
    config = _config(tmp_path)
    config.repository_root = root
    config.install_roots = tuple(install_roots.values())
    checkout = _LocalInputs().read(config, subject)
    return root, install_roots, subject, checkout


def test_checkout_file_manifest_binds_compatibility_and_source_packages(tmp_path):
    root, install_roots, subject, checkout = _checkout_binding_fixture(tmp_path)
    static_records = attestor_module._static_records(
        root,
        repository=subject.repository,
        producer_sha256="d" * 64,
        role="compatibility.module",
        source_commit=subject.source_commit,
        source_tree_digest=subject.source_tree_digest,
        readback_digest="e" * 64,
    )
    package_records = attestor_module._package_records(
        root,
        install_roots,
        subject,
        producer_sha256="d" * 64,
        readback_digest="f" * 64,
    )

    attestor_module._validate_checkout_file_bindings(
        checkout,
        root,
        static_records,
        package_records,
    )


def test_checkout_file_manifest_rejects_change_after_checkout_observation(tmp_path):
    root, install_roots, subject, checkout = _checkout_binding_fixture(tmp_path)
    (root / "skills" / "orchestrator" / "scripts" / "gwo_v8" / "entry.py").write_text(
        "def changed():\n    return True\n",
        encoding="utf-8",
    )
    static_records = attestor_module._static_records(
        root,
        repository=subject.repository,
        producer_sha256="d" * 64,
        role="compatibility.module",
        source_commit=subject.source_commit,
        source_tree_digest=subject.source_tree_digest,
        readback_digest="e" * 64,
    )
    package_records = attestor_module._package_records(
        root,
        install_roots,
        subject,
        producer_sha256="d" * 64,
        readback_digest="f" * 64,
    )

    with pytest.raises(BootstrapError) as error:
        attestor_module._validate_checkout_file_bindings(
            checkout,
            root,
            static_records,
            package_records,
        )

    assert error.value.code == "STATIC_INPUT_SOURCE_UNAVAILABLE"


def test_static_and_package_attestation_never_installs_copies_or_replaces(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "repo"
    _write_static_fixture(root)
    install_roots = {
        surface: tmp_path / surface / "skills"
        for surface in (".agents", ".codex", ".claude")
    }
    _write_package_fixture(root, install_roots)
    subject = _subject()
    monkeypatch.setattr(
        attestor_module,
        "ProductionPathScanner",
        lambda _root: SimpleNamespace(read=lambda scan_subject: _compatibility(scan_subject)),
    )
    calls: list[str] = []
    monkeypatch.setattr(shutil, "copytree", lambda *_args, **_kwargs: calls.append("copytree"))
    monkeypatch.setattr(shutil, "copy2", lambda *_args, **_kwargs: calls.append("copy2"))
    monkeypatch.setattr(os, "replace", lambda *_args, **_kwargs: calls.append("replace"))

    attestor_module._read_stable_static_inputs(root, subject, producer_sha256="d" * 64)
    attestor_module._read_stable_package_inputs(
        root,
        install_roots,
        subject,
        producer_sha256="d" * 64,
    )

    assert calls == []


def _create_store_fixture(tmp_path: Path, *, active: bool = False) -> SimpleNamespace:
    from gwo_v8.activation import LocalPlanPublication
    from gwo_v8.kernel import Kernel

    config = _config(tmp_path)
    config.expected_store_tables = attestor_module._fixed_store_contract()[0]
    config.rollback_store.write_bytes(b"rollback Store fixture")
    config.prior_store.write_bytes(b"prior Store fixture")
    config.expected_rollback_store_sha256 = digest_bytes(config.rollback_store.read_bytes())
    config.expected_prior_store_sha256 = digest_bytes(config.prior_store.read_bytes())

    LocalPlanPublication(config.fresh_store)
    gc.collect()
    connection = sqlite3.connect(config.fresh_store)
    try:
        connection.row_factory = sqlite3.Row
        Kernel.ensure_store_schema(connection)
        connection.execute(
            'insert into "v8_writer_generations" values (?, ?)',
            (config.repository, config.store_generation),
        )
        if active:
            plan = "1" * 64
            connection.execute(
                'insert into "v8_admissions" values (?, ?, ?, ?, ?, ?)',
                ("admission:1", config.repository, plan, "node:1", "goal:1", "admitted"),
            )
            connection.execute(
                'insert into "v8_attempts" values (?, ?, ?, ?, ?, ?)',
                ("attempt:1", config.repository, plan, "node:1", "admission:1", "running"),
            )
            connection.execute(
                'insert into "v8_integration_leases" values (?, ?)',
                (config.repository, "lease-owner"),
            )
            connection.execute(
                'insert into "v8_resource_claims" values (?, ?, ?, ?)',
                (config.repository, "resource:1", "admission:1", "attempt:1"),
            )
        connection.commit()
        schema_digest = attestor_module._sqlite_schema_digest(connection)
        generation_rows = tuple(
            tuple(row)
            for row in connection.execute(
                'select repository, writer_generation from "v8_writer_generations" order by repository'
            ).fetchall()
        )
        row_counts = tuple(
            (
                table,
                int(connection.execute(f'select count(*) from "{table}"').fetchone()[0]),
            )
            for table in config.expected_store_tables
        )
    finally:
        connection.close()
    config.expected_fresh_store_sha256 = digest_bytes(config.fresh_store.read_bytes())
    config.expected_fresh_receipt_schema_digest = schema_digest
    config.expected_fresh_receipt_generation_rows = generation_rows
    config.expected_fresh_receipt_row_counts = row_counts
    receipt = {
        "schema": "gwo-v8-fresh-store-provision.v1",
        "repository": config.repository,
        "source_main_sha": config.merged_main_sha,
        "source_main_tree": config.merged_main_git_tree,
        "runbook_sha256": config.expected_fresh_receipt_runbook_sha256,
        "store_path": str(config.fresh_store.resolve()),
        "store_generation": config.store_generation,
        "store_sha256": config.expected_fresh_store_sha256,
        "integrity": "ok",
        "tables": list(config.expected_store_tables),
        "schema_digest": schema_digest,
        "generation_rows": [list(row) for row in generation_rows],
        "row_counts": dict(row_counts),
        "existing_store_hashes_before": {
            str(config.rollback_store.resolve()): config.expected_rollback_store_sha256,
            str(config.prior_store.resolve()): config.expected_prior_store_sha256,
        },
        "existing_store_hashes_after": {
            str(config.rollback_store.resolve()): config.expected_rollback_store_sha256,
            str(config.prior_store.resolve()): config.expected_prior_store_sha256,
        },
        "old_stores_untouched": True,
    }
    config.fresh_receipt.write_bytes(canonical_bytes(receipt))
    config.expected_fresh_receipt_sha256 = digest_bytes(config.fresh_receipt.read_bytes())
    return config


def _refresh_store_fixture(config: SimpleNamespace) -> None:
    connection = sqlite3.connect(config.fresh_store)
    try:
        schema_digest = attestor_module._sqlite_schema_digest(connection)
        generation_rows = tuple(
            tuple(row)
            for row in connection.execute(
                'select repository, writer_generation from "v8_writer_generations" order by repository'
            ).fetchall()
        )
        row_counts = tuple(
            (
                table,
                int(connection.execute(f'select count(*) from "{table}"').fetchone()[0]),
            )
            for table in config.expected_store_tables
        )
    finally:
        connection.close()
    receipt = load_canonical_json(config.fresh_receipt.read_bytes())
    config.expected_fresh_store_sha256 = digest_bytes(config.fresh_store.read_bytes())
    config.expected_fresh_receipt_schema_digest = schema_digest
    config.expected_fresh_receipt_generation_rows = generation_rows
    config.expected_fresh_receipt_row_counts = row_counts
    receipt["store_sha256"] = config.expected_fresh_store_sha256
    receipt["schema_digest"] = schema_digest
    receipt["generation_rows"] = [list(row) for row in generation_rows]
    receipt["row_counts"] = dict(row_counts)
    config.fresh_receipt.write_bytes(canonical_bytes(receipt))
    config.expected_fresh_receipt_sha256 = digest_bytes(config.fresh_receipt.read_bytes())


def test_ownership_reads_real_immutable_store_and_reports_active_facts(
    tmp_path,
    monkeypatch,
):
    config = _create_store_fixture(tmp_path, active=True)
    subject = _subject()
    attempt = _attempt(subject)
    real_connect = sqlite3.connect
    sqlite_calls: list[tuple[object, bool]] = []
    mutation_calls: list[int] = []
    mutation_actions = {
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_ALTER_TABLE,
    }

    def tracked_connect(database, *args, **kwargs):
        sqlite_calls.append((database, kwargs.get("uri") is True))
        connection = real_connect(database, *args, **kwargs)

        def authorizer(action, _arg1, _arg2, _database, _trigger):
            if action in mutation_actions:
                mutation_calls.append(action)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorizer)
        return connection

    monkeypatch.setattr(attestor_module.sqlite3, "connect", tracked_connect)

    observed = attestor_module._read_store(config, subject, attempt)

    assert observed.active_admissions == ("admission:1",)
    assert observed.active_attempts == ("attempt:1",)
    assert observed.integration_lease_owner == "lease-owner"
    assert observed.resource_claims == ("claim:resource:1",)
    assert observed.durable.generation_id == subject.store_generation
    assert len(sqlite_calls) == 1
    assert "mode=ro&immutable=1" in str(sqlite_calls[0][0])
    assert sqlite_calls[0][1] is True
    assert mutation_calls == []


@pytest.mark.parametrize(
    "case",
    (
        "store_path",
        "store_hash",
        "receipt_path",
        "receipt_hash",
        "receipt_identity",
        "schema",
        "generation",
        "sidecar",
        "duplicate",
        "cross_link",
    ),
)
def test_store_rejects_path_hash_schema_receipt_generation_and_link_drift(
    tmp_path,
    case,
):
    config = _create_store_fixture(tmp_path, active=case == "cross_link")
    if case == "store_path":
        config.fresh_store = tmp_path / "missing-store.sqlite3"
    elif case == "store_hash":
        config.expected_fresh_store_sha256 = "0" * 64
    elif case == "receipt_path":
        config.fresh_receipt = tmp_path / "missing-receipt.json"
    elif case == "receipt_hash":
        config.expected_fresh_receipt_sha256 = "0" * 64
    elif case == "receipt_identity":
        receipt = load_canonical_json(config.fresh_receipt.read_bytes())
        receipt["repository"] = "other/repo"
        config.fresh_receipt.write_bytes(canonical_bytes(receipt))
        config.expected_fresh_receipt_sha256 = digest_bytes(config.fresh_receipt.read_bytes())
    elif case == "schema":
        connection = sqlite3.connect(config.fresh_store)
        try:
            connection.execute("create table unexpected_table (value text)")
            connection.commit()
        finally:
            connection.close()
        _refresh_store_fixture(config)
    elif case == "generation":
        connection = sqlite3.connect(config.fresh_store)
        try:
            connection.execute(
                'update "v8_writer_generations" set writer_generation = ?',
                ("store:v8:other",),
            )
            connection.commit()
        finally:
            connection.close()
        _refresh_store_fixture(config)
    elif case == "sidecar":
        Path(f"{config.fresh_store}-wal").write_bytes(b"unexpected sidecar")
    elif case == "duplicate":
        connection = sqlite3.connect(config.fresh_store)
        try:
            connection.execute(
                'insert into "v8_integration_leases" values (?, ?)',
                ("other/repo", "other-owner"),
            )
            connection.commit()
        finally:
            connection.close()
        _refresh_store_fixture(config)
    else:
        connection = sqlite3.connect(config.fresh_store)
        try:
            connection.execute(
                'insert into "v8_admissions" values (?, ?, ?, ?, ?, ?)',
                ("admission:2", config.repository, "1" * 64, "node:2", "goal:2", "admitted"),
            )
            connection.execute(
                'insert into "v8_resource_claims" values (?, ?, ?, ?)',
                (config.repository, "resource:2", "admission:2", "attempt:1"),
            )
            connection.commit()
        finally:
            connection.close()
        _refresh_store_fixture(config)

    with pytest.raises(BootstrapError) as error:
        attestor_module._read_store(config, _subject(), _attempt(_subject()))

    assert error.value.code == "STORE_SOURCE_UNAVAILABLE"


def test_store_rejects_receipt_replacement_during_immutable_read(tmp_path, monkeypatch):
    config = _create_store_fixture(tmp_path)
    real_connect = sqlite3.connect

    def replacing_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        replacement = config.fresh_receipt.with_suffix(".replacement")
        replacement.write_bytes(config.fresh_receipt.read_bytes())
        os.replace(replacement, config.fresh_receipt)
        return connection

    monkeypatch.setattr(attestor_module.sqlite3, "connect", replacing_connect)

    with pytest.raises(BootstrapError) as error:
        attestor_module._read_store(config, _subject(), _attempt(_subject()))

    assert error.value.code == "STORE_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize(
    ("receipt_case", "expected_detail"),
    (
        ("digest", "fresh Store receipt bytes changed"),
        ("content", "fresh Store receipt schema is not exact"),
    ),
)
def test_store_validates_receipt_before_opening_fresh_store(
    tmp_path,
    receipt_case,
    expected_detail,
):
    config = _create_store_fixture(tmp_path)
    config.fresh_store = tmp_path / "missing-store.sqlite3"
    receipt = load_canonical_json(config.fresh_receipt.read_bytes())
    receipt["store_path"] = str(config.fresh_store.resolve())
    config.fresh_receipt.write_bytes(canonical_bytes(receipt))
    if receipt_case == "digest":
        config.expected_fresh_receipt_sha256 = "0" * 64
    else:
        config.fresh_receipt.write_bytes(b"{}")
        config.expected_fresh_receipt_sha256 = digest_bytes(config.fresh_receipt.read_bytes())

    with pytest.raises(BootstrapError) as error:
        attestor_module._read_store(config, _subject(), _attempt(_subject()))

    assert error.value.code == "STORE_SOURCE_UNAVAILABLE"
    assert error.value.detail == expected_detail
    assert not config.fresh_store.exists()
