from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
from dataclasses import replace
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = REPO_ROOT / "scripts" / "run_beta3_live_guard.py"
spec = importlib.util.spec_from_file_location("run_beta3_live_guard_test", RUNNER_PATH)
if spec is None or spec.loader is None:
    raise ModuleNotFoundError("run_beta3_live_guard.py is not implemented")
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)

EXACT_SCRIPTS = REPO_ROOT / "skills" / "orchestrator" / "scripts"
if str(EXACT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(EXACT_SCRIPTS))
from gwo_v8._canonical import digest_value  # noqa: E402
from gwo_v8.cutover_guard import (  # noqa: E402
    CompatibilityPathReadback,
    CutoverBlocker,
    CutoverGuardReceipt,
    CutoverGuardReport,
    CutoverReadbackBundle,
    CutoverSubject,
    DurableStateReadback,
    GuardCheck,
    LegacyReadback,
    OwnershipReadback,
    PackageIdentity,
    PackageReadback,
    RuntimePreflightReadback,
    RuntimeSelectorReadback,
    WriterFenceReadback,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_store(path: Path, *, fresh: bool = False) -> str:
    connection = sqlite3.connect(path)
    try:
        if fresh:
            from gwo_v8.activation import LocalPlanPublication
            from gwo_v8.kernel import Kernel

            connection.close()
            LocalPlanPublication(path)
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            Kernel.ensure_store_schema(connection)
            connection.execute(
                'insert into "v8_writer_generations" values (?, ?)',
                ("owner/repo", "store:v8:fixture:081500Z"),
            )
        else:
            connection.execute("create table marker (value text not null)")
            connection.execute("insert into marker values ('read-only fixture')")
        connection.commit()
    finally:
        connection.close()
    return _sha256(path)


def _package_content_digest(package_root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        (
            candidate
            for candidate in package_root.rglob("*")
            if candidate.is_file()
            and candidate.name != ".skill-package.json"
            and "__pycache__" not in candidate.parts
            and candidate.suffix != ".pyc"
        ),
        key=lambda candidate: candidate.relative_to(package_root).as_posix(),
    )
    for path in files:
        relative = path.relative_to(package_root).as_posix().encode("utf-8")
        content = path.read_bytes()
        if path.suffix.lower() in {".toml", ".md", ".py", ".yaml", ".yml", ".json", ".txt"}:
            content = content.replace(b"\r\n", b"\n")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _write_package_manifest(package_root: Path, package_name: str) -> None:
    manifest = {
        "content_sha256": _package_content_digest(package_root),
        "schema_version": 1,
        "skill": package_name,
        "version": "8.0.0",
    }
    (package_root / ".skill-package.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def _git_runner_factory(
    config: Any,
    *,
    status: str = "?? .codex-tmp/quoted path\0?? .codex-tmp/nested path\0",
    origin: str | None = None,
):
    calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def run(args, *, cwd, env):
        calls.append((tuple(args), dict(env)))
        if args[:3] == ["rev-parse", "--verify", "HEAD"]:
            stdout = config.expected_head
        elif args[:3] == ["rev-parse", "--verify", "HEAD^{tree}"]:
            stdout = config.expected_tree
        elif args[:3] == ["rev-parse", "--verify", "origin/main"]:
            stdout = config.expected_head if origin is None else origin
        elif args == ["status", "--porcelain=v1", "-z", "--untracked-files=all"]:
            stdout = status
        else:
            raise AssertionError(f"unexpected git command: {args}")
        return subprocess.CompletedProcess(["git", *args], 0, stdout=stdout, stderr="")

    run.calls = calls
    return run


def _write_receipt(config: Any) -> None:
    receipt = {
        "schema": "gwo-v8-fresh-store-provision.v1",
        "repository": config.repository,
        "source_main_sha": config.expected_head,
        "source_main_tree": config.expected_tree,
        "runbook_sha256": config.expected_fresh_receipt_runbook_sha256,
        "store_path": str(config.fresh_store),
        "store_generation": config.store_generation,
        "store_sha256": config.expected_fresh_store_sha256,
        "integrity": "ok",
        "tables": list(config.expected_store_tables),
        "schema_digest": config.expected_fresh_receipt_schema_digest,
        "generation_rows": [
            list(row) for row in (config.expected_fresh_receipt_generation_rows or ())
        ],
        "row_counts": dict(config.expected_fresh_receipt_row_counts or ()),
        "existing_store_hashes_before": {
            str(config.rollback_store): config.expected_rollback_store_sha256,
            str(config.prior_store): config.expected_prior_store_sha256,
        },
        "existing_store_hashes_after": {
            str(config.rollback_store): config.expected_rollback_store_sha256,
            str(config.prior_store): config.expected_prior_store_sha256,
        },
        "old_stores_untouched": True,
    }
    config.fresh_receipt.write_bytes(
        runner.canonical_json_bytes(receipt)
    )


def _fixture_config(tmp_path: Path):
    root = tmp_path / "repo root with spaces"
    evidence = tmp_path / "evidence root with spaces"
    stores = tmp_path / "stores"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "skills").mkdir()
    evidence.mkdir()
    stores.mkdir()
    for package in ("implement-gwo", "orchestrator"):
        source = root / "skills" / package
        source.mkdir()
        (source / "SKILL.md").write_text(f"# {package}\n", encoding="utf-8")
    install_roots = tuple(
        tmp_path / name / "skills" for name in (".agents", ".codex", ".claude")
    )
    for install_root in install_roots:
        install_root.mkdir(parents=True)
        for package in ("implement-gwo", "orchestrator"):
            target = install_root / package
            target.mkdir()
            (target / "SKILL.md").write_text(
                f"# {package}\n", encoding="utf-8"
            )
    for package_name in ("implement-gwo", "orchestrator"):
        _write_package_manifest(root / "skills" / package_name, package_name)
        for install_root in install_roots:
            _write_package_manifest(install_root / package_name, package_name)
    fresh = stores / "fresh.sqlite3"
    rollback = stores / "rollback.sqlite3"
    prior = stores / "prior.sqlite3"
    fresh_hash = _make_store(fresh, fresh=True)
    rollback_hash = _make_store(rollback)
    prior_hash = _make_store(prior)
    config = runner.RunnerConfig(
        repository_root=root,
        evidence_root=evidence,
        repository="owner/repo",
        expected_head="a" * 40,
        expected_tree="b" * 40,
        control_branch="gwo-control",
        target_branch="main",
        source_writer_generation="v6.1",
        target_writer_generation="v8",
        fresh_store=fresh,
        store_generation="store:v8:fixture:081500Z",
        expected_fresh_store_sha256=fresh_hash,
        rollback_store=rollback,
        expected_rollback_store_sha256=rollback_hash,
        prior_store=prior,
        expected_prior_store_sha256=prior_hash,
        fresh_receipt=evidence / "fresh-receipt.json",
        report_path=evidence / "report.json",
        evidence_path=evidence / "evidence.json",
        install_roots=install_roots,
        package_names=("implement-gwo", "orchestrator"),
        expected_store_tables=runner.EXPECTED_STORE_TABLES,
        expected_fresh_receipt_runbook_sha256="c" * 64,
        expected_fresh_receipt_schema_digest=runner.EXPECTED_FRESH_RECEIPT_SCHEMA_DIGEST,
        expected_fresh_receipt_generation_rows=(
            ("owner/repo", "store:v8:fixture:081500Z"),
        ),
        expected_fresh_receipt_row_counts=tuple(
            {
                **{table: 0 for table in runner.EXPECTED_STORE_TABLES},
                "v8_writer_generations": 1,
            }.items()
        ),
        expected_package_content_digests=tuple(
            (
                package_name,
                _package_content_digest(root / "skills" / package_name),
            )
            for package_name in ("implement-gwo", "orchestrator")
        ),
        gateway_store_path=evidence / "forbidden-gateway.sqlite3",
        artifact_root=evidence / "forbidden-artifacts",
    )
    _write_receipt(config)
    config = replace(
        config,
        expected_fresh_receipt_sha256=_sha256(config.fresh_receipt),
    )
    return config


def _typed_with_digest(value_type, values):
    body = dict(values)
    def projection(value):
        canonical = getattr(value, "canonical", None)
        if callable(canonical):
            return canonical()
        if type(value) is tuple:
            return [projection(item) for item in value]
        if type(value) is list:
            return [projection(item) for item in value]
        if type(value) is dict:
            return {key: projection(item) for key, item in value.items()}
        return value

    body["readback_digest"] = digest_value(projection(body))
    return value_type(**body)


