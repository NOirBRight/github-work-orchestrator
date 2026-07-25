from __future__ import annotations

import json
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any

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
    ModelCapabilities,
    PaseoCliClient,
    PaseoRuntimeAdapter,
    PlanCompiler,
    ProviderCapabilities,
    RuntimeAdapterError,
    RuntimeCapabilities,
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


def test_adapter_normalize_profile_requires_exact_k2_7_reasoning_on():
    adapter = InMemoryRuntimeAdapter(Path("/tmp/normalize-test"))

    for thinking in ("TRUE", "yes", "enabled", "off"):
        with pytest.raises(RuntimeAdapterError) as error:
            adapter.normalize_profile(
                RuntimeProfile(
                    name="standard",
                    provider="kimi-cli",
                    model="kimi-code/kimi-for-coding",
                    thinking=thinking,
                    mode="yolo",
                    features={},
                )
            )
        assert error.value.code == "RUNTIME_THINKING_INVALID"

    normalized = adapter.normalize_profile(
        RuntimeProfile(
            name="standard",
            provider="kimi-cli",
            model="kimi-code/kimi-for-coding",
            thinking="on",
            mode="yolo",
            features={},
        )
    )
    assert normalized.thinking == "on"


def test_adapter_normalize_profile_rejects_unknown_codex_capabilities():
    capabilities = RuntimeCapabilities(
        providers={
            "codex": ProviderCapabilities(
                models={
                    "gpt-5.6-sol": ModelCapabilities(
                        thinking_options=frozenset({"high"}),
                        features=frozenset(),
                    ),
                },
                modes=frozenset({"full-access"}),
                features=frozenset(),
            )
        }
    )
    adapter = InMemoryRuntimeAdapter(
        Path("/tmp/normalize-codex-test"),
        capabilities=capabilities,
    )

    with pytest.raises(RuntimeAdapterError) as error:
        adapter.normalize_profile(
            RuntimeProfile(
                name="frontier",
                provider="codex",
                model="sol/xhigh",
                thinking="xhigh",
                mode="full-access",
                features={},
            )
        )
    assert error.value.code == "RUNTIME_MODEL_UNSUPPORTED"

    with pytest.raises(RuntimeAdapterError) as error:
        adapter.normalize_profile(
            RuntimeProfile(
                name="frontier",
                provider="codex",
                model="gpt-5.6-sol",
                thinking="xhigh",
                mode="full-access",
                features={},
            )
        )
    assert error.value.code == "RUNTIME_THINKING_INVALID"


def test_resolve_worker_profile_falsey_repository_override_is_invalid():
    with pytest.raises(RuntimeAdapterError) as error:
        resolve_worker_profile(
            _config(repositories={"owner/repo": []}),
            repository="owner/repo",
            difficulty="standard",
        )
    assert error.value.code == "RUNTIME_CONFIG_INVALID"

    with pytest.raises(RuntimeAdapterError) as error:
        resolve_worker_profile(
            _config(
                repositories={
                    "owner/repo": {
                        "tiers": [],
                    }
                }
            ),
            repository="owner/repo",
            difficulty="standard",
        )
    assert error.value.code == "RUNTIME_TIER_PROFILE_INVALID"


def _frontier_kernel(tmp_path: Path) -> Kernel:
    """Minimal Kernel for frontier-profile helper tests."""

    store_path = tmp_path / "v8.sqlite3"
    return Kernel(
        store_path=store_path,
        publication=LocalPlanPublication(store_path),
        runtime=InMemoryRuntimeAdapter(tmp_path / "runtime"),
        verifier=EvidenceVerifier(),
        repository_path=tmp_path / "repo",
        integration_branch="main",
        writer_generation="frontier-test",
        runtime_config={
            "tiers": {
                "light": _binding("kimi-cli", "kimi-code/kimi-for-coding", "on", "yolo"),
                "standard": _binding(
                    "kimi-cli", "kimi-code/kimi-for-coding", "on", "yolo"
                ),
                "heavy": _binding("kimi-cli", "kimi-code/k3", "high", "yolo"),
                "frontier": _binding("codex", "sol/xhigh", "xhigh", "full-access"),
            }
        },
    )


def _frontier_state() -> dict[str, Any]:
    return {
        "repository": "local/frontier",
        "plan_digest": "a" * 64,
        "node_key": "node:1",
        "activation_id": "activation:1",
    }


def test_kernel_freeze_legacy_frontier_profile_resolves_and_persists_missing_key(
    tmp_path,
):
    kernel = _frontier_kernel(tmp_path)
    state = _frontier_state()

    profile = kernel._freeze_legacy_frontier_profile(state)

    assert profile.model == "sol/xhigh"
    assert state["frontier_runtime_profile"]["model"] == "sol/xhigh"
    assert state["frontier_profile_digest"] == profile.digest


