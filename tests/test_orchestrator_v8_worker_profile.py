from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8 import (  # noqa: E402
    EvidenceVerifier,
    InMemoryRuntimeAdapter,
    Kernel,
    KernelError,
    LocalPlanPublication,
    PlanCompiler,
    RuntimeAdapterError,
    RuntimeProfile,
    resolve_worker_profile,
)


def _binding(
    provider: str,
    model: str,
    thinking: str,
    mode: str,
    features: dict | None = None,
) -> dict:
    return {
        "provider": provider,
        "settings": {
            "model": model,
            "thinkingOptionId": thinking,
            "modeId": mode,
            "features": {} if features is None else features,
        },
    }


def _default_tiers() -> dict[str, dict]:
    return {
        "light": _binding("kimi-cli", "kimi-code/kimi-for-coding", "on", "yolo"),
        "standard": _binding("kimi-cli", "kimi-code/kimi-for-coding", "on", "yolo"),
        "heavy": _binding("kimi-cli", "kimi-code/k3", "high", "yolo"),
        "frontier": _binding("codex", "gpt-5.6-sol", "xhigh", "full-access"),
    }


def _config(tiers: dict[str, dict] | None = None, repositories: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "tiers": tiers if tiers is not None else _default_tiers(),
        "role_profiles": {
            "reviewer_standard": _binding("codex", "gpt-5.6-sol", "high", "full-access"),
        },
        "review_profiles": {
            "standard_axis": "reviewer_standard",
        },
        "repositories": repositories or {},
    }


@pytest.mark.parametrize(
    "difficulty, expected_tier, expected_model, expected_thinking",
    [
        ("routine", "light", "kimi-code/kimi-for-coding", "on"),
        ("standard", "standard", "kimi-code/kimi-for-coding", "on"),
        ("complex", "heavy", "kimi-code/k3", "high"),
        ("frontier", "frontier", "gpt-5.6-sol", "xhigh"),
    ],
)
def test_resolve_worker_profile_maps_difficulty_to_tier(
    difficulty, expected_tier, expected_model, expected_thinking
):
    profile = resolve_worker_profile(
        _config(),
        repository="owner/repo",
        difficulty=difficulty,
    )

    assert isinstance(profile, RuntimeProfile)
    assert profile.name == expected_tier
    assert profile.model == expected_model
    assert profile.thinking == expected_thinking


def test_resolve_worker_profile_repository_override_precedes_global():
    config = _config(
        repositories={
            "owner/repo": {
                "tiers": {
                    "standard": _binding(
                        "codex", "repo-sol", "high", "full-access"
                    ),
                },
            },
        },
    )

    profile = resolve_worker_profile(
        config,
        repository="owner/repo",
        difficulty="standard",
    )

    assert profile.provider == "codex"
    assert profile.model == "repo-sol"
    assert profile.thinking == "high"
    assert profile.mode == "full-access"


def test_resolve_worker_profile_falls_back_to_global_when_repository_lacks_tier():
    config = _config(
        repositories={
            "owner/repo": {
                "tiers": {
                    "standard": _binding(
                        "codex", "repo-sol", "high", "full-access"
                    ),
                },
            },
        },
    )

    profile = resolve_worker_profile(
        config,
        repository="owner/repo",
        difficulty="complex",
    )

    assert profile.name == "heavy"
    assert profile.provider == "kimi-cli"
    assert profile.model == "kimi-code/k3"


def test_resolve_worker_profile_ignores_role_profiles():
    config = _config()
    config["role_profiles"] = {
        "reviewer_standard": _binding("codex", "role-sol", "max", "full-access"),
        "coordinator_auto": _binding("kimi-cli", "role-k3", "max", "yolo"),
    }

    profile = resolve_worker_profile(
        config,
        repository="owner/repo",
        difficulty="standard",
    )

    assert profile.model == "kimi-code/kimi-for-coding"


