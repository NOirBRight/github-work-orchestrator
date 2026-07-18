#!/usr/bin/env python3
"""Provider-neutral authorization contract for Paseo archive operations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _decision(*errors: str) -> dict[str, Any]:
    ordered_errors = list(dict.fromkeys(errors))
    return {
        "authorized": not ordered_errors,
        "error": ordered_errors[0] if ordered_errors else None,
        "errors": ordered_errors,
    }


def _same_path(left: str, right: str) -> bool:
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(
        str(Path(right).resolve())
    )


def authorize_agent_archive(
    *,
    actor_kind: str,
    actor_agent_id: str | None,
    target_agent_id: str,
    target_parent_agent_id: str | None,
    target_idle: bool,
    force: bool,
) -> dict[str, Any]:
    """Authorize one Agent archive without performing any mutation."""

    if actor_kind not in {"agent", "supervisor"}:
        raise ValueError("actor_kind must be agent or supervisor")
    if not isinstance(target_agent_id, str) or not target_agent_id.strip():
        raise ValueError("target_agent_id must be an exact Paseo Agent ID")
    if not isinstance(target_idle, bool):
        raise ValueError("target_idle must be boolean")
    if not isinstance(force, bool):
        raise ValueError("force must be boolean")
    errors: list[str] = []
    if actor_kind == "agent":
        if not isinstance(actor_agent_id, str) or not actor_agent_id.strip():
            raise ValueError("agent actor requires an exact Paseo Agent ID")
        if actor_agent_id == target_agent_id:
            errors.append("SELF_ARCHIVE_FORBIDDEN")
        if target_parent_agent_id is None:
            errors.append("ROOT_ARCHIVE_REQUIRES_SUPERVISOR")
        elif target_parent_agent_id != actor_agent_id:
            errors.append("ARCHIVE_TARGET_NOT_DIRECT_CHILD")
        if force:
            errors.append("FORCE_REQUIRES_SUPERVISOR")
    if not target_idle and not (actor_kind == "supervisor" and force):
        errors.append("AGENT_NOT_IDLE")
    return _decision(*errors)


def authorize_worktree_archive(
    *,
    actor_kind: str,
    actor_agent_id: str | None,
    actor_worktree: str | None,
    protected_control_worktree: str,
    target_worktree: str,
    bound_agent_ids: list[str],
) -> dict[str, Any]:
    """Authorize one worktree archive without performing any mutation."""

    if actor_kind not in {"agent", "supervisor"}:
        raise ValueError("actor_kind must be agent or supervisor")
    if not isinstance(target_worktree, str) or not Path(target_worktree).is_absolute():
        raise ValueError("target_worktree must be an absolute path")
    if not isinstance(protected_control_worktree, str) or not Path(
        protected_control_worktree
    ).is_absolute():
        raise ValueError("protected_control_worktree must be an absolute path")
    if not isinstance(bound_agent_ids, list) or any(
        not isinstance(agent_id, str) or not agent_id.strip()
        for agent_id in bound_agent_ids
    ):
        raise ValueError("bound_agent_ids must contain exact Paseo Agent IDs")
    errors: list[str] = []
    if _same_path(protected_control_worktree, target_worktree):
        errors.append("CONTROL_WORKTREE_PROTECTED")
    if actor_kind == "agent":
        if not isinstance(actor_agent_id, str) or not actor_agent_id.strip():
            raise ValueError("agent actor requires an exact Paseo Agent ID")
        if not isinstance(actor_worktree, str) or not Path(actor_worktree).is_absolute():
            raise ValueError("agent actor worktree must be an absolute path")
        if _same_path(actor_worktree, target_worktree):
            errors.append("CONTROL_WORKTREE_PROTECTED")
    if bound_agent_ids:
        errors.append("WORKTREE_IN_USE")
    return _decision(*errors)