def test_kernel_freeze_legacy_frontier_profile_fails_closed_on_invalid_value(
    tmp_path,
):
    kernel = _frontier_kernel(tmp_path)
    state = _frontier_state()
    state["frontier_runtime_profile"] = None

    with pytest.raises(KernelError) as error:
        kernel._freeze_legacy_frontier_profile(state)
    assert error.value.code == "RUNTIME_PROFILE_FROZEN_INVALID"


def test_kernel_start_frontier_attempt_branch_uses_freeze_helper():
    """Structural proof that the start_frontier_attempt gate uses the freeze helper."""

    import inspect

    source = inspect.getsource(Kernel._handle_semantic_rejection)
    start_index = source.find("start_frontier_attempt")
    freeze_index = source.find("_freeze_legacy_frontier_profile")
    assert start_index != -1
    assert freeze_index != -1
    assert freeze_index > start_index


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
            "heavy": _binding("codex", "gpt-5.6-sol", "high", "full-access"),
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
    assert profile.model == "gpt-5.6-sol"
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
    assert durable["runtime_profile"]["model"] == "gpt-5.6-sol"
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



def test_paseo_cli_capabilities_read_real_payload_and_reject_unknown():
    """Fresh capability readback validates against one requested provider."""

    client = PaseoCliClient()

    def _mock_run(args: list[str], *, failure_class: str = "transient"):  # noqa: ARG001
        if args == ["provider", "ls", "--json"]:
            return [
                {
                    "provider": "codex",
                    "status": "ready",
                    "enabled": "Enabled",
                    "defaultMode": "auto",
                    "modes": "Full Access,YOLO",
                },
                {
                    "provider": "kimi",
                    "status": "ready",
                    "enabled": True,
                    "defaultMode": "default",
                    "modes": "Plan,Auto,YOLO",
                },
            ]
        if args[:3] == ["provider", "models", "codex"]:
            return [
                {
                    "id": "gpt-5.6-sol",
                    "thinkingOptionIds": "high,xhigh",
                    "defaultThinkingOptionId": "xhigh",
                },
                {
                    "id": "sol/xhigh",
                    "thinkingOptionIds": "xhigh",
                    "defaultThinkingOptionId": "xhigh",
                },
            ]
        if args[:3] == ["provider", "models", "kimi"]:
            return [
                {
                    "id": "kimi-code/kimi-for-coding",
                    "thinkingOptionIds": "",
                    "defaultThinkingOptionId": "",
                },
                {
                    "id": "kimi-code/k3",
                    "thinkingOptionIds": "high,max",
                    "defaultThinkingOptionId": "high",
                },
            ]
        raise RuntimeError(f"unexpected args: {args}")

    client._run = _mock_run  # type: ignore[method-assign]
    adapter = PaseoRuntimeAdapter(client)

    # Codex: gpt-5.6-sol/high/yolo and sol/xhigh/xhigh/full-access accepted.
    assert adapter.normalize_profile(
        RuntimeProfile(
            name="standard",
            provider="codex",
            model="gpt-5.6-sol",
            thinking="high",
            mode="yolo",
            features={},
        )
    ).model == "gpt-5.6-sol"
    assert adapter.normalize_profile(
        RuntimeProfile(
            name="frontier",
            provider="codex",
            model="sol/xhigh",
            thinking="xhigh",
            mode="full-access",
            features={},
        )
    ).model == "sol/xhigh"

    # Kimi: K3/high/yolo accepted; exact K2.7 "on" accepted via narrow exception.
    assert adapter.normalize_profile(
        RuntimeProfile(
            name="heavy",
            provider="kimi-cli",
            model="kimi-code/k3",
            thinking="high",
            mode="yolo",
            features={},
        )
    ).model == "kimi-code/k3"
    assert adapter.normalize_profile(
        RuntimeProfile(
            name="light",
            provider="kimi-cli",
            model="kimi-code/kimi-for-coding",
            thinking="on",
            mode="yolo",
            features={},
        )
    ).thinking == "on"

    # Unknown provider and disabled/non-ready providers fail closed.
    with pytest.raises(RuntimeAdapterError) as error:
        client.capabilities("unknown-provider")
    assert error.value.code == "RUNTIME_PROVIDER_UNSUPPORTED"

    # Unknown model/thinking/mode rejected for known provider.
    with pytest.raises(RuntimeAdapterError) as error:
        adapter.normalize_profile(
            RuntimeProfile(
                name="frontier",
                provider="codex",
                model="unknown-sol",
                thinking="xhigh",
                mode="full-access",
                features={},
            )
        )
    assert error.value.code == "RUNTIME_MODEL_UNSUPPORTED"
