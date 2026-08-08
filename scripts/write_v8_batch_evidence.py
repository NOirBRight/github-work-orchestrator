from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs/e2e/gwo-v8-batch-integrator.md"
LOCAL_EVIDENCE_DIR = (
    ROOT
    / ".superpowers"
    / "sdd"
    / "2026-08-06-gwo-v8-c2-beta2-feature-complete"
    / "batch-evidence"
)
LOCAL_EVIDENCE_STATE = LOCAL_EVIDENCE_DIR / "verification.json"
BATCH_EVIDENCE_SCHEMA = "gwo-v8-batch-beta2-evidence.v1"
FOCUSED_TESTS = {
    "BatchIntegrator": ("tests/test_v8_batch_integrator.py",),
    "Batch recovery": ("tests/test_v8_batch_recovery.py",),
    "Beta2 boundary": ("tests/test_v8_batch_beta2.py",),
}
LOCAL_VERIFICATION_FIELDS = {"focused", "gates"}
FOCUSED_RECEIPT_FIELDS = {
    "command",
    "command_digest",
    "junit_path",
    "log_digest",
    "manifest_digest",
    "tests",
    "failures",
    "errors",
    "skipped",
}
GATE_RECEIPT_FIELDS = {"command", "exit_code", "manifest_digest"}
GATE_COMMANDS = (
    ("py", "-3.13", "scripts/quick_validate.py"),
    ("py", "-3.13", "scripts/sync_orchestrator.py"),
    ("py", "-3.13", "scripts/sync_orchestrator.py", "--check"),
    ("git", "diff", "--check"),
)
RELEASE_STATEMENT = (
    "Beta2 feature-complete preview; no V3 writer cutover and no GA admission."
)


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def _display(arguments: list[str]) -> str:
    return shlex.join(arguments)


def _resolve_commit(reference: str) -> str:
    return _run(
        ["git", "rev-parse", "--verify", f"{reference}^{{commit}}"]
    ).stdout.strip()


def _require_merged(commit_sha: str) -> None:
    _run(["git", "merge-base", "--is-ancestor", commit_sha, "HEAD"])


def _object_id_length() -> int:
    object_format = _run(["git", "rev-parse", "--show-object-format"]).stdout.strip()
    if object_format == "sha1":
        return 40
    if object_format == "sha256":
        return 64
    raise SystemExit(f"unsupported Git object format: {object_format}")