def test_resolve_worker_profile_missing_tier_fails_closed():
    config = _config(tiers={"light": _default_tiers()["light"]})

    with pytest.raises(RuntimeAdapterError) as error:
        resolve_worker_profile(
            config,
            repository="owner/repo",
            difficulty="standard",
        )

    assert error.value.code == "RUNTIME_TIER_PROFILE_MISSING"
    assert "standard" in error.value.detail


def test_resolve_worker_profile_invalid_difficulty_fails_closed():
    with pytest.raises(RuntimeAdapterError) as error:
        resolve_worker_profile(
            _config(),
            repository="owner/repo",
            difficulty="unknown",
        )

    assert error.value.code == "WORKER_DIFFICULTY_INVALID"


def test_resolve_worker_profile_malformed_repositories_list_fails_closed():
    config = _config()
    config["repositories"] = []

    with pytest.raises(RuntimeAdapterError) as error:
        resolve_worker_profile(
            config,
            repository="owner/repo",
            difficulty="standard",
        )

    assert error.value.code == "RUNTIME_CONFIG_INVALID"


@pytest.mark.parametrize(
    "settings",
    [
        {"model": "", "thinkingOptionId": "on", "modeId": "yolo", "features": {}},
        {
            "model": "kimi-code/kimi-for-coding",
            "thinkingOptionId": "",
            "modeId": "yolo",
            "features": {},
        },
        {
            "model": "kimi-code/kimi-for-coding",
            "thinkingOptionId": "on",
            "modeId": "",
            "features": {},
        },
        {
            "model": "kimi-code/kimi-for-coding",
            "thinkingOptionId": "on",
            "modeId": "yolo",
            "features": [],
        },
    ],
)
def test_resolve_worker_profile_incomplete_mapping_fails_closed(settings):
    config = _config(tiers={"standard": {"provider": "kimi-cli", "settings": settings}})

    with pytest.raises(RuntimeAdapterError) as error:
        resolve_worker_profile(
            config,
            repository="owner/repo",
            difficulty="standard",
        )

    assert error.value.code in {
        "RUNTIME_TIER_PROFILE_INVALID",
        "RUNTIME_PROVIDER_INVALID",
        "RUNTIME_SETTINGS_INVALID",
    }


def test_resolve_worker_profile_preserves_unnormalized_provider_and_reasoning():
    # Shape parsing no longer normalizes provider aliases or Kimi reasoning.
    # That contract lives behind the RuntimeAdapter.normalize_profile seam.
    config = _config(
        tiers={
            "standard": _binding("kimi", "kimi-code/kimi-for-coding", "  TRUE  ", "yolo"),
        },
    )

    profile = resolve_worker_profile(
        config,
        repository="owner/repo",
        difficulty="standard",
    )

    assert profile.provider == "kimi"
    assert profile.thinking == "TRUE"


def test_adapter_normalize_profile_rejects_unknown_provider_model_reasoning_mode():
    adapter = InMemoryRuntimeAdapter(Path("/tmp/normalize-test"))

    with pytest.raises(RuntimeAdapterError) as error:
        adapter.normalize_profile(
            RuntimeProfile(
                name="standard",
                provider="unknown-provider",
                model="unknown-model",
                thinking="unknown-reasoning",
                mode="unknown-mode",
                features={},
            )
        )
    assert error.value.code == "RUNTIME_PROVIDER_UNSUPPORTED"

    with pytest.raises(RuntimeAdapterError) as error:
        adapter.normalize_profile(
            RuntimeProfile(
                name="standard",
                provider="kimi-cli",
                model="unknown-model",
                thinking="on",
                mode="yolo",
                features={},
            )
        )
    assert error.value.code == "RUNTIME_MODEL_UNSUPPORTED"

    with pytest.raises(RuntimeAdapterError) as error:
        adapter.normalize_profile(
            RuntimeProfile(
                name="standard",
                provider="kimi-cli",
                model="kimi-code/kimi-for-coding",
                thinking="high",
                mode="yolo",
                features={},
            )
        )
    assert error.value.code == "RUNTIME_THINKING_INVALID"

    with pytest.raises(RuntimeAdapterError) as error:
        adapter.normalize_profile(
            RuntimeProfile(
                name="standard",
                provider="kimi-cli",
                model="kimi-code/kimi-for-coding",
                thinking="on",
                mode="unknown-mode",
                features={},
            )
        )
    assert error.value.code == "RUNTIME_MODE_UNSUPPORTED"


