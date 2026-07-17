#!/usr/bin/env python3
"""Resolve a Paseo selector and advertised high-autonomy mode without fallback."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any


ROLE_CATEGORIES = {"impl", "ui", "research", "planning", "audit"}
SELECTOR_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*/\S+$", re.IGNORECASE)


class ProviderPolicyError(RuntimeError):
    pass


def load_preferences(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ProviderPolicyError(f"cannot read orchestration preferences: {path}") from error
    except json.JSONDecodeError as error:
        raise ProviderPolicyError("orchestration preferences must be valid JSON") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("providers"), dict):
        raise ProviderPolicyError("orchestration preferences require a providers object")
    return payload


def resolve_provider(
    *,
    role_category: str,
    preferences: dict[str, Any],
    available_providers: set[str],
    explicit_override: str | None = None,
) -> dict[str, str]:
    if role_category not in ROLE_CATEGORIES:
        raise ProviderPolicyError("unknown role category")

    configured = preferences.get("providers", {}).get(role_category)
    if explicit_override and explicit_override.strip():
        selector = explicit_override.strip()
        source = "explicit-override"
    elif isinstance(configured, str) and configured.strip():
        selector = configured.strip()
        source = "orchestration-preferences"
    else:
        raise ProviderPolicyError(f"no provider configured for role category {role_category}")

    if not SELECTOR_RE.fullmatch(selector):
        raise ProviderPolicyError("provider selector must be <provider>/<model-id>")
    provider = selector.split("/", 1)[0]
    if provider not in available_providers:
        raise ProviderPolicyError(f"configured provider is unavailable: {provider}")
    return {
        "role_category": role_category,
        "selector": selector,
        "provider": provider,
        "source": source,
    }


def resolve_highest_permission_mode(
    available_modes: list[dict[str, Any]],
) -> dict[str, str]:
    """Choose the most autonomous advertised mode without provider-name rules."""
    ranked: list[tuple[int, int, dict[str, Any], str]] = []
    for index, mode in enumerate(available_modes):
        if not isinstance(mode, dict):
            continue
        mode_id = mode.get("id")
        label = mode.get("label")
        description = mode.get("description", "")
        if not isinstance(mode_id, str) or not mode_id.strip():
            continue
        text = " ".join(
            value for value in (mode_id, label, description) if isinstance(value, str)
        ).lower()
        if any(marker in text for marker in ("plan", "read-only", "read only")):
            continue
        if any(marker in text for marker in ("always ask", "prompts for permission")):
            continue
        if any(
            marker in text
            for marker in (
                "bypass",
                "yolo",
                "full access",
                "unrestricted",
                "skip all permission",
            )
        ):
            score = 100
            evidence = "advertised-unattended-full-access"
        elif any(
            marker in text
            for marker in ("auto approve", "automatically approves", "auto mode")
        ):
            score = 80
            evidence = "advertised-automatic-approval"
        elif any(marker in text for marker in ("build", "execute", "tools")):
            score = 60
            evidence = "advertised-execution-mode"
        else:
            continue
        ranked.append((score, -index, mode, evidence))
    if not ranked:
        raise ProviderPolicyError("provider advertises no unattended execution mode")
    _, _, selected, evidence = max(ranked, key=lambda item: (item[0], item[1]))
    return {
        "runtime_mode_id": selected["id"].strip(),
        "runtime_mode_label": str(selected.get("label", selected["id"])).strip(),
        "evidence": evidence,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role-category", choices=sorted(ROLE_CATEGORIES), required=True)
    parser.add_argument(
        "--preferences",
        type=Path,
        default=Path.home() / ".paseo" / "orchestration-preferences.json",
    )
    parser.add_argument("--available-provider", action="append", required=True)
    parser.add_argument("--explicit")
    parser.add_argument(
        "--available-modes-json",
        type=Path,
        help="optional JSON array returned by Paseo provider/agent inspection",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        result = resolve_provider(
            role_category=arguments.role_category,
            preferences=load_preferences(arguments.preferences),
            available_providers=set(arguments.available_provider),
            explicit_override=arguments.explicit,
        )
        mode = None
        if arguments.available_modes_json:
            mode = resolve_highest_permission_mode(
                json.loads(arguments.available_modes_json.read_text(encoding="utf-8"))
            )
    except (ProviderPolicyError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, "provider": result, "mode": mode}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