def _exact_fixture_subject(config):
    return CutoverSubject(
        repository=config.repository,
        control_branch=config.control_branch,
        target_branch=config.target_branch,
        source_writer_generation=config.source_writer_generation,
        target_writer_generation=config.target_writer_generation,
        store_generation=config.store_generation,
        source_commit=config.expected_head,
        source_tree_digest="tree",
        production_entry_refs=runner.PRODUCTION_ENTRY_REFS,
    )


def _exact_fixture_readbacks(config, subject):
    legacy = _typed_with_digest(
        LegacyReadback,
        {
            "repository": config.repository,
            "writer_generation": "v6.1",
            "authority_state": "authoritative_quiescent",
            "active_dispatches": (),
            "active_workers": (),
            "integration_lease_owner": None,
            "v2_execution_refs": (),
            "v2_execution_state": "none",
            "original_decoder_readable": True,
            "durable_state_digest": digest_value("legacy-durable"),
        },
    )
    durable = _typed_with_digest(
        DurableStateReadback,
        {
            "repository": config.repository,
            "generation_id": config.store_generation,
            "state_schema": "gwo.v8.store.v1",
            "compatible": True,
            "active_plan_digests": (),
            "pending_activation_ids": (),
            "predecessor_identity_refs": (),
        },
    )
    writer = _typed_with_digest(
        WriterFenceReadback,
        {
            "repository": config.repository,
            "writer_generation": "v6.1",
            "authority_state": "authoritative",
            "record_id": "initial-writer",
            "activation_id": None,
            "control_ref_digest": digest_value("control-stable"),
        },
    )
    ownership = _typed_with_digest(
        OwnershipReadback,
        {
            "repository": config.repository,
            "active_admissions": (),
            "active_attempts": (),
            "integration_lease_owner": None,
            "runtime_resource_refs": (),
        },
    )
    compatibility = _typed_with_digest(
        CompatibilityPathReadback,
        {
            "repository": config.repository,
            "source_commit": config.expected_head,
            "source_tree_digest": subject.source_tree_digest,
            "audit_version": "gwo.cutover-path-audit.v1",
            "reachable_v2_projection_refs": (),
            "reachable_v3_compatibility_refs": (),
            "reachable_legacy_writer_refs": (),
            "proven_unreachable_refs": tuple(sorted(subject.forbidden_production_refs)),
        },
    )
    selectors = tuple(
        RuntimeSelectorReadback(
            selector=selector,
            profile_digest=digest_value(selector),
            fallback_profile_digest=None,
            configuration_source="host_global",
        )
        for selector in subject.required_runtime_selectors
    )
    runtime = _typed_with_digest(
        RuntimePreflightReadback,
        {
            "repository": config.repository,
            "selectors": selectors,
            "configuration_digest": digest_value("runtime-stable"),
            "provider_action_refs": (),
            "persistence_write_refs": (),
        },
    )
    source_packages = tuple(
        PackageIdentity(
            package_name=name,
            version="8.0.0",
            content_digest=digest_value(name + "-content"),
            manifest_content_digest=digest_value(name + "-manifest"),
            install_surface=None,
        )
        for name in subject.package_names
    )
    installed_packages = tuple(
        PackageIdentity(
            package_name=name,
            version="8.0.0",
            content_digest=source.content_digest,
            manifest_content_digest=source.manifest_content_digest,
            install_surface=surface,
        )
        for surface in subject.install_surfaces
        for source in source_packages
        for name in (source.package_name,)
    )
    packages = _typed_with_digest(
        PackageReadback,
        {
            "source_packages": source_packages,
            "installed_packages": installed_packages,
            "drift": (),
        },
    )
    return {
        "legacy": legacy,
        "durable_state": durable,
        "writer_fence": writer,
        "ownership": ownership,
        "compatibility": compatibility,
        "runtime": runtime,
        "packages": packages,
    }


def _stable_dependencies(*, decision: str = "GO", changed: dict[str, Any] | None = None):
    calls: list[str] = []
    control_reads = 0
    package_reads = 0
    subject_holder: list[CutoverSubject] = []

    def live_guard(_config, subject):
        calls.append("guard")
        subject_holder[:] = [subject]
        readbacks = _exact_fixture_readbacks(_config, subject)
        checks = tuple(
            GuardCheck(
                check_id,
                decision == "GO",
                digest_value(readbacks[runner.CHECK_TO_GUARD_PORT[check_id]].canonical()),
            )
            for check_id in runner.EXPECTED_CHECK_IDS
        )
        blockers = ()
        if decision == "NO_GO":
            blockers = (
                CutoverBlocker(
                    "CUTOVER_V2_ACTIVE",
                    "legacy_quiescence",
                    checks[1].observed_digest,
                    "fixture blocker",
                ),
            )
        readback_digest = digest_value(
            {name: readbacks[name].canonical() for name in runner.GUARD_PORT_ORDER}
        )
        receipt = None
        if decision == "GO":
            receipt_body = {
                "schema": "gwo.cutover-guard-receipt.v1",
                "repository": _config.repository,
                "subject_digest": digest_value(subject.canonical()),
                "readback_digest": readback_digest,
                "source_writer_generation": "v6.1",
                "target_writer_generation": "v8",
                "store_generation": _config.store_generation,
                "writer_control_ref_digest": readbacks["writer_fence"].control_ref_digest,
                "runtime_configuration_digest": readbacks["runtime"].configuration_digest,
                "compatibility_audit_digest": readbacks["compatibility"].readback_digest,
                "package_readback_digest": readbacks["packages"].readback_digest,
            }
            receipt = CutoverGuardReceipt(
                **receipt_body,
                receipt_digest=digest_value(receipt_body),
            )
        report = CutoverGuardReport(
            schema="gwo.cutover-guard.v1",
            decision=decision,
            repository=_config.repository,
            subject_digest=digest_value(subject.canonical()),
            readback_digest=readback_digest,
            checks=checks,
            blockers=blockers,
            receipt=receipt,
        )
        bundle = CutoverReadbackBundle(
            schema=runner.GUARD_READBACK_SCHEMA,
            subject=subject,
            **readbacks,
        )
        return runner.GuardExecution(report, subject, bundle, runner._guard_contract())

    def control_read():
        nonlocal control_reads
        calls.append("control")
        control_reads += 1
        if control_reads > 1 and changed and "control" in changed:
            return changed["control"]
        return {"writer_generation": "v6.1", "control_ref": "control-stable"}

    def package_read(_config):
        nonlocal package_reads
        calls.append("packages")
        package_reads += 1
        if package_reads > 1 and changed and "packages" in changed:
            return changed["packages"]
        return "packages-stable"

    dependencies = runner.ExecutionDependencies(
        live_guard=live_guard,
        control_read=control_read,
        package_read=package_read,
        subject_factory=_exact_fixture_subject,
        guard_contract=runner._guard_contract(),
    )
    return dependencies, calls


def test_default_preflight_is_zero_write_and_accepts_quoted_nul_status(tmp_path):
    config = _fixture_config(tmp_path)
    git_runner = _git_runner_factory(config)
    before = {path: path.read_bytes() for path in config.evidence_root.iterdir()}

    result = runner.run(config, execute=False, git_runner=git_runner)

    assert result["status"] == "PREFLIGHT_OK"
    assert result["exit_code"] == 0
    assert {path: path.read_bytes() for path in config.evidence_root.iterdir()} == before
    assert any(args[:2] == ("status", "--porcelain=v1") for args, _ in git_runner.calls)
    assert all(env["GIT_OPTIONAL_LOCKS"] == "0" for _, env in git_runner.calls)


def test_preflight_rejects_tracked_or_non_codex_tmp_nul_status(tmp_path):
    config = _fixture_config(tmp_path)
    git_runner = _git_runner_factory(
        config,
        status=" M tracked.py\0?? other-temp.txt\0",
    )

    with pytest.raises(runner.RunnerError) as error:
        runner.preflight(config, git_runner=git_runner)

    assert error.value.code == "GIT_STATUS_DIRTY"


def test_preflight_rejects_identity_and_hash_drift(tmp_path):
    config = _fixture_config(tmp_path)
    config.fresh_store.write_bytes(b"drift")
    git_runner = _git_runner_factory(config)

    with pytest.raises(runner.RunnerError) as error:
        runner.preflight(config, git_runner=git_runner)

    assert error.value.code == "FRESH_STORE_HASH_MISMATCH"