def test_adapter_normalize_profile_normalizes_kimi_provider_and_reasoning():
    adapter = InMemoryRuntimeAdapter(Path("/tmp/normalize-test"))

    normalized = adapter.normalize_profile(
        RuntimeProfile(
            name="standard",
            provider="kimi",
            model="kimi-code/kimi-for-coding",
            thinking="  TRUE  ",
            mode="yolo",
            features={},
        )
    )

    assert normalized.provider == "kimi-cli"
    assert normalized.thinking == "on"


def test_resolve_worker_profile_preserves_features():
    config = _config(
        tiers={
            "standard": _binding(
                "codex",
                "gpt-5.6-sol",
                "high",
                "full-access",
                features={"custom": True},
            ),
        },
    )

    profile = resolve_worker_profile(
        config,
        repository="owner/repo",
        difficulty="standard",
    )

    assert profile.features == {"custom": True}


def test_resolve_worker_profile_requires_config_object():
    with pytest.raises(RuntimeAdapterError) as error:
        resolve_worker_profile(
            None,
            repository="owner/repo",
            difficulty="standard",
        )

    assert error.value.code == "RUNTIME_CONFIG_MISSING"

    with pytest.raises(RuntimeAdapterError) as error:
        resolve_worker_profile(
            "not-a-config",
            repository="owner/repo",
            difficulty="standard",
        )

    assert error.value.code == "RUNTIME_CONFIG_INVALID"


