"""Strict canonical JSON and immutable planner-view helpers for V3."""

from __future__ import annotations

import hashlib
import json
from types import MappingProxyType
from typing import Any

from ._v3_types import PlanControlError


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def strict_json_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise PlanControlError(
            "CANONICAL_JSON_INVALID", "value is not strict canonical JSON"
        ) from error
    return rendered.encode("utf-8")


def strict_json_decode(value: bytes) -> Any:
    def reject_constant(constant: str) -> None:
        raise ValueError(f"non-finite JSON constant: {constant}")

    try:
        decoded = json.loads(
            value.decode("utf-8"),
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise PlanControlError(
            "CANONICAL_JSON_INVALID", "bytes are not strict canonical JSON"
        ) from error
    if strict_json_bytes(decoded) != value:
        raise PlanControlError(
            "CANONICAL_JSON_MISMATCH", "JSON bytes are not canonical"
        )
    return decoded


def deep_immutable(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: deep_immutable(child) for key, child in value.items()}
        )
    if isinstance(value, list):
        return tuple(deep_immutable(child) for child in value)
    return value
