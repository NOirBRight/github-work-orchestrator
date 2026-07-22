#!/usr/bin/env python3
"""gwo_lease: repository Integration Lease helpers and validation.

This module is imported by the gwo CLI; it contains only pure validation and
formatting helpers. All durable writes live in gwo_store.
"""

from __future__ import annotations

import re
from typing import Any


LEASE_SCOPE_RE = re.compile(r"^repo:[^/\s]+/[^/\s]+:integration$")
CHAIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")


def validate_scope(value: str) -> str:
    if not isinstance(value, str) or not LEASE_SCOPE_RE.fullmatch(value):
        raise ValueError("lease scope must be 'repo:<owner>/<repo>:integration'")
    return value


def validate_chain_id(value: str) -> str:
    if not isinstance(value, str) or not CHAIN_ID_RE.fullmatch(value):
        raise ValueError("chain_id is invalid")
    return value


def lease_record(
    *,
    lease_id: str,
    scope: str,
    holder_agent: str | None,
    acquired_at: float | None,
    released_at: float | None,
) -> dict[str, Any]:
    """Return a normalized lease row dict. Does not touch the store."""
    return {
        "lease_id": validate_chain_id(lease_id),
        "scope": validate_scope(scope),
        "holder_agent": holder_agent,
        "acquired_at": acquired_at,
        "released_at": released_at,
    }


def chain_record(
    *,
    chain_id: str,
    scope: str,
    candidate_sha: str,
    task_id: str,
    prior_chain_id: str | None,
    created_at: float,
) -> dict[str, Any]:
    """Return a normalized integration chain node dict. Does not touch the store."""
    import gwo_review

    return {
        "chain_id": validate_chain_id(chain_id),
        "scope": validate_scope(scope),
        "candidate_sha": gwo_review.validate_sha("candidate_sha", candidate_sha),
        "task_id": gwo_review.validate_identifier("task_id", task_id),
        "prior_chain_id": (
            validate_chain_id(prior_chain_id) if prior_chain_id is not None else None
        ),
        "created_at": created_at,
    }