def _git(repository: Path, *args: str) -> str:
    result = __import__("subprocess").run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _temporary_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    __import__("subprocess").run(
        ["git", "init", "-b", "main", str(repository)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    _git(repository, "config", "user.name", "Worker Profile Test")
    _git(repository, "config", "user.email", "worker-profile@example.invalid")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "base")
    return repository


def test_kernel_resolves_worker_profile_from_runtime_config_at_admission(tmp_path):
    repository = _temporary_repository(tmp_path)
    source = {
        "repository": "local/worker-profile",
        "work_items": [
            {
                "work_item_key": "issue:70",
                "tracker_state": "ready-for-agent",
                "source_ref": "synthetic://issue/70",
                "title": "Exercise Admission profile resolution",
                "outcome_contract": {"path": "result.txt", "content": "ok\n"},
            }
        ],
    }
    intent = {
        "parent_plan_digest": None,
        "goals": [
            {
                "goal_key": "goal:profile",
                "objective": "Verify Admission resolves the Worker profile.",
                "acceptance": ["result.txt contains ok"],
            }
        ],
        "nodes": [
            {
                "goal_key": "goal:profile",
                "work_item_key": "issue:70",
                "kind": "work",
                "inputs": {"file_changes": [{"path": "result.txt", "content": "ok\n"}]},
                "output_contract": {
                    "required_evidence": [
                        {"kind": "candidate"},
                        {"kind": "check", "check_id": "result-content"},
                    ],
                    "checks": [
                        {
                            "check_id": "result-content",
                            "command": [
                                "python",
                                "-c",
                                "from pathlib import Path; assert Path('result.txt').read_text() == 'ok\\n'",
                            ],
                        }
                    ],
                },
                "effect_contract": {
                    "write_scopes": ["result.txt"],
                    "external_effects": [],
                },
                "resource_claims": [],
                "runtime_requirements": {"capabilities": ["git", "local_check"]},
                "difficulty": "complex",
                "risk": "low",
                "recovery_policy": {"semantic_attempts": 1, "repair_rounds": 0},
                "skill_reference": None,
            }
        ],
        "edges": [],
    }
    compiled = PlanCompiler().compile(intent, source, {"version": 1})
    publication = LocalPlanPublication(tmp_path / "v8.sqlite3")
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="profile-test",
    )

    runtime_config = {
        "tiers": {
            "heavy": _binding(
                "codex", "admission-resolved-sol", "high", "full-access"
            ),
        },
    }
    runtime = InMemoryRuntimeAdapter(tmp_path / "runtime")
    captured: dict[str, object] = {}
    original_materialize = runtime.materialize

    def _capturing_materialize(
        admission,
        prompt=None,
    ):
        captured["profile"] = admission.runtime_profile
        return original_materialize(admission, prompt)

    runtime.materialize = _capturing_materialize

    kernel = Kernel(
        store_path=tmp_path / "v8.sqlite3",
        publication=publication,
        runtime=runtime,
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="profile-test",
        runtime_config=runtime_config,
    )

    outcome = kernel.reconcile_once("local/worker-profile")

    assert outcome.directive == "goal_complete"
    profile = captured["profile"]
    assert isinstance(profile, RuntimeProfile)
    assert profile.name == "heavy"
    assert profile.provider == "codex"
    assert profile.model == "admission-resolved-sol"
    assert profile.thinking == "high"
    assert profile.mode == "full-access"

    # The Admission/Attempt durable state must carry the immutable profile snapshot
    # and digest so restart or config changes cannot reselect the same Admission.
    compiled_plan = json.loads(compiled.canonical_bytes)
    work_nodes = [n for n in compiled_plan["nodes"] if n["kind"] == "work"]
    assert len(work_nodes) == 1
    node_key = work_nodes[0]["node_key"]
    import sqlite3

    with sqlite3.connect(tmp_path / "v8.sqlite3") as conn:
        row = conn.execute(
            "SELECT state_json FROM v8_node_execution_state "
            "WHERE repository = ? AND node_key = ?",
            ("local/worker-profile", node_key),
        ).fetchone()
    assert row is not None
    durable = json.loads(row[0])
    assert durable["runtime_profile"]["name"] == "heavy"
    assert durable["runtime_profile"]["provider"] == "codex"
    assert durable["runtime_profile"]["model"] == "admission-resolved-sol"
    assert durable["runtime_profile"]["thinking"] == "high"
    assert durable["runtime_profile"]["mode"] == "full-access"
    assert durable["profile_digest"] == profile.digest


def test_kernel_rejects_mixed_injected_profile_and_runtime_config(tmp_path):
    runtime = InMemoryRuntimeAdapter(tmp_path / "runtime")
    with pytest.raises(KernelError) as error:
        Kernel(
            store_path=tmp_path / "v8.sqlite3",
            publication=object(),  # type: ignore[arg-type]
            runtime=runtime,
            verifier=EvidenceVerifier(),
            repository_path=tmp_path,
            integration_branch="main",
            writer_generation="mixed-test",
            runtime_config={"tiers": {"light": _binding("kimi-cli", "m", "on", "yolo")}},
            runtime_profile=RuntimeProfile(
                name="injected",
                provider="kimi-cli",
                model="m",
                thinking="on",
                mode="yolo",
                features={},
            ),
        )
    assert error.value.code == "RUNTIME_PROFILE_INJECTION_CONFLICT"


