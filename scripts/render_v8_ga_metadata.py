#!/usr/bin/env python3
"""Render static V8 GA evidence and the static release record."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import shutil
import stat
from types import SimpleNamespace
import tempfile
from typing import Sequence

from scripts.verify_v8_ga_release import (
    DYNAMIC_METADATA_FIELDS,
    ReleaseGateError,
    digest_value,
    _reject_local_hosted_fields,
    _strict_canonical_json_loads,
    canonical_json_bytes,
    write_ga_release_record,
)


_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PRODUCTION_WRITER_GENERATION = "v8-generation-1"
_METADATA_BRIDGE_SCHEMA = "gwo-v8-ga-metadata-bridge.v1"


def _require_sha(name: str, value: str) -> str:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        raise ReleaseGateError(f"GA_{name.upper()}_SHA_INVALID")
    return value


_STATIC_SHA_KEYS = frozenset({"evidence_base_sha", "canary_target_sha"})
_STATIC_RECEIPT_KEYS = frozenset(
    {
        "acceptance_receipt_digest",
        "activation_receipt_digest",
        "canary_receipt_digest",
        "default_writer_receipt_digest",
        "named_admission_receipt_digest",
        "readback_digest",
        "receipt_digest",
    }
)
_DYNAMIC_KEY_ALIASES = frozenset(
    {
        "ci",
        "ci_head",
        "ci_run",
        "ci_run_id",
        "ci_status",
        "commit",
        "commit_hash",
        "commit_sha",
        "conclusion",
        "dynamic_pass_summary",
        "final_commit",
        "final_metadata_commit",
        "head",
        "head_sha",
        "main",
        "main_commit",
        "metadata_commit",
        "pytest_count",
        "run_id",
        "run_number",
        "sha",
        "tag_candidate",
        "tag_sha",
        "workflow_run",
        "workflow_run_id",
    }
)

_EVIDENCE_BRIDGE_FIELDS = frozenset(
    {
        "bridge_digest",
        "default_writer",
        "local_root_canary",
        "production_activation",
        "production_canary",
        "release_subject",
        "repository",
        "schema",
    }
)
_EVIDENCE_LOCAL_FIELDS = frozenset(
    {
        "acceptance_mode",
        "activation_id",
        "campaign_key",
        "canary_target_sha",
        "producer_receipt_digest",
        "repository",
        "schema",
        "source_file",
        "source_file_sha256",
        "writer_generation",
    }
)
_EVIDENCE_CANARY_FIELDS = frozenset(
    {
        "evidence_ref_count",
        "manifest_ref",
        "package_digest",
        "package_repository",
        "readback_receipt_digest",
        "source_file",
        "source_file_sha256",
    }
)
_EVIDENCE_ACTIVATION_FIELDS = frozenset(
    {
        "activation_id",
        "previous_writer_generation",
        "readback_receipt_digest",
        "run_id",
        "source_file",
        "source_file_sha256",
        "transition_record_id",
        "writer_generation",
    }
)
_EVIDENCE_DEFAULT_FIELDS = frozenset(
    {
        "activation_id",
        "legacy_writer_fence_stopped",
        "readback_receipt_digest",
        "record_id",
        "source_file",
        "source_file_sha256",
        "writer_generation",
    }
)
_EVIDENCE_RELEASE_SUBJECT_FIELDS = frozenset(
    {"merged_main_sha", "merged_main_tree", "release_subject_digest"}
)


def _normalized_key(key: object) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key))
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()


def _is_dynamic_key(key: object) -> bool:
    normalized = _normalized_key(key)
    if normalized in _STATIC_SHA_KEYS or normalized in _STATIC_RECEIPT_KEYS:
        return False
    if normalized in DYNAMIC_METADATA_FIELDS or normalized in _DYNAMIC_KEY_ALIASES:
        return True
    if normalized.startswith("ci_") or normalized.startswith("pytest_"):
        return True
    if normalized.endswith("_sha") or normalized.endswith("_commit"):
        return True
    return normalized.endswith("_run_id") or normalized.endswith("_run_number")


def _reject_dynamic_metadata(value: object, path: str = "input") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _is_dynamic_key(key):
                raise ReleaseGateError(
                    "GA_DYNAMIC_METADATA_INPUT",
                    f"dynamic SHA/CI field at {path}.{key}",
                )
            _reject_dynamic_metadata(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_dynamic_metadata(child, f"{path}[{index}]")


def _validate_metadata_json(value: object, path: str) -> None:
    try:
        canonical_json_bytes(value)
    except (TypeError, UnicodeError, ValueError) as error:
        raise ReleaseGateError(
            "GA_METADATA_INPUT_INVALID", f"invalid JSON value at {path}"
        ) from error


_IDENTITY_ALIASES = {
    "repository": frozenset({"repository", "repo"}),
    "campaign_key": frozenset({"campaign_key", "campaign", "campaign_id"}),
    "activation_id": frozenset({"activation_id", "activation"}),
    "writer_generation": frozenset({"writer_generation", "writer"}),
}


def _assert_input_identities(
    value: object,
    expected: Mapping[str, str],
    *,
    allow_none: frozenset[str] = frozenset(),
) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = _normalized_key(key)
            for field, aliases in _IDENTITY_ALIASES.items():
                if normalized not in aliases or field not in expected:
                    continue
                if child is None and field in allow_none:
                    continue
                if type(child) is not str or child != expected[field]:
                    raise ReleaseGateError("GA_METADATA_IDENTITY_MISMATCH")
            _assert_input_identities(child, expected, allow_none=allow_none)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_input_identities(child, expected, allow_none=allow_none)


def _renderer_identity_context(
    acceptance: Mapping[str, object],
    named_admission: Mapping[str, object],
    default_writer: Mapping[str, object],
) -> tuple[str, str, str, str]:
    try:
        repository = acceptance["repository"]
        campaign_key = acceptance["campaign_key"]
        activation_id = default_writer["activation_id"]
        writer_generation = default_writer["writer_generation"]
    except KeyError as error:
        raise ReleaseGateError("GA_METADATA_INPUT_INVALID") from error
    if any(
        type(value) is not str or not value.strip()
        for value in (repository, campaign_key, activation_id, writer_generation)
    ):
        raise ReleaseGateError("GA_METADATA_INPUT_INVALID")
    canary_receipt_digest = acceptance.get("receipt_digest")
    if type(canary_receipt_digest) is not str or not canary_receipt_digest.strip():
        raise ReleaseGateError("GA_METADATA_INPUT_INVALID")
    expected = {
        "repository": repository,
        "campaign_key": campaign_key,
        "activation_id": activation_id,
        "writer_generation": writer_generation,
    }
    _assert_input_identities(acceptance, expected)
    _assert_input_identities(named_admission, expected)
    _assert_input_identities(
        default_writer,
        expected,
        allow_none=frozenset({"campaign_key"}),
    )
    for value in (named_admission, default_writer):
        receipt_digest = value.get("receipt_digest")
        if type(receipt_digest) is not str or not receipt_digest.strip():
            raise ReleaseGateError("GA_METADATA_INPUT_INVALID")
        acceptance_digest = value.get("acceptance_receipt_digest")
        if acceptance_digest is not None and (
            type(acceptance_digest) is not str
            or acceptance_digest != canary_receipt_digest
        ):
            raise ReleaseGateError("GA_METADATA_IDENTITY_MISMATCH")
    return repository, campaign_key, activation_id, writer_generation


def _bridge_text(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ReleaseGateError("GA_METADATA_BRIDGE_INPUT_INVALID", field)
    return value


def _bridge_sha256(value: object, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ReleaseGateError("GA_METADATA_BRIDGE_DIGEST_INVALID", field)
    return value


def _bridge_fields(value: Mapping[str, object], allowed: frozenset[str]) -> None:
    if set(value) - allowed:
        raise ReleaseGateError("GA_METADATA_BRIDGE_FIELDS_INVALID")


def _renderer_evidence_bridge_context(
    evidence_bridge: Mapping[str, object],
) -> dict[str, object]:
    _bridge_fields(evidence_bridge, _EVIDENCE_BRIDGE_FIELDS)
    if evidence_bridge.get("schema") != "gwo-v8-ga-evidence-bridge.v1":
        raise ReleaseGateError("GA_METADATA_BRIDGE_SCHEMA_INVALID")
    bridge_digest = _bridge_sha256(
        evidence_bridge.get("bridge_digest"), "GA evidence bridge digest"
    )
    payload = dict(evidence_bridge)
    payload.pop("bridge_digest", None)
    if digest_value(payload) != bridge_digest:
        raise ReleaseGateError("GA_METADATA_BRIDGE_DIGEST_MISMATCH")

    repository = _bridge_text(
        evidence_bridge.get("repository"), "GA evidence bridge repository"
    )
    local_root = evidence_bridge.get("local_root_canary")
    production_canary = evidence_bridge.get("production_canary")
    production_activation = evidence_bridge.get("production_activation")
    default_writer = evidence_bridge.get("default_writer")
    release_subject = evidence_bridge.get("release_subject")
    if not all(
        isinstance(value, Mapping)
        for value in (
            local_root,
            production_canary,
            production_activation,
            default_writer,
            release_subject,
        )
    ):
        raise ReleaseGateError("GA_METADATA_BRIDGE_INPUT_INVALID")

    _bridge_fields(local_root, _EVIDENCE_LOCAL_FIELDS)
    if (
        local_root.get("schema") != "gwo-v8-root-canary-acceptance.v2"
        or local_root.get("acceptance_mode") != "local-only-v1"
        or local_root.get("repository") != repository
    ):
        raise ReleaseGateError("GA_METADATA_BRIDGE_LOCAL_ROOT_INVALID")
    local_campaign = _bridge_text(
        local_root.get("campaign_key"), "GA evidence local campaign"
    )
    local_activation_id = _bridge_text(
        local_root.get("activation_id"), "GA evidence local activation"
    )
    local_writer_generation = _bridge_text(
        local_root.get("writer_generation"), "GA evidence local writer"
    )
    local_target_sha = _require_sha(
        "canary_target", local_root.get("canary_target_sha")
    )
    local_receipt_digest = _bridge_sha256(
        local_root.get("producer_receipt_digest"),
        "GA evidence local receipt digest",
    )
    _bridge_text(local_root.get("source_file"), "GA evidence local source file")
    _bridge_sha256(
        local_root.get("source_file_sha256"), "GA evidence local source digest"
    )

    _bridge_fields(production_canary, _EVIDENCE_CANARY_FIELDS)
    package_repository = _bridge_text(
        production_canary.get("package_repository"),
        "GA evidence package repository",
    )
    if package_repository != "NOirBRight/gwo-v8-canary":
        raise ReleaseGateError("GA_METADATA_BRIDGE_IDENTITY_MISMATCH")
    package_digest = _bridge_sha256(
        production_canary.get("package_digest"),
        "GA evidence package digest",
    )
    _bridge_text(
        production_canary.get("manifest_ref"), "GA evidence package manifest ref"
    )
    _bridge_sha256(
        production_canary.get("readback_receipt_digest"),
        "GA evidence package receipt digest",
    )
    evidence_ref_count = production_canary.get("evidence_ref_count")
    if type(evidence_ref_count) is not int or evidence_ref_count < 1:
        raise ReleaseGateError("GA_METADATA_BRIDGE_INPUT_INVALID")
    _bridge_text(production_canary.get("source_file"), "GA evidence package source")
    _bridge_sha256(
        production_canary.get("source_file_sha256"),
        "GA evidence package source digest",
    )

    _bridge_fields(production_activation, _EVIDENCE_ACTIVATION_FIELDS)
    production_activation_id = _bridge_text(
        production_activation.get("activation_id"),
        "GA evidence production activation",
    )
    production_writer_generation = _bridge_text(
        production_activation.get("writer_generation"),
        "GA evidence production writer",
    )
    if production_writer_generation != _PRODUCTION_WRITER_GENERATION:
        raise ReleaseGateError("GA_METADATA_BRIDGE_WRITER_GENERATION_INVALID")
    production_activation_receipt_digest = _bridge_sha256(
        production_activation.get("readback_receipt_digest"),
        "GA evidence activation receipt digest",
    )
    transition_record_id = _bridge_text(
        production_activation.get("transition_record_id"),
        "GA evidence transition record",
    )
    _bridge_text(production_activation.get("run_id"), "GA evidence activation run")
    _bridge_text(
        production_activation.get("previous_writer_generation"),
        "GA evidence previous writer",
    )
    _bridge_text(
        production_activation.get("source_file"), "GA evidence activation source"
    )
    _bridge_sha256(
        production_activation.get("source_file_sha256"),
        "GA evidence activation source digest",
    )

    _bridge_fields(default_writer, _EVIDENCE_DEFAULT_FIELDS)
    if default_writer.get("legacy_writer_fence_stopped") is not True:
        raise ReleaseGateError("GA_METADATA_BRIDGE_DEFAULT_WRITER_INVALID")
    if (
        _bridge_text(
            default_writer.get("activation_id"), "GA evidence default activation"
        )
        != production_activation_id
    ):
        raise ReleaseGateError("GA_METADATA_BRIDGE_IDENTITY_MISMATCH")
    if (
        _bridge_text(
            default_writer.get("writer_generation"), "GA evidence default writer"
        )
        != production_writer_generation
    ):
        raise ReleaseGateError("GA_METADATA_BRIDGE_WRITER_GENERATION_INVALID")
    if (
        _bridge_text(default_writer.get("record_id"), "GA evidence default record")
        != transition_record_id
    ):
        raise ReleaseGateError("GA_METADATA_BRIDGE_IDENTITY_MISMATCH")
    default_receipt_digest = _bridge_sha256(
        default_writer.get("readback_receipt_digest"),
        "GA evidence default readback digest",
    )
    _bridge_text(default_writer.get("source_file"), "GA evidence default source")
    _bridge_sha256(
        default_writer.get("source_file_sha256"),
        "GA evidence default source digest",
    )
    if local_activation_id == production_activation_id or (
        local_writer_generation == production_writer_generation
    ):
        raise ReleaseGateError("GA_METADATA_BRIDGE_IDENTITY_COLLISION")

    _bridge_fields(release_subject, _EVIDENCE_RELEASE_SUBJECT_FIELDS)
    _require_sha("merged_main", release_subject.get("merged_main_sha"))
    _require_sha("merged_main_tree", release_subject.get("merged_main_tree"))
    _bridge_sha256(
        release_subject.get("release_subject_digest"),
        "GA evidence release subject digest",
    )

    links = {
        "activation_id": production_activation_id,
        "default_writer_readback_receipt_digest": default_receipt_digest,
        "local_root_canary_receipt_digest": local_receipt_digest,
        "production_activation_readback_receipt_digest": production_activation_receipt_digest,
        "production_canary_package_digest": package_digest,
        "production_canary_repository": package_repository,
        "transition_record_id": transition_record_id,
        "writer_generation": production_writer_generation,
    }
    normalized_acceptance = {
        "acceptance_mode": local_root["acceptance_mode"],
        "activation_id": local_activation_id,
        "campaign_key": local_campaign,
        "canary_target_sha": local_target_sha,
        "receipt_digest": local_receipt_digest,
        "repository": repository,
        "schema": local_root["schema"],
        "writer_generation": local_writer_generation,
    }
    normalized_default_writer = {
        "activation_id": production_activation_id,
        "campaign_key": None,
        "mode": "default_v8",
        "receipt_digest": default_receipt_digest,
        "repository": repository,
        "writer_generation": production_writer_generation,
    }
    normalized_named_admission = {
        "activation_id": production_activation_id,
        "receipt_digest": production_activation_receipt_digest,
        "repository": repository,
        "writer_generation": production_writer_generation,
    }
    return {
        "acceptance": normalized_acceptance,
        "bridge": dict(evidence_bridge),
        "default_writer": normalized_default_writer,
        "links": links,
        "named_admission": normalized_named_admission,
        "repository": repository,
        "campaign_key": local_campaign,
        "activation_id": production_activation_id,
        "writer_generation": production_writer_generation,
        "canary_target_sha": local_target_sha,
        "canary_receipt_digest": local_receipt_digest,
        "named_receipt_digest": production_activation_receipt_digest,
        "default_receipt_digest": default_receipt_digest,
    }


def _write_markdown_json(path: Path, title: str, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fence = "`" * 3
    path.write_text(
        f"# {title}\n\n"
        + fence
        + "json\n"
        + json.dumps(payload, allow_nan=False, sort_keys=True, indent=2)
        + "\n"
        + fence
        + "\n",
        encoding="utf-8",
    )


_PUBLICATION_JOURNAL_NAME = ".gwo-v8-ga-metadata-publication.json"


def _fsync_file(path: Path) -> None:
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(str(path), flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


_REPARSE_POINT_ATTRIBUTE = 0x0400


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _reject_reparse_path(path: Path) -> None:
    current = _absolute_without_resolving(path)
    while True:
        try:
            observed = os.lstat(current)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise ReleaseGateError("GA_METADATA_PUBLICATION_TARGET_INVALID") from error
        else:
            if stat.S_ISLNK(observed.st_mode) or bool(
                getattr(observed, "st_file_attributes", 0) & _REPARSE_POINT_ATTRIBUTE
            ):
                raise ReleaseGateError("GA_METADATA_PUBLICATION_TARGET_INVALID")
        parent = current.parent
        if parent == current:
            return
        current = parent


def _validate_publication_targets(staged: Mapping[Path, Path]) -> None:
    if not staged:
        raise ReleaseGateError("GA_METADATA_PUBLICATION_INVALID")
    for target, temporary in staged.items():
        _reject_reparse_path(target)
        _reject_reparse_path(temporary)


def _publication_root(staged: Mapping[Path, Path]) -> Path:
    try:
        return _absolute_without_resolving(
            Path(os.path.commonpath(str(target.parent) for target in staged))
        )
    except (OSError, ValueError) as error:
        raise ReleaseGateError("GA_METADATA_PUBLICATION_INVALID") from error


def _publication_journal_path(output_root: Path) -> Path:
    return output_root / _PUBLICATION_JOURNAL_NAME


def _read_publication_journal(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
        if canonical_json_bytes(value) != raw or not isinstance(value, dict):
            raise ValueError("publication journal is not canonical")
        return value
    except (
        OSError,
        UnicodeError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise ReleaseGateError("GA_METADATA_PUBLICATION_RECOVERY_FAILED") from error


def _journal_entries(
    journal: Mapping[str, object], output_root: Path
) -> tuple[tuple[Path, Path | None], ...]:
    if journal.get("schema") != "gwo-v8-ga-publication.v1":
        raise ReleaseGateError("GA_METADATA_PUBLICATION_RECOVERY_FAILED")
    entries = journal.get("entries")
    if not isinstance(entries, list):
        raise ReleaseGateError("GA_METADATA_PUBLICATION_RECOVERY_FAILED")
    root = _absolute_without_resolving(output_root)
    result: list[tuple[Path, Path | None]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ReleaseGateError("GA_METADATA_PUBLICATION_RECOVERY_FAILED")
        target_value = entry.get("target")
        backup_value = entry.get("backup")
        if not isinstance(target_value, str) or (
            backup_value is not None and not isinstance(backup_value, str)
        ):
            raise ReleaseGateError("GA_METADATA_PUBLICATION_RECOVERY_FAILED")
        target = _absolute_without_resolving(Path(target_value))
        backup = (
            _absolute_without_resolving(Path(backup_value))
            if backup_value is not None
            else None
        )
        _reject_reparse_path(target)
        if backup is not None:
            _reject_reparse_path(backup)
        if not target.is_relative_to(root) or (
            backup is not None and not backup.is_relative_to(root)
        ):
            raise ReleaseGateError("GA_METADATA_PUBLICATION_RECOVERY_FAILED")
        result.append((target, backup))
    if tuple(target for target, _backup in result) != tuple(
        sorted((target for target, _backup in result), key=str)
    ):
        raise ReleaseGateError("GA_METADATA_PUBLICATION_RECOVERY_FAILED")
    return tuple(result)


def _remove_publication_journal(output_root: Path) -> None:
    _publication_journal_path(output_root).unlink(missing_ok=True)
    _fsync_directory(output_root)


def _rollback_publication(output_root: Path, journal: Mapping[str, object]) -> None:
    entries = _journal_entries(journal, output_root)
    for target, backup in reversed(entries):
        if backup is None:
            target.unlink(missing_ok=True)
        else:
            if not backup.is_file():
                raise ReleaseGateError("GA_METADATA_PUBLICATION_RECOVERY_FAILED")
            os.replace(backup, target)
        _fsync_directory(target.parent)
    _remove_publication_journal(output_root)
    staged_paths = [
        _absolute_without_resolving(Path(entry["staged"]))
        for entry in journal.get("entries", [])
        if isinstance(entry, Mapping) and isinstance(entry.get("staged"), str)
    ]
    if staged_paths:
        staging_root = Path(os.path.commonpath(tuple(map(str, staged_paths))))
        if staging_root.is_relative_to(_absolute_without_resolving(output_root)):
            shutil.rmtree(staging_root, ignore_errors=True)


def _recover_metadata_publication(output_root: Path) -> None:
    journal_path = _publication_journal_path(output_root)
    _reject_reparse_path(output_root)
    _reject_reparse_path(journal_path)
    if not journal_path.exists():
        return
    journal = _read_publication_journal(journal_path)
    _rollback_publication(output_root, journal)


def _publish_staged_documents(staged: Mapping[Path, Path]) -> None:
    _validate_publication_targets(staged)
    output_root = _publication_root(staged)
    _reject_reparse_path(output_root)
    _recover_metadata_publication(output_root)
    journal_path = _publication_journal_path(output_root)
    entries: list[dict[str, str | None]] = []
    expected_bytes: dict[Path, bytes] = {}
    staging_root = _absolute_without_resolving(
        Path(os.path.commonpath(tuple(map(str, staged.values()))))
    )
    backup_root = staging_root / ".backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    for index, (target, temporary) in enumerate(
        sorted(staged.items(), key=lambda item: str(item[0]))
    ):
        target = _absolute_without_resolving(target)
        temporary = _absolute_without_resolving(temporary)
        _reject_reparse_path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not temporary.is_file():
            raise ReleaseGateError("GA_METADATA_PUBLICATION_INVALID")
        expected_bytes[target] = temporary.read_bytes()
        _fsync_file(temporary)
        _fsync_directory(temporary.parent)
        backup: Path | None = None
        if target.exists():
            backup = backup_root / f"{index}.bak"
            backup.write_bytes(target.read_bytes())
            _fsync_file(backup)
        entries.append(
            {
                "target": str(target),
                "staged": str(temporary),
                "backup": None if backup is None else str(backup),
            }
        )
    journal = {"schema": "gwo-v8-ga-publication.v1", "entries": entries}
    journal_temporary = staging_root / ".publication-journal"
    journal_temporary.write_bytes(canonical_json_bytes(journal))
    _fsync_file(journal_temporary)
    os.replace(journal_temporary, journal_path)
    _fsync_directory(output_root)
    try:
        for entry in entries:
            os.replace(entry["staged"], entry["target"])
            target = Path(entry["target"])
            _fsync_file(target)
            if target.read_bytes() != expected_bytes[target]:
                raise ReleaseGateError("GA_METADATA_PUBLICATION_READBACK_FAILED")
            _fsync_directory(target.parent)
        _remove_publication_journal(output_root)
    except BaseException:
        _rollback_publication(output_root, journal)
        raise


def _write_staged_changelog(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def render_ga_documents(
    output_root: Path,
    *,
    evidence_base_sha: str,
    tickets: Mapping[str, object],
    acceptance: Mapping[str, object] | None = None,
    named_admission: Mapping[str, object] | None = None,
    default_writer: Mapping[str, object] | None = None,
    evidence_bridge: Mapping[str, object] | None = None,
) -> tuple[Path, Path, Path]:
    evidence_base_sha = _require_sha("evidence_base", evidence_base_sha)
    evidence_context: dict[str, object] | None = None
    if evidence_bridge is not None:
        if not isinstance(evidence_bridge, Mapping):
            raise ReleaseGateError("GA_METADATA_INPUT_INVALID")
        _validate_metadata_json(evidence_bridge, "evidence_bridge")
        evidence_context = _renderer_evidence_bridge_context(evidence_bridge)
        if acceptance is None:
            acceptance = evidence_context["acceptance"]
        if named_admission is None:
            named_admission = evidence_context["named_admission"]
        if default_writer is None:
            default_writer = evidence_context["default_writer"]
    static_named_admission = named_admission
    inputs: list[tuple[str, object | None]] = [
        ("tickets", tickets),
        ("acceptance", acceptance),
        ("named_admission", static_named_admission),
        ("default_writer", default_writer),
    ]
    for name, value in inputs:
        if value is None:
            if name == "named_admission" and evidence_context is not None:
                continue
            raise ReleaseGateError(
                "GA_METADATA_INPUT_INVALID", f"{name} is not an object"
            )
        if not isinstance(value, Mapping):
            raise ReleaseGateError(
                "GA_METADATA_INPUT_INVALID", f"{name} is not an object"
            )
        _reject_dynamic_metadata(value, name)
        if name != "tickets":
            _reject_local_hosted_fields(value)
        _validate_metadata_json(value, name)
    static_tickets = tickets
    static_acceptance = acceptance
    static_default_writer = default_writer
    if not all(
        isinstance(value, Mapping)
        for value in (
            static_tickets,
            static_acceptance,
            static_named_admission,
            static_default_writer,
        )
    ):
        raise ReleaseGateError("GA_METADATA_INPUT_INVALID")
    try:
        canary_receipt_digest = static_acceptance["receipt_digest"]
        canary_target_value = static_acceptance["canary_target_sha"]
        default_receipt_digest = static_default_writer["receipt_digest"]
    except KeyError as error:
        raise ReleaseGateError("GA_METADATA_INPUT_INVALID") from error
    if type(canary_receipt_digest) is not str or not canary_receipt_digest.strip():
        raise ReleaseGateError("GA_METADATA_INPUT_INVALID")
    if type(default_receipt_digest) is not str or not default_receipt_digest.strip():
        raise ReleaseGateError("GA_METADATA_INPUT_INVALID")
    canary_target_sha = _require_sha("canary_target", canary_target_value)
    bridge: dict[str, object] | None = None
    if evidence_context is not None:
        bridge = evidence_context["bridge"]
        repository = evidence_context["repository"]
        campaign_key = evidence_context["campaign_key"]
        activation_id = evidence_context["activation_id"]
        writer_generation = evidence_context["writer_generation"]
        canary_target_sha = evidence_context["canary_target_sha"]
        canary_receipt_digest = evidence_context["canary_receipt_digest"]
        named_receipt_digest = evidence_context["named_receipt_digest"]
        default_receipt_digest = evidence_context["default_receipt_digest"]
        expected_identity = {
            "repository": repository,
            "campaign_key": campaign_key,
            "activation_id": evidence_context["acceptance"]["activation_id"],
            "writer_generation": evidence_context["acceptance"]["writer_generation"],
        }
    else:
        if not isinstance(static_named_admission, Mapping):
            raise ReleaseGateError("GA_METADATA_INPUT_INVALID")
        try:
            named_receipt_digest = static_named_admission["receipt_digest"]
        except KeyError as error:
            raise ReleaseGateError("GA_METADATA_INPUT_INVALID") from error
        if type(named_receipt_digest) is not str or not named_receipt_digest.strip():
            raise ReleaseGateError("GA_METADATA_INPUT_INVALID")
        repository, campaign_key, activation_id, writer_generation = (
            _renderer_identity_context(
                static_acceptance,
                static_named_admission,
                static_default_writer,
            )
        )
        expected_identity = {
            "repository": repository,
            "campaign_key": campaign_key,
            "activation_id": activation_id,
            "writer_generation": writer_generation,
        }
    _assert_input_identities(static_tickets, expected_identity)
    common: dict[str, object] = {
        "repository": repository,
        "campaign_key": campaign_key,
        "verification_mode": "local-only-v1",
        "evidence_base_sha": evidence_base_sha,
        "canary_target_sha": canary_target_sha,
        "ticket_manifest": static_tickets,
        "canary_receipt_digest": canary_receipt_digest,
        "named_admission_receipt_digest": named_receipt_digest,
        "default_writer_receipt_digest": default_receipt_digest,
        "activation_id": activation_id,
        "writer_generation": writer_generation,
    }
    if bridge is not None:
        common["evidence_bridge"] = bridge
        if evidence_context is not None:
            common["evidence_bridge_links"] = evidence_context["links"]
            common["production_canary_package_digest"] = evidence_context["links"][
                "production_canary_package_digest"
            ]
    _reject_reparse_path(output_root)
    changelog = output_root / "CHANGELOG.md"
    acceptance_doc = output_root / "docs/e2e/gwo-v8-root-canary.md"
    release_note = output_root / "docs/releases/v8.0.0.md"
    output_root.mkdir(parents=True, exist_ok=True)
    for target in (changelog, acceptance_doc, release_note):
        _reject_reparse_path(target)
    _recover_metadata_publication(output_root)
    entry = (
        "## 8.0.0\n\n"
        f"- Accepted root Canary receipt `{common['canary_receipt_digest']}`.\n"
        f"- Evidence base `{evidence_base_sha}` and Canary target `"
        f"{canary_target_sha}` were read back.\n"
        "- Repository release verification is Local Verification Only "
        "(`local-only-v1`); the pre-tag receipt binds the exact subject "
        "SHA/tree and successful full pytest readback.\n"
        "- Product Hosted-CI delivery remains separate and is not satisfied "
        "by repository release verification.\n"
    )
    if bridge is not None:
        entry += (
            "- Local Root Canary evidence is explicitly bridged to the external "
            "Production Canary package, Production Activation, and the exact "
            "default-writer readback; their Campaign/activation identities are "
            "not treated as interchangeable.\n"
        )
    previous = changelog.read_text(encoding="utf-8") if changelog.exists() else ""
    if "## 8.0.0" in previous:
        raise ReleaseGateError("GA_CHANGELOG_VERSION_ALREADY_PRESENT")
    if previous.startswith("# Changelog"):
        previous = previous[len("# Changelog") :].lstrip("\n")
    changelog_content = (
        "# Changelog\n\n" + entry + ("\n" + previous if previous else "")
    )
    with tempfile.TemporaryDirectory(prefix=".ga-metadata-", dir=output_root) as raw:
        staging_root = Path(raw)
        staged: dict[Path, Path] = {}
        staged_changelog = staging_root / "CHANGELOG.md"
        _write_staged_changelog(staged_changelog, changelog_content)
        _fsync_file(staged_changelog)
        staged[changelog] = staged_changelog
        staged_acceptance = staging_root / "docs/e2e/gwo-v8-root-canary.md"
        _write_markdown_json(
            staged_acceptance,
            "GWO V8 Root Canary Evidence",
            common | {"acceptance": static_acceptance},
        )
        staged[acceptance_doc] = staged_acceptance
        staged_release_note = staging_root / "docs/releases/v8.0.0.md"
        _write_markdown_json(
            staged_release_note,
            "GWO V8.0.0",
            common
            | {
                "release": {
                    "version": "8.0.0",
                    "verification_mode": "local-only-v1",
                    "verification_source": "local verification manifest and Git readback",
                }
            },
        )
        staged[release_note] = staged_release_note
        _publish_staged_documents(staged)
    return changelog, acceptance_doc, release_note


def write_live_release_record(
    path: Path,
    *,
    evidence_base_sha: str,
    acceptance: Mapping[str, object] | None = None,
    named_admission: Mapping[str, object] | None = None,
    default_writer: Mapping[str, object] | None = None,
    evidence_bridge: Mapping[str, object] | None = None,
) -> Path:
    evidence_context: dict[str, object] | None = None
    if evidence_bridge is not None:
        if not isinstance(evidence_bridge, Mapping):
            raise ReleaseGateError("GA_METADATA_INPUT_INVALID")
        _validate_metadata_json(evidence_bridge, "evidence_bridge")
        evidence_context = _renderer_evidence_bridge_context(evidence_bridge)
        if acceptance is None:
            acceptance = evidence_context["acceptance"]
        if named_admission is None:
            named_admission = evidence_context["named_admission"]
        if default_writer is None:
            default_writer = evidence_context["default_writer"]
    static_named_admission = named_admission
    for name, value in (
        ("acceptance", acceptance),
        ("named_admission", static_named_admission),
        ("default_writer", default_writer),
    ):
        if value is None:
            raise ReleaseGateError(
                "GA_METADATA_INPUT_INVALID", f"{name} is not an object"
            )
        if not isinstance(value, Mapping):
            raise ReleaseGateError(
                "GA_METADATA_INPUT_INVALID", f"{name} is not an object"
            )
        _reject_dynamic_metadata(value, name)
        _reject_local_hosted_fields(value)
        _validate_metadata_json(value, name)
    if evidence_context is not None:
        repository = evidence_context["repository"]
        campaign_key = evidence_context["campaign_key"]
        activation_id = evidence_context["activation_id"]
        writer_generation = evidence_context["writer_generation"]
        canary_receipt_digest = evidence_context["canary_receipt_digest"]
        canary_target_sha = evidence_context["canary_target_sha"]
        activation_receipt_digest = evidence_context["named_receipt_digest"]
    else:
        repository, campaign_key, activation_id, writer_generation = (
            _renderer_identity_context(
                acceptance,
                static_named_admission,
                default_writer,
            )
        )
        canary_receipt_digest = str(acceptance["receipt_digest"])
        canary_target_sha = _require_sha(
            "canary_target", str(acceptance["canary_target_sha"])
        )
        activation_receipt_digest = str(static_named_admission["receipt_digest"])
    fixture = SimpleNamespace(
        version="8.0.0",
        repository=repository,
        evidence_base_sha=_require_sha("evidence_base", evidence_base_sha),
        canary_target_sha=canary_target_sha,
        canary_receipt_digest=canary_receipt_digest,
        activation_receipt_digest=activation_receipt_digest,
        default_writer_receipt_digest=str(default_writer["receipt_digest"]),
        campaign_key=campaign_key,
        activation_id=activation_id,
        writer_generation=writer_generation,
    )
    return write_ga_release_record(path, fixture)


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = _strict_canonical_json_loads(path.read_bytes())
    except (OSError, UnicodeError, ReleaseGateError) as error:
        raise ReleaseGateError("GA_METADATA_INPUT_INVALID") from error
    if not isinstance(value, dict):
        raise ReleaseGateError("GA_METADATA_INPUT_INVALID")
    return value


def render_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--evidence-base-sha", required=True)
    parser.add_argument("--tickets", type=Path, required=True)
    parser.add_argument("--acceptance", type=Path)
    parser.add_argument("--named-admission", type=Path)
    parser.add_argument("--evidence-bridge", type=Path)
    parser.add_argument("--default-writer", type=Path)
    parser.add_argument("--release-record", type=Path)
    args = parser.parse_args(argv)
    inputs = {
        "evidence_base_sha": args.evidence_base_sha,
        "tickets": _read_json(args.tickets),
        "acceptance": (
            _read_json(args.acceptance) if args.acceptance is not None else None
        ),
        "named_admission": (
            _read_json(args.named_admission)
            if args.named_admission is not None
            else None
        ),
        "evidence_bridge": (
            _read_json(args.evidence_bridge)
            if args.evidence_bridge is not None
            else None
        ),
        "default_writer": (
            _read_json(args.default_writer) if args.default_writer is not None else None
        ),
    }
    render_ga_documents(
        args.root,
        evidence_base_sha=inputs["evidence_base_sha"],
        tickets=inputs["tickets"],
        acceptance=inputs["acceptance"],
        named_admission=inputs["named_admission"],
        default_writer=inputs["default_writer"],
        evidence_bridge=inputs["evidence_bridge"],
    )
    if args.release_record is not None:
        write_live_release_record(
            args.release_record,
            evidence_base_sha=inputs["evidence_base_sha"],
            acceptance=inputs["acceptance"],
            named_admission=inputs["named_admission"],
            default_writer=inputs["default_writer"],
            evidence_bridge=inputs["evidence_bridge"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(render_main())