def test_preflight_rejects_fresh_receipt_identity_drift(tmp_path):
    config = _fixture_config(tmp_path)
    receipt = json.loads(config.fresh_receipt.read_text(encoding="utf-8"))
    receipt["source_main_tree"] = "wrong"
    config.fresh_receipt.write_bytes(runner.canonical_json_bytes(receipt))

    with pytest.raises(runner.RunnerError) as error:
        runner.preflight(config, git_runner=_git_runner_factory(config))

    assert error.value.code == "FRESH_RECEIPT_IDENTITY_MISMATCH"


def test_preflight_rejects_store_sidecar(tmp_path):
    config = _fixture_config(tmp_path)
    sidecar = Path(f"{config.fresh_store}-journal")
    sidecar.write_bytes(b"journal")

    with pytest.raises(runner.RunnerError) as error:
        runner.preflight(config, git_runner=_git_runner_factory(config))

    assert error.value.code == "STORE_SIDECAR_PRESENT"


def test_preflight_rejects_package_manifest_version_drift(tmp_path):
    config = _fixture_config(tmp_path)
    manifest_path = config.repository_root / "skills" / "orchestrator" / ".skill-package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "7.9.0"
    manifest_path.write_bytes(runner.canonical_json_bytes(manifest))

    with pytest.raises(runner.RunnerError) as error:
        runner.preflight(config, git_runner=_git_runner_factory(config))

    assert error.value.code == "PACKAGE_MANIFEST_INVALID"


def test_preflight_rejects_unknown_fresh_receipt_field(tmp_path):
    config = _fixture_config(tmp_path)
    receipt = json.loads(config.fresh_receipt.read_text(encoding="utf-8"))
    receipt["unexpected"] = True
    config.fresh_receipt.write_bytes(runner.canonical_json_bytes(receipt))

    with pytest.raises(runner.RunnerError) as error:
        runner.preflight(config, git_runner=_git_runner_factory(config))

    assert error.value.code == "FRESH_RECEIPT_SCHEMA_MISMATCH"


def test_preflight_rejects_fresh_receipt_path_alias(tmp_path):
    config = _fixture_config(tmp_path)
    receipt = json.loads(config.fresh_receipt.read_text(encoding="utf-8"))
    receipt["store_path"] = (
        str(config.fresh_store.parent)
        + "\\.\\"
        + config.fresh_store.name
    )
    config.fresh_receipt.write_bytes(runner.canonical_json_bytes(receipt))
    config = replace(
        config,
        expected_fresh_receipt_sha256=_sha256(config.fresh_receipt),
    )

    with pytest.raises(runner.RunnerError) as error:
        runner.preflight(config, git_runner=_git_runner_factory(config))

    assert error.value.code == "FRESH_RECEIPT_STORE_MISMATCH"


def test_preflight_rejects_fresh_receipt_digest_mismatch(tmp_path):
    config = _fixture_config(tmp_path)
    config = replace(config, expected_fresh_receipt_sha256="0" * 64)

    with pytest.raises(runner.RunnerError) as error:
        runner.preflight(config, git_runner=_git_runner_factory(config))

    assert error.value.code == "FRESH_RECEIPT_DIGEST_MISMATCH"


@pytest.mark.parametrize("store_name", ("fresh_store", "rollback_store", "prior_store"))
def test_preflight_rejects_any_store_staging_sidecar(tmp_path, store_name):
    config = _fixture_config(tmp_path)
    store_path = getattr(config, store_name)
    Path(f"{store_path}.staging").write_bytes(b"staging")

    with pytest.raises(runner.RunnerError) as error:
        runner.preflight(config, git_runner=_git_runner_factory(config))

    assert error.value.code == "STORE_SIDECAR_PRESENT"


@pytest.mark.parametrize(
    ("kind", "suffix"),
    (("gateway", "-wal"), ("gateway", ".staging"), ("artifact", ".staging"), ("artifact", ".partial")),
)
def test_preflight_rejects_gateway_artifact_sidecar_family(tmp_path, kind, suffix):
    config = _fixture_config(tmp_path)
    base = config.gateway_store_path if kind == "gateway" else config.artifact_root
    Path(f"{base}{suffix}").write_bytes(b"forbidden sidecar")

    with pytest.raises(runner.RunnerError) as error:
        runner.preflight(config, git_runner=_git_runner_factory(config))

    assert error.value.code == f"{kind.upper()}_SIDECAR_PRESENT"


def test_collision_is_rejected_before_live_guard(tmp_path):
    config = _fixture_config(tmp_path)
    config.report_path.write_bytes(b"existing report")
    dependencies, calls = _stable_dependencies()
    git_runner = _git_runner_factory(config)

    result = runner.run(
        config,
        execute=True,
        git_runner=git_runner,
        dependencies=dependencies,
    )

    assert result["status"] == "REFUSED"
    assert result["code"] == "OUTPUT_COLLISION"
    assert calls == []
    assert config.report_path.read_bytes() == b"existing report"


