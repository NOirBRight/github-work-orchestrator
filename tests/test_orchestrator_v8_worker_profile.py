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


def test_resolve_worker_profile_kimi_k27_rejects_non_toggle_reasoning():
    config = _config(
        tiers={
            "standard": _binding(
                "kimi-cli", "kimi-code/kimi-for-coding", "high", "yolo"
            ),
        },
    )

    with pytest.raises(RuntimeAdapterError) as error:
        resolve_worker_profile(
            config,
            repository="owner/repo",
            difficulty="standard",
        )

    assert error.value.code == "RUNTIME_THINKING_INVALID"


def test_resolve_worker_profile_kimi_k3_rejects_low_reasoning():
    config = _config(
        tiers={
            "heavy": _binding("kimi-cli", "kimi-code/k3", "on", "yolo"),
        },
    )

    with pytest.raises(RuntimeAdapterError) as error:
        resolve_worker_profile(
            config,
            repository="owner/repo",
            difficulty="complex",
        )

    assert error.value.code == "RUNTIME_THINKING_INVALID"


def test_resolve_worker_profile_normalizes_kimi_reasoning_synonyms():
    config = _config(
        tiers={
            "light": _binding(
                "kimi-cli", "kimi-code/kimi-for-coding", "  TRUE  ", "yolo"
            ),
        },
    )

    profile = resolve_worker_profile(
        config,
        repository="owner/repo",
        difficulty="routine",
    )

    assert profile.thinking == "on"


def test_resolve_worker_profile_normalizes_kimi_provider_alias():
    config = _config(
        tiers={
            "standard": _binding("kimi", "kimi-code/kimi-for-coding", "on", "yolo"),
        },
    )

    profile = resolve_worker_profile(
        config,
        repository="owner/repo",
        difficulty="standard",
    )

    assert profile.provider == "kimi-cli"


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