def _require_object_id(name: str, value: Any, length: int) -> str:
    if (
        not isinstance(value, str)
        or len(value) != length
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SystemExit(f"{name} is not an exact lowercase Git object ID")
    return value


def _validate_subject(
    name: str, value: Any, object_id_length: int
) -> dict[str, Any]:
    required = {"sha", "tree", "parents"}
    if not isinstance(value, dict) or set(value) != required:
        raise SystemExit(f"{name} fields differ from the exact subject schema")
    sha = _require_object_id(f"{name}.sha", value["sha"], object_id_length)
    tree = _require_object_id(f"{name}.tree", value["tree"], object_id_length)
    parents = value["parents"]
    if not isinstance(parents, list):
        raise SystemExit(f"{name}.parents is not an exact Git parent list")
    return {
        "sha": sha,
        "tree": tree,
        "parents": [
            _require_object_id(f"{name}.parents[{index}]", parent, object_id_length)
            for index, parent in enumerate(parents)
        ],
    }


def _require_digest(name: str, value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SystemExit(f"{name} is not an exact lowercase SHA-256 digest")
    return value


def _digest_value(value: Any) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _subject_readback(commit_sha: str, object_id_length: int) -> dict[str, Any]:
    resolved = _resolve_commit(commit_sha)
    _require_object_id("subject.sha", resolved, object_id_length)
    tree = _run(["git", "rev-parse", "--verify", f"{resolved}^{{tree}}"])
    tree_sha = _require_object_id("subject.tree", tree.stdout.strip(), object_id_length)
    parents_line = _run(
        ["git", "rev-list", "--parents", "-n", "1", resolved]
    ).stdout.strip()
    parent_values = parents_line.split()[1:]
    parents = [
        _require_object_id(f"subject.parents[{index}]", parent, object_id_length)
        for index, parent in enumerate(parent_values)
    ]
    return {"sha": resolved, "tree": tree_sha, "parents": parents}


def _validate_delivery_proof(
    name: str,
    value: Any,
    expected_member_ticket_keys: list[str],
    object_id_length: int,
    expected_batch_sha: str | None,
) -> str:
    required = {
        "delivery_stable_action_id",
        "delivery_request_digest",
        "batch_id",
        "batch_sha",
        "member_ticket_keys",
        "local_check_receipt_digest",
        "publication_receipt_digest",
        "pull_request_number",
        "pull_request_head_sha",
        "hosted_result_receipt_digest",
        "integration_lease_digest",
        "target_branch",
        "target_head_sha",
        "target_readback_digest",
        "target_contains_batch_sha",
        "pull_request_merge_target_sha",
        "merge_method",
        "proof_digest",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise SystemExit(f"{name} fields differ from BatchDeliveryProof.v1")
    if (
        not isinstance(value["delivery_stable_action_id"], str)
        or not value["delivery_stable_action_id"]
        or not isinstance(value["batch_id"], str)
        or not value["batch_id"]
        or value["member_ticket_keys"] != expected_member_ticket_keys
    ):
        raise SystemExit(f"{name} delivery action, Batch, or member identity changed")
    _require_digest(
        f"{name}.delivery_request_digest",
        value["delivery_request_digest"],
    )
    batch_sha = _require_object_id(
        f"{name}.batch_sha", value["batch_sha"], object_id_length
    )
    if expected_batch_sha is not None and batch_sha != expected_batch_sha:
        raise SystemExit(f"{name}.batch_sha differs from the direct Batch")
    if _require_object_id(
        f"{name}.pull_request_head_sha",
        value["pull_request_head_sha"],
        object_id_length,
    ) != batch_sha:
        raise SystemExit(f"{name} PR head differs from the exact Batch SHA")
    if type(value["pull_request_number"]) is not int or value["pull_request_number"] <= 0:
        raise SystemExit(f"{name} pull-request number is not positive")
    for field in (
        "local_check_receipt_digest",
        "publication_receipt_digest",
        "hosted_result_receipt_digest",
        "integration_lease_digest",
        "target_readback_digest",
    ):
        _require_digest(f"{name}.{field}", value[field])
    target_head_sha = _require_object_id(
        f"{name}.target_head_sha",
        value["target_head_sha"],
        object_id_length,
    )
    if (
        not isinstance(value["target_branch"], str)
        or not value["target_branch"]
        or value["target_contains_batch_sha"] is not True
        or value["merge_method"] != "merge"
        or _require_object_id(
            f"{name}.pull_request_merge_target_sha",
            value["pull_request_merge_target_sha"],
            object_id_length,
        )
        != target_head_sha
    ):
        raise SystemExit(f"{name} target or merge readback is not exact")
    proof_digest = _require_digest(f"{name}.proof_digest", value["proof_digest"])
    body = dict(value)
    body.pop("proof_digest")
    if proof_digest != _digest_value(
        {"kind": "batch-delivery-proof.v1", **body}
    ):
        raise SystemExit(f"{name} proof digest does not cover exact readbacks")
    return batch_sha


def _pytest_receipt(
    label: str,
    targets: tuple[str, ...],
    junit_path: Path,
) -> dict[str, Any]:
    execution = [
        sys.executable,
        "-m",
        "pytest",
        *targets,
        "-q",
        f"--junitxml={junit_path}",
    ]
    root = ET.parse(junit_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    counts = {
        key: sum(int(suite.attrib.get(key, "0")) for suite in suites)
        for key in ("tests", "failures", "errors", "skipped")
    }
    if counts["failures"] or counts["errors"]:
        raise SystemExit(f"{label} did not pass")
    display = ["py", "-3.13", "-m", "pytest", *targets, "-q"]
    return {
        "command": _display(display),
        "command_digest": _digest_value(display),
        "junit_path": junit_path.relative_to(ROOT).as_posix(),
        "log_digest": _file_digest(junit_path),
        "manifest_digest": _file_digest(
            ROOT / "skills" / "orchestrator" / ".skill-package.json"
        ),
        **counts,
    }


def _run_pytest_receipt(
    label: str,
    targets: tuple[str, ...],
    junit_path: Path,
) -> dict[str, Any]:
    execution = [
        sys.executable,
        "-m",
        "pytest",
        *targets,
        "-q",
        f"--junitxml={junit_path}",
    ]
    _run(execution)
    return _pytest_receipt(label, targets, junit_path)


def _gate_receipt(display: list[str], execution: list[str]) -> dict[str, Any]:
    _run(execution)
    return {
        "command": _display(display),
        "exit_code": 0,
        "manifest_digest": _file_digest(
            ROOT / "skills" / "orchestrator" / ".skill-package.json"
        ),
    }


def _write_local_verification(
    focused: dict[str, dict[str, Any]], gates: list[dict[str, Any]]
) -> None:
    LOCAL_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    payload = _local_verification_payload(focused, gates)
    temporary = LOCAL_EVIDENCE_STATE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(LOCAL_EVIDENCE_STATE)


def _read_local_verification(
    *, validate_receipts: bool = True
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if not LOCAL_EVIDENCE_STATE.is_file():
        raise SystemExit(f"local verification manifest is missing: {LOCAL_EVIDENCE_STATE}")
    try:
        payload = json.loads(LOCAL_EVIDENCE_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit("local verification manifest is malformed") from error
    return _validate_local_verification(payload, validate_files=validate_receipts)


def _local_verification_payload(
    focused: dict[str, dict[str, Any]], gates: list[dict[str, Any]]
) -> dict[str, Any]:
    return {"focused": focused, "gates": gates}


def _expected_focused_command(label: str) -> list[str]:
    return ["py", "-3.13", "-m", "pytest", *FOCUSED_TESTS[label], "-q"]


def _expected_focused_junit_path(label: str) -> str:
    return (
        LOCAL_EVIDENCE_DIR
        / f"{label.lower().replace(' ', '-')}.xml"
    ).relative_to(ROOT).as_posix()


def _validate_focused_receipt(
    label: str,
    value: Any,
    *,
    manifest_digest: str,
    validate_files: bool,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != FOCUSED_RECEIPT_FIELDS:
        raise SystemExit(f"{label} receipt fields differ from the exact schema")
    expected_command = _expected_focused_command(label)
    if value["command"] != _display(expected_command):
        raise SystemExit(f"{label} receipt command is not exact")
    _require_digest(f"{label} receipt command_digest", value["command_digest"])
    if value["command_digest"] != _digest_value(expected_command):
        raise SystemExit(f"{label} receipt command digest is stale")
    if value["junit_path"] != _expected_focused_junit_path(label):
        raise SystemExit(f"{label} receipt JUnit path is not exact")
    for field in ("log_digest", "manifest_digest"):
        _require_digest(f"{label} receipt {field}", value[field])
    if validate_files and value["manifest_digest"] != manifest_digest:
        raise SystemExit(f"{label} receipt manifest digest is stale")
    for field in ("tests", "failures", "errors", "skipped"):
        if type(value[field]) is not int or value[field] < 0:
            raise SystemExit(f"{label} receipt {field} count is not exact")
    if value["failures"] or value["errors"]:
        raise SystemExit(f"{label} receipt does not describe a passing suite")
    if validate_files:
        junit_path = ROOT / value["junit_path"]
        if not junit_path.is_file() or _file_digest(junit_path) != value["log_digest"]:
            raise SystemExit("focused receipt log digest is stale")
    return value


def _validate_gate_receipt(
    index: int,
    value: Any,
    *,
    manifest_digest: str,
    validate_files: bool,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != GATE_RECEIPT_FIELDS:
        raise SystemExit(f"gate receipt {index} fields differ from the exact schema")
    expected_command = list(GATE_COMMANDS[index])
    if value["command"] != _display(expected_command):
        raise SystemExit(f"gate receipt {index} command is not exact")
    if type(value["exit_code"]) is not int or value["exit_code"] != 0:
        raise SystemExit(f"gate receipt {index} exit code is not exact")
    _require_digest(f"gate receipt {index} manifest_digest", value["manifest_digest"])
    if validate_files and value["manifest_digest"] != manifest_digest:
        raise SystemExit(f"gate receipt {index} manifest digest is stale")
    return value


def _validate_local_verification(
    payload: Any,
    *,
    validate_files: bool,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(payload, dict) or set(payload) != LOCAL_VERIFICATION_FIELDS:
        raise SystemExit("local verification fields differ from the exact schema")
    focused = payload["focused"]
    gates = payload["gates"]
    if not isinstance(focused, dict) or set(focused) != set(FOCUSED_TESTS):
        raise SystemExit("local verification focused suites are not exact")
    if not isinstance(gates, list) or len(gates) != len(GATE_COMMANDS):
        raise SystemExit("local verification gates are not exact")
    manifest_digest = _file_digest(
        ROOT / "skills" / "orchestrator" / ".skill-package.json"
    )
    ordered_focused = {
        label: _validate_focused_receipt(
            label,
            focused[label],
            manifest_digest=manifest_digest,
            validate_files=validate_files,
        )
        for label in FOCUSED_TESTS
    }
    ordered_gates = [
        _validate_gate_receipt(
            index,
            receipt,
            manifest_digest=manifest_digest,
            validate_files=validate_files,
        )
        for index, receipt in enumerate(gates)
    ]
    return ordered_focused, ordered_gates


def _validate_batch_readback(
    name: str,
    value: Any,
    expected_member_count: int,
    object_id_length: int,
) -> None:
    required = {
        "candidate_shas",
        "member_ticket_keys",
        "batch_sha",
        "local_sha",
        "publication_sha",
        "pr_head_sha",
        "hosted_sha",
        "target_readback_batch_sha",
        "delivery_proofs",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise SystemExit(f"{name} readback fields differ from the exact schema")
    candidate_shas = value.get("candidate_shas")
    if not isinstance(candidate_shas, list) or len(candidate_shas) != expected_member_count:
        raise SystemExit(f"{name} must contain {expected_member_count} Candidate SHAs")
    for index, candidate_sha in enumerate(candidate_shas):
        _require_object_id(
            f"{name}.candidate_shas[{index}]", candidate_sha, object_id_length
        )
    member_ticket_keys = value.get("member_ticket_keys")
    if (
        not isinstance(member_ticket_keys, list)
        or len(member_ticket_keys) != expected_member_count
        or any(not isinstance(ticket_key, str) or not ticket_key for ticket_key in member_ticket_keys)
        or len(set(member_ticket_keys)) != len(member_ticket_keys)
    ):
        raise SystemExit(f"{name} member Ticket partition is not exact")
    batch_sha = _require_object_id(
        f"{name}.batch_sha", value.get("batch_sha"), object_id_length
    )
    for field in (
        "local_sha",
        "publication_sha",
        "pr_head_sha",
        "hosted_sha",
        "target_readback_batch_sha",
    ):
        observed = _require_object_id(
            f"{name}.{field}", value.get(field), object_id_length
        )
        if observed != batch_sha:
            raise SystemExit(f"{name}.{field} does not equal the exact Batch SHA")
    delivery_proofs = value.get("delivery_proofs")
    if not isinstance(delivery_proofs, list) or len(delivery_proofs) != 1:
        raise SystemExit(f"{name} direct completion must expose exactly one proof")
    _validate_delivery_proof(
        f"{name}.delivery_proofs[0]",
        delivery_proofs[0],
        member_ticket_keys,
        object_id_length,
        batch_sha,
    )


def _require_exact_object(
    name: str, value: Any, required: set[str]
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        raise SystemExit(f"{name} fields differ from the exact Beta2 schema")
    return value


def _validate_readbacks(readbacks: Any, object_id_length: int) -> dict[str, Any]:
    _reject_remote_urls(readbacks)
    if not isinstance(readbacks, dict):
        raise SystemExit("readback artifact must be a JSON object")
    required = {
        "schema",
        "standard_batch",
        "strict_batch",
        "infrastructure_retry",
        "successful_fallback",
        "singleton_fallback",
        "restart_adoption",
        "negative_paths",
    }
    if set(readbacks) != required:
        raise SystemExit(
            "readback artifact keys differ from the exact Beta2 evidence schema"
        )
    if readbacks["schema"] != BATCH_EVIDENCE_SCHEMA:
        raise SystemExit("readback artifact schema is not the exact Beta2 evidence schema")
    _validate_batch_readback(
        "standard_batch", readbacks["standard_batch"], 3, object_id_length
    )
    _validate_batch_readback(
        "strict_batch", readbacks["strict_batch"], 1, object_id_length
    )
    standard = readbacks["standard_batch"]
    strict = readbacks["strict_batch"]
    standard_candidate_shas = standard["candidate_shas"]
    standard_member_ticket_keys = standard["member_ticket_keys"]
    if strict["candidate_shas"] != [strict["batch_sha"]]:
        raise SystemExit(
            "strict Singleton Candidate SHA does not bind to its Batch SHA"
        )

    retry = _require_exact_object(
        "infrastructure_retry",
        readbacks["infrastructure_retry"],
        {"batch_sha", "retry_count", "retry_shas"},
    )
    retry_sha = _require_object_id(
        "infrastructure_retry.batch_sha",
        retry["batch_sha"],
        object_id_length,
    )
    retry_shas = retry["retry_shas"]
    retry_count = retry["retry_count"]
    if (
        type(retry_count) is not int
        or retry_count != 2
        or not isinstance(retry_shas, list)
        or len(retry_shas) != 2
    ):
        raise SystemExit("infrastructure retry evidence must contain exactly two retries")
    for observed in retry_shas:
        if _require_object_id(
            "infrastructure_retry.retry_sha", observed, object_id_length
        ) != retry_sha:
            raise SystemExit("infrastructure retry changed the Batch SHA")
    if retry_sha != standard["batch_sha"]:
        raise SystemExit("infrastructure retry Batch SHA is not the standard Batch SHA")

    successful = _require_exact_object(
        "successful_fallback",
        readbacks["successful_fallback"],
        {
            "parent_phase",
            "parent_fallback_generation",
            "parent_receipt_digest",
            "member_ticket_keys",
            "delivery_proofs",
        },
    )
    successful_ticket_keys = successful["member_ticket_keys"]
    successful_proofs = successful["delivery_proofs"]
    if (
        successful["parent_phase"] != "complete"
        or successful["parent_fallback_generation"] != 1
        or not isinstance(successful_ticket_keys, list)
        or len(successful_ticket_keys) != 3
        or any(
            not isinstance(ticket_key, str) or not ticket_key
            for ticket_key in successful_ticket_keys
        )
        or len(set(successful_ticket_keys)) != 3
        or not isinstance(successful_proofs, list)
        or len(successful_proofs) != 3
        or successful_ticket_keys != standard_member_ticket_keys
    ):
        raise SystemExit("successful fallback is not a three-Singleton completion")
    _require_digest(
        "successful_fallback.parent_receipt_digest",
        successful["parent_receipt_digest"],
    )
    successful_batch_shas = [
        _validate_delivery_proof(
            f"successful_fallback.delivery_proofs[{index}]",
            proof,
            [successful_ticket_keys[index]],
            object_id_length,
            None,
        )
        for index, proof in enumerate(successful_proofs)
    ]
    if (
        len(set(successful_batch_shas)) != 3
        or len(
            {
                proof["delivery_stable_action_id"]
                for proof in successful_proofs
            }
        )
        != 3
    ):
        raise SystemExit("successful fallback reused a child action or Batch SHA")

    fallback = _require_exact_object(
        "singleton_fallback",
        readbacks["singleton_fallback"],
        {"resume_directives", "singleton_candidate_shas", "unaffected_evidence"},
    )
    singleton_shas = fallback["singleton_candidate_shas"]
    if not isinstance(singleton_shas, list) or len(singleton_shas) != 3:
        raise SystemExit("Singleton fallback must preserve exactly three Candidate SHAs")
    for observed in singleton_shas:
        _require_object_id(
            "singleton_fallback.singleton_candidate_sha",
            observed,
            object_id_length,
        )
    if singleton_shas != standard_candidate_shas:
        raise SystemExit(
            "Singleton fallback Candidate partition differs from the standard Batch"
        )
    if successful_batch_shas != singleton_shas:
        raise SystemExit(
            "Singleton proof Batch SHAs do not bind to the Candidate partition"
        )
    unaffected = fallback["unaffected_evidence"]
    if not isinstance(unaffected, dict) or set(unaffected) != {"issue:2", "issue:3"}:
        raise SystemExit("Singleton fallback unaffected Evidence mapping is not exact")
    for ticket_key, digests in unaffected.items():
        if not isinstance(digests, list) or not digests:
            raise SystemExit(f"{ticket_key} has no preserved Evidence digests")
        for digest in digests:
            _require_digest(f"{ticket_key} Evidence digest", digest)
    directives = fallback["resume_directives"]
    if not isinstance(directives, list) or len(directives) != 1:
        raise SystemExit("Singleton fallback must contain one fixed resume directive")
    directive = directives[0]
    if (
        not isinstance(directive, list)
        or len(directive) != 2
        or directive[0] != "work-run:1"
    ):
        raise SystemExit("Singleton fallback resume directive named the wrong Work Run")
    _require_digest("Singleton fallback Review Finding ledger", directive[1])

    adoption = _require_exact_object(
        "restart_adoption",
        readbacks["restart_adoption"],
        {"batch_sha", "provider_rereads", "receipt_digest"},
    )
    standard_proof = standard["delivery_proofs"][0]
    _require_object_id(
        "restart_adoption.batch_sha",
        adoption["batch_sha"],
        object_id_length,
    )
    if adoption["batch_sha"] != standard["batch_sha"]:
        raise SystemExit("restart adoption Batch SHA is not the standard Batch SHA")
    _require_digest("restart_adoption.receipt_digest", adoption["receipt_digest"])
    if adoption["receipt_digest"] != standard_proof["hosted_result_receipt_digest"]:
        raise SystemExit("restart adoption receipt is not the standard hosted receipt")
    if type(adoption["provider_rereads"]) is not int or adoption["provider_rereads"] != 0:
        raise SystemExit("restart adoption performed a provider reread")

    expected_negative = {
        "wrong_sha": "DeliveryIdentityMismatch",
        "wrong_receipt": "DeliveryIdentityMismatch",
        "ambiguous_attribution": "DeliveryAttributionAmbiguous",
        "wrong_merge_target": "DeliveryIdentityMismatch",
        "squash": "DeliveryIdentityMismatch",
        "rebase": "DeliveryIdentityMismatch",
    }
    if readbacks["negative_paths"] != expected_negative:
        raise SystemExit("negative-path evidence differs from the exact expected errors")
    return readbacks


def _reject_remote_urls(value: Any, path: str = "readbacks") -> None:
    """Keep this evidence artifact local and content-addressed."""

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if isinstance(child, str) and re.match(r"https?://", child):
                raise SystemExit(
                    f"remote repository-check URL is not permitted: {child_path}"
                )
            _reject_remote_urls(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_remote_urls(child, f"{path}[{index}]")


def _render(
    merged_shas: dict[str, str],
    subject: dict[str, Any],
    focused: dict[str, dict[str, Any]],
    gates: list[dict[str, Any]],
    readbacks: dict[str, Any],
    publication_mode: bool = False,
) -> str:
    lines = [
        "# GWO V8 BatchIntegrator Beta2 Evidence",
        "",
        "## Verification Boundary",
        "",
        f"- Schema: `{BATCH_EVIDENCE_SCHEMA}`.",
        "- Mode: `Local Verification Only`.",
    ]
    if publication_mode:
        lines.extend(["", "## Publication Subject", ""])
    else:
        lines.extend(["- Subject:", ""])
    lines.extend(
        [
            "```json",
            json.dumps(subject, indent=2, sort_keys=True),
            "```",
            "",
            "## Merged Results",
            "",
            "| Issue | Merged commit |",
            "| --- | --- |",
        ]
    )
    lines.extend(
        f"| {issue} | `{sha}` |" for issue, sha in merged_shas.items()
    )
    lines.extend(
        [
            "",
            "## Focused pytest Receipts",
            "",
            "| Suite | Command | Command digest | Log digest | Manifest digest | Tests | Failures | Errors | Skipped |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for label, receipt in focused.items():
        lines.append(
            f"| {label} | `{receipt['command']}` | `{receipt['command_digest']}` | "
            f"`{receipt['log_digest']}` | `{receipt['manifest_digest']}` | {receipt['tests']} | "
            f"{receipt['failures']} | {receipt['errors']} | {receipt['skipped']} |"
        )
    lines.extend(f"- `{gate['command']}`: exit {gate['exit_code']}." for gate in gates)
    lines.extend(
        [
            "",
            "## Local Verification Receipts",
            "",
            "```json",
            json.dumps(
                _local_verification_payload(focused, gates),
                indent=2,
                sort_keys=True,
            ),
            "```",
            "",
            "## Exact Git, CI, Target, Recovery, and Receipt Readbacks",
            "",
            "```json",
            json.dumps(readbacks, indent=2, sort_keys=True),
            "```",
            "",
            "## Release Train Decision",
            "",
            RELEASE_STATEMENT,
            "",
        ]
    )
    return "\n".join(lines)


def _required(name: str, value: str | None) -> str:
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _document_merged_shas(path: Path) -> dict[str, str]:
    try:
        document = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SystemExit(f"canonical evidence document is unreadable: {path}") from error
    try:
        table = document.split("## Merged Results", 1)[1].split(
            "## Focused pytest Receipts", 1
        )[0]
    except IndexError as error:
        raise SystemExit("canonical evidence document has no merged-results table") from error
    merged: dict[str, str] = {}
    for line in table.splitlines():
        match = re.fullmatch(r"\|\s*(#(?:115|116|117))\s*\|\s*`([0-9a-f]+)`\s*\|", line)
        if match:
            merged[match.group(1)] = match.group(2)
    if set(merged) != {"#115", "#116", "#117"}:
        raise SystemExit("canonical evidence document has an incomplete merged-results table")
    return merged


def _document_publication_subject(path: Path) -> dict[str, Any] | None:
    try:
        document = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SystemExit(f"canonical evidence document is unreadable: {path}") from error
    if "## Publication Subject" not in document:
        return None
    try:
        section = document.split("## Publication Subject", 1)[1].split(
            "## Merged Results", 1
        )[0]
        payload = section.split("```json\n", 1)[1].split("\n```", 1)[0]
        value = json.loads(payload)
    except (IndexError, json.JSONDecodeError) as error:
        raise SystemExit(
            "canonical evidence document has no valid publication subject"
        ) from error
    if value is None:
        raise SystemExit(
            "canonical evidence document has no valid publication subject"
        )
    return value


def _document_local_verification(path: Path) -> dict[str, Any]:
    try:
        document = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SystemExit(f"canonical evidence document is unreadable: {path}") from error
    try:
        section = document.split("## Local Verification Receipts", 1)[1].split(
            "## Exact Git, CI, Target, Recovery, and Receipt Readbacks", 1
        )[0]
        payload = section.split("```json\n", 1)[1].split("\n```", 1)[0]
        value = json.loads(payload)
    except (IndexError, json.JSONDecodeError) as error:
        raise SystemExit(
            "canonical evidence document has no valid local verification receipts"
        ) from error
    if not isinstance(value, dict):
        raise SystemExit("canonical local verification receipts are not a JSON object")
    return value


def _document_readbacks(path: Path) -> dict[str, Any]:
    try:
        document = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SystemExit(f"canonical evidence document is unreadable: {path}") from error
    try:
        fenced = document.split(
            "## Exact Git, CI, Target, Recovery, and Receipt Readbacks", 1
        )[1]
        payload = fenced.split("```json\n", 1)[1].split("\n```", 1)[0]
        value = json.loads(payload)
    except (IndexError, json.JSONDecodeError) as error:
        raise SystemExit("canonical evidence document has no valid readback JSON") from error
    if not isinstance(value, dict):
        raise SystemExit("canonical evidence readbacks are not a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--issue-115-sha", default=os.environ.get("GWO_ISSUE_115_MERGED_SHA")
    )
    parser.add_argument(
        "--issue-116-sha", default=os.environ.get("GWO_ISSUE_116_MERGED_SHA")
    )
    parser.add_argument(
        "--issue-117-sha", default=os.environ.get("GWO_ISSUE_117_MERGED_SHA")
    )
    parser.add_argument(
        "--publication-sha", default=os.environ.get("GWO_PUBLICATION_SHA")
    )
    parser.add_argument(
        "--readbacks", default=os.environ.get("GWO_BATCH_READBACK_JSON")
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    object_id_length = _object_id_length()
    document_path = args.output.resolve()
    canonical_merged = (
        _document_merged_shas(document_path) if args.check else {}
    )
    canonical_publication = (
        _document_publication_subject(document_path) if args.check else None
    )
    merged_shas = {
        "#115": _require_object_id(
            "#115 merged commit",
            args.issue_115_sha
            or canonical_merged.get("#115")
            or _required("issue 115 merged SHA", args.issue_115_sha),
            object_id_length,
        ),
        "#116": _require_object_id(
            "#116 merged commit",
            args.issue_116_sha
            or canonical_merged.get("#116")
            or _required("issue 116 merged SHA", args.issue_116_sha),
            object_id_length,
        ),
        "#117": _require_object_id(
            "#117 merged commit",
            args.issue_117_sha
            or canonical_merged.get("#117")
            or _required("issue 117 merged SHA", args.issue_117_sha),
            object_id_length,
        ),
    }
    publication_mode = canonical_publication is not None or (
        not args.check and args.publication_sha is not None
    )
    for issue, sha in merged_shas.items():
        if _resolve_commit(sha) != sha:
            raise SystemExit(f"{issue} merged SHA did not resolve to itself")
        if not publication_mode:
            _require_merged(sha)
    if canonical_publication is not None:
        expected_subject = _validate_subject(
            "canonical publication subject", canonical_publication, object_id_length
        )
        subject = _subject_readback(expected_subject["sha"], object_id_length)
        _require_merged(subject["sha"])
        if subject != expected_subject:
            raise SystemExit(
                "canonical publication subject differs from Git readback"
            )
    elif publication_mode:
        publication_sha = _require_object_id(
            "publication SHA", args.publication_sha, object_id_length
        )
        subject = _subject_readback(publication_sha, object_id_length)
        _require_merged(subject["sha"])
    else:
        subject = _subject_readback(merged_shas["#117"], object_id_length)

    if args.readbacks:
        readbacks = json.loads(
            Path(args.readbacks).resolve().read_text(encoding="utf-8")
        )
    elif args.check:
        readbacks = _document_readbacks(document_path)
    else:
        readback_path = Path(
            _required("readback artifact", args.readbacks)
        ).resolve()
        readbacks = json.loads(readback_path.read_text(encoding="utf-8"))
    readbacks = _validate_readbacks(readbacks, object_id_length)
    if args.check:
        if LOCAL_EVIDENCE_STATE.is_file():
            focused, gates = _read_local_verification(
                validate_receipts=os.environ.get("GWO_BATCH_EVIDENCE_WRITING") != "1"
            )
            embedded = _document_local_verification(document_path)
            embedded_focused, embedded_gates = _validate_local_verification(
                embedded,
                validate_files=False,
            )
            if _local_verification_payload(focused, gates) != _local_verification_payload(
                embedded_focused, embedded_gates
            ):
                raise SystemExit(
                    "canonical embedded local verification receipts differ from the scratch manifest"
                )
        else:
            embedded = _document_local_verification(document_path)
            focused, gates = _validate_local_verification(
                embedded,
                validate_files=False,
            )
    else:
        LOCAL_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        previous_marker = os.environ.get("GWO_BATCH_EVIDENCE_WRITING")
        os.environ["GWO_BATCH_EVIDENCE_WRITING"] = "1"
        try:
            focused = {
                label: _run_pytest_receipt(
                    label,
                    targets,
                    LOCAL_EVIDENCE_DIR / f"{label.lower().replace(' ', '-')}.xml",
                )
                for label, targets in FOCUSED_TESTS.items()
            }
        finally:
            if previous_marker is None:
                os.environ.pop("GWO_BATCH_EVIDENCE_WRITING", None)
            else:
                os.environ["GWO_BATCH_EVIDENCE_WRITING"] = previous_marker
        gates = [
            _gate_receipt(
                ["py", "-3.13", "scripts/quick_validate.py"],
                [sys.executable, "scripts/quick_validate.py"],
            ),
            _gate_receipt(
                ["py", "-3.13", "scripts/sync_orchestrator.py"],
                [sys.executable, "scripts/sync_orchestrator.py"],
            ),
            _gate_receipt(
                ["py", "-3.13", "scripts/sync_orchestrator.py", "--check"],
                [sys.executable, "scripts/sync_orchestrator.py", "--check"],
            ),
            _gate_receipt(["git", "diff", "--check"], ["git", "diff", "--check"]),
        ]
        final_manifest_digest = _file_digest(
            ROOT / "skills" / "orchestrator" / ".skill-package.json"
        )
        for receipt in focused.values():
            receipt["manifest_digest"] = final_manifest_digest
        _write_local_verification(focused, gates)
    rendered = _render(
        merged_shas,
        subject,
        focused,
        gates,
        readbacks,
        publication_mode=publication_mode,
    )
    output = args.output.resolve()
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"evidence document is stale: {output}")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
