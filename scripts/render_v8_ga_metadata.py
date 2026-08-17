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
from types import SimpleNamespace
import tempfile
from typing import Sequence

from scripts.verify_v8_ga_release import (
    DYNAMIC_METADATA_FIELDS,
    ReleaseGateError,
    _strict_canonical_json_loads,
    canonical_json_bytes,
    write_ga_release_record,
)


_SHA = re.compile(r"[0-9a-f]{40}\Z")


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


def _publication_root(staged: Mapping[Path, Path]) -> Path:
    try:
        return Path(
            os.path.commonpath(str(target.parent) for target in staged)
        ).resolve()
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
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ReleaseGateError("GA_METADATA_PUBLICATION_RECOVERY_FAILED") from error


def _journal_entries(
    journal: Mapping[str, object], output_root: Path
) -> tuple[tuple[Path, Path | None], ...]:
    if journal.get("schema") != "gwo-v8-ga-publication.v1":
        raise ReleaseGateError("GA_METADATA_PUBLICATION_RECOVERY_FAILED")
    entries = journal.get("entries")
    if not isinstance(entries, list):
        raise ReleaseGateError("GA_METADATA_PUBLICATION_RECOVERY_FAILED")
    root = output_root.resolve()
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
        target = Path(target_value).resolve()
        backup = Path(backup_value).resolve() if backup_value is not None else None
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
        Path(entry["staged"]).resolve()
        for entry in journal.get("entries", [])
        if isinstance(entry, Mapping) and isinstance(entry.get("staged"), str)
    ]
    if staged_paths:
        staging_root = Path(os.path.commonpath(tuple(map(str, staged_paths))))
        if staging_root.is_relative_to(output_root.resolve()):
            shutil.rmtree(staging_root, ignore_errors=True)


def _recover_metadata_publication(output_root: Path) -> None:
    journal_path = _publication_journal_path(output_root)
    if not journal_path.exists():
        return
    journal = _read_publication_journal(journal_path)
    _rollback_publication(output_root, journal)


