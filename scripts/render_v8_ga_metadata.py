#!/usr/bin/env python3
"""Render static V8 GA evidence and the static release record."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
from pathlib import Path
import re
from types import SimpleNamespace
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


def _without_dynamic_metadata(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            key: _without_dynamic_metadata(item)
            for key, item in value.items()
            if str(key) not in DYNAMIC_METADATA_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [_without_dynamic_metadata(item) for item in value]
    return value


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
    static_tickets = _without_dynamic_metadata(tickets)
    static_acceptance = _without_dynamic_metadata(acceptance)
    static_named_admission = _without_dynamic_metadata(named_admission)
    static_default_writer = _without_dynamic_metadata(default_writer)
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
    changelog.parent.mkdir(parents=True, exist_ok=True)
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
    changelog.write_text(
        "# Changelog\n\n" + entry + ("\n" + previous if previous else ""),
        encoding="utf-8",
    )
    acceptance_doc = output_root / "docs/e2e/gwo-v8-root-canary.md"
    _write_markdown_json(
        acceptance_doc,
        "GWO V8 Root Canary Evidence",
        common | {"acceptance": static_acceptance},
    )
    release_note = output_root / "docs/releases/v8.0.0.md"
    _write_markdown_json(
        release_note,
        "GWO V8.0.0",
        common
        | {
            "release": {
                "version": "8.0.0",
                "tag_and_ci_source": "pre-tag ReleaseGateReceipt",
            }
        },
    )
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
