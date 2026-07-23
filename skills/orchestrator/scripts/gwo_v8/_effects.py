"""Private Effect Contract normalization shared by compile and execution."""

from __future__ import annotations

from typing import Any


class EffectContractError(ValueError):
    pass


def normalized_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EffectContractError("write path is invalid")
    normalized = value.strip().replace("\\", "/").strip("/")
    if value.startswith(("/", "\\")) or ".." in normalized.split("/"):
        raise EffectContractError("write path escapes repository")
    return normalized


def authorized_file_changes(node: dict[str, Any]) -> list[dict[str, Any]]:
    changes = (node.get("inputs") or {}).get("file_changes")
    scopes = (node.get("effect_contract") or {}).get("write_scopes")
    if not isinstance(changes, list) or not isinstance(scopes, list):
        raise EffectContractError("file changes and Write Scopes must be lists")
    normalized_scopes = [normalized_relative_path(scope) for scope in scopes]
    for change in changes:
        if not isinstance(change, dict):
            raise EffectContractError("file change must be an object")
        path = normalized_relative_path(change.get("path"))
        if not any(
            path == scope or path.startswith(f"{scope}/")
            for scope in normalized_scopes
        ):
            raise EffectContractError(
                f"file change is outside the authorized Write Scope: {path}"
            )
    return changes
