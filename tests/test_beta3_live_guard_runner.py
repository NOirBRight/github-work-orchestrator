from __future__ import annotations

import hashlib
import io
import importlib.util
import inspect
import json
import multiprocessing
from dataclasses import replace
import os
from pathlib import Path
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
SCRIPTS = REPO_ROOT / "scripts"
for path in (EXACT_SCRIPTS, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
from gwo_v8._canonical import digest_value  # noqa: E402
from gwo_v8.cutover_guard import (  # noqa: E402
    CompatibilityPathReadback,
    CutoverSubject,
    DurableStateReadback,
    LegacyReadback,
    OwnershipReadback,
    PackageIdentity,
    PackageReadback,
    RuntimePreflightReadback,
    RuntimeSelectorReadback,
    WriterFenceReadback,
)
from beta3_bootstrap_model import (  # noqa: E402
    AttestedCutoverBundle,
    AttemptIdentity,
    BootstrapError,
    ComponentObservation,
    FieldBinding,
    SourceRecord,
    WriterAuthorityObservation,
)
from beta3_replay_guard import ReplayResult, evaluate_attested_bundle  # noqa: E402


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
        if path.suffix.lower() in {
            ".toml",
            ".md",
            ".py",
            ".yaml",
            ".yml",
            ".json",
            ".txt",
        }:
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
            stdout = config.merged_main_sha
        elif args[:3] == ["rev-parse", "--verify", "HEAD^{tree}"]:
            stdout = config.merged_main_git_tree
        elif args[:3] == ["rev-parse", "--verify", "origin/main"]:
            stdout = config.merged_main_sha if origin is None else origin
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
        "source_main_sha": config.merged_main_sha,
        "source_main_tree": config.merged_main_git_tree,
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
    config.fresh_receipt.write_bytes(runner.canonical_json_bytes(receipt))


def _fixture_config(tmp_path: Path):
    root = tmp_path / "repo root with spaces"
    evidence = tmp_path / "evidence root with spaces"
    stores = tmp_path / "stores"
    runtime_config = tmp_path / "runtime-config.json"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "skills").mkdir()
    evidence.mkdir()
    stores.mkdir()
    runtime_config.write_text("{}\n", encoding="utf-8")
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
            (target / "SKILL.md").write_text(f"# {package}\n", encoding="utf-8")
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
        merged_main_sha="a" * 40,
        merged_main_git_tree="b" * 40,
        audited_source_tree_digest="c" * 64,
        release_subject_digest="d" * 64,
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
        runtime_config_path=runtime_config,
        expected_package_version="8.0.0",
    )
    _write_receipt(config)
    config = replace(
        config,
        expected_fresh_receipt_sha256=_sha256(config.fresh_receipt),
    )
    return config


class _FixtureBinding:
    def __init__(self, config, *, fixture_dependencies=False):
        self.config = config
        self.manifest_path = config.evidence_root / "gwo-v8-release-subject.json"
        self.manifest_path.write_bytes(b'{"fixture":true}\n')
        self.subject = SimpleNamespace(
            merged_main_sha=config.merged_main_sha,
            merged_main_git_tree=config.merged_main_git_tree,
            audited_source_tree_digest=config.audited_source_tree_digest,
            subject_digest=config.release_subject_digest,
        )
        self._stable = True
        if fixture_dependencies:
            self.git_runner = _git_runner_factory(config)
            self.dependencies, _calls = _stable_dependencies()

    def assert_stable(self):
        if not self._stable:
            raise runner.RunnerError(
                "RELEASE_SUBJECT_DRIFT", "fixture release subject was replaced"
            )

    def replace_for_test(self):
        self._stable = False

    def close(self):
        self.manifest_path.unlink(missing_ok=True)


def _fixture_binding(tmp_path: Path):
    return _FixtureBinding(
        _fixture_config(tmp_path),
        fixture_dependencies=True,
    )


def _fixture_config_for_binding(binding):
    return binding.config


def _preflight_in_child(config, result_queue):
    try:
        runner.preflight(config, git_runner=_git_runner_factory(config))
    except runner.RunnerError as error:
        result_queue.put(("runner-error", error.code))
    except BaseException as error:
        result_queue.put(("unexpected-error", type(error).__name__, str(error)))
    else:
        result_queue.put(("ok",))


def _run_fixture(config=None, *, execute, run_id=None, **injections):
    binding = _FixtureBinding(config)
    for name, value in injections.items():
        setattr(binding, name, value)
    return runner.run_fixture(
        config,
        binding=binding,
        execute=execute,
        run_id=run_id,
    )


def _run_fixture_guard_to_completion(tmp_path: Path, *, run_id: str):
    binding = _fixture_binding(tmp_path)
    config = _fixture_config_for_binding(binding)
    result = runner.run_fixture(
        config,
        binding=binding,
        execute=True,
        run_id=run_id,
    )
    assert result["status"] == "GO", result
    return {
        "result": result,
        "report": json.loads(config.report_path.read_text(encoding="utf-8")),
        "evidence": json.loads(config.evidence_path.read_text(encoding="utf-8")),
        "subject": {
            "release_subject_digest": binding.subject.subject_digest,
            "release_subject_path": str(binding.manifest_path),
            "merged_main_sha": binding.subject.merged_main_sha,
            "merged_main_git_tree": binding.subject.merged_main_git_tree,
            "audited_source_tree_digest": binding.subject.audited_source_tree_digest,
        },
    }


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
        source_commit=config.merged_main_sha,
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
            "source_commit": config.merged_main_sha,
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


def _stable_dependencies(
    *,
    decision: str = "GO",
    changed: dict[str, Any] | None = None,
    unavailable_role: str | None = None,
    check_authoritative_sources: bool = False,
    drift_observation: int | None = None,
):
    dependencies, calls, _counts = _attested_dependencies(
        None,
        decision=decision,
        changed=changed,
        unavailable_role=unavailable_role,
        check_authoritative_sources=check_authoritative_sources,
        drift_observation=drift_observation,
    )
    return dependencies, calls


def test_execute_requires_operator_run_id_but_preflight_does_not(tmp_path):
    config = _fixture_config(tmp_path)
    assert (
        _run_fixture(
            config,
            execute=False,
            git_runner=_git_runner_factory(config),
        )["exit_code"]
        == 0
    )
    assert (
        _run_fixture(
            config,
            execute=True,
            run_id=None,
            git_runner=_git_runner_factory(config),
        )["exit_code"]
        == 1
    )


def test_execute_checks_run_id_before_execute_source_preflight(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    events: list[str] = []
    original_preflight = runner.preflight

    def recording_preflight(*args, **kwargs):
        events.append("preflight")
        return original_preflight(*args, **kwargs)

    monkeypatch.setattr(runner, "preflight", recording_preflight)
    result = _run_fixture(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
    )

    assert result["status"] == "REFUSED"
    assert result["exit_code"] == 1
    assert result["code"] == "RUN_ID_REQUIRED"
    assert events == []
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


@pytest.mark.parametrize(
    "field", ("authoritative_legacy_snapshot", "production_readers")
)
def test_obsolete_injection_is_rejected_before_source_or_nonce(
    tmp_path, monkeypatch, field
):
    config = _fixture_config(tmp_path)
    value = (
        tmp_path / "legacy-snapshot.json"
        if field == "authoritative_legacy_snapshot"
        else object()
    )
    config = replace(config, **{field: value})

    def forbidden(*_args, **_kwargs):
        raise AssertionError("obsolete injection reached a source or nonce")

    monkeypatch.setattr(runner, "_git_snapshot", forbidden)
    monkeypatch.setattr(runner.secrets, "token_hex", forbidden)
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
    )

    assert result["status"] == "UNAVAILABLE", result
    assert result["exit_code"] == 3
    assert result["code"] == "DEPENDENCY_INJECTION_FORBIDDEN"
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


