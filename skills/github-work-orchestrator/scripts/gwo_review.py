#!/usr/bin/env python3
"""gwo_review: review-round helpers and identity validation.

This module is imported by the gwo CLI; it contains only pure validation,
formatting, and digest helpers. All durable writes live in gwo_store.
"""

from __future__ import annotations

import re
from typing import Any


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
VALID_SCOPES = {"full", "delta"}
VERDICTS = {"approved", "rejected", "needs_work", "withdrawn"}
AXES = {"spec", "quality", "combined"}


def validate_sha(name: str, value: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise ValueError(f"{name} must be a 40-hex SHA")
    return value


def validate_sha256(name: str, value: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a 64-hex SHA-256")
    return value


def validate_identifier(name: str, value: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} is invalid")
    return value


def validate_scope(value: str) -> str:
    if value not in VALID_SCOPES:
        raise ValueError("scope must be 'full' or 'delta'")
    return value


def validate_round(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("round must be a positive integer")
    return value


def validate_verdict(value: str) -> str:
    if value not in VERDICTS:
        raise ValueError(f"verdict must be one of {sorted(VERDICTS)}")
    return value


def validate_axis(value: str) -> str:
    if value not in AXES:
        raise ValueError(f"axis must be one of {sorted(AXES)}")
    return value


def round_identity_digest(
    dispatch_id: str,
    round: int,
    candidate_sha: str,
    base_sha: str,
    diff_digest: str,
    acceptance_digest: str,
    scope: str,
    prior_round_id: str | None,
) -> str:
    """Return a deterministic SHA-256 digest of the round identity fields.

    Used for lock receipt comparisons: the CLI issues the row, reviewers
    reference it, and any tampering with the canonical fields changes the
    digest. This is a helper; the authoritative identity is the signed store
    row issued by the coordinator.
    """
    import hashlib

    parts = [
        dispatch_id,
        str(round),
        candidate_sha,
        base_sha,
        diff_digest,
        acceptance_digest,
        scope,
        prior_round_id or "",
    ]
    joined = "\n".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def normalize_findings(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("findings must be a JSON object")
    return dict(value)


def result_record(
    *,
    round_id: str,
    axis: str,
    verdict: str,
    agent_id: str,
    findings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a normalized review result dict. Does not touch the store."""
    return {
        "round_id": validate_identifier("round_id", round_id),
        "axis": validate_axis(axis),
        "verdict": validate_verdict(verdict),
        "agent_id": validate_identifier("agent_id", agent_id),
        "findings_json": normalize_findings(findings),
    }


def round_record(
    *,
    round_id: str,
    dispatch_id: str,
    round: int,
    candidate_sha: str,
    base_sha: str,
    diff_digest: str,
    acceptance_digest: str,
    scope: str,
    prior_round_id: str | None,
    issued_by: str,
    issued_at: float,
    is_current: bool = True,
    assigned_axis: str | None = None,
) -> dict[str, Any]:
    """Return a normalized review-round dict. Does not touch the store."""
    record: dict[str, Any] = {
        "round_id": validate_identifier("round_id", round_id),
        "dispatch_id": validate_identifier("dispatch_id", dispatch_id),
        "round": validate_round(round),
        "candidate_sha": validate_sha("candidate_sha", candidate_sha),
        "base_sha": validate_sha("base_sha", base_sha),
        "diff_digest": validate_sha256("diff_digest", diff_digest),
        "acceptance_digest": validate_sha256("acceptance_digest", acceptance_digest),
        "scope": validate_scope(scope),
        "prior_round_id": (
            validate_identifier("prior_round_id", prior_round_id)
            if prior_round_id is not None else None
        ),
        "issued_by": validate_identifier("issued_by", issued_by),
        "issued_at": issued_at,
        "is_current": int(is_current),
    }
    if assigned_axis is not None:
        record["assigned_axis"] = validate_axis(assigned_axis)
    return record


TIER_AXES: dict[str, tuple[str, ...]] = {
    "fast": (),
    "standard": ("combined",),
    "strict": ("spec", "quality"),
}


def tier_axes(risk: str) -> tuple[str, ...]:
    """Return the required review axes for a risk tier."""
    if risk not in TIER_AXES:
        raise ValueError(f"risk must be one of {sorted(TIER_AXES)}")
    return TIER_AXES[risk]
