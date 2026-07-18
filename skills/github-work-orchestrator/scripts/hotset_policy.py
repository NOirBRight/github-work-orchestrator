#!/usr/bin/env python3
"""Canonical repository-relative Hotset validation and overlap rules."""

from __future__ import annotations

from typing import Any


def normalize_hotset_entry(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Hotset entries must be nonempty text")
    normalized = value.replace("\\", "/")
    is_drive_path = (
        len(normalized) >= 2 and normalized[0].isalpha() and normalized[1] == ":"
    )
    components = normalized.split("/")
    if (
        value != value.strip()
        or normalized.startswith("/")
        or is_drive_path
        or "\x00" in normalized
        or any(component in {"", ".", ".."} for component in components)
    ):
        raise ValueError("Hotset entries must be canonical repository-relative paths")
    return "/".join(components)


def normalize_hotset(value: Any, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("Hotset must be a list")
    if not value and not allow_empty:
        raise ValueError("Hotset must be nonempty")
    normalized = [normalize_hotset_entry(entry) for entry in value]
    return sorted(set(normalized))


def _comparison_key(value: str, case_sensitive: bool) -> str:
    return value if case_sensitive else value.casefold()


def entries_overlap(left: str, right: str, *, case_sensitive: bool) -> bool:
    if not isinstance(case_sensitive, bool):
        raise ValueError("case_sensitive must be boolean")
    left_normalized = normalize_hotset_entry(left)
    right_normalized = normalize_hotset_entry(right)
    left_key = _comparison_key(left_normalized, case_sensitive)
    right_key = _comparison_key(right_normalized, case_sensitive)
    return (
        left_key == right_key
        or left_key.startswith(f"{right_key}/")
        or right_key.startswith(f"{left_key}/")
    )


def hotsets_overlap(left: list[str], right: list[str], *, case_sensitive: bool) -> bool:
    return any(
        entries_overlap(a, b, case_sensitive=case_sensitive)
        for a in left
        for b in right
    )


def hotset_is_within(
    candidate: list[str],
    container: list[str],
    *,
    case_sensitive: bool,
) -> bool:
    if not isinstance(case_sensitive, bool):
        raise ValueError("case_sensitive must be boolean")
    normalized_candidate = normalize_hotset(candidate)
    normalized_container = normalize_hotset(container)
    return all(
        any(
            _comparison_key(item, case_sensitive)
            == _comparison_key(parent, case_sensitive)
            or _comparison_key(item, case_sensitive).startswith(
                f"{_comparison_key(parent, case_sensitive)}/"
            )
            for parent in normalized_container
        )
        for item in normalized_candidate
    )