def _publish_staged_documents(staged: Mapping[Path, Path]) -> None:
    output_root = _publication_root(staged)
    _recover_metadata_publication(output_root)
    journal_path = _publication_journal_path(output_root)
    entries: list[dict[str, str | None]] = []
    expected_bytes: dict[Path, bytes] = {}
    staging_root = Path(
        os.path.commonpath(tuple(map(str, staged.values())))
    ).resolve()
    backup_root = staging_root / ".backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    for index, (target, temporary) in enumerate(
        sorted(staged.items(), key=lambda item: str(item[0]))
    ):
        target = target.resolve()
        temporary = temporary.resolve()
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
    acceptance: Mapping[str, object],
    named_admission: Mapping[str, object],
    default_writer: Mapping[str, object],
) -> tuple[Path, Path, Path]:
    evidence_base_sha = _require_sha("evidence_base", evidence_base_sha)
    for name, value in (
        ("tickets", tickets),
        ("acceptance", acceptance),
        ("named_admission", named_admission),
        ("default_writer", default_writer),
    ):
        if not isinstance(value, Mapping):
            raise ReleaseGateError(
                "GA_METADATA_INPUT_INVALID", f"{name} is not an object"
            )
        _reject_dynamic_metadata(value, name)
        _validate_metadata_json(value, name)
    static_tickets = tickets
    static_acceptance = acceptance
    static_named_admission = named_admission
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
        named_receipt_digest = static_named_admission["receipt_digest"]
        default_receipt_digest = static_default_writer["receipt_digest"]
    except KeyError as error:
        raise ReleaseGateError("GA_METADATA_INPUT_INVALID") from error
    for value in (canary_receipt_digest, named_receipt_digest, default_receipt_digest):
        if type(value) is not str or not value.strip():
            raise ReleaseGateError("GA_METADATA_INPUT_INVALID")
    canary_target_sha = _require_sha("canary_target", canary_target_value)
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
        "evidence_base_sha": evidence_base_sha,
        "canary_target_sha": canary_target_sha,
        "ticket_manifest": static_tickets,
        "canary_receipt_digest": canary_receipt_digest,
        "named_admission_receipt_digest": named_receipt_digest,
        "default_writer_receipt_digest": default_receipt_digest,
        "activation_id": activation_id,
        "writer_generation": writer_generation,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    _recover_metadata_publication(output_root)
    changelog = output_root / "CHANGELOG.md"
    entry = (
        "## 8.0.0\n\n"
        f"- Accepted root Canary receipt `{common['canary_receipt_digest']}`.\n"
        f"- Evidence base `{evidence_base_sha}` and Canary target `"
        f"{canary_target_sha}` were read back.\n"
        "- Final tag-candidate SHA and exact CI are verified by the pre-tag "
        "receipt after this metadata commit is merged.\n"
    )
    previous = changelog.read_text(encoding="utf-8") if changelog.exists() else ""
    if "## 8.0.0" in previous:
        raise ReleaseGateError("GA_CHANGELOG_VERSION_ALREADY_PRESENT")
    if previous.startswith("# Changelog"):
        previous = previous[len("# Changelog") :].lstrip("\n")
    changelog_content = (
        "# Changelog\n\n" + entry + ("\n" + previous if previous else "")
    )
    acceptance_doc = output_root / "docs/e2e/gwo-v8-root-canary.md"
    release_note = output_root / "docs/releases/v8.0.0.md"
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
                    "tag_and_ci_source": "pre-tag ReleaseGateReceipt",
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
    acceptance: Mapping[str, object],
    named_admission: Mapping[str, object],
    default_writer: Mapping[str, object],
) -> Path:
    for name, value in (
        ("acceptance", acceptance),
        ("named_admission", named_admission),
        ("default_writer", default_writer),
    ):
        if not isinstance(value, Mapping):
            raise ReleaseGateError("GA_METADATA_INPUT_INVALID", f"{name} is not an object")
        _reject_dynamic_metadata(value, name)
        _validate_metadata_json(value, name)
    _renderer_identity_context(acceptance, named_admission, default_writer)
    fixture = SimpleNamespace(
        version="8.0.0",
        repository=str(acceptance["repository"]),
        evidence_base_sha=_require_sha("evidence_base", evidence_base_sha),
        canary_target_sha=_require_sha(
            "canary_target", str(acceptance["canary_target_sha"])
        ),
        canary_receipt_digest=str(acceptance["receipt_digest"]),
        activation_receipt_digest=str(named_admission["receipt_digest"]),
        default_writer_receipt_digest=str(default_writer["receipt_digest"]),
        campaign_key=str(acceptance["campaign_key"]),
        activation_id=str(default_writer["activation_id"]),
        writer_generation=str(default_writer["writer_generation"]),
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
    parser.add_argument("--acceptance", type=Path, required=True)
    parser.add_argument("--named-admission", type=Path, required=True)
    parser.add_argument("--default-writer", type=Path, required=True)
    parser.add_argument("--release-record", type=Path)
    args = parser.parse_args(argv)
    inputs = {
        "evidence_base_sha": args.evidence_base_sha,
        "tickets": _read_json(args.tickets),
        "acceptance": _read_json(args.acceptance),
        "named_admission": _read_json(args.named_admission),
        "default_writer": _read_json(args.default_writer),
    }
    render_ga_documents(
        args.root,
        evidence_base_sha=inputs["evidence_base_sha"],
        tickets=inputs["tickets"],
        acceptance=inputs["acceptance"],
        named_admission=inputs["named_admission"],
        default_writer=inputs["default_writer"],
    )
    if args.release_record is not None:
        write_live_release_record(
            args.release_record,
            evidence_base_sha=inputs["evidence_base_sha"],
            acceptance=inputs["acceptance"],
            named_admission=inputs["named_admission"],
            default_writer=inputs["default_writer"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(render_main())