def test_short_exclusive_write_fails_closed_without_partial_output(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()

    def short_write(_descriptor, data):
        return max(0, len(data) - 1)

    monkeypatch.setattr(runner.os, "write", short_write)
    result = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["exit_code"] == 3
    assert result["code"] == "OUTPUT_WRITE_FAILED"
    assert config.report_path.is_file()
    assert config.report_path.read_bytes() == b""
    assert not config.evidence_path.exists()


def test_evidence_collision_recovers_owned_report_only(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    original_write = runner._write_exclusive_json
    writes = 0
    competitor = b"competitor evidence"

    def collide_on_evidence(path, value, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 2:
            config.evidence_path.write_bytes(competitor)
        return original_write(path, value, **kwargs)

    monkeypatch.setattr(runner, "_write_exclusive_json", collide_on_evidence)
    result = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "REFUSED"
    assert result["exit_code"] == 1
    assert result["code"] == "OUTPUT_COLLISION"
    assert config.report_path.is_file()
    assert config.report_path.stat().st_size > 0
    assert config.evidence_path.read_bytes() == competitor


def test_output_collision_does_not_delete_the_current_attempt_report(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    original_write = runner._write_exclusive_json
    writes = 0

    def collide_on_evidence(path, value, **kwargs):
        nonlocal writes
        writes += 1
        if writes == 2:
            config.evidence_path.write_bytes(b"competitor evidence")
        return original_write(path, value, **kwargs)

    def forbidden_cleanup(_output):
        raise AssertionError("publication protocol must leave files for retry")

    monkeypatch.setattr(runner, "_write_exclusive_json", collide_on_evidence)
    monkeypatch.setattr(runner, "_remove_owned_output", forbidden_cleanup)

    result = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "REFUSED"
    assert result["code"] == "OUTPUT_COLLISION"
    assert config.report_path.is_file()
    assert config.evidence_path.read_bytes() == b"competitor evidence"


def test_exclusive_output_readback_mismatch_fails_closed(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    original_read_descriptor_bytes = runner._read_descriptor_bytes
    reads = 0

    def corrupt_readback(descriptor, code):
        nonlocal reads
        reads += 1
        value = original_read_descriptor_bytes(descriptor, code)
        if reads == 2:
            return value + b"corrupt"
        return value

    monkeypatch.setattr(runner, "_read_descriptor_bytes", corrupt_readback)
    result = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["exit_code"] == 3
    assert result["code"] == "OUTPUT_WRITE_FAILED"
    assert config.report_path.is_file()
    assert not config.evidence_path.exists()


def test_exclusive_json_reopens_the_windows_owned_file_handle(tmp_path):
    path = tmp_path / "evidence root with spaces" / "report.json"
    path.parent.mkdir()
    value = {"schema": "owned-output-v1", "value": "stable"}

    observed_digest = runner._write_exclusive_json(path, value)

    expected_bytes = runner.canonical_json_bytes(value)
    assert observed_digest == hashlib.sha256(expected_bytes).hexdigest()
    assert path.read_bytes() == expected_bytes


def test_publication_lease_retains_no_reparse_component_handles(tmp_path):
    path = tmp_path / "ancestor" / "evidence root with spaces"
    path.mkdir(parents=True)

    with runner._PublicationLease(path) as lease:
        assert getattr(lease, "component_identities", ())


def test_publication_lease_retains_each_path_component_identity(tmp_path):
    path = tmp_path / "ancestor" / "evidence root with spaces"
    path.mkdir(parents=True)

    with runner._PublicationLease(path) as lease:
        identities = getattr(lease, "component_identities", ())
        assert len(identities) == len(runner._directory_components(path))


def test_intermediate_directory_rename_is_blocked_by_publication_lease(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows handle contract")
    ancestor = tmp_path / "ancestor"
    path = ancestor / "evidence root with spaces"
    path.mkdir(parents=True)
    renamed = tmp_path / "ancestor-renamed"

    with runner._PublicationLease(path):
        try:
            ancestor.rename(renamed)
        except OSError:
            blocked = True
        else:
            renamed.rename(ancestor)
            blocked = False

    assert blocked


def test_output_create_receives_the_held_publication_parent(tmp_path, monkeypatch):
    path = tmp_path / "evidence root with spaces" / "report.json"
    path.parent.mkdir()
    seen: list[object] = []
    original = runner._create_exclusive_output_handle

    def create(path_value, code, **kwargs):
        seen.append(kwargs.get("parent"))
        return original(path_value, code, **kwargs)

    monkeypatch.setattr(runner, "_create_exclusive_output_handle", create)
    with runner._PublicationLease(path.parent) as parent:
        runner._write_exclusive_json(path, {"value": "stable"}, parent=parent)
        assert seen == [parent]


def test_operator_snapshot_held_input_lease_rehashes_same_size_mutation(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows handle contract")
    path = tmp_path / "operator-legacy-snapshot.json"
    path.write_bytes(b"a" * 64)
    import ctypes
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = ctypes.c_void_p
    writer_handle = kernel32.CreateFileW(
        str(path),
        0x80000000 | 0x40000000,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x00000080 | 0x00200000,
        None,
    )
    assert writer_handle not in (None, ctypes.c_void_p(-1).value)
    writer = msvcrt.open_osfhandle(writer_handle, os.O_RDWR)
    try:
        identity = runner._windows_handle_identity(writer, "LIVE_INPUT_DRIFT", directory=False)

        def hold_existing_writer(
            _path,
            _code,
            *,
            expected_identity=None,
            components_out=None,
            component_identities_out=None,
        ):
            del expected_identity
            del components_out, component_identities_out
            return os.dup(writer), dict(identity)

        monkeypatch.setattr(runner, "_open_bound_handle", hold_existing_writer)
        with runner._InputLease({path: identity}) as lease:
            os.lseek(writer, 0, os.SEEK_SET)
            os.write(writer, b"b" * 64)
            os.fsync(writer)
            with pytest.raises(runner.RunnerError) as error:
                lease.assert_stable()
            assert error.value.code == "LIVE_INPUT_DRIFT"
    finally:
        os.close(writer)


def test_input_lease_stays_held_through_both_publications(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    state = {"closed": False, "writes": 0}

    class HeldLease:
        def __enter__(self):
            return self

        def assert_stable(self):
            assert not state["closed"]

        def __exit__(self, _exc_type, _exc_value, _traceback):
            state["closed"] = True

    original_input_lease = runner._input_lease
    original_write = runner._write_exclusive_json

    def input_lease(_config, _preflight):
        return HeldLease()

    def write(path, value, **kwargs):
        assert not state["closed"]
        state["writes"] += 1
        return original_write(path, value, **kwargs)

    monkeypatch.setattr(runner, "_input_lease", input_lease)
    monkeypatch.setattr(runner, "_write_exclusive_json", write)

    result = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "GO"
    assert state == {"closed": True, "writes": 2}
    assert runner._input_lease is input_lease
    assert runner._write_exclusive_json is write
    assert original_input_lease is not None


def test_retry_rejects_report_only_residue_without_overwriting_it(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    original_write = runner._write_exclusive_json
    report_bytes: list[bytes] = []

    def crash_after_report(path, value, **kwargs):
        result = original_write(path, value, **kwargs)
        if path == config.report_path:
            report_bytes.append(path.read_bytes())
            raise RuntimeError("simulated crash after report publication")
        return result

    monkeypatch.setattr(runner, "_write_exclusive_json", crash_after_report)
    first = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert first["status"] == "UNAVAILABLE"
    assert config.report_path.is_file()
    assert not config.evidence_path.exists()
    assert report_bytes == [config.report_path.read_bytes()]

    monkeypatch.setattr(runner, "_write_exclusive_json", original_write)
    retry_dependencies, retry_calls = _stable_dependencies()
    second = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=retry_dependencies,
    )

    assert second["status"] == "REFUSED", second
    assert second["exit_code"] == 1
    assert second["code"] == "OUTPUT_COLLISION"
    assert retry_calls == []
    assert config.report_path.read_bytes() == report_bytes[0]
    assert not config.evidence_path.exists()


def test_fail_closed_report_only_residue_is_never_resumed(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    first_dependencies, _ = _stable_dependencies()
    original_write = runner._write_exclusive_json

    def crash_after_report(path, value, **kwargs):
        result = original_write(path, value, **kwargs)
        if path == config.report_path:
            raise RuntimeError("simulated crash after report publication")
        return result

    monkeypatch.setattr(runner, "_write_exclusive_json", crash_after_report)
    first = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=first_dependencies,
    )

    assert first["status"] == "UNAVAILABLE"
    assert config.report_path.is_file()
    assert not config.evidence_path.exists()
    report_bytes = config.report_path.read_bytes()

    monkeypatch.setattr(runner, "_write_exclusive_json", original_write)
    fresh_dependencies, calls = _stable_dependencies()
    second = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=fresh_dependencies,
    )

    assert second["status"] == "REFUSED", second
    assert second["exit_code"] == 1
    assert second["code"] == "OUTPUT_COLLISION"
    assert calls == []
    assert config.report_path.read_bytes() == report_bytes
    assert not config.evidence_path.exists()


def test_fail_closed_complete_pair_is_never_adopted(tmp_path):
    config = _fixture_config(tmp_path)
    first_dependencies, _ = _stable_dependencies()
    first = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=first_dependencies,
    )

    assert first["status"] == "GO"
    report_bytes = config.report_path.read_bytes()
    evidence_bytes = config.evidence_path.read_bytes()

    fresh_dependencies, calls = _stable_dependencies()
    second = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=fresh_dependencies,
    )

    assert second["status"] == "REFUSED", second
    assert second["exit_code"] == 1
    assert second["code"] == "OUTPUT_COLLISION"
    assert calls == []
    assert config.report_path.read_bytes() == report_bytes
    assert config.evidence_path.read_bytes() == evidence_bytes


def test_fail_closed_report_appearing_after_preflight_is_rejected_before_dependencies(
    tmp_path, monkeypatch
):
    (tmp_path / "authentic").mkdir()
    authentic_config = _fixture_config(tmp_path / "authentic")
    authentic_dependencies, _ = _stable_dependencies()
    authentic = runner.run(
        authentic_config,
        execute=True,
        git_runner=_git_runner_factory(authentic_config),
        dependencies=authentic_dependencies,
    )
    assert authentic["status"] == "GO"
    authentic_report = authentic_config.report_path.read_bytes()

    (tmp_path / "target").mkdir()
    config = _fixture_config(tmp_path / "target")
    fresh_dependencies, calls = _stable_dependencies()
    original_precheck = runner._precheck_existing_output_bytes
    restored = False

    def restore_report(config_value):
        nonlocal restored
        assert config_value is config
        restored = True
        config.report_path.write_bytes(authentic_report)
        return original_precheck(config_value)

    monkeypatch.setattr(runner, "_precheck_existing_output_bytes", restore_report)
    second = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=fresh_dependencies,
    )

    assert restored
    assert second["status"] == "REFUSED", second
    assert second["exit_code"] == 1
    assert second["code"] == "OUTPUT_COLLISION"
    assert calls == []
    assert config.report_path.read_bytes() == authentic_report
    assert not config.evidence_path.exists()


def test_fail_closed_run_does_not_call_resume_existing_outputs(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    dependencies, calls = _stable_dependencies()

    def tripwire(*_args, **_kwargs):
        raise AssertionError("_resume_existing_outputs must be unreachable")

    monkeypatch.setattr(runner, "_resume_existing_outputs", tripwire)
    result = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "GO", result
    assert result["exit_code"] == 0
    assert calls.count("guard") == 1


def test_retry_revalidates_the_held_report_before_recovery_evidence(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    original_write = runner._write_exclusive_json
    original_recovery = runner._recovery_evidence

    def crash_after_report(path, value, **kwargs):
        result = original_write(path, value, **kwargs)
        if path == config.report_path:
            raise RuntimeError("simulated crash after report publication")
        return result

    monkeypatch.setattr(runner, "_write_exclusive_json", crash_after_report)
    first = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )
    assert first["status"] == "UNAVAILABLE"
    monkeypatch.setattr(runner, "_write_exclusive_json", original_write)

    original_bytes = config.report_path.read_bytes()
    mutated = original_bytes.replace(b'"decision":"GO"', b'"decision":"NO"', 1)

    def mutate_report(*args, **kwargs):
        config.report_path.write_bytes(mutated)
        return original_recovery(*args, **kwargs)

    monkeypatch.setattr(runner, "_recovery_evidence", mutate_report)
    second = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert second["status"] != "GO", second
    assert not config.evidence_path.exists()


def test_retry_rejects_unknown_existing_evidence_field(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    first = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )
    assert first["status"] == "GO"
    evidence = json.loads(config.evidence_path.read_text(encoding="utf-8"))
    evidence["unexpected"] = True
    config.evidence_path.write_bytes(runner.canonical_json_bytes(evidence))

    second = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert second["status"] != "GO", second


def test_retry_rejects_unbound_existing_evidence_metadata(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    first = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )
    assert first["status"] == "GO"
    evidence = json.loads(config.evidence_path.read_text(encoding="utf-8"))
    evidence["head"] = "wrong"
    config.evidence_path.write_bytes(runner.canonical_json_bytes(evidence))

    second = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert second["status"] != "GO", second


def test_round4_retry_rejects_a_self_consistent_arbitrary_guard_readback(
    tmp_path, monkeypatch
):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    original_write = runner._write_exclusive_json

    def crash_after_report(path, value, **kwargs):
        result = original_write(path, value, **kwargs)
        if path == config.report_path:
            raise RuntimeError("simulated crash after report publication")
        return result

    monkeypatch.setattr(runner, "_write_exclusive_json", crash_after_report)
    first = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )
    assert first["status"] == "UNAVAILABLE"
    monkeypatch.setattr(runner, "_write_exclusive_json", original_write)

    report = json.loads(config.report_path.read_text(encoding="utf-8"))
    arbitrary_body = {"arbitrary": "not-a-current-main-readback"}
    arbitrary_readback = {
        **arbitrary_body,
        "readback_digest": runner._guard_digest(arbitrary_body),
    }
    for check in report["checks"]:
        if check["check_id"] == "durable_state":
            check["observed_digest"] = runner._guard_digest(arbitrary_readback)
    for item in report["readback_bundle"]:
        if item["check_id"] == "durable_state":
            item["readback"] = arbitrary_readback
    report["readback_digest"] = runner._guard_digest(
        {
            runner.CHECK_TO_GUARD_PORT[item["check_id"]]: item["readback"]
            for item in report["readback_bundle"]
        }
    )
    report["receipt"]["readback_digest"] = report["readback_digest"]
    report["receipt"]["receipt_digest"] = runner._digest_without(
        report["receipt"], "receipt_digest"
    )
    config.report_path.write_bytes(runner.canonical_json_bytes(report))

    second = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert second["status"] != "GO", second


def test_round4_retry_rejects_unbound_nested_live_observations(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    first = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )
    assert first["status"] == "GO"

    evidence = json.loads(config.evidence_path.read_text(encoding="utf-8"))
    forged_control = {"writer_generation": "v6.1", "control_ref": "forged"}
    evidence["before"]["control"] = forged_control
    evidence["after"]["control"] = forged_control
    config.evidence_path.write_bytes(runner.canonical_json_bytes(evidence))

    second = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert second["status"] != "GO", second


def _real_production_readers(config, subject, effects):
    from gwo_v8.runtime_gateway import ProfileMapping, RuntimeConfiguration
    from gwo_v8.runtime_profile import RuntimeProfile

    readbacks = _exact_fixture_readbacks(config, subject)

    class ReadOnlyPort:
        def __init__(self, name, value):
            self.name = name
            self.value = value
            self.calls: list[str] = []

        def read(self, repository):
            assert repository == config.repository
            self.calls.append(self.name)
            return self.value

    class ForbiddenExternal:
        def __getattr__(self, name):
            if name in {"start", "write", "mutate", "activate", "cas", "advance"}:
                raise AssertionError(f"forbidden external operation: {name}")
            raise AttributeError(name)

    class RejectGateway:
        def __call__(self, *_args, **_kwargs):
            effects.append("gateway")
            raise AssertionError("Gateway construction is forbidden")

    ports = {
        name: ReadOnlyPort(name, readbacks[name])
        for name in ("legacy", "durable_state", "writer_fence", "ownership")
    }
    ports["durable_state"].sqlite_uri = (
        f"file:{config.fresh_store}?mode=ro&immutable=1"
    )
    readers = runner.ProductionReaders(
        legacy_read=ports["legacy"],
        durable_state_read=None,
        writer_fence_read=ports["writer_fence"],
        ownership_read=ports["ownership"],
        content_client=ForbiddenExternal(),
        source=ForbiddenExternal(),
        repository=ForbiddenExternal(),
    )
    profile = RuntimeProfile(
        name="fixture",
        provider="fixture",
        model="fixture",
        thinking="fixture",
        mode="fixture",
        features={},
    )
    runtime_configuration = RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings={
            selector: ProfileMapping(profile.digest)
            for selector in subject.required_runtime_selectors
        },
    )
    return readers, runtime_configuration, ports, RejectGateway


def test_round4_real_composition_rejects_a_claimed_immutable_external_port(
    tmp_path, monkeypatch
):
    config = _fixture_config(tmp_path)
    effects: list[str] = []
    subject = _exact_fixture_subject(config)
    readers, runtime_configuration, ports, _reject_gateway = _real_production_readers(
        config, subject, effects
    )
    readers = replace(readers, durable_state_read=ports["durable_state"])
    config = replace(config, production_readers=readers)
    monkeypatch.setattr(runner, "_profile_configuration", lambda _config: runtime_configuration)

    with pytest.raises(runner.RunnerError) as error:
        runner._production_dependencies(config)

    assert error.value.code == "LIVE_GUARD_UNAVAILABLE"


def test_round4_runner_owned_durable_read_does_not_use_claimed_port(tmp_path):
    config = _fixture_config(tmp_path)
    subject = _exact_fixture_subject(config)
    expected = _exact_fixture_readbacks(config, subject)["durable_state"]

    class ClaimedPort:
        sqlite_uri = f"file:{config.fresh_store}?mode=ro&immutable=1"

        def __init__(self):
            self.calls = 0

        def read(self, _repository):
            self.calls += 1
            return _typed_with_digest(
                DurableStateReadback,
                {
                    "repository": config.repository,
                    "generation_id": "unrelated-store",
                    "state_schema": expected.state_schema,
                    "compatible": True,
                    "active_plan_digests": (),
                    "pending_activation_ids": (),
                    "predecessor_identity_refs": (),
                },
            )

    claimed = ClaimedPort()
    adapter = runner._ImmutableDurableStateReadPort(
        config.fresh_store,
        config.repository,
        config.store_generation,
        config.expected_store_tables,
        runner._guard_contract(),
        json.loads(config.fresh_receipt.read_text(encoding="utf-8")),
    )
    observed = adapter.read(config.repository)

    assert type(observed) is DurableStateReadback
    assert observed == expected
    assert claimed.calls == 0
    body = observed.canonical()
    assert observed.readback_digest == runner._guard_digest(
        {key: value for key, value in body.items() if key != "readback_digest"}
    )


def test_round4_runner_owned_durable_read_checks_sidecars_after_base_exception(
    tmp_path, monkeypatch
):
    config = _fixture_config(tmp_path)
    calls: list[Path] = []
    original_check = runner._check_sidecars

    def checked(path):
        calls.append(path)
        return original_check(path)

    def interrupted(_adapter, _connection):
        raise KeyboardInterrupt("fixture interruption")

    monkeypatch.setattr(runner, "_check_sidecars", checked)
    monkeypatch.setattr(
        runner._ImmutableDurableStateReadPort,
        "_read_from_connection",
        interrupted,
    )
    adapter = runner._ImmutableDurableStateReadPort(
        config.fresh_store,
        config.repository,
        config.store_generation,
        config.expected_store_tables,
        runner._guard_contract(),
        json.loads(config.fresh_receipt.read_text(encoding="utf-8")),
    )

    with pytest.raises(runner.RunnerError) as error:
        adapter.read(config.repository)

    assert error.value.code == "LIVE_GUARD_UNAVAILABLE"
    assert calls == [config.fresh_store.resolve(), config.fresh_store.resolve()]


def test_production_composition_uses_real_host_and_typed_read_ports_without_mutation(
    tmp_path, monkeypatch
):
    config = _fixture_config(tmp_path)
    effects: list[str] = []
    module_root = config.repository_root / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    module_root.mkdir(parents=True)
    (module_root / "__init__.py").write_text("# fixture exact-main module\n", encoding="utf-8")
    (module_root / "plan_control_host.py").write_text(
        "class ProductionPlanControlStartHost:\n    def start(self):\n        return None\n",
        encoding="utf-8",
    )
    (module_root / "execution_kernel.py").write_text(
        "def advance():\n    return None\n\ndef inspect():\n    return None\n",
        encoding="utf-8",
    )
    from gwo_v8.cutover_guard import source_tree_digest

    subject = replace(
        _exact_fixture_subject(config),
        source_tree_digest=source_tree_digest(config.repository_root),
    )
    readers, runtime_configuration, ports, reject_gateway = _real_production_readers(
        config, subject, effects
    )
    config = replace(config, production_readers=readers)
    monkeypatch.setattr(runner, "_profile_configuration", lambda _config: runtime_configuration)

    dependencies = runner._production_dependencies(config)
    result = dependencies.live_guard(config, subject)

    assert type(result) is runner.GuardExecution
    assert type(result.report) is CutoverGuardReport
    assert type(result.subject) is CutoverSubject
    assert type(result.readback_bundle) is CutoverReadbackBundle
    assert tuple(type(getattr(result.readback_bundle, name)) for name in runner.GUARD_PORT_ORDER) == (
        LegacyReadback,
        DurableStateReadback,
        WriterFenceReadback,
        OwnershipReadback,
        CompatibilityPathReadback,
        RuntimePreflightReadback,
        PackageReadback,
    )
    assert {name: port.calls for name, port in ports.items()} == {
        "legacy": ["legacy", "legacy"],
        "durable_state": [],
        "writer_fence": ["writer_fence", "writer_fence"],
        "ownership": ["ownership", "ownership"],
    }
    assert effects == []
    assert not config.gateway_store_path.exists()
    assert not config.artifact_root.exists()
    assert reject_gateway is not None


def test_real_composition_requires_a_proven_immutable_durable_adapter(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    effects: list[str] = []
    subject = _exact_fixture_subject(config)
    readers, runtime_configuration, ports, _reject_gateway = _real_production_readers(
        config, subject, effects
    )
    del ports["durable_state"].sqlite_uri
    config = replace(
        config,
        production_readers=replace(
            readers,
            durable_state_read=ports["durable_state"],
        ),
    )
    monkeypatch.setattr(runner, "_profile_configuration", lambda _config: runtime_configuration)

    with pytest.raises(runner.RunnerError) as error:
        runner._production_dependencies(config)

    assert error.value.code == "LIVE_GUARD_UNAVAILABLE"


def test_real_composition_rejects_ordinary_mode_ro_durable_adapter(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    effects: list[str] = []
    subject = _exact_fixture_subject(config)
    readers, runtime_configuration, ports, _reject_gateway = _real_production_readers(
        config, subject, effects
    )
    ports["durable_state"].sqlite_uri = f"file:{config.fresh_store}?mode=ro"
    config = replace(
        config,
        production_readers=replace(
            readers,
            durable_state_read=ports["durable_state"],
        ),
    )
    monkeypatch.setattr(runner, "_profile_configuration", lambda _config: runtime_configuration)

    with pytest.raises(runner.RunnerError) as error:
        runner._production_dependencies(config)

    assert error.value.code == "LIVE_GUARD_UNAVAILABLE"


def test_live_durable_read_rejects_a_sidecar_created_during_the_real_read(
    tmp_path, monkeypatch
):
    config = _fixture_config(tmp_path)
    effects: list[str] = []
    module_root = config.repository_root / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    module_root.mkdir(parents=True)
    (module_root / "__init__.py").write_text("# fixture exact-main module\n", encoding="utf-8")
    (module_root / "plan_control_host.py").write_text(
        "class ProductionPlanControlStartHost:\n    def start(self):\n        return None\n",
        encoding="utf-8",
    )
    (module_root / "execution_kernel.py").write_text(
        "def advance():\n    return None\n\ndef inspect():\n    return None\n",
        encoding="utf-8",
    )
    from gwo_v8.cutover_guard import source_tree_digest

    subject = replace(
        _exact_fixture_subject(config),
        source_tree_digest=source_tree_digest(config.repository_root),
    )
    readers, runtime_configuration, _ports, _reject_gateway = _real_production_readers(
        config, subject, effects
    )
    sidecar = Path(f"{config.fresh_store}-wal")
    original_read = runner._ImmutableDurableStateReadPort._read_from_connection

    def read_with_sidecar(adapter, connection):
        result = original_read(adapter, connection)
        sidecar.write_bytes(b"live sidecar")
        return result

    monkeypatch.setattr(
        runner._ImmutableDurableStateReadPort,
        "_read_from_connection",
        read_with_sidecar,
    )
    config = replace(config, production_readers=readers)
    monkeypatch.setattr(runner, "_profile_configuration", lambda _config: runtime_configuration)
    dependencies = runner._production_dependencies(config)

    with pytest.raises(runner.RunnerError) as error:
        dependencies.live_guard(config, subject)

    assert error.value.code == "LIVE_GUARD_UNAVAILABLE"


def test_live_durable_read_rejects_a_sidecar_created_during_the_read(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    effects: list[str] = []
    subject = _exact_fixture_subject(config)
    readers, runtime_configuration, _ports, _reject_gateway = _real_production_readers(
        config, subject, effects
    )
    sidecar = Path(f"{config.fresh_store}-wal")
    original_read = runner._ImmutableDurableStateReadPort._read_from_connection

    def read_with_sidecar(adapter, connection):
        result = original_read(adapter, connection)
        sidecar.write_bytes(b"live sidecar")
        return result

    monkeypatch.setattr(
        runner._ImmutableDurableStateReadPort,
        "_read_from_connection",
        read_with_sidecar,
    )
    config = replace(config, production_readers=readers)
    monkeypatch.setattr(runner, "_profile_configuration", lambda _config: runtime_configuration)
    dependencies = runner._production_dependencies(config)

    with pytest.raises(runner.RunnerError) as error:
        dependencies.live_guard(config, subject)

    assert error.value.code == "LIVE_GUARD_UNAVAILABLE"


def test_go_writes_report_then_evidence_exclusively_and_preserves_contract(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies, calls = _stable_dependencies()
    git_runner = _git_runner_factory(config)

    result = runner.run(
        config,
        execute=True,
        git_runner=git_runner,
        dependencies=dependencies,
    )

    assert result["status"] == "GO"
    assert result["exit_code"] == 0
    assert calls == [
        "control",
        "packages",
        "control",
        "packages",
        "guard",
        "control",
        "packages",
    ]
    report = json.loads(config.report_path.read_text(encoding="utf-8"))
    evidence = json.loads(config.evidence_path.read_text(encoding="utf-8"))
    assert len(report["checks"]) == 7
    assert tuple(item["check_id"] for item in report["checks"]) == runner.EXPECTED_CHECK_IDS
    assert report["receipt"] is not None
    assert report["activation_performed"] is False
    assert evidence["activation_performed"] is False
    assert evidence["default_writer_changed"] is False
    assert evidence["writer_generation"] == "v6.1"
    assert config.report_path.read_bytes() == runner.canonical_json_bytes(report)
    assert config.evidence_path.read_bytes() == runner.canonical_json_bytes(evidence)

    original_report = config.report_path.read_bytes()
    original_evidence = config.evidence_path.read_bytes()
    retry_dependencies, retry_calls = _stable_dependencies()
    result = runner.run(
        config,
        execute=True,
        git_runner=git_runner,
        dependencies=retry_dependencies,
    )
    assert result["status"] == "REFUSED"
    assert result["exit_code"] == 1
    assert result["code"] == "OUTPUT_COLLISION"
    assert retry_calls == []
    assert config.report_path.read_bytes() == original_report
    assert config.evidence_path.read_bytes() == original_evidence
    assert calls.count("guard") == 1


def test_no_go_writes_canonical_evidence_and_returns_two(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies(decision="NO_GO")
    result = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "NO_GO"
    assert result["exit_code"] == 2
    assert config.report_path.is_file()
    assert config.evidence_path.is_file()
    report = json.loads(config.report_path.read_text(encoding="utf-8"))
    evidence = json.loads(config.evidence_path.read_text(encoding="utf-8"))
    assert report["decision"] == "NO_GO"
    assert report["receipt"] is None
    assert evidence["decision"] == "NO_GO"
    assert evidence["canonical_guard_evidence"]["blockers"]


@pytest.mark.parametrize("drift_key", ("control", "packages"))
def test_store_control_and_package_drift_after_guard_refuses_without_go(tmp_path, drift_key):
    config = _fixture_config(tmp_path)
    dependencies, calls = _stable_dependencies(changed={drift_key: "changed"})

    result = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "REFUSED"
    assert result["code"] == "LIVE_INPUT_DRIFT"
    assert calls == ["control", "packages", "control", "packages"]
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


def test_store_hash_drift_after_guard_refuses_without_go(tmp_path):
    config = _fixture_config(tmp_path)
    stable, _ = _stable_dependencies()

    def mutating_guard(guard_config, subject):
        report = stable.live_guard(guard_config, subject)
        guard_config.fresh_store.write_bytes(b"drift after Guard")
        return report

    dependencies = runner.ExecutionDependencies(
        live_guard=mutating_guard,
        control_read=stable.control_read,
        package_read=stable.package_read,
    )
    result = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "REFUSED"
    assert result["exit_code"] == 1
    assert result["code"] == "LIVE_INPUT_DRIFT"
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


def test_live_guard_exception_is_unavailable_and_never_fakes_go(tmp_path):
    config = _fixture_config(tmp_path)
    calls: list[str] = []

    def raises(_config, _subject):
        calls.append("guard")
        raise RuntimeError("gateway must not be used")

    dependencies = runner.ExecutionDependencies(
        live_guard=raises,
        control_read=lambda: {"writer_generation": "v6.1", "control_ref": "stable"},
        package_read=lambda _config: "stable",
    )
    result = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["exit_code"] == 3
    assert result["code"] == "LIVE_GUARD_UNAVAILABLE"
    assert calls == ["guard"]
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


def test_execute_path_does_not_create_gateway_artifact_or_sqlite_sidecar(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()

    result = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["exit_code"] == 0
    assert not config.gateway_store_path.exists()
    assert not config.artifact_root.exists()
    for path in (config.fresh_store, config.rollback_store, config.prior_store):
        assert not Path(f"{path}-wal").exists()
        assert not Path(f"{path}-shm").exists()


def test_main_default_prints_canonical_preflight_json(tmp_path, capsys):
    config = _fixture_config(tmp_path)
    code = runner.main([], config=config, git_runner=_git_runner_factory(config))

    assert code == 0
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "PREFLIGHT_OK"
    assert output == output.strip() + "\n"


def test_go_rejects_unbound_guard_digests(tmp_path):
    config = _fixture_config(tmp_path)
    stable, _ = _stable_dependencies()

    def unbound_guard(guard_config, subject):
        valid = stable.live_guard(guard_config, subject)
        original = valid.report.canonical()
        original["subject_digest"] = "f" * 64
        return runner.GuardExecution(
            SimpleNamespace(
                decision=valid.report.decision,
                canonical=lambda: original,
            ),
            valid.subject,
            valid.readback_bundle,
        )

    dependencies = runner.ExecutionDependencies(
        live_guard=unbound_guard,
        control_read=stable.control_read,
        package_read=stable.package_read,
    )

    result = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["exit_code"] == 3
    assert result["code"] == "LIVE_GUARD_INVALID"
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


def test_guard_input_substitution_and_restoration_is_not_silent(tmp_path):
    config = _fixture_config(tmp_path)
    stable, _ = _stable_dependencies()
    source_file = config.repository_root / "skills" / "implement-gwo" / "SKILL.md"
    backup = tmp_path / "source-backup.md"
    replacement = tmp_path / "replacement.md"
    shutil.copy2(source_file, replacement)

    def swapping_guard(guard_config, subject):
        source_file.replace(backup)
        try:
            replacement.replace(source_file)
            source_file.replace(replacement)
            backup.replace(source_file)
        finally:
            if backup.exists() and not source_file.exists():
                backup.replace(source_file)
        return stable.live_guard(guard_config, subject)

    dependencies = runner.ExecutionDependencies(
        live_guard=swapping_guard,
        control_read=stable.control_read,
        package_read=stable.package_read,
    )
    result = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] != "GO", result
    assert result["exit_code"] != 0
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


def test_pre_guard_refresh_rejects_same_identity_package_content_drift(tmp_path):
    config = _fixture_config(tmp_path)
    stable, _ = _stable_dependencies()
    source_file = config.repository_root / "skills" / "implement-gwo" / "SKILL.md"
    original_stat = source_file.stat()
    original_length = len(source_file.read_bytes())
    first = True

    def mutating_control():
        nonlocal first
        value = stable.control_read()
        if first:
            first = False
            source_file.write_bytes(b"X" * original_length)
            os.utime(
                source_file,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
        return value

    dependencies = runner.ExecutionDependencies(
        live_guard=stable.live_guard,
        control_read=mutating_control,
        package_read=stable.package_read,
    )
    result = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] != "GO", result
    assert result["exit_code"] != 0
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


def test_default_config_pins_the_authoritative_fresh_receipt_digest():
    assert runner.DEFAULT_CONFIG.expected_fresh_receipt_sha256 == (
        "46814d166c857e3d7f847b7da6f3da5b39c394b42402b2f1d2cdd61d78ce7781"
    )


def test_actual_skill_manifest_contract_is_accepted_by_preflight(tmp_path):
    config = _fixture_config(tmp_path)
    for package_name in config.package_names:
        roots = [
            config.repository_root / "skills" / package_name,
            *(root / package_name for root in config.install_roots),
        ]
        for package_root in roots:
            (package_root / ".skill-package.json").write_text(
                json.dumps(
                    {
                        "content_sha256": _package_content_digest(package_root),
                        "schema_version": 1,
                        "skill": package_name,
                        "version": "8.0.0",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

    result = runner.preflight(config, git_runner=_git_runner_factory(config))

    assert result["status"] == "PREFLIGHT_OK"


def test_sync_orchestrator_indent_two_manifest_contract_is_accepted(tmp_path):
    config = _fixture_config(tmp_path)
    for package_name in config.package_names:
        roots = [
            config.repository_root / "skills" / package_name,
            *(root / package_name for root in config.install_roots),
        ]
        for package_root in roots:
            manifest = json.loads((package_root / ".skill-package.json").read_text(encoding="utf-8"))
            (package_root / ".skill-package.json").write_text(
                json.dumps(manifest, indent=2) + "\n",
                encoding="utf-8",
            )

    result = runner.preflight(config, git_runner=_git_runner_factory(config))

    assert result["status"] == "PREFLIGHT_OK"


def test_current_main_readback_digest_includes_the_typed_readback_digest_field():
    body = {"repository": "owner/repo", "value": "stable"}
    supplied = runner._guard_digest(body)
    value = SimpleNamespace(
        canonical=lambda: {**body, "readback_digest": supplied}
    )

    assert runner._readback_digest(value) == runner._guard_digest(
        {**body, "readback_digest": supplied}
    )


def test_fake_simple_namespace_guard_report_is_not_a_typed_current_main_report(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    subject = _exact_fixture_subject(config)
    valid = dependencies.live_guard(config, subject)
    result = runner.GuardExecution(
        SimpleNamespace(decision=valid.report.decision, canonical=valid.report.canonical),
        valid.subject,
        valid.readback_bundle,
        valid.contract,
    )

    with pytest.raises(runner.RunnerError) as error:
        runner._guard_payload(result, config.repository, expected_subject=subject)

    assert error.value.code == "LIVE_GUARD_INVALID"


def test_malformed_git_quoted_status_record_fails_closed(tmp_path):
    config = _fixture_config(tmp_path)
    git_runner = _git_runner_factory(
        config,
        status='?? ".codex-tmp/bad\\q"\0',
    )

    with pytest.raises(runner.RunnerError) as error:
        runner.preflight(config, git_runner=git_runner)

    assert error.value.code == "GIT_STATUS_INVALID"


def test_dynamic_gateway_temp_sidecar_family_is_rejected(tmp_path):
    config = _fixture_config(tmp_path)
    Path(f"{config.gateway_store_path}.0123456789abcdef.tmp").write_bytes(b"temp")

    with pytest.raises(runner.RunnerError) as error:
        runner.preflight(config, git_runner=_git_runner_factory(config))

    assert error.value.code == "GATEWAY_SIDECAR_PRESENT"


def test_production_store_adapter_is_immutable_and_sidecar_checked_per_read():
    source = inspect.getsource(runner._read_only_sqlite)

    assert "mode=ro&immutable=1" in source
    assert "_check_sidecars" not in source
    composition = inspect.getsource(runner._production_dependencies)
    assert "sqlite3.connect" not in composition


def test_legacy_read_does_not_synthesize_lease_decoder_or_use_paseo_provider():
    source = inspect.getsource(runner._production_dependencies)

    assert "integration_lease=False" not in source
    assert '"original_decoder_readable": True' not in source
    assert "Paseo" not in source
    assert "GitHubLegacyWriterControl" not in source
    assert "production_legacy_writer_control" not in source


def test_output_cleanup_is_handle_owned_not_path_stat_owned():
    source = inspect.getsource(runner._remove_owned_output)

    assert "os.unlink(path)" not in source
    assert "st_ino" not in source
    assert "st_mtime_ns" not in source


def test_round5_retry_rejects_a_self_consistent_exact_typed_durable_readback(
    tmp_path, monkeypatch
):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    original_write = runner._write_exclusive_json

    def crash_after_report(path, value, **kwargs):
        result = original_write(path, value, **kwargs)
        if path == config.report_path:
            raise RuntimeError("simulated crash after report publication")
        return result

    monkeypatch.setattr(runner, "_write_exclusive_json", crash_after_report)
    first = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )
    assert first["status"] == "UNAVAILABLE"
    monkeypatch.setattr(runner, "_write_exclusive_json", original_write)

    report = json.loads(config.report_path.read_text(encoding="utf-8"))
    forged = _typed_with_digest(
        DurableStateReadback,
        {
            "repository": config.repository,
            "generation_id": config.store_generation,
            "state_schema": "gwo.v8.store.v1",
            "compatible": False,
            "active_plan_digests": ("a" * 64,),
            "pending_activation_ids": (),
            "predecessor_identity_refs": (),
        },
    ).canonical()
    for check in report["checks"]:
        if check["check_id"] == "durable_state":
            check["observed_digest"] = runner._guard_digest(forged)
    for item in report["readback_bundle"]:
        if item["check_id"] == "durable_state":
            item["readback"] = forged
    report["readback_digest"] = runner._guard_digest(
        {
            runner.CHECK_TO_GUARD_PORT[item["check_id"]]: item["readback"]
            for item in report["readback_bundle"]
        }
    )
    report["receipt"]["readback_digest"] = report["readback_digest"]
    report["receipt"]["receipt_digest"] = runner._digest_without(
        report["receipt"], "receipt_digest"
    )
    config.report_path.write_bytes(runner.canonical_json_bytes(report))

    second = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert second["status"] != "GO", second
    assert not config.evidence_path.exists()


def test_round5_retry_rejects_a_no_go_report_that_has_a_receipt(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies(decision="NO_GO")
    original_write = runner._write_exclusive_json

    def crash_after_report(path, value, **kwargs):
        result = original_write(path, value, **kwargs)
        if path == config.report_path:
            raise RuntimeError("simulated crash after report publication")
        return result

    monkeypatch.setattr(runner, "_write_exclusive_json", crash_after_report)
    first = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )
    assert first["status"] == "UNAVAILABLE"
    monkeypatch.setattr(runner, "_write_exclusive_json", original_write)

    report = json.loads(config.report_path.read_text(encoding="utf-8"))
    by_check = {
        item["check_id"]: item["readback"] for item in report["readback_bundle"]
    }
    receipt_body = {
        "schema": "gwo.cutover-guard-receipt.v1",
        "repository": config.repository,
        "subject_digest": report["subject_digest"],
        "readback_digest": report["readback_digest"],
        "source_writer_generation": "v6.1",
        "target_writer_generation": "v8",
        "store_generation": config.store_generation,
        "writer_control_ref_digest": by_check["source_writer"]["control_ref_digest"],
        "runtime_configuration_digest": by_check["runtime_configuration"][
            "configuration_digest"
        ],
        "compatibility_audit_digest": by_check["production_paths"]["readback_digest"],
        "package_readback_digest": by_check["package_installation"]["readback_digest"],
    }
    report["receipt"] = {
        **receipt_body,
        "receipt_digest": runner._guard_digest(receipt_body),
    }
    config.report_path.write_bytes(runner.canonical_json_bytes(report))

    second = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert second["status"] == "REFUSED", second
    assert second["code"] == "OUTPUT_COLLISION"
    assert not config.evidence_path.exists()


def test_round5_retry_rejects_a_noncanonical_or_unbound_capture_timestamp(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    first = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )
    assert first["status"] == "GO"

    evidence = json.loads(config.evidence_path.read_text(encoding="utf-8"))
    evidence["captured_at"] = "2026-08-10T00:00:00Z"
    config.evidence_path.write_bytes(runner.canonical_json_bytes(evidence))

    second = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert second["status"] != "GO", second


def test_round5_retry_rechecks_a_fresh_complete_observation_before_adoption(tmp_path):
    config = _fixture_config(tmp_path)
    stable, _ = _stable_dependencies()
    control_reads = 0

    def control_read():
        nonlocal control_reads
        control_reads += 1
        if control_reads >= 6:
            return {"writer_generation": "v6.1", "control_ref": "drift-before-adoption"}
        return {"writer_generation": "v6.1", "control_ref": "control-stable"}

    dependencies = replace(stable, control_read=control_read)
    first = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )
    assert first["status"] == "GO"

    second = runner.run(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert second["status"] != "GO", second


def test_round5_runner_owned_read_rejects_the_weak_value_text_schema(tmp_path):
    config = _fixture_config(tmp_path)
    adapter = runner._ImmutableDurableStateReadPort(
        config.fresh_store,
        config.repository,
        config.store_generation,
        config.expected_store_tables,
        runner._guard_contract(),
    )

    with pytest.raises(runner.RunnerError) as error:
        adapter.read(config.repository)

    assert error.value.code == "LIVE_GUARD_UNAVAILABLE"


def test_round5_exact_current_main_ddl_yields_exact_typed_durable_readback(tmp_path):
    config = _fixture_config(tmp_path)
    adapter = runner._ImmutableDurableStateReadPort(
        config.fresh_store,
        config.repository,
        config.store_generation,
        config.expected_store_tables,
        runner._guard_contract(),
        json.loads(config.fresh_receipt.read_text(encoding="utf-8")),
    )

    observed = adapter.read(config.repository)

    assert type(observed) is DurableStateReadback
    assert observed.repository == config.repository
    assert observed.generation_id == config.store_generation
    assert observed.state_schema == "gwo.v8.store.v1"
    assert observed.compatible is True
    assert observed.active_plan_digests == ()
    assert observed.pending_activation_ids == ()
    assert observed.predecessor_identity_refs == ()
    assert observed.readback_digest == runner._guard_digest(
        {
            key: value
            for key, value in observed.canonical().items()
            if key != "readback_digest"
        }
    )


def test_round5_runner_owned_read_rejects_schema_digest_drift_from_extra_index(tmp_path):
    config = _fixture_config(tmp_path)
    connection = sqlite3.connect(config.fresh_store)
    try:
        connection.execute(
            'create index "round5_schema_drift" on "v8_active_plans" (plan_digest)'
        )
        connection.commit()
    finally:
        connection.close()
    adapter = runner._ImmutableDurableStateReadPort(
        config.fresh_store,
        config.repository,
        config.store_generation,
        config.expected_store_tables,
        runner._guard_contract(),
        json.loads(config.fresh_receipt.read_text(encoding="utf-8")),
    )

    with pytest.raises(runner.RunnerError) as error:
        adapter.read(config.repository)

    assert error.value.code == "LIVE_GUARD_UNAVAILABLE"