def test_kernel_allows_injected_profile_only_without_runtime_config(tmp_path):
    runtime = InMemoryRuntimeAdapter(tmp_path / "runtime")
    # Construction succeeds as a test seam; production always supplies runtime_config.
    kernel = Kernel(
        store_path=tmp_path / "v8.sqlite3",
        publication=object(),  # type: ignore[arg-type]
        runtime=runtime,
        verifier=EvidenceVerifier(),
        repository_path=tmp_path,
        integration_branch="main",
        writer_generation="injection-test",
        runtime_profile=RuntimeProfile(
            name="injected",
            provider="kimi-cli",
            model="m",
            thinking="on",
            mode="yolo",
            features={},
        ),
    )
    assert kernel.runtime_profile is not None
    assert kernel.runtime_config is None


@pytest.mark.parametrize(
    "difficulty, expected_tier, expected_model",
    [
        ("routine", "light", "kimi-code/kimi-for-coding"),
        ("standard", "standard", "kimi-code/kimi-for-coding"),
        ("complex", "heavy", "kimi-code/k3"),
        ("frontier", "frontier", "gpt-5.6-sol"),
    ],
)
def test_kernel_canary_selects_configured_tier_without_injection(
    tmp_path,
    difficulty,
    expected_tier,
    expected_model,
):
    repository = _temporary_repository(tmp_path)
    source = {
        "repository": "local/canary",
        "work_items": [
            {
                "work_item_key": "issue:70",
                "tracker_state": "ready-for-agent",
                "source_ref": "synthetic://issue/70",
                "title": "Exercise canary tier selection",
                "outcome_contract": {"path": "result.txt", "content": "ok\n"},
            }
        ],
    }
    intent = {
        "parent_plan_digest": None,
        "goals": [
            {
                "goal_key": "goal:canary",
                "objective": "Verify canary tier selection.",
                "acceptance": ["result.txt contains ok"],
            }
        ],
        "nodes": [
            {
                "goal_key": "goal:canary",
                "work_item_key": "issue:70",
                "kind": "work",
                "inputs": {"file_changes": [{"path": "result.txt", "content": "ok\n"}]},
                "output_contract": {
                    "required_evidence": [
                        {"kind": "candidate"},
                        {"kind": "check", "check_id": "result-content"},
                    ],
                    "checks": [
                        {
                            "check_id": "result-content",
                            "command": [
                                "python",
                                "-c",
                                "from pathlib import Path; assert Path('result.txt').read_text() == 'ok\\n'",
                            ],
                        }
                    ],
                },
                "effect_contract": {
                    "write_scopes": ["result.txt"],
                    "external_effects": [],
                },
                "resource_claims": [],
                "runtime_requirements": {"capabilities": ["git", "local_check"]},
                "difficulty": difficulty,
                "risk": "low",
                "recovery_policy": {"semantic_attempts": 1, "repair_rounds": 0},
                "skill_reference": None,
            }
        ],
        "edges": [],
    }
    compiled = PlanCompiler().compile(intent, source, {"version": 1})
    publication = LocalPlanPublication(tmp_path / "v8.sqlite3")
    publication.publish_and_activate(
        compiled,
        expected_active_digest=None,
        writer_generation="canary-test",
    )

    runtime_config = {"tiers": _default_tiers()}
    runtime = InMemoryRuntimeAdapter(tmp_path / "runtime")
    captured: dict[str, object] = {}
    original_materialize = runtime.materialize

    def _capturing_materialize(admission, prompt=None):
        captured["profile"] = admission.runtime_profile
        return original_materialize(admission, prompt)

    runtime.materialize = _capturing_materialize

    kernel = Kernel(
        store_path=tmp_path / "v8.sqlite3",
        publication=publication,
        runtime=runtime,
        verifier=EvidenceVerifier(),
        repository_path=repository,
        integration_branch="main",
        writer_generation="canary-test",
        runtime_config=runtime_config,
    )

    outcome = kernel.reconcile_once("local/canary")

    assert outcome.directive == "goal_complete"
    profile = captured["profile"]
    assert isinstance(profile, RuntimeProfile)
    assert profile.name == expected_tier
    assert profile.model == expected_model
