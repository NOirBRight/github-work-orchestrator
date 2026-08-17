#!/usr/bin/env python3
"""Render static V8 GA evidence and the static release record."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import tempfile
from typing import Sequence

from scripts.verify_v8_ga_release import (
    DYNAMIC_METADATA_FIELDS,
    ReleaseGateError,
    write_ga_release_record,
)


_SHA = re.compile(r"[0-9a-f]{40}\Z")


def _require_sha(name: str, value: str) -> str:
    if _SHA.fullmatch(value) is None:
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


def _write_markdown_json(path: Path, title: str, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fence = "`" * 3
    path.write_text(
        f"# {title}\n\n"
        + fence
        + "json\n"
        + json.dumps(payload, sort_keys=True, indent=2)
        + "\n"
        + fence
        + "\n",
        encoding="utf-8",
    )


def _publish_staged_documents(
    staged: Mapping[Path, Path],
) -> None:
    originals: dict[Path, bytes | None] = {}
    published: list[Path] = []
    try:
        for target in staged:
            target.parent.mkdir(parents=True, exist_ok=True)
            originals[target] = target.read_bytes() if target.exists() else None
        for target, temporary in staged.items():
            os.replace(temporary, target)
            published.append(target)
    except BaseException:
        for target in reversed(published):
            original = originals[target]
            if original is None:
                target.unlink(missing_ok=True)
                continue
            restore = target.with_name(f".{target.name}.restore")
            restore.write_bytes(original)
            os.replace(restore, target)
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
    canary_target_sha = _require_sha(
        "canary_target", str(static_acceptance["canary_target_sha"])
    )
    common: dict[str, object] = {
        "repository": str(static_acceptance["repository"]),
        "campaign_key": str(static_acceptance["campaign_key"]),
        "evidence_base_sha": evidence_base_sha,
        "canary_target_sha": canary_target_sha,
        "ticket_manifest": static_tickets,
        "canary_receipt_digest": str(static_acceptance["receipt_digest"]),
        "named_admission_receipt_digest": str(static_named_admission["receipt_digest"]),
        "default_writer_receipt_digest": str(static_default_writer["receipt_digest"]),
        "activation_id": str(static_default_writer["activation_id"]),
        "writer_generation": str(static_default_writer["writer_generation"]),
    }
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
    output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ga-metadata-", dir=output_root) as raw:
        staging_root = Path(raw)
        staged: dict[Path, Path] = {}
        staged_changelog = staging_root / "CHANGELOG.md"
        _write_staged_changelog(staged_changelog, changelog_content)
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
    value = json.loads(path.read_text(encoding="utf-8"))
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
