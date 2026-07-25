from __future__ import annotations

import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gwo_v8 import (  # noqa: E402
    InMemoryPaseoClient,
    PaseoRuntimeAdapter,
    RuntimeAdmission,
    RuntimeProfile,
    RuntimePrompt,
)
import orch_core as core  # noqa: E402


def _load_config_command():
    spec = importlib.util.spec_from_file_location(
        "orch_config_test",
        SCRIPTS / "orch_config.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy(path: Path, *, valid: bool = True) -> None:
    tiers = {
        tier: {
            **({"provider": "provider"} if valid else {}),
            "model": f"model-{tier}",
        }
        for tier in ("light", "standard", "heavy")
    }
    path.write_text(json.dumps({"tiers": tiers}), encoding="utf-8")


def _binding(
    name: str,
    *,
    provider: str = "codex",
    thinking: str = "high",
    mode: str = "full-access",
) -> dict:
    return {
        "provider": provider,
        "settings": {
            "model": f"model-{name}",
            "thinkingOptionId": thinking,
            "modeId": mode,
            "features": {"profile": name},
        },
    }


def _runtime_patch() -> dict:
    return {
        "tiers": {"standard": _binding("repository-standard")},
        "role_profiles": {
            "reviewer_standard": _binding("repository-reviewer"),
        },
    }


def test_scoped_runtime_config_repository_command_preserves_every_other_scope(
    tmp_path,
):
    command = _load_config_command()
    config_path = tmp_path / "config.json"
    config = core.default_config()
    config["global"]["custom_global"] = {"preserve": True}
    config["tiers"]["light"] = _binding("global-light")
    config["role_profiles"]["reviewer_recovery"] = _binding("global-recovery")
    config["repositories"] = {
        "owner/repository-a": {
            "integration_branch": "dev",
            "custom": {"preserve": ["exactly"]},
            "tiers": {"light": _binding("repository-a-light")},
            "role_profiles": {
                "reviewer_recovery": _binding("repository-a-recovery"),
            },
        },
        "owner/repository-b": {
            "integration_branch": "release",
            "custom": {"untouched": True},
            "tiers": {"heavy": _binding("repository-b-heavy")},
            "role_profiles": {
                "reviewer_strict": _binding("repository-b-strict"),
            },
        },
    }
    config_path.write_text(
        json.dumps(config, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    before = json.loads(config_path.read_text(encoding="utf-8"))
    patch = _runtime_patch()

    result = command.run(
        command.parse_args(
            [
                "set-repository-runtime",
                "--config",
                str(config_path),
                "--repository",
                "owner/repository-a",
                "--patch",
                json.dumps(patch),
            ]
        )
    )

    installed = json.loads(config_path.read_text(encoding="utf-8"))
    assert installed["global"] == before["global"]
    assert installed["tiers"] == before["tiers"]
    assert installed["role_profiles"] == before["role_profiles"]
    assert (
        installed["repositories"]["owner/repository-b"]
        == before["repositories"]["owner/repository-b"]
    )
    selected_before = before["repositories"]["owner/repository-a"]
    selected = installed["repositories"]["owner/repository-a"]
    assert selected["integration_branch"] == selected_before["integration_branch"]
    assert selected["custom"] == selected_before["custom"]
    assert selected["tiers"]["light"] == selected_before["tiers"]["light"]
    assert selected["tiers"]["standard"] == patch["tiers"]["standard"]
    assert (
        selected["role_profiles"]["reviewer_recovery"]
        == selected_before["role_profiles"]["reviewer_recovery"]
    )
    assert (
        selected["role_profiles"]["reviewer_standard"]
        == patch["role_profiles"]["reviewer_standard"]
    )
    assert result["scope"] == "repository"
    assert result["repository"] == "owner/repository-a"
    assert result["updated"] == {
        "tiers": ["standard"],
        "role_profiles": ["reviewer_standard"],
    }
    assert "config" not in result
    assert len(json.dumps(result)) < 1024
    assert result["config_sha256"] == hashlib.sha256(
        config_path.read_bytes()
    ).hexdigest()


def test_runtime_config_ownership_global_command_is_separately_explicit(tmp_path):
    command = _load_config_command()
    config_path = tmp_path / "config.json"
    config = core.default_config()
    config["global"]["custom"] = "preserve"
    config["repositories"] = {
        "owner/repository-a": {
            "custom": {"preserve": "a"},
            "tiers": {"standard": _binding("repository-a-standard")},
            "role_profiles": {
                "reviewer_standard": _binding("repository-a-reviewer"),
            },
        },
        "owner/repository-b": {
            "custom": {"preserve": "b"},
            "tiers": {"standard": _binding("repository-b-standard")},
            "role_profiles": {
                "reviewer_standard": _binding("repository-b-reviewer"),
            },
        },
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")
    before = json.loads(config_path.read_text(encoding="utf-8"))
    patch = {
        "tiers": {"light": _binding("global-light-updated")},
        "role_profiles": {
            "reviewer_strict": _binding("global-strict-updated"),
        },
    }

    result = command.run(
        command.parse_args(
            [
                "set-global-runtime",
                "--config",
                str(config_path),
                "--patch",
                json.dumps(patch),
            ]
        )
    )

    installed = json.loads(config_path.read_text(encoding="utf-8"))
    assert installed["repositories"] == before["repositories"]
    assert installed["global"] == before["global"]
    assert installed["tiers"]["light"] == patch["tiers"]["light"]
    assert installed["tiers"]["standard"] == before["tiers"]["standard"]
    assert (
        installed["role_profiles"]["reviewer_strict"]
        == patch["role_profiles"]["reviewer_strict"]
    )
    assert (
        installed["role_profiles"]["reviewer_standard"]
        == before["role_profiles"]["reviewer_standard"]
    )
    assert result["scope"] == "global"
    assert "repository" not in result
    with pytest.raises(SystemExit):
        command.parse_args(
            [
                "set-repository-runtime",
                "--config",
                str(config_path),
                "--patch",
                json.dumps(patch),
            ]
        )
    with pytest.raises(SystemExit):
        command.parse_args(
            [
                "set-global-runtime",
                "--config",
                str(config_path),
                "--repository",
                "owner/repository-a",
                "--patch",
                json.dumps(patch),
            ]
        )


def test_scoped_runtime_config_accepts_one_complete_optional_fallback(tmp_path):
    config_path = tmp_path / "config.json"
    worker = _binding("worker-primary")
    worker["fallback"] = _binding("worker-fallback", thinking="max")
    reviewer = _binding("reviewer-primary")
    reviewer["fallback"] = _binding("reviewer-fallback", thinking="max")
    patch = {
        "tiers": {"heavy": worker},
        "role_profiles": {"reviewer_standard": reviewer},
    }

    updated = core.set_repository_runtime_config(
        config_path,
        "owner/repository-a",
        patch,
    )

    repository = updated["repositories"]["owner/repository-a"]
    assert repository["tiers"]["heavy"] == worker
    assert repository["role_profiles"]["reviewer_standard"] == reviewer
    assert (
        json.loads(config_path.read_text(encoding="utf-8"))["repositories"][
            "owner/repository-a"
        ]
        == repository
    )


@pytest.mark.parametrize(
    "patch",
    [
        {},
        {"global": {"default_tier": "light"}},
        {"tiers": {}},
        {"tiers": {"unknown": _binding("unknown")}},
        {
            "tiers": {
                "standard": {
                    "provider": "codex",
                    "settings": {"model": "partial"},
                }
            }
        },
        {
            "role_profiles": {
                "reviewer_standard": {
                    "provider": "codex",
                    "settings": {
                        "model": "reviewer",
                        "thinkingOptionId": "high",
                        "modeId": "full-access",
                        "features": [],
                    },
                }
            }
        },
        {
            "tiers": {
                "heavy": {
                    **_binding("primary"),
                    "fallback": {
                        "provider": "codex",
                        "settings": {"model": "partial-fallback"},
                    },
                }
            }
        },
        {
            "role_profiles": {
                "reviewer_standard": {
                    **_binding("primary-reviewer"),
                    "fallback": {
                        **_binding("fallback-reviewer"),
                        "fallback": _binding("chained-reviewer"),
                    },
                }
            }
        },
    ],
)
def test_scoped_runtime_config_invalid_or_partial_patch_preserves_exact_bytes(
    tmp_path,
    patch,
):
    config_path = tmp_path / "config.json"
    original = (
        b'{"schema_version":1, "global":{"custom":"keep"}, '
        b'"repositories":{"owner/repository-a":{"custom":"keep"}}}'
    )
    config_path.write_bytes(original)

    with pytest.raises(core.PolicyError):
        core.set_repository_runtime_config(
            config_path,
            "owner/repository-a",
            patch,
        )

    assert config_path.read_bytes() == original
    assert not core.runtime_config_lock_path(config_path).exists()
    assert list(tmp_path.glob(f".{config_path.name}.*.tmp")) == []


@pytest.mark.parametrize("scope", ["repository", "global"])
def test_scoped_runtime_config_absent_file_starts_from_validated_defaults(
    tmp_path,
    scope,
):
    config_path = tmp_path / scope / "config.json"
    patch = _runtime_patch()

    if scope == "repository":
        updated = core.set_repository_runtime_config(
            config_path,
            "owner/repository-a",
            patch,
        )
        target = updated["repositories"]["owner/repository-a"]
        assert updated["tiers"] == core.default_config()["tiers"]
        assert updated["role_profiles"] == core.default_config()["role_profiles"]
    else:
        updated = core.set_global_runtime_config(config_path, patch)
        target = updated
        assert updated["repositories"] == {}

    assert target["tiers"]["standard"] == patch["tiers"]["standard"]
    assert (
        target["role_profiles"]["reviewer_standard"]
        == patch["role_profiles"]["reviewer_standard"]
    )
    installed = json.loads(config_path.read_text(encoding="utf-8"))
    assert core.validate_config(installed) == updated
    assert not core.runtime_config_lock_path(config_path).exists()


def test_scoped_runtime_config_replace_failure_preserves_exact_bytes(
    tmp_path,
    monkeypatch,
):
    config_path = tmp_path / "config.json"
    original = (json.dumps(core.default_config(), indent=1) + "\n").encode("utf-8")
    config_path.write_bytes(original)

    def fail_replace(source, destination):
        raise OSError("simulated atomic replacement failure")

    monkeypatch.setattr(core.os, "replace", fail_replace)

    with pytest.raises(OSError):
        core.set_repository_runtime_config(
            config_path,
            "owner/repository-a",
            _runtime_patch(),
        )

    assert config_path.read_bytes() == original
    assert not core.runtime_config_lock_path(config_path).exists()
    assert list(tmp_path.glob(f".{config_path.name}.*.tmp")) == []


def test_runtime_config_ownership_exclusive_lock_blocks_racing_command(tmp_path):
    config_path = tmp_path / "config.json"
    original = (json.dumps(core.default_config(), indent=1) + "\n").encode("utf-8")
    config_path.write_bytes(original)
    lock_path = core.runtime_config_lock_path(config_path)
    lock_path.write_text("other-command", encoding="ascii")

    with pytest.raises(core.PolicyError) as locked:
        core.set_global_runtime_config(config_path, _runtime_patch())

    assert locked.value.code == "CONFIG_COMMAND_LOCKED"
    assert config_path.read_bytes() == original
    assert lock_path.read_text(encoding="ascii") == "other-command"


def test_scoped_runtime_config_validation_readback_rejects_temporary_corruption(
    tmp_path,
    monkeypatch,
):
    config_path = tmp_path / "config.json"
    original = (json.dumps(core.default_config(), indent=1) + "\n").encode("utf-8")
    config_path.write_bytes(original)
    real_write = core._write_unique_temporary

    def corrupt_temporary(target, content):
        temporary = real_write(target, content)
        temporary.write_bytes(b'{"schema_version": 1, "corrupted": true}')
        return temporary

    monkeypatch.setattr(core, "_write_unique_temporary", corrupt_temporary)

    with pytest.raises(core.PolicyError) as invalid:
        core.set_repository_runtime_config(
            config_path,
            "owner/repository-a",
            _runtime_patch(),
        )

    assert invalid.value.code == "CONFIG_PUBLISH_VALIDATION_FAILED"
    assert config_path.read_bytes() == original
    assert not core.runtime_config_lock_path(config_path).exists()
    assert list(tmp_path.glob(f".{config_path.name}.*.tmp")) == []


def test_runtime_config_ownership_runtime_paths_are_read_only_for_tiers_and_roles(
    tmp_path,
    monkeypatch,
):
    config_path = tmp_path / "config.json"
    config = core.default_config()
    config["repositories"]["owner/repository-a"] = _runtime_patch()
    config_path.write_text(
        json.dumps(config, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    original = config_path.read_bytes()

    def mutation_forbidden(*args, **kwargs):
        raise AssertionError("runtime path invoked explicit config mutation")

    monkeypatch.setattr(core, "set_repository_runtime_config", mutation_forbidden)
    monkeypatch.setattr(core, "set_global_runtime_config", mutation_forbidden)

    loaded = core.load_or_migrate_config(config_path)
    worker = core.resolve_runtime_request(
        loaded,
        repository="owner/repository-a",
        issue={"difficulty": "standard"},
        coordinator_runtime=_binding("coordinator"),
    )
    reviewer = core.resolve_role_runtime_request(
        loaded,
        repository="owner/repository-a",
        role="reviewer_standard",
    )
    assert worker["settings"]["model"] == "model-repository-standard"
    assert reviewer["settings"]["model"] == "model-repository-reviewer"

    client = InMemoryPaseoClient()
    adapter = PaseoRuntimeAdapter(client)
    profile = RuntimeProfile(
        name="standard",
        provider=worker["provider"],
        model=worker["settings"]["model"],
        thinking=worker["settings"]["thinkingOptionId"],
        mode=worker["settings"]["modeId"],
        features=worker["settings"]["features"],
    )
    admission = RuntimeAdmission(
        repository="owner/repository-a",
        plan_digest="plan:read-only",
        node_key="node:read-only",
        admission_id="admission:read-only",
        repository_path=tmp_path,
        base_sha="a" * 40,
        runtime_profile=profile,
    )
    prompt = RuntimePrompt(text="read-only", digest="f" * 64)
    launched = adapter.materialize(admission, prompt)
    assert adapter.read_binding(admission, prompt) == launched

    reconciled = core.plan_reconcile(
        {
            "issues": [],
            "closed_issues": [],
            "runtime_agents": [],
            "execution_slots": 3,
            "integration_wip_limit": 6,
        }
    )
    integrated = core.plan_integration(
        {
            "pr": 65,
            "base": "dev",
            "integration_branch": "dev",
            "workspace": {"dirty": False},
            "checks": "green",
            "review": "accepted",
            "contract_valid": True,
            "behind": False,
        }
    )
    assert reconciled["status"] == "idle"
    assert integrated["actions"] == [{"type": "merge", "pr": 65}]
    assert config_path.read_bytes() == original


def test_runtime_config_load_never_writes_even_through_the_legacy_flag(tmp_path):
    legacy = tmp_path / "providers.json"
    config = tmp_path / "config.json"
    _legacy(legacy)

    default_loaded = core.load_or_migrate_config(config, legacy)
    legacy_loaded = core.load_or_migrate_config(
        config,
        legacy,
        write_migration=True,
    )

    assert default_loaded == legacy_loaded
    assert not config.exists()
    assert not (tmp_path / "providers.v5.backup.json").exists()


def test_explicit_command_atomically_migrates_valid_legacy_config(tmp_path):
    command = _load_config_command()
    legacy = tmp_path / "providers.json"
    config = tmp_path / "config.json"
    _legacy(legacy)

    result = command.run(
        command.parse_args(
            [
                "migrate",
                "--legacy",
                str(legacy),
                "--config",
                str(config),
            ]
        )
    )

    assert result["status"] == "idle"
    assert core.validate_config(json.loads(config.read_text(encoding="utf-8")))
    assert (tmp_path / "providers.v5.backup.json").read_bytes() == legacy.read_bytes()
    assert not (tmp_path / "config.json.tmp").exists()


def test_explicit_migration_preserves_existing_config_and_overrides(tmp_path):
    command = _load_config_command()
    legacy = tmp_path / "providers.json"
    config = tmp_path / "config.json"
    _legacy(legacy)
    original = (
        b'{"schema_version":1,"global":{"custom":"keep"},'
        b'"repositories":{"owner/repo":{"custom":"keep"}}}'
    )
    config.write_bytes(original)

    with pytest.raises(command.core.PolicyError) as existing:
        command.run(
            command.parse_args(
                [
                    "migrate",
                    "--legacy",
                    str(legacy),
                    "--config",
                    str(config),
                ]
            )
        )

    assert existing.value.code == "CONFIG_ALREADY_EXISTS"
    assert config.read_bytes() == original
    assert not (tmp_path / "providers.v5.backup.json").exists()


def test_invalid_explicit_migration_never_publishes_config_or_backup(tmp_path):
    legacy = tmp_path / "providers.json"
    config = tmp_path / "config.json"
    _legacy(legacy, valid=False)

    with pytest.raises(core.PolicyError):
        core.migrate_config_file(legacy, config)

    assert not config.exists()
    assert not (tmp_path / "providers.v5.backup.json").exists()


def test_frontier_worker_never_falls_back_to_coordinator_runtime():
    config = core.default_config()
    del config["tiers"]["frontier"]

    with pytest.raises(core.PolicyError) as missing:
        core.resolve_runtime_request(
            config,
            repository="owner/repo",
            issue={"difficulty": "frontier"},
            coordinator_runtime={
                "provider": "kimi-cli",
                "settings": {"model": "kimi-code/k3", "modeId": "yolo"},
            },
        )

    assert missing.value.code == "RUNTIME_FRONTIER_PROFILE_MISSING"


@pytest.mark.parametrize(
    "mapping",
    [
        [],
        {"provider": "codex", "settings": {}},
        {
            "provider": "codex",
            "settings": {
                "model": "gpt-5.6-sol",
                "thinkingOptionId": "xhigh",
                "modeId": "full-access",
            },
        },
    ],
)
def test_frontier_worker_rejects_an_incomplete_profile(mapping):
    config = core.default_config()
    config["tiers"]["frontier"] = mapping

    with pytest.raises(core.PolicyError) as invalid:
        core.resolve_runtime_request(
            config,
            repository="owner/repo",
            issue={"difficulty": "frontier"},
            coordinator_runtime={
                "provider": "codex",
                "settings": {"model": "gpt-5.6-sol", "modeId": "full-access"},
            },
        )

    assert invalid.value.code == "RUNTIME_FRONTIER_PROFILE_INVALID"


def test_kimi_and_codex_launch_and_readback_leave_config_bytes_unchanged(tmp_path):
    config_path = tmp_path / "config.json"
    config = core.default_config()
    config["global"]["unrelated_override"] = "preserve"
    config["repositories"]["owner/repo"] = {
        "integration_branch": "dev",
        "custom_override": {"preserve": True},
    }
    config_path.write_text(
        json.dumps(config, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    original = config_path.read_bytes()
    client = InMemoryPaseoClient()
    adapter = PaseoRuntimeAdapter(client)
    profiles = (
        RuntimeProfile(
            name="standard",
            provider="kimi-cli",
            model="kimi-code/kimi-for-coding",
            thinking="on",
            mode="yolo",
            features={},
        ),
        RuntimeProfile(
            name="frontier",
            provider="codex",
            model="gpt-5.6-sol",
            thinking="xhigh",
            mode="full-access",
            features={},
        ),
    )

    for ordinal, profile in enumerate(profiles, start=1):
        admission = RuntimeAdmission(
            repository="owner/repo",
            plan_digest=f"plan:{ordinal}",
            node_key=f"node:{ordinal}",
            admission_id=f"admission:{ordinal}",
            repository_path=tmp_path,
            base_sha="a" * 40,
            runtime_profile=profile,
        )
        prompt = RuntimePrompt(text=f"work-{ordinal}", digest=f"{ordinal}" * 64)

        launched = adapter.materialize(admission, prompt)
        read_back = adapter.read_binding(admission, prompt)

        assert read_back == launched
        assert config_path.read_bytes() == original


@pytest.mark.parametrize(
    "settings",
    [
        {"model": "", "thinkingOptionId": "xhigh", "modeId": "full-access", "features": {}},
        {"model": "   ", "thinkingOptionId": "xhigh", "modeId": "full-access", "features": {}},
        {"model": "gpt-5.6-sol", "thinkingOptionId": "", "modeId": "full-access", "features": {}},
        {"model": "gpt-5.6-sol", "thinkingOptionId": "\t", "modeId": "full-access", "features": {}},
        {"model": "gpt-5.6-sol", "thinkingOptionId": "xhigh", "modeId": "", "features": {}},
        {"model": "gpt-5.6-sol", "thinkingOptionId": "xhigh", "modeId": "\n", "features": {}},
        {"model": "gpt-5.6-sol", "thinkingOptionId": "xhigh", "modeId": "full-access", "features": []},
    ],
)
def test_frontier_worker_rejects_empty_or_wrong_type_profile_fields(settings):
    config = core.default_config()
    config["tiers"]["frontier"] = {"provider": "codex", "settings": settings}

    with pytest.raises(core.PolicyError) as invalid:
        core.resolve_runtime_request(
            config,
            repository="owner/repo",
            issue={"difficulty": "frontier"},
            coordinator_runtime={
                "provider": "codex",
                "settings": {"model": "gpt-5.6-sol", "modeId": "full-access"},
            },
        )

    assert invalid.value.code == "RUNTIME_FRONTIER_PROFILE_INVALID"


@pytest.mark.parametrize("provider", ["", " ", "\t"])
def test_frontier_worker_rejects_empty_provider(provider):
    config = core.default_config()
    config["tiers"]["frontier"] = {
        "provider": provider,
        "settings": {
            "model": "gpt-5.6-sol",
            "thinkingOptionId": "xhigh",
            "modeId": "full-access",
            "features": {},
        },
    }

    with pytest.raises(core.PolicyError) as invalid:
        core.resolve_runtime_request(
            config,
            repository="owner/repo",
            issue={"difficulty": "frontier"},
            coordinator_runtime={
                "provider": "codex",
                "settings": {"model": "gpt-5.6-sol", "modeId": "full-access"},
            },
        )

    assert invalid.value.code == "RUNTIME_FRONTIER_PROFILE_INVALID"


@pytest.mark.parametrize(
    "mapping",
    [
        [],
        {"provider": "codex", "settings": []},
    ],
)
def test_file_backed_frontier_profile_uses_precise_validation_error(
    tmp_path, mapping
):
    config = core.default_config()
    config["tiers"]["frontier"] = mapping
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(core.PolicyError) as invalid:
        core.load_or_migrate_config(config_path)

    assert invalid.value.code == "RUNTIME_FRONTIER_PROFILE_INVALID"


def test_migration_does_not_clobber_config_created_concurrently(tmp_path, monkeypatch):
    legacy = tmp_path / "providers.json"
    config = tmp_path / "config.json"
    _legacy(legacy)
    concurrent_bytes = b'{"concurrent": true}'

    real_link = os.link

    def racing_link(src, dst):
        if Path(dst) == config:
            config.write_bytes(concurrent_bytes)
            raise FileExistsError(errno.EEXIST, "simulated race", str(dst))
        return real_link(src, dst)

    monkeypatch.setattr(os, "link", racing_link)

    with pytest.raises(core.PolicyError) as error:
        core.migrate_config_file(legacy, config)

    assert error.value.code == "CONFIG_ALREADY_EXISTS"
    assert config.read_bytes() == concurrent_bytes
    assert (tmp_path / "providers.v5.backup.json").read_bytes() == legacy.read_bytes()


def test_migration_never_unlinks_backup_replaced_before_config_rollback(
    tmp_path, monkeypatch
):
    legacy = tmp_path / "providers.json"
    config = tmp_path / "config.json"
    backup = tmp_path / "providers.v5.backup.json"
    replacement = tmp_path / "replacement-backup.json"
    _legacy(legacy)
    replacement_bytes = b'{"replacement backup": true}'
    concurrent_config_bytes = b'{"concurrent config": true}'
    replacement.write_bytes(replacement_bytes)

    real_link = os.link
    real_unlink = Path.unlink
    backup_published = False
    backup_unlinks = []

    def racing_link(src, dst):
        nonlocal backup_published
        destination = Path(dst)
        if destination == backup:
            result = real_link(src, dst)
            backup_published = True
            return result
        if destination == config:
            assert backup_published
            os.replace(replacement, backup)
            config.write_bytes(concurrent_config_bytes)
            raise FileExistsError(errno.EEXIST, "simulated race", str(dst))
        return real_link(src, dst)

    def recording_unlink(path, *args, **kwargs):
        if path == backup:
            backup_unlinks.append(path)
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "link", racing_link)
    monkeypatch.setattr(Path, "unlink", recording_unlink)

    with pytest.raises(core.PolicyError) as error:
        core.migrate_config_file(legacy, config)

    assert error.value.code == "CONFIG_ALREADY_EXISTS"
    assert config.read_bytes() == concurrent_config_bytes
    assert backup.read_bytes() == replacement_bytes
    assert backup_unlinks == []


def test_migration_does_not_clobber_backup_created_concurrently(tmp_path, monkeypatch):
    legacy = tmp_path / "providers.json"
    config = tmp_path / "config.json"
    _legacy(legacy)
    backup = tmp_path / "providers.v5.backup.json"
    concurrent_bytes = b'{"concurrent backup": true}'

    real_link = os.link

    def racing_link(src, dst):
        if Path(dst) == backup:
            backup.write_bytes(concurrent_bytes)
            raise FileExistsError(errno.EEXIST, "simulated race", str(dst))
        return real_link(src, dst)

    monkeypatch.setattr(os, "link", racing_link)

    with pytest.raises(core.PolicyError) as error:
        core.migrate_config_file(legacy, config)

    assert error.value.code == "CONFIG_MIGRATION_BACKUP_CONFLICT"
    assert backup.read_bytes() == concurrent_bytes
    assert not config.exists()


def test_migration_does_not_publish_mutated_predictable_temporary(
    tmp_path, monkeypatch
):
    legacy = tmp_path / "providers.json"
    config = tmp_path / "config.json"
    predictable_temporary = tmp_path / "config.json.tmp"
    injected = b'{"unvalidated": true}'
    _legacy(legacy)

    real_link = os.link

    def mutate_predictable_temporary(src, dst):
        if Path(dst) == tmp_path / "providers.v5.backup.json":
            predictable_temporary.write_bytes(injected)
        return real_link(src, dst)

    monkeypatch.setattr(os, "link", mutate_predictable_temporary)

    migrated = core.migrate_config_file(legacy, config)
    installed = json.loads(config.read_text(encoding="utf-8"))

    assert core.validate_config(installed) == migrated
    assert config.read_bytes() != injected


def _explicit_runtime_profile(
    model: str,
    *,
    provider: str = "codex",
    thinking: str = "high",
    mode: str = "full-access",
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


def _fallback_capabilities() -> dict:
    return {
        "provider": "codex",
        "models": {
            "primary": {"thinking": ["high"]},
            "fallback": {"thinking": ["high"]},
        },
        "modes": ["full-access"],
        "features": ["supported"],
    }


def _fallback_coordinator_runtime() -> dict:
    return {
        "provider": "coordinator-provider",
        "settings": {
            "model": "coordinator-model",
            "thinkingOptionId": "high",
            "modeId": "coordinator-mode",
            "features": {},
        },
    }


def test_runtime_profile_fallback_primary_success_remains_primary():
    config = core.default_config()
    primary = _explicit_runtime_profile("primary")
    config["tiers"]["standard"] = {
        **primary,
        "fallback": _explicit_runtime_profile("not-advertised"),
    }

    selected = core.resolve_runtime(
        config,
        repository="owner/repo",
        issue={"difficulty": "standard"},
        coordinator_runtime=_fallback_coordinator_runtime(),
        capabilities=_fallback_capabilities(),
    )

    assert selected == {"tier": "standard", **primary}


@pytest.mark.parametrize(
    "primary",
    [
        _explicit_runtime_profile("primary", provider="unavailable-provider"),
        _explicit_runtime_profile("not-advertised"),
        _explicit_runtime_profile("primary", thinking="max"),
        _explicit_runtime_profile("primary", mode="sandbox"),
        _explicit_runtime_profile("primary", features={"missing": True}),
    ],
)
def test_runtime_profile_fallback_selects_only_for_typed_unavailability(primary):
    config = core.default_config()
    fallback = _explicit_runtime_profile("fallback")
    config["tiers"]["heavy"] = {**primary, "fallback": fallback}

    selected = core.resolve_runtime(
        config,
        repository="owner/repo",
        issue={"difficulty": "heavy"},
        coordinator_runtime=_fallback_coordinator_runtime(),
        capabilities=_fallback_capabilities(),
    )

    assert selected == {"tier": "heavy", **fallback}
    assert "fallback" not in selected


def test_runtime_profile_fallback_both_unavailable_is_typed():
    config = core.default_config()
    config["tiers"]["standard"] = {
        **_explicit_runtime_profile("primary-unavailable"),
        "fallback": _explicit_runtime_profile("fallback-unavailable"),
    }

    with pytest.raises(core.PolicyError) as unavailable:
        core.resolve_runtime(
            config,
            repository="owner/repo",
            issue={"difficulty": "standard"},
            coordinator_runtime=_fallback_coordinator_runtime(),
            capabilities=_fallback_capabilities(),
        )

    assert unavailable.value.code == "RUNTIME_MODEL_UNAVAILABLE"
    assert str(unavailable.value) == "model unavailable: primary-unavailable"
    assert unavailable.value.__notes__ == [
        "configured fallback unavailable "
        "(RUNTIME_MODEL_UNAVAILABLE: model unavailable: fallback-unavailable)"
    ]
    assert isinstance(unavailable.value.__cause__, core.PolicyError)
    assert unavailable.value.__cause__.code == "RUNTIME_MODEL_UNAVAILABLE"


def test_runtime_profile_fallback_configuration_error_never_selects_fallback():
    config = core.default_config()
    config["tiers"]["standard"] = {
        "provider": "codex",
        "settings": {
            "model": "primary",
            "modeId": "full-access",
            "features": {},
        },
        "fallback": _explicit_runtime_profile("fallback"),
    }

    with pytest.raises(core.PolicyError) as invalid:
        core.resolve_runtime(
            config,
            repository="owner/repo",
            issue={"difficulty": "standard"},
            coordinator_runtime=_fallback_coordinator_runtime(),
            capabilities=_fallback_capabilities(),
        )

    assert invalid.value.code == "RUNTIME_THINKING_MISSING"


def test_runtime_profile_fallback_repository_override_is_preserved():
    config = core.default_config()
    global_fallback = _explicit_runtime_profile("global-fallback")
    repository_fallback = _explicit_runtime_profile("repository-fallback")
    config["tiers"]["standard"]["fallback"] = global_fallback
    config["repositories"]["owner/repo"] = {
        "tiers": {
            "standard": {
                **_explicit_runtime_profile("repository-primary"),
                "fallback": repository_fallback,
            }
        }
    }

    request = core.resolve_runtime_request(
        config,
        repository="owner/repo",
        issue={"difficulty": "standard"},
        coordinator_runtime=_fallback_coordinator_runtime(),
    )

    assert request["settings"]["model"] == "repository-primary"
    assert request["fallback"] == repository_fallback
    assert request["fallback"] is not repository_fallback


def test_runtime_profile_fallback_worker_tier_request_preserves_identity():
    config = core.default_config()
    fallback = _explicit_runtime_profile("worker-fallback")
    config["tiers"]["frontier"]["fallback"] = fallback

    request = core.resolve_runtime_request(
        config,
        repository="owner/repo",
        issue={"difficulty": "frontier"},
        coordinator_runtime=_fallback_coordinator_runtime(),
    )

    assert request["tier"] == "frontier"
    assert request["fallback"] == fallback
    assert set(request["fallback"]) == {"provider", "settings"}


def test_runtime_profile_fallback_managed_role_request_and_selection():
    config = core.default_config()
    fallback = _explicit_runtime_profile("fallback")
    config["role_profiles"]["coordinator_auto"] = {
        **_explicit_runtime_profile("not-advertised"),
        "fallback": fallback,
    }

    request = core.resolve_role_runtime_request(
        config,
        repository="owner/repo",
        role="coordinator_auto",
    )
    selected = core.resolve_role_runtime(
        config,
        repository="owner/repo",
        role="coordinator_auto",
        coordinator_runtime=_fallback_coordinator_runtime(),
        capabilities=_fallback_capabilities(),
    )

    assert request == {
        "role": "coordinator_auto",
        **_explicit_runtime_profile("not-advertised"),
        "fallback": fallback,
    }
    assert selected == {"role": "coordinator_auto", **fallback}


@pytest.mark.parametrize(
    "fallback",
    [
        {
            **_explicit_runtime_profile("lower"),
            "tier": "light",
        },
        {
            **_explicit_runtime_profile("recursive"),
            "fallback": _explicit_runtime_profile("second-fallback"),
        },
    ],
)
def test_runtime_profile_fallback_rejects_lower_or_recursive_shape(fallback):
    config = core.default_config()
    config["tiers"]["heavy"] = {
        **_explicit_runtime_profile("primary"),
        "fallback": fallback,
    }

    with pytest.raises(core.PolicyError) as invalid:
        core.validate_config(config)

    assert invalid.value.code == "RUNTIME_PROFILE_FALLBACK_INVALID"