def test_missing_execute_run_id_is_rejected_before_source_or_nonce(
    tmp_path, monkeypatch
):
    config = _fixture_config(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("missing run_id reached a source or nonce")

    monkeypatch.setattr(runner, "_git_snapshot", forbidden)
    monkeypatch.setattr(runner.secrets, "token_hex", forbidden)
    result = _run_fixture(
        config,
        execute=True,
        git_runner=_git_runner_factory(config),
    )

    assert result["status"] == "REFUSED", result
    assert result["exit_code"] == 1
    assert result["code"] == "RUN_ID_REQUIRED"


def test_runner_config_exposes_exact_attestor_configuration(tmp_path):
    config = _fixture_config(tmp_path)

    assert config.runtime_config_path.is_file()
    assert config.expected_package_version == "8.0.0"


@pytest.mark.parametrize(
    "injection",
    (
        {"dependencies": object()},
        {"guard_factory": lambda *_args: object()},
        {"control_reader": lambda: object()},
        {"package_reader": lambda _config: object()},
        {"git_runner": lambda *_args, **_kwargs: object()},
    ),
)
def test_fixed_production_subject_rejects_dependency_injection_before_source_access(
    monkeypatch, injection
):
    class Binding:
        subject = object()

        def assert_stable(self):
            return None

        def close(self):
            return None

    def unexpected_preflight(*_args, **_kwargs):
        raise AssertionError(
            "fixed production injection must be rejected before preflight"
        )

    monkeypatch.setattr(runner, "load_production_release_subject", lambda: Binding())
    monkeypatch.setattr(
        runner,
        "_bind_runner_config_from_subject",
        lambda _subject: runner.DEFAULT_CONFIG,
    )
    monkeypatch.setattr(runner, "preflight", unexpected_preflight)
    result = runner.run(
        execute=True,
        run_id="beta3-prod-001",
        **injection,
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["exit_code"] == 3
    assert result["code"] == "DEPENDENCY_INJECTION_FORBIDDEN"


def test_production_preflight_rejects_injected_git_runner_before_preflight(
    monkeypatch,
):
    events: list[str] = []

    class Binding:
        subject = object()

        def assert_stable(self):
            return None

        def close(self):
            return None

    def fake_git_runner(*_args, **_kwargs):
        events.append("git")
        raise AssertionError("injected Git runner was called")

    def unexpected_preflight(*_args, git_runner, **_kwargs):
        events.append("preflight")
        git_runner([], cwd=Path.cwd(), env={})

    monkeypatch.setattr(runner, "load_production_release_subject", lambda: Binding())
    monkeypatch.setattr(
        runner,
        "_bind_runner_config_from_subject",
        lambda _subject: runner.DEFAULT_CONFIG,
    )
    monkeypatch.setattr(runner, "preflight", unexpected_preflight)

    result = runner.run(execute=False, git_runner=fake_git_runner)

    assert events == []
    assert result["status"] == "UNAVAILABLE"
    assert result["exit_code"] == 3
    assert result["code"] == "DEPENDENCY_INJECTION_FORBIDDEN"


def test_explicit_fixture_configuration_is_not_the_production_default(tmp_path):
    config = _fixture_config(tmp_path)
    assert config is not runner.DEFAULT_CONFIG
    assert config.release_subject_digest == "d" * 64


@pytest.mark.parametrize("missing_port", ("git_runner", "dependencies"))
def test_run_fixture_requires_explicit_ports_before_live_access(
    tmp_path, monkeypatch, missing_port
):
    config = _fixture_config(tmp_path)
    binding = _FixtureBinding(config)
    dependencies, _ = _stable_dependencies()
    fixture_git_runner = _git_runner_factory(config)
    events: list[str] = []

    def recording_git(*args, **kwargs):
        events.append("git")
        return fixture_git_runner(*args, **kwargs)

    def forbidden(name):
        def call(*_args, **_kwargs):
            events.append(name)
            raise AssertionError(f"fixture port validation reached {name}")

        return call

    if missing_port != "git_runner":
        binding.git_runner = recording_git
    if missing_port != "dependencies":
        binding.dependencies = dependencies

    monkeypatch.setattr(runner, "_default_git_runner", forbidden("default_git"))
    monkeypatch.setattr(
        runner, "_production_dependencies", forbidden("production_dependencies")
    )
    monkeypatch.setattr(
        runner, "_production_source_command", forbidden("provider_or_cim")
    )
    monkeypatch.setattr(
        runner.secrets,
        "token_hex",
        lambda count: events.append("nonce") or "a" * (count * 2),
    )
    monkeypatch.setattr(runner, "_write_exclusive_json", forbidden("output"))

    result = runner.run_fixture(
        config,
        binding=binding,
        execute=True,
        run_id="fixture-ports-required",
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["exit_code"] == 3
    assert result["code"] == "DEPENDENCY_INVALID"
    assert events == []
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


def test_attestor_provenance_rejects_shadowed_import_origin(tmp_path, monkeypatch):
    shadow = tmp_path / "beta3_control_ownership_attestor.py"
    shadow.write_text("# shadow\n", encoding="utf-8")
    monkeypatch.setitem(
        sys.modules,
        "beta3_control_ownership_attestor",
        SimpleNamespace(__file__=str(shadow)),
    )

    with pytest.raises(runner.RunnerError) as error:
        runner._attestor_source_sha256()

    assert error.value.code == "ATTESTATION_PROVENANCE_MISMATCH"


def test_invalid_dependency_shape_is_rejected_before_source_or_nonce(
    tmp_path, monkeypatch
):
    config = _fixture_config(tmp_path)
    events: list[str] = []

    def forbidden(*_args, **_kwargs):
        events.append("source-or-nonce")
        raise AssertionError("invalid dependency reached a source or nonce")

    monkeypatch.setattr(runner, "_git_snapshot", forbidden)
    monkeypatch.setattr(runner.secrets, "token_hex", forbidden)
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=object(),
    )

    assert result["status"] == "UNAVAILABLE", result
    assert result["exit_code"] == 3
    assert result["code"] == "DEPENDENCY_INVALID"
    assert events == []


def test_invalid_git_runner_is_rejected_before_source_or_nonce(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("invalid git runner reached a source or nonce")

    monkeypatch.setattr(runner, "_git_snapshot", forbidden)
    monkeypatch.setattr(runner.secrets, "token_hex", forbidden)
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=object(),
    )

    assert result["status"] == "UNAVAILABLE", result
    assert result["exit_code"] == 3
    assert result["code"] == "DEPENDENCY_INVALID"


def test_reviewed_provenance_rejects_a_noncanonical_runner_path(tmp_path):
    path = Path(runner.__file__).resolve()
    alias = path.parent / ".." / path.parent.name / path.name

    assert alias != path
    with pytest.raises(runner.RunnerError) as error:
        runner._canonical_provenance_path(str(alias), "runner path")

    assert error.value.code == "ATTESTATION_PROVENANCE_MISMATCH"


def test_reviewed_provenance_rejects_a_shadowed_runner_spec_origin(
    tmp_path, monkeypatch
):
    spec = runner.__spec__
    assert spec is not None
    monkeypatch.setattr(spec, "origin", str(tmp_path / "shadow.py"))

    with pytest.raises(runner.RunnerError) as error:
        runner._reviewed_provenance()

    assert error.value.code == "ATTESTATION_PROVENANCE_MISMATCH"


def test_reviewed_provenance_pins_canonical_runner_and_attestor_origins():
    runner_path = Path(runner.__file__).resolve()

    assert runner._fixture_runbook_hash() == _sha256(runner_path)
    assert len(runner._fixture_attestor_source_sha256()) == 64


def test_reviewed_provenance_hashes_match_current_observer_bytes():
    runner_path = Path(runner.__file__).resolve()
    manifest = runner._reviewed_provenance()

    assert manifest["runner"]["sha256"] == _sha256(runner_path)
    assert runner._runbook_hash() == manifest["runner"]["sha256"]
    assert runner._attestor_source_sha256() == manifest["attestor_bundle_sha256"]


def test_attestor_configuration_is_part_of_fixed_production_subject():
    assert runner.DEFAULT_CONFIG.merged_main_sha == ""
    assert runner.DEFAULT_CONFIG.merged_main_git_tree == ""
    assert runner.DEFAULT_CONFIG.audited_source_tree_digest == ""
    assert runner.DEFAULT_CONFIG.release_subject_digest == ""


def test_execution_dependencies_are_attestation_only():
    dependencies = runner.ExecutionDependencies(
        control_ownership_attestor=object(),
        legacy_attestor=object(),
        replay_guard=lambda _bundle: object(),
    )
    assert dependencies.control_ownership_attestor is not None


def test_public_dependency_annotations_are_exact_attestation_contracts():
    dependency_source = inspect.getsource(runner.ExecutionDependencies)
    attest_source = inspect.getsource(runner.ProductionBootstrapAttestor.attest)

    assert (
        'replay_guard: Callable[["AttestedCutoverBundle"], "ReplayResult"]'
        in dependency_source
    )
    assert (
        '-> tuple["AttestedCutoverBundle", "BootstrapLease", dict[str, object]]'
        in attest_source
    )


def test_attestor_observes_control_then_legacy_and_freezes_one_bundle(
    tmp_path, monkeypatch
):
    config = _fixture_config(tmp_path)
    subject = _exact_fixture_subject(config)
    readbacks = _exact_fixture_readbacks(config, subject)
    control_record = SourceRecord(
        role="control.fixture",
        locator="fixture://owner/repo/control",
        repository=config.repository,
        read_mode="FIXTURE",
        identity=(("fixture_id", "control"),),
        content_sha256="1" * 64,
        readback_digest=None,
        producer_sha256="2" * 64,
    )
    legacy_record = SourceRecord(
        role="legacy.fixture",
        locator="fixture://owner/repo/legacy",
        repository=config.repository,
        read_mode="FIXTURE",
        identity=(("fixture_id", "legacy"),),
        content_sha256="3" * 64,
        readback_digest=None,
        producer_sha256="2" * 64,
    )
    all_targets = tuple(
        target
        for name, value in (("subject", subject), *readbacks.items())
        for target in (f"{name}.{field}" for field in value.canonical())
    )
    bindings = tuple(
        FieldBinding(
            target=target,
            source_record_digests=(control_record.digest, legacy_record.digest),
            derivation="fixture",
        )
        for target in all_targets
    )
    writer = WriterAuthorityObservation(
        writer_generation="v6.1",
        record_id="writer",
        authority_state="authoritative",
        activation_id=None,
        legacy_stopped=False,
        source_record_digests=(control_record.digest,),
    )
    control = ComponentObservation(
        readbacks=tuple(
            (name, readbacks[name])
            for name in (
                "durable_state",
                "writer_fence",
                "ownership",
                "compatibility",
                "runtime",
                "packages",
            )
        ),
        source_records=(control_record,),
        field_bindings=bindings,
        writer_authority=writer,
    )
    legacy = ComponentObservation(
        readbacks=(("legacy", readbacks["legacy"]),),
        source_records=(legacy_record,),
        field_bindings=(),
        writer_authority=writer,
    )
    attempt = AttemptIdentity(
        run_id="beta3-prod-001",
        challenge_nonce="a" * 32,
        repository=config.repository,
        evidence_root=str(config.evidence_root),
        cutover_subject_digest=digest_value(subject.canonical()),
        runner_sha256="4" * 64,
        attestor_sha256="2" * 64,
    )
    events = []

    class Control:
        def observe(self, **_kwargs):
            events.append("control")
            return control

    class Legacy:
        def observe(self, **kwargs):
            events.append(("legacy", kwargs["writer"]))
            return legacy

    monkeypatch.setattr(
        runner,
        "_default_subject_factory",
        lambda _config, _release_subject: subject,
    )
    production_attestor = runner.ProductionBootstrapAttestor(
        control_ownership_attestor=Control(),
        legacy_attestor=Legacy(),
    )

    bundle, lease, metadata = production_attestor.attest(
        config,
        attempt,
        runner._fixture_release_subject(config),
    )

    assert type(bundle) is AttestedCutoverBundle
    assert events[0] == "control"
    assert events[1][0] == "legacy"
    assert events[2] == "control"
    assert events[3][0] == "legacy"
    assert events[1][1] == writer
    assert events[3][1] == writer
    assert tuple(record.digest for record in bundle.source_records) == tuple(
        sorted(record.digest for record in bundle.source_records)
    )
    assert bundle.attestation_digest
    assert type(lease).__name__ == "BootstrapLease"
    assert set(metadata) >= {"attestation_a", "attestation_b"}


def test_attestor_passes_the_same_release_subject_to_control_initial_and_refresh(
    tmp_path,
):
    config = _fixture_config(tmp_path)
    release_subject = runner._fixture_release_subject(config)
    observed_subjects: list[object] = []
    dependencies, _calls, _counts = _attested_dependencies(
        config,
        release_subjects=observed_subjects,
    )
    cutover_subject = runner._default_subject_factory(config, release_subject)
    attempt = AttemptIdentity(
        run_id="beta3-prod-001",
        challenge_nonce="a" * 32,
        repository=config.repository,
        evidence_root=str(config.evidence_root),
        cutover_subject_digest=digest_value(cutover_subject.canonical()),
        runner_sha256="4" * 64,
        attestor_sha256="2" * 64,
    )
    attestor = runner.ProductionBootstrapAttestor(
        control_ownership_attestor=dependencies.control_ownership_attestor,
        legacy_attestor=dependencies.legacy_attestor,
    )

    _bundle, lease, _metadata = attestor.attest(
        config,
        attempt,
        release_subject,
    )
    lease.assert_stable()

    assert len(observed_subjects) >= 3
    assert all(observed is release_subject for observed in observed_subjects)


def test_attempt_is_created_before_dependency_composition_and_attestation(
    tmp_path, monkeypatch
):
    config = _fixture_config(tmp_path)
    stable, _ = _stable_dependencies()
    events: list[str] = []
    original_preflight = runner.preflight
    original_dependencies = runner._dependencies_or_raise

    def recording_preflight(*args, **kwargs):
        events.append("preflight")
        return original_preflight(*args, **kwargs)

    def recording_dependencies(*args, **kwargs):
        events.append("dependencies")
        return original_dependencies(*args, **kwargs)

    class Control:
        def observe(self, **kwargs):
            events.append("attest")
            return stable.control_ownership_attestor.observe(**kwargs)

    monkeypatch.setattr(runner, "preflight", recording_preflight)
    monkeypatch.setattr(runner, "_dependencies_or_raise", recording_dependencies)
    monkeypatch.setattr(
        runner.secrets,
        "token_hex",
        lambda count: events.append(f"nonce:{count}") or "a" * (count * 2),
    )
    dependencies = replace(stable, control_ownership_attestor=Control())

    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "GO", result
    assert events.index("preflight") < events.index("nonce:16")
    assert events.index("nonce:16") < events.index("dependencies")
    assert events.index("dependencies") < events.index("attest")


def test_subject_drift_during_attestor_hash_read_is_rejected_before_nonce(
    tmp_path, monkeypatch
):
    config = _fixture_config(tmp_path)
    release_subject = runner._fixture_release_subject(config)
    nonce_calls: list[int] = []

    class Binding:
        drifted = False

        def assert_stable(self):
            if self.drifted:
                raise runner.RunnerError(
                    "RELEASE_SUBJECT_DRIFT",
                    "release subject drifted during the attestor hash read",
                )

    class NoopContext:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    binding = Binding()

    def drift_during_attestor_hash():
        binding.drifted = True
        return "2" * 64

    monkeypatch.setattr(
        runner,
        "preflight",
        lambda *_args, **_kwargs: {"_evidence_parent_identity": {}},
    )
    monkeypatch.setattr(runner, "_PublicationLease", lambda _path: NoopContext())
    monkeypatch.setattr(runner, "_assert_publication_parent", lambda *_a, **_k: None)
    monkeypatch.setattr(
        runner, "_precheck_existing_output_bytes", lambda *_a, **_k: None
    )
    monkeypatch.setattr(runner, "_input_lease", lambda *_a, **_k: NoopContext())
    monkeypatch.setattr(runner, "_pre_guard_refresh", lambda *_a, **_k: None)
    monkeypatch.setattr(runner, "_runbook_hash", lambda: "1" * 64)
    monkeypatch.setattr(runner, "_attestor_source_sha256", drift_during_attestor_hash)
    monkeypatch.setattr(
        runner.secrets,
        "token_hex",
        lambda count: nonce_calls.append(count) or "a" * (count * 2),
    )

    result = runner._run_bound(
        config,
        execute=True,
        run_id="beta3-prod-001",
        release_subject=release_subject,
        subject_binding=binding,
        production=True,
    )

    assert result["code"] == "RELEASE_SUBJECT_DRIFT"
    assert nonce_calls == []


def _attested_dependencies(
    config,
    *,
    decision: str = "GO",
    changed: dict[str, Any] | None = None,
    unavailable_role: str | None = None,
    check_authoritative_sources: bool = False,
    drift_observation: int | None = None,
    release_subjects: list[object] | None = None,
):
    calls: list[str] = []
    observation_counts = {"control": 0, "legacy": 0}
    config_holder = [config]

    def make_components(config, subject, attempt, writer=None):
        readbacks = _exact_fixture_readbacks(config, subject)
        control_record = SourceRecord(
            role="control.fixture",
            locator="fixture://owner/repo/control",
            repository=config.repository,
            read_mode="FIXTURE",
            identity=(("fixture_id", "control"),),
            content_sha256="1" * 64,
            readback_digest=None,
            producer_sha256=attempt.attestor_sha256,
        )
        legacy_record = SourceRecord(
            role="legacy.fixture",
            locator="fixture://owner/repo/legacy",
            repository=config.repository,
            read_mode="FIXTURE",
            identity=(("fixture_id", "legacy"),),
            content_sha256="3" * 64,
            readback_digest=None,
            producer_sha256=attempt.attestor_sha256,
        )
        if decision == "NO_GO":
            readbacks["legacy"] = _typed_with_digest(
                LegacyReadback,
                {
                    "repository": config.repository,
                    "writer_generation": "v6.1",
                    "authority_state": "active",
                    "active_dispatches": ("dispatch:active",),
                    "active_workers": (),
                    "integration_lease_owner": None,
                    "v2_execution_refs": (),
                    "v2_execution_state": "none",
                    "original_decoder_readable": True,
                    "durable_state_digest": digest_value("legacy-durable"),
                },
            )
        writer = writer or WriterAuthorityObservation(
            writer_generation="v6.1",
            record_id="writer",
            authority_state="authoritative",
            activation_id=None,
            legacy_stopped=False,
            source_record_digests=(control_record.digest,),
        )
        all_targets = tuple(
            target
            for name, value in (("subject", subject), *readbacks.items())
            for target in (f"{name}.{field}" for field in value.canonical())
        )
        bindings = tuple(
            FieldBinding(
                target=target,
                source_record_digests=(control_record.digest, legacy_record.digest),
                derivation="fixture",
            )
            for target in all_targets
        )
        control = ComponentObservation(
            readbacks=tuple(
                (name, readbacks[name])
                for name in (
                    "durable_state",
                    "writer_fence",
                    "ownership",
                    "compatibility",
                    "runtime",
                    "packages",
                )
            ),
            source_records=(control_record,),
            field_bindings=bindings,
            writer_authority=writer,
        )
        legacy = ComponentObservation(
            readbacks=(("legacy", readbacks["legacy"]),),
            source_records=(legacy_record,),
            field_bindings=(),
            writer_authority=writer,
        )
        return control, legacy

    class Control:
        def observe(self, *, config, subject, attempt, release_subject=None):
            config_holder[0] = config
            observation_counts["control"] += 1
            calls.append("control")
            if release_subjects is not None:
                release_subjects.append(release_subject)
            if check_authoritative_sources:
                required = (
                    config.fresh_receipt,
                    config.fresh_store,
                    config.rollback_store,
                    config.prior_store,
                    *(
                        root / package / ".skill-package.json"
                        for root in (
                            config.repository_root / "skills",
                            *config.install_roots,
                        )
                        for package in config.package_names
                    ),
                )
                if any(not Path(path).is_file() for path in required):
                    raise BootstrapError(
                        "SOURCE_UNAVAILABLE",
                        "fixture authoritative source is unavailable",
                    )
            if unavailable_role and not unavailable_role.startswith("legacy."):
                raise RuntimeError(f"unavailable source: {unavailable_role}")
            control, _legacy = make_components(config, subject, attempt)
            if drift_observation == observation_counts["control"]:
                record = control.source_records[0]
                control = replace(
                    control,
                    source_records=(
                        replace(
                            record,
                            identity=(("fixture_id", f"drift-{drift_observation}"),),
                        ),
                    ),
                )
            if observation_counts["control"] > 1 and changed and "control" in changed:
                control = replace(
                    control,
                    writer_authority=replace(
                        control.writer_authority,
                        record_id=str(changed["control"]),
                    ),
                )
            if observation_counts["control"] > 1 and changed and "packages" in changed:
                package = replace(
                    control.readbacks[-1][1],
                    drift=(str(changed["packages"]),),
                )
                control = replace(
                    control,
                    readbacks=tuple(
                        (name, package if name == "packages" else value)
                        for name, value in control.readbacks
                    ),
                )
            if (
                observation_counts["control"] > 1
                and changed
                and any(
                    role not in {"control", "packages"}
                    and not role.startswith("legacy.")
                    for role in changed
                )
            ):
                role = next(
                    role
                    for role in changed
                    if role not in {"control", "packages"}
                    and not role.startswith("legacy.")
                )
                record = control.source_records[0]
                control = replace(
                    control,
                    source_records=(
                        replace(
                            record,
                            identity=(("fixture_id", f"control-{role}"),),
                        ),
                    ),
                )
            return control

    class Legacy:
        def observe(self, *, subject, attempt, writer):
            observation_counts["legacy"] += 1
            calls.append("legacy")
            if unavailable_role and unavailable_role.startswith("legacy."):
                raise RuntimeError(f"unavailable source: {unavailable_role}")
            active_config = config_holder[0]
            if active_config is None:
                raise AssertionError("legacy observation needs the fixture config")
            legacy = make_components(active_config, subject, attempt, writer)[1]
            if observation_counts["legacy"] > 1 and changed and "legacy" in changed:
                legacy = replace(
                    legacy,
                    source_records=(
                        replace(
                            legacy.source_records[0],
                            content_sha256="f" * 64,
                        ),
                    ),
                )
            if (
                observation_counts["legacy"] > 1
                and changed
                and any(
                    role.startswith("legacy.") and role != "legacy" for role in changed
                )
            ):
                role = next(
                    role
                    for role in changed
                    if role.startswith("legacy.") and role != "legacy"
                )
                record = legacy.source_records[0]
                legacy = replace(
                    legacy,
                    source_records=(
                        replace(
                            record,
                            identity=(("fixture_id", f"legacy-{role}"),),
                        ),
                    ),
                )
            return legacy

    def replay(bundle):
        calls.append("guard")
        return evaluate_attested_bundle(bundle)

    dependencies = runner.ExecutionDependencies(
        control_ownership_attestor=Control(),
        legacy_attestor=Legacy(),
        replay_guard=replay,
    )
    return dependencies, calls, observation_counts


def test_runner_replays_only_the_frozen_attested_bundle(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    subject = _exact_fixture_subject(config)
    dependencies, calls, _counts = _attested_dependencies(config)
    monkeypatch.setattr(
        runner,
        "_default_subject_factory",
        lambda _config, _release_subject: subject,
    )

    result = _run_fixture(
        config=config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "GO", result
    assert calls.count("guard") == 1
    assert config.report_path.exists()
    assert config.evidence_path.exists()


def test_replay_requires_exact_complete_current_main_report(tmp_path):
    config = _fixture_config(tmp_path)
    stable, _ = _stable_dependencies()

    def forged_replay(bundle):
        valid = evaluate_attested_bundle(bundle)

        class ForgedReport:
            decision = "GO"

            def canonical(self):
                return {
                    "schema": "gwo.cutover-guard.v1",
                    "decision": "GO",
                    "repository": bundle.subject.repository,
                    "subject_digest": valid.report.subject_digest,
                    "readback_digest": valid.report.readback_digest,
                    "checks": [],
                    "blockers": [],
                    "receipt": {},
                }

        return ReplayResult(
            report=ForgedReport(),
            subject=valid.subject,
            readback_bundle=valid.readback_bundle,
            attestation_digest=valid.attestation_digest,
        )

    dependencies = replace(stable, replay_guard=forged_replay)
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "UNAVAILABLE", result
    assert result["exit_code"] == 3
    assert result["code"] == "ATTESTATION_MISMATCH"
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


def test_execute_does_not_read_authoritative_receipt_store_or_package_sources_outside_attestors(
    tmp_path, monkeypatch
):
    config = _fixture_config(tmp_path)
    dependencies, calls = _stable_dependencies()

    def forbidden_reader(*_args, **_kwargs):
        raise AssertionError("authoritative source reader escaped the attestor seam")

    for name in ("_validate_receipt", "_store_snapshots", "_package_snapshot"):
        monkeypatch.setattr(runner, name, forbidden_reader)

    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "GO", result
    assert result["exit_code"] == 0
    assert calls.count("guard") == 1


@pytest.mark.parametrize("digest_kind", ("runner", "attestor"))
def test_attempt_digests_cannot_diverge_from_held_local_inputs(
    tmp_path, monkeypatch, digest_kind
):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    fake_digest = "e" * 64
    if digest_kind == "runner":
        monkeypatch.setattr(runner, "_fixture_runbook_hash", lambda: fake_digest)
    else:
        monkeypatch.setattr(
            runner, "_fixture_attestor_source_sha256", lambda: fake_digest
        )

    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "UNAVAILABLE", result
    assert result["exit_code"] == 3
    assert result["code"] == "ATTESTATION_MISMATCH"
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


def test_runner_requires_the_exact_bootstrap_lease_contract(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    original_attest = runner.ProductionBootstrapAttestor.attest

    class FakeLease:
        def assert_stable(self):
            return None

        def close(self):
            return None

    def invalid_attest(self, attest_config, attempt, release_subject):
        bundle, _lease, metadata = original_attest(
            self,
            attest_config,
            attempt,
            release_subject,
        )
        return bundle, FakeLease(), metadata

    monkeypatch.setattr(runner.ProductionBootstrapAttestor, "attest", invalid_attest)
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "UNAVAILABLE", result
    assert result["exit_code"] == 3
    assert result["code"] == "LEASE_INVALID"
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


ALL_SOURCE_ROLES = (
    "legacy.dispatches",
    "legacy.workers",
    "legacy.worker.inspect",
    "legacy.processes",
    "legacy.decoder",
    "control.ref",
    "control.writer",
    "control.active_plan",
    "control.legacy_fence",
    "control.local_inputs",
    "store.fresh",
    "store.rollback",
    "store.prior",
    "receipt",
    "runtime.registry",
    "runtime.config",
    "compatibility.module",
    "package.file",
)


@pytest.mark.parametrize("role", ALL_SOURCE_ROLES)
def test_each_source_identity_drift_refuses_before_guard_or_publication(tmp_path, role):
    config = _fixture_config(tmp_path)
    dependencies, calls = _stable_dependencies(changed={role: "changed"})

    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "REFUSED", result
    assert result["exit_code"] == 1
    assert result["code"] == "LIVE_INPUT_DRIFT"
    assert "guard" not in calls
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


@pytest.mark.parametrize("role", ALL_SOURCE_ROLES)
def test_each_unavailable_source_is_exit_three_and_never_go(tmp_path, role):
    config = _fixture_config(tmp_path)
    dependencies, calls = _stable_dependencies(unavailable_role=role)

    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "UNAVAILABLE", result
    assert result["exit_code"] == 3
    assert "guard" not in calls
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


@pytest.mark.parametrize("missing", ("receipt", "fresh_store", "package"))
def test_missing_authoritative_source_is_unavailable(tmp_path, missing):
    config = _fixture_config(tmp_path)
    if missing == "receipt":
        config = replace(
            config, fresh_receipt=config.evidence_root / "missing-receipt.json"
        )
    elif missing == "fresh_store":
        config = replace(config, fresh_store=tmp_path / "missing-fresh.sqlite3")
    else:
        (
            config.repository_root / "skills" / "implement-gwo" / ".skill-package.json"
        ).unlink()
    dependencies, _ = _stable_dependencies(check_authoritative_sources=True)

    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "UNAVAILABLE", result
    assert result["exit_code"] == 3
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


def test_preflight_source_unavailability_is_unavailable(tmp_path):
    config = _fixture_config(tmp_path)
    config = replace(config, fresh_store=tmp_path / "missing-fresh.sqlite3")

    result = _run_fixture(
        config,
        execute=False,
        git_runner=_git_runner_factory(config),
    )

    assert result["status"] == "UNAVAILABLE", result
    assert result["exit_code"] == 3


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO contract")
def test_preflight_rejects_a_posix_fifo_without_blocking(tmp_path):
    config = _fixture_config(tmp_path)
    config.fresh_receipt.unlink()
    os.mkfifo(config.fresh_receipt, 0o600)
    context = multiprocessing.get_context("fork")
    result_queue = context.Queue()
    process = context.Process(
        target=_preflight_in_child,
        args=(config, result_queue),
    )
    process.start()
    try:
        process.join(timeout=2)
        assert not process.is_alive(), "FIFO read path blocked preflight"
        assert process.exitcode == 0
        assert result_queue.get(timeout=1) == (
            "runner-error",
            "FRESH_RECEIPT_INVALID",
        )
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=1)
        result_queue.close()
        result_queue.join_thread()


def test_default_preflight_is_zero_write_and_accepts_quoted_nul_status(tmp_path):
    config = _fixture_config(tmp_path)
    git_runner = _git_runner_factory(config)
    before = {path: path.read_bytes() for path in config.evidence_root.iterdir()}

    result = _run_fixture(config, execute=False, git_runner=git_runner)

    assert result["status"] == "PREFLIGHT_OK"
    assert result["exit_code"] == 0
    assert {
        path: path.read_bytes() for path in config.evidence_root.iterdir()
    } == before
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
    manifest_path = (
        config.repository_root / "skills" / "orchestrator" / ".skill-package.json"
    )
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
        str(config.fresh_store.parent) + "\\.\\" + config.fresh_store.name
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
    (
        ("gateway", "-wal"),
        ("gateway", ".staging"),
        ("artifact", ".staging"),
        ("artifact", ".partial"),
    ),
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

    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=git_runner,
        dependencies=dependencies,
    )

    assert result["status"] == "REFUSED"
    assert result["code"] == "OUTPUT_COLLISION"
    assert calls == []
    assert config.report_path.read_bytes() == b"existing report"


def test_short_exclusive_write_fails_closed_without_partial_output(
    tmp_path, monkeypatch
):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()

    def short_write(_descriptor, data):
        return max(0, len(data) - 1)

    monkeypatch.setattr(runner.os, "write", short_write)
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
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
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "REFUSED"
    assert result["exit_code"] == 1
    assert result["code"] == "OUTPUT_COLLISION"
    assert config.report_path.is_file()
    assert config.report_path.stat().st_size > 0
    assert config.evidence_path.read_bytes() == competitor


def test_output_collision_does_not_delete_the_current_attempt_report(
    tmp_path, monkeypatch
):
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

    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
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
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
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


def test_windows_parent_relative_read_only_open_allows_existing_writer(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows handle contract")
    path = tmp_path / "input.json"
    expected = b'{"stable":true}\n'
    path.write_bytes(expected)

    with path.open("r+b"):
        descriptors, _identities = runner._open_directory_components(
            path.parent, "FILE_READ_FAILED"
        )
        try:
            descriptor = runner._open_path_handle(
                path.name,
                "FILE_READ_FAILED",
                directory=False,
                parent=descriptors[-1],
            )
            try:
                assert (
                    runner._read_held_bytes(descriptor, "FILE_READ_FAILED") == expected
                )
            finally:
                os.close(descriptor)
        finally:
            runner._close_descriptors(descriptors)


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


def test_operator_snapshot_held_input_lease_rehashes_same_size_mutation(
    tmp_path, monkeypatch
):
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
        identity = runner._windows_handle_identity(
            writer, "LIVE_INPUT_DRIFT", directory=False
        )

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

        def assert_attempt_identity(self, _attempt):
            assert not state["closed"]

        def retained_identities(self):
            return {}

        def __exit__(self, _exc_type, _exc_value, _traceback):
            state["closed"] = True

    original_input_lease = runner._input_lease
    original_write = runner._write_exclusive_json

    def input_lease(_config, _preflight, **_kwargs):
        return HeldLease()

    def write(path, value, **kwargs):
        assert not state["closed"]
        state["writes"] += 1
        return original_write(path, value, **kwargs)

    monkeypatch.setattr(runner, "_input_lease", input_lease)
    monkeypatch.setattr(runner, "_write_exclusive_json", write)

    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "GO"
    assert state == {"closed": True, "writes": 2}
    assert runner._input_lease is input_lease
    assert runner._write_exclusive_json is write
    assert original_input_lease is not None


@pytest.mark.parametrize("drift_before_evidence", (False, True))
def test_real_input_lease_holds_fixture_manifest_through_publication(
    tmp_path, monkeypatch, drift_before_evidence
):
    binding = _fixture_binding(tmp_path)
    config = _fixture_config_for_binding(binding)
    original_input_lease = runner._input_lease
    original_write = runner._write_exclusive_json
    observed: dict[str, object] = {}
    writes: list[Path] = []

    def capture_input_lease(*args, **kwargs):
        lease = original_input_lease(*args, **kwargs)
        observed["lease"] = lease
        return lease

    def write(path, value, **kwargs):
        lease = observed["lease"]
        manifest_bindings = [
            item for item in lease._bindings if item.path == binding.manifest_path
        ]
        assert len(manifest_bindings) == 1
        assert manifest_bindings[0].descriptor >= 0
        assert str(binding.manifest_path.resolve()) in lease.retained_identities()
        writes.append(path)
        digest = original_write(path, value, **kwargs)
        if drift_before_evidence and path == config.report_path:
            binding.manifest_path.write_bytes(b'{"fixture":"drift"}\n')
        return digest

    monkeypatch.setattr(runner, "_input_lease", capture_input_lease)
    monkeypatch.setattr(runner, "_write_exclusive_json", write)

    result = runner.run_fixture(
        config,
        binding=binding,
        execute=True,
        run_id="real-manifest-lease",
    )

    if drift_before_evidence:
        assert result["status"] == "REFUSED"
        assert result["code"] == "LIVE_INPUT_DRIFT"
        assert writes == [config.report_path]
        assert not config.evidence_path.exists()
    else:
        assert result["status"] == "GO", result
        assert writes == [config.report_path, config.evidence_path]


def test_input_lease_retains_runtime_runbook_and_attestor_files(tmp_path):
    config = _fixture_config(tmp_path)
    preflight_result = runner.preflight(config, git_runner=_git_runner_factory(config))

    lease = runner._input_lease(config, preflight_result)
    expected_paths = set(lease._expected)

    assert config.runtime_config_path in expected_paths
    assert Path(runner.__file__) in expected_paths
    for name in (
        "beta3_bootstrap_model.py",
        "beta3_control_ownership_attestor.py",
        "beta3_legacy_attestor.py",
        "beta3_replay_guard.py",
    ):
        assert Path(runner.__file__).with_name(name) in expected_paths


def test_input_lease_retains_store_receipt_checkout_and_package_files(tmp_path):
    config = _fixture_config(tmp_path)
    preflight_result = runner.preflight(config, git_runner=_git_runner_factory(config))

    lease = runner._input_lease(config, preflight_result)
    expected_paths = set(lease._expected)
    try:
        assert {
            config.fresh_store,
            config.rollback_store,
            config.prior_store,
            config.fresh_receipt,
        } <= expected_paths
        for package_name in config.package_names:
            assert (
                config.repository_root / "skills" / package_name / "SKILL.md"
                in expected_paths
            )
            for install_root in config.install_roots:
                assert install_root / package_name / "SKILL.md" in expected_paths
    finally:
        lease.close()


def test_input_lease_binds_authoritative_files_to_the_preflight_snapshot(tmp_path):
    config = _fixture_config(tmp_path)
    preflight_result = runner.preflight(config, git_runner=_git_runner_factory(config))
    original = config.fresh_store.read_bytes()
    config.fresh_store.write_bytes(original + b"drift")

    with pytest.raises(runner.RunnerError) as error:
        with runner._input_lease(config, preflight_result):
            pass

    assert error.value.code == "LIVE_INPUT_DRIFT"


@pytest.mark.parametrize("surface", ("source", "installed"))
def test_input_lease_rejects_a_new_package_file_after_preflight(tmp_path, surface):
    config = _fixture_config(tmp_path)
    preflight_result = runner.preflight(config, git_runner=_git_runner_factory(config))
    if surface == "source":
        package_root = config.repository_root / "skills" / "implement-gwo"
    else:
        package_root = config.install_roots[0] / "implement-gwo"
    (package_root / "new-file.txt").write_text("drift\n", encoding="utf-8")

    with pytest.raises(runner.RunnerError) as error:
        runner._input_lease(config, preflight_result)

    assert error.value.code == "LIVE_INPUT_DRIFT"


def test_execute_refuses_package_file_added_before_lease(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    original_input_lease = runner._input_lease
    monkeypatch.setattr(runner, "_runbook_hash", lambda: _sha256(Path(runner.__file__)))
    attestor_digest = hashlib.sha256()
    for name in runner._ATTESTOR_MODULE_NAMES:
        path = Path(runner.__file__).with_name(name)
        content = path.read_bytes()
        encoded_name = name.encode("utf-8")
        attestor_digest.update(len(encoded_name).to_bytes(4, "big"))
        attestor_digest.update(encoded_name)
        attestor_digest.update(len(content).to_bytes(8, "big"))
        attestor_digest.update(content)
    monkeypatch.setattr(
        runner,
        "_attestor_source_sha256",
        lambda: attestor_digest.hexdigest(),
    )

    def add_package_file_before_lease(active_config, preflight_result, **kwargs):
        package_file = (
            active_config.repository_root
            / "skills"
            / "implement-gwo"
            / "added-after-preflight.txt"
        )
        package_file.write_text("drift\n", encoding="utf-8")
        return original_input_lease(active_config, preflight_result, **kwargs)

    monkeypatch.setattr(runner, "_input_lease", add_package_file_before_lease)
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "REFUSED", result
    assert result["exit_code"] == 1
    assert result["code"] == "LIVE_INPUT_DRIFT"
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


def test_input_lease_retains_all_configured_output_parent_components(tmp_path):
    config = _fixture_config(tmp_path)
    gateway_parent = tmp_path / "gateway parent"
    artifact_parent = tmp_path / "artifact parent"
    gateway_parent.mkdir()
    artifact_parent.mkdir()
    config = replace(
        config,
        gateway_store_path=gateway_parent / "gateway.sqlite3",
        artifact_root=artifact_parent / "artifacts",
    )
    preflight_result = runner.preflight(config, git_runner=_git_runner_factory(config))
    lease = runner._input_lease(config, preflight_result)

    try:
        assert {
            config.report_path.parent,
            config.evidence_path.parent,
            config.gateway_store_path.parent,
            config.artifact_root.parent,
        } <= set(lease._directories)
    finally:
        lease.close()


def test_input_lease_is_held_before_attestor_observation(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    stable, _ = _stable_dependencies()
    state = {"entered": False}
    original_enter = runner._InputLease.__enter__

    def capture_enter(lease):
        entered = original_enter(lease)
        state["entered"] = True
        return entered

    class Control:
        def observe(self, **kwargs):
            assert state["entered"]
            return stable.control_ownership_attestor.observe(**kwargs)

    monkeypatch.setattr(runner._InputLease, "__enter__", capture_enter)
    dependencies = replace(stable, control_ownership_attestor=Control())
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "GO", result


def test_retry_rejects_report_only_residue_without_overwriting_it(
    tmp_path, monkeypatch
):
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
    first = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert first["status"] == "UNAVAILABLE"
    assert config.report_path.is_file()
    assert not config.evidence_path.exists()
    assert report_bytes == [config.report_path.read_bytes()]

    monkeypatch.setattr(runner, "_write_exclusive_json", original_write)
    retry_dependencies, retry_calls = _stable_dependencies()
    second = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
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
    first = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=first_dependencies,
    )

    assert first["status"] == "UNAVAILABLE"
    assert config.report_path.is_file()
    assert not config.evidence_path.exists()
    report_bytes = config.report_path.read_bytes()

    monkeypatch.setattr(runner, "_write_exclusive_json", original_write)
    fresh_dependencies, calls = _stable_dependencies()
    second = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
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
    first = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=first_dependencies,
    )

    assert first["status"] == "GO"
    report_bytes = config.report_path.read_bytes()
    evidence_bytes = config.evidence_path.read_bytes()

    fresh_dependencies, calls = _stable_dependencies()
    second = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
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
    authentic = _run_fixture(
        authentic_config,
        execute=True,
        run_id="beta3-prod-001",
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
    second = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
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
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "GO", result
    assert result["exit_code"] == 0
    assert calls.count("guard") == 1


def test_recovery_and_adoption_paths_have_no_executable_old_guard_reach_through():
    resume_source = inspect.getsource(runner._resume_existing_outputs)
    recovery_source = inspect.getsource(runner._recovery_evidence)

    for source in (resume_source, recovery_source):
        assert "control_read" not in source
        assert "package_read" not in source
        assert "_decode_bundle" not in source
        assert "_ReplayReadPort" not in source
        assert "CutoverGuard(" not in source
    assert "    return _evidence(" not in recovery_source
    assert "_existing_report_payload" not in resume_source
    assert "_validate_existing" not in resume_source
    assert "_fresh_complete_observation" not in resume_source
    assert not hasattr(runner, "_post_observation")
    assert not hasattr(runner, "_fresh_complete_observation")
    assert "GuardTypeContract" not in inspect.getsource(
        runner._ImmutableDurableStateReadPort
    )


def test_retry_revalidates_the_held_report_before_recovery_evidence(
    tmp_path, monkeypatch
):
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
    first = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
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
    second = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert second["status"] != "GO", second
    assert not config.evidence_path.exists()


def test_retry_rejects_unknown_existing_evidence_field(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    first = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )
    assert first["status"] == "GO"
    evidence = json.loads(config.evidence_path.read_text(encoding="utf-8"))
    evidence["unexpected"] = True
    config.evidence_path.write_bytes(runner.canonical_json_bytes(evidence))

    second = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert second["status"] != "GO", second


def test_retry_rejects_unbound_existing_evidence_metadata(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    first = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )
    assert first["status"] == "GO"
    evidence = json.loads(config.evidence_path.read_text(encoding="utf-8"))
    evidence["head"] = "wrong"
    config.evidence_path.write_bytes(runner.canonical_json_bytes(evidence))

    second = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
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
    first = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
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

    second = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert second["status"] != "GO", second


def test_round4_retry_rejects_unbound_nested_live_observations(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    first = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )
    assert first["status"] == "GO"

    evidence = json.loads(config.evidence_path.read_text(encoding="utf-8"))
    forged_control = {"writer_generation": "v6.1", "control_ref": "forged"}
    evidence["before"]["control"] = forged_control
    evidence["after"]["control"] = forged_control
    config.evidence_path.write_bytes(runner.canonical_json_bytes(evidence))

    second = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert second["status"] != "GO", second


def test_round4_real_composition_rejects_obsolete_dependency_injection(tmp_path):
    config = replace(_fixture_config(tmp_path), production_readers=object())

    with pytest.raises(runner.RunnerError) as error:
        runner._production_dependencies(config, "8" * 64)

    assert error.value.code == "DEPENDENCY_INJECTION_FORBIDDEN"


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


def test_production_composition_uses_attestors_without_mutation(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies = runner._production_dependencies(config, "8" * 64)

    assert type(dependencies) is runner.ExecutionDependencies
    assert callable(dependencies.replay_guard)
    assert callable(dependencies.control_ownership_attestor.observe)
    assert callable(dependencies.legacy_attestor.observe)
    assert not config.gateway_store_path.exists()
    assert not config.artifact_root.exists()


def test_real_composition_requires_a_proven_immutable_durable_adapter(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies = runner._production_dependencies(config, "8" * 64)
    assert (
        type(dependencies.control_ownership_attestor).__name__
        == "ControlOwnershipAttestor"
    )


def test_real_composition_rejects_ordinary_mode_ro_durable_adapter(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies = runner._production_dependencies(config, "8" * 64)
    assert type(dependencies.legacy_attestor).__name__ == "LegacyAttestor"


def test_live_durable_read_rejects_a_sidecar_created_during_the_real_read(
    tmp_path, monkeypatch
):
    config = _fixture_config(tmp_path)
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


def test_live_durable_read_rejects_a_sidecar_created_during_the_read(
    tmp_path, monkeypatch
):
    config = _fixture_config(tmp_path)
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


def test_go_writes_report_then_evidence_exclusively_and_preserves_contract(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies, calls = _stable_dependencies()
    git_runner = _git_runner_factory(config)

    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=git_runner,
        dependencies=dependencies,
    )

    assert result["status"] == "GO"
    assert result["exit_code"] == 0
    assert calls[:4] == ["control", "legacy", "control", "legacy"]
    assert calls.count("guard") == 1

    report = json.loads(config.report_path.read_text(encoding="utf-8"))
    evidence = json.loads(config.evidence_path.read_text(encoding="utf-8"))
    assert len(report["checks"]) == 7
    assert (
        tuple(item["check_id"] for item in report["checks"])
        == runner.EXPECTED_CHECK_IDS
    )
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
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
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


def test_publication_embeds_one_complete_attestation_in_exactly_two_outputs(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()

    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "GO"
    report = json.loads(config.report_path.read_text(encoding="utf-8"))
    evidence = json.loads(config.evidence_path.read_text(encoding="utf-8"))
    expected_shared = (
        "attempt_identity",
        "attestation",
        "attestation_digest",
        "readback_bundle",
        "attested_readback_bundle",
        "source_records",
        "field_bindings",
        "activation_performed",
        "mutation_flags",
    )
    for name in expected_shared:
        assert report[name] == evidence[name]
    assert report["attestation"]["attestation_digest"] == report["attestation_digest"]
    assert report["activation_performed"] is False
    assert all(value is False for value in report["mutation_flags"].values())
    assert all(value is False for value in evidence["safety"].values())
    retained = evidence["retained_input_identities"]
    runner_path = Path(runner.__file__).resolve()
    assert str(config.runtime_config_path.resolve()) in retained
    assert str(runner_path) in retained
    attempt = report["attempt_identity"]
    assert attempt == evidence["attempt_identity"]
    assert attempt["runner_sha256"] == retained[str(runner_path)]["sha256"]
    attestor_digest = hashlib.sha256()
    for name in runner._ATTESTOR_MODULE_NAMES:
        path = runner_path.with_name(name)
        identity = retained[str(path)]
        assert identity["sha256"] == _sha256(path)
        encoded_name = name.encode("utf-8")
        content = path.read_bytes()
        attestor_digest.update(len(encoded_name).to_bytes(4, "big"))
        attestor_digest.update(encoded_name)
        attestor_digest.update(len(content).to_bytes(8, "big"))
        attestor_digest.update(content)
    assert attempt["attestor_sha256"] == attestor_digest.hexdigest()
    assert {
        path.name
        for path in config.evidence_root.iterdir()
        if path.name != config.fresh_receipt.name
    } == {config.report_path.name, config.evidence_path.name}


@pytest.mark.parametrize("output_kind", ("report", "evidence"))
def test_publication_rejects_runbook_digest_divergence(
    tmp_path, monkeypatch, output_kind
):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    if output_kind == "report":
        original_builder = runner._attested_report

        def forged_report(*args, **kwargs):
            value = original_builder(*args, **kwargs)
            return {**value, "runbook_sha256": "f" * 64}

        monkeypatch.setattr(runner, "_attested_report", forged_report)
    else:
        original_builder = runner._attested_evidence

        def forged_evidence(*args, **kwargs):
            value = original_builder(*args, **kwargs)
            return {**value, "runbook_sha256": "f" * 64}

        monkeypatch.setattr(runner, "_attested_evidence", forged_evidence)

    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "UNAVAILABLE", result
    assert result["exit_code"] == 3
    assert result["code"] == "OUTPUT_WRITE_FAILED"


@pytest.mark.parametrize("output_kind", ("report", "evidence"))
def test_publication_rejects_forged_top_level_subject_digest(
    tmp_path, monkeypatch, output_kind
):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    builder_name = f"_attested_{output_kind}"
    original_builder = getattr(runner, builder_name)

    def forged_builder(*args, **kwargs):
        value = original_builder(*args, **kwargs)
        return {**value, "subject_digest": "f" * 64}

    monkeypatch.setattr(runner, builder_name, forged_builder)
    result = _run_fixture(
        config,
        execute=True,
        run_id="forged-subject-digest",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "UNAVAILABLE", result
    assert result["exit_code"] == 3
    assert result["code"] == "OUTPUT_WRITE_FAILED"


def test_guard_report_and_attested_bundle_mismatch_is_unavailable(tmp_path):
    config = _fixture_config(tmp_path)
    stable, _ = _stable_dependencies()

    def mismatched_replay(bundle):
        result = evaluate_attested_bundle(bundle)
        return replace(result, report=replace(result.report, subject_digest="f" * 64))

    dependencies = replace(stable, replay_guard=mismatched_replay)
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "UNAVAILABLE", result
    assert result["exit_code"] == 3
    assert result["code"] == "ATTESTATION_MISMATCH"
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


def test_no_go_writes_canonical_evidence_and_returns_two(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies(decision="NO_GO")
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
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


REAL_LEASE_BOUNDARIES = {
    "before Guard": 3,
    "immediately after Guard": 4,
    "before report create": 6,
    "before evidence create": 8,
    "after both outputs": 9,
}


@pytest.mark.parametrize("boundary,observation", REAL_LEASE_BOUNDARIES.items())
def test_real_bootstrap_lease_source_drift_refuses_at_every_publication_boundary(
    tmp_path, boundary, observation
):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies(drift_observation=observation)
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "REFUSED", result
    assert result["exit_code"] == 1
    assert result["code"] == "LIVE_INPUT_DRIFT"
    if boundary == "after both outputs":
        assert config.report_path.exists()
        assert config.evidence_path.exists()
    elif boundary == "before evidence create":
        assert config.report_path.exists()
        assert not config.evidence_path.exists()
    else:
        assert not config.report_path.exists()
        assert not config.evidence_path.exists()


def test_real_input_lease_drift_after_guard_refuses(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    stable, _ = _stable_dependencies()
    original_enter = runner._InputLease.__enter__
    held_inputs = {}

    def capture_input_lease(lease):
        entered = original_enter(lease)
        held_inputs["lease"] = entered
        return entered

    monkeypatch.setattr(runner._InputLease, "__enter__", capture_input_lease)

    def mutate_during_replay(bundle):
        result = stable.replay_guard(bundle)
        lease = held_inputs["lease"]
        assert any(
            item.path.resolve() == config.runtime_config_path.resolve()
            for item in lease._bindings
        )
        data = b'{"changed":true}\n'
        os.ftruncate(writer, 0)
        os.lseek(writer, 0, os.SEEK_SET)
        os.write(writer, data)
        os.fsync(writer)
        return result

    dependencies = replace(stable, replay_guard=mutate_during_replay)
    writer = os.open(config.runtime_config_path, os.O_RDWR)
    try:
        result = _run_fixture(
            config,
            execute=True,
            run_id="beta3-prod-001",
            git_runner=_git_runner_factory(config),
            dependencies=dependencies,
        )
    finally:
        os.close(writer)

    assert result["status"] == "REFUSED", result
    assert result["exit_code"] == 1
    assert result["code"] == "LIVE_INPUT_DRIFT"
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


@pytest.mark.parametrize("boundary", ("before evidence create", "after both outputs"))
def test_real_owned_output_drift_refuses_at_publication_boundary(
    tmp_path, monkeypatch, boundary
):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    original_write = runner._write_exclusive_json

    def open_writable_output(path):
        import ctypes
        import msvcrt

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.CreateFileW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        handle = kernel32.CreateFileW(
            str(path),
            0x80000000 | 0x40000000,
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,
            0x00000080,
            None,
        )
        assert handle not in (None, ctypes.c_void_p(-1).value)
        return msvcrt.open_osfhandle(handle, os.O_RDWR | getattr(os, "O_BINARY", 0))

    def replace_owned_output(path, value, **kwargs):
        digest = original_write(path, value, **kwargs)
        if (boundary == "before evidence create" and path == config.report_path) or (
            boundary == "after both outputs" and path == config.evidence_path
        ):
            ownership = kwargs["ownership_out"]
            output = ownership[-1]
            os.close(output.descriptor)
            output.descriptor = open_writable_output(path)
            data = b"replaced output"
            os.ftruncate(output.descriptor, 0)
            os.lseek(output.descriptor, 0, os.SEEK_SET)
            os.write(output.descriptor, data)
            os.fsync(output.descriptor)
        return digest

    monkeypatch.setattr(runner, "_write_exclusive_json", replace_owned_output)
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "REFUSED", result
    assert result["exit_code"] == 1
    assert result["code"] == "LIVE_INPUT_DRIFT"
    assert config.report_path.exists()
    if boundary == "before evidence create":
        assert not config.evidence_path.exists()
    else:
        assert config.evidence_path.exists()


@pytest.mark.parametrize("drift_key", ("control", "packages"))
def test_store_control_and_package_drift_after_guard_refuses_without_go(
    tmp_path, drift_key
):
    config = _fixture_config(tmp_path)
    dependencies, calls = _stable_dependencies(changed={drift_key: "changed"})

    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "REFUSED"
    assert result["code"] == "LIVE_INPUT_DRIFT"
    assert calls == ["control", "legacy", "control", "legacy"]
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


def test_bootstrap_lease_drift_after_guard_refuses_without_go(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    stable, _ = _stable_dependencies()
    calls = 0
    original_assert = runner._assert_combined_stable

    def drift_after_guard(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise runner.RunnerError(
                "LIVE_INPUT_DRIFT", "Store identity changed after Guard"
            )
        return original_assert(*args, **kwargs)

    monkeypatch.setattr(runner, "_assert_combined_stable", drift_after_guard)
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=stable,
    )

    assert result["status"] == "REFUSED"
    assert result["exit_code"] == 1
    assert result["code"] == "LIVE_INPUT_DRIFT"
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


def test_live_guard_exception_is_unavailable_and_never_fakes_go(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies, calls = _stable_dependencies()

    def raises(_bundle):
        calls.append("guard")
        raise RuntimeError("gateway must not be used")

    dependencies = replace(dependencies, replay_guard=raises)
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["exit_code"] == 3
    assert result["code"] == "ATTESTATION_UNAVAILABLE"
    assert calls[-1] == "guard"
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


def test_execute_path_does_not_create_gateway_artifact_or_sqlite_sidecar(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()

    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["exit_code"] == 0
    assert not config.gateway_store_path.exists()
    assert not config.artifact_root.exists()
    for path in (config.fresh_store, config.rollback_store, config.prior_store):
        assert not Path(f"{path}-wal").exists()
        assert not Path(f"{path}-shm").exists()


def test_fixture_preflight_returns_ok_without_execution(tmp_path):
    config = _fixture_config(tmp_path)
    result = _run_fixture(
        config,
        execute=False,
        git_runner=_git_runner_factory(config),
    )

    assert result["exit_code"] == 0
    assert result["status"] == "PREFLIGHT_OK"


def test_main_dispatches_parser_to_run_and_prints_canonical_json(monkeypatch):
    calls: list[tuple[object, object, object]] = []
    expected = {"status": "NO_GO", "exit_code": 2, "decision": "NO_GO"}

    def fake_run(config, *, execute, run_id, **_ports):
        calls.append((config, execute, run_id))
        return expected

    stdout = io.StringIO()
    monkeypatch.setattr(runner, "run", fake_run)

    exit_code = runner.main(
        ["--execute", "--run-id", "cli-dispatch"],
        stdout=stdout,
    )

    assert calls == [(None, True, "cli-dispatch")]
    assert stdout.getvalue() == runner.canonical_json_bytes(expected).decode("utf-8")
    assert exit_code == 2


def test_go_rejects_unbound_guard_digests(tmp_path):
    config = _fixture_config(tmp_path)
    stable, _ = _stable_dependencies()

    def unbound_replay(bundle):
        valid = evaluate_attested_bundle(bundle)
        report = replace(valid.report, subject_digest="f" * 64)
        return replace(valid, report=report)

    dependencies = replace(stable, replay_guard=unbound_replay)

    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["exit_code"] == 3
    assert result["code"] == "ATTESTATION_MISMATCH"
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


def test_guard_input_substitution_and_restoration_is_not_silent(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    stable, _ = _stable_dependencies()
    original_assert = runner._assert_combined_stable
    calls = 0

    def swapping_input(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise runner.RunnerError(
                "LIVE_INPUT_DRIFT",
                "held source input was substituted after Guard",
            )
        return original_assert(*args, **kwargs)

    monkeypatch.setattr(runner, "_assert_combined_stable", swapping_input)
    result = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=stable,
    )

    assert result["status"] == "REFUSED", result
    assert result["exit_code"] == 1
    assert result["code"] == "LIVE_INPUT_DRIFT"
    assert not config.report_path.exists()
    assert not config.evidence_path.exists()


def test_attestor_observation_rejects_same_identity_package_content_drift(tmp_path):
    config = _fixture_config(tmp_path)
    stable, _ = _stable_dependencies()
    source_file = config.repository_root / "skills" / "implement-gwo" / "SKILL.md"
    original_stat = source_file.stat()
    original_length = len(source_file.read_bytes())
    first = True

    class MutatingControl:
        def observe(self, **kwargs):
            nonlocal first
            value = stable.control_ownership_attestor.observe(**kwargs)
            if first:
                first = False
                mutation_handle.seek(0)
                mutation_handle.write(b"X" * original_length)
                mutation_handle.flush()
                os.fsync(mutation_handle.fileno())
                os.utime(
                    source_file,
                    ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                )
            else:
                package = next(
                    readback for name, readback in value.readbacks if name == "packages"
                )
                changed_package = replace(
                    package,
                    drift=(hashlib.sha256(source_file.read_bytes()).hexdigest(),),
                )
                value = replace(
                    value,
                    readbacks=tuple(
                        (name, changed_package if name == "packages" else readback)
                        for name, readback in value.readbacks
                    ),
                )
            return value

    dependencies = replace(
        stable,
        control_ownership_attestor=MutatingControl(),
    )
    with source_file.open("r+b") as mutation_handle:
        result = _run_fixture(
            config,
            execute=True,
            run_id="beta3-prod-001",
            git_runner=_git_runner_factory(config),
            dependencies=dependencies,
        )

    assert result["status"] == "REFUSED", result
    assert result["exit_code"] == 1
    assert result["code"] == "LIVE_INPUT_DRIFT"
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
            manifest = json.loads(
                (package_root / ".skill-package.json").read_text(encoding="utf-8")
            )
            (package_root / ".skill-package.json").write_text(
                json.dumps(manifest, indent=2) + "\n",
                encoding="utf-8",
            )

    result = runner.preflight(config, git_runner=_git_runner_factory(config))

    assert result["status"] == "PREFLIGHT_OK"


def test_attested_digest_includes_the_typed_readback_digest_field():
    body = {"repository": "owner/repo", "value": "stable"}
    supplied = runner._guard_digest(body)
    value = SimpleNamespace(canonical=lambda: {**body, "readback_digest": supplied})

    assert runner._guard_digest(value.canonical()) == runner._guard_digest(
        {**body, "readback_digest": supplied}
    )


def test_fake_simple_namespace_guard_report_is_not_a_typed_current_main_report(
    tmp_path,
):
    with pytest.raises(runner.RunnerError) as error:
        runner._validate_attested_replay(object(), SimpleNamespace())

    assert error.value.code == "ATTESTATION_MISMATCH"


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
    first = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
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

    second = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
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
    first = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
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

    second = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert second["status"] == "REFUSED", second
    assert second["code"] == "OUTPUT_COLLISION"
    assert not config.evidence_path.exists()


def test_round5_retry_rejects_a_noncanonical_or_unbound_capture_timestamp(tmp_path):
    config = _fixture_config(tmp_path)
    dependencies, _ = _stable_dependencies()
    first = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )
    assert first["status"] == "GO"

    evidence = json.loads(config.evidence_path.read_text(encoding="utf-8"))
    evidence["captured_at"] = "2026-08-10T00:00:00Z"
    config.evidence_path.write_bytes(runner.canonical_json_bytes(evidence))

    second = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )

    assert second["status"] != "GO", second


def test_round5_retry_rechecks_a_fresh_complete_observation_before_adoption(tmp_path):
    config = _fixture_config(tmp_path)
    stable, _ = _stable_dependencies()
    control_reads = 0
    drift_before_adoption = False

    class Control:
        def observe(self, **kwargs):
            nonlocal control_reads, drift_before_adoption
            control_reads += 1
            value = stable.control_ownership_attestor.observe(**kwargs)
            if drift_before_adoption:
                return replace(
                    value,
                    writer_authority=replace(
                        value.writer_authority,
                        record_id="drift-before-adoption",
                    ),
                )
            return value

    dependencies = replace(
        stable,
        control_ownership_attestor=Control(),
    )

    first = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
        git_runner=_git_runner_factory(config),
        dependencies=dependencies,
    )
    assert first["status"] == "GO"
    drift_before_adoption = True

    second = _run_fixture(
        config,
        execute=True,
        run_id="beta3-prod-001",
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


def test_round5_runner_owned_read_rejects_schema_digest_drift_from_extra_index(
    tmp_path,
):
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


def _fixture_subject(
    *,
    merged_main_sha: str = "a" * 40,
    merged_main_git_tree: str = "b" * 40,
    audited_source_tree_digest: str = "c" * 64,
):
    return SimpleNamespace(
        merged_main_sha=merged_main_sha,
        merged_main_git_tree=merged_main_git_tree,
        audited_source_tree_digest=audited_source_tree_digest,
    )


def test_production_run_loads_subject_before_git_and_nonce(
    monkeypatch: pytest.MonkeyPatch,
):
    events: list[str] = []

    def missing_subject():
        events.append("subject")
        raise runner.RunnerError(
            "RELEASE_SUBJECT_UNAVAILABLE", "test manifest is absent"
        )

    monkeypatch.setattr(runner, "load_production_release_subject", missing_subject)
    monkeypatch.setattr(
        runner, "_default_git_runner", lambda *args, **kwargs: events.append("git")
    )
    monkeypatch.setattr(
        runner.secrets, "token_hex", lambda *args: events.append("nonce")
    )
    result = runner.run(execute=True, run_id="subject-order-red")
    assert result["code"] == "RELEASE_SUBJECT_UNAVAILABLE"
    assert events == ["subject"]


def test_default_subject_keeps_git_tree_and_audited_digest_separate(tmp_path):
    config = _fixture_config(tmp_path)
    config = replace(
        config,
        merged_main_sha="a" * 40,
        merged_main_git_tree="b" * 40,
        audited_source_tree_digest="c" * 64,
        release_subject_digest="d" * 64,
    )
    manifest = _fixture_subject(
        merged_main_sha="a" * 40,
        merged_main_git_tree="b" * 40,
        audited_source_tree_digest="c" * 64,
    )
    subject = runner._default_subject_factory(config, manifest)
    assert subject.source_commit == "a" * 40
    assert subject.source_tree_digest == "c" * 64
    assert config.merged_main_git_tree == "b" * 40


def test_default_subject_rejects_shadowed_gwo_v8_dependency(tmp_path, monkeypatch):
    config = _fixture_config(tmp_path)
    manifest = _fixture_subject(
        merged_main_sha=config.merged_main_sha,
        merged_main_git_tree=config.merged_main_git_tree,
        audited_source_tree_digest=config.audited_source_tree_digest,
    )

    shadow_root = tmp_path / "shadow-gwo-v8"
    shadow_root.mkdir()

    class ShadowCutoverSubject:
        def __init__(self, **_kwargs):
            pass

    shadow_package = SimpleNamespace(
        __file__=str(shadow_root / "__init__.py"),
        __path__=[str(shadow_root)],
        __spec__=SimpleNamespace(origin=str(shadow_root / "__init__.py")),
    )
    shadow_module = SimpleNamespace(
        __file__=str(shadow_root / "cutover_guard.py"),
        __spec__=SimpleNamespace(origin=str(shadow_root / "cutover_guard.py")),
        CutoverSubject=ShadowCutoverSubject,
    )
    monkeypatch.setitem(sys.modules, "gwo_v8", shadow_package)
    monkeypatch.setitem(sys.modules, "gwo_v8.cutover_guard", shadow_module)

    with pytest.raises(runner.RunnerError) as error:
        runner._default_subject_factory(config, manifest, strict=True)

    assert error.value.code == "ATTESTATION_PROVENANCE_MISMATCH"


def test_default_subject_rejects_noncanonical_repository_without_v8_fallback(tmp_path):
    config = replace(_fixture_config(tmp_path), repository="owner/noncanonical")
    manifest = _fixture_subject(
        merged_main_sha=config.merged_main_sha,
        merged_main_git_tree=config.merged_main_git_tree,
        audited_source_tree_digest=config.audited_source_tree_digest,
    )

    with pytest.raises(runner.RunnerError) as error:
        runner._default_subject_factory(config, manifest, strict=True)

    assert error.value.code == "ATTESTATION_PROVENANCE_MISMATCH"


def test_cli_has_no_subject_or_identity_override():
    parser = runner.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--subject", r"C:\tmp\other-subject.json"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--expected-head", "a" * 40])


def test_subject_drift_is_refused_before_report_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    binding = _fixture_binding(tmp_path)
    config = _fixture_config_for_binding(binding)
    report = config.report_path
    binding.assert_stable()
    binding.replace_for_test()
    result = runner.run_fixture(
        config, binding=binding, execute=True, run_id="drift-before-output"
    )
    assert result["code"] == "RELEASE_SUBJECT_DRIFT"
    assert not report.exists()


def test_report_and_evidence_carry_external_subject_digest(tmp_path: Path):
    record = _run_fixture_guard_to_completion(tmp_path, run_id="report-subject-digest")
    expected_cutover_subject = CutoverSubject(
        repository="owner/repo",
        control_branch="gwo-control",
        target_branch="main",
        source_writer_generation="v6.1",
        target_writer_generation="v8",
        store_generation="store:v8:fixture:081500Z",
        source_commit=record["subject"]["merged_main_sha"],
        source_tree_digest=record["subject"]["audited_source_tree_digest"],
        production_entry_refs=runner.PRODUCTION_ENTRY_REFS,
    )
    expected_cutover_digest = digest_value(expected_cutover_subject.canonical())
    for output in (record["report"], record["evidence"]):
        assert output["release_subject_digest"] == record["subject"]["release_subject_digest"]
        assert output["release_subject_path"] == record["subject"]["release_subject_path"]
        assert output["merged_main_sha"] == record["subject"]["merged_main_sha"]
        assert output["merged_main_git_tree"] == record["subject"]["merged_main_git_tree"]
        assert output["subject_digest"] == expected_cutover_digest
        assert output["subject_digest"] != output["release_subject_digest"]
