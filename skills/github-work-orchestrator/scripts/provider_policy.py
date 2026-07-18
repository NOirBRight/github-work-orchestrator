#!/usr/bin/env python3
"""Resolve a Paseo selector and advertised high-autonomy mode without fallback."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any


ROLE_CATEGORIES = {"impl", "ui", "research", "planning", "audit"}
SELECTOR_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*/[^/\s]+(?:/[^/\s]+)*$", re.IGNORECASE)


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


def resolve_campaign_orchestrator_provider(
    *,
    campaign_id: str,
    preferences: dict[str, Any],
    available_providers: set[str],
    explicit_override: str | None = None,
) -> dict[str, str]:
    """Resolve an auditable, Campaign-local Orchestrator Provider Binding."""

    if not isinstance(campaign_id, str) or not campaign_id.strip():
        raise ProviderPolicyError("campaign_id must be exact")
    binding = resolve_provider(
        role_category="planning",
        preferences=preferences,
        available_providers=available_providers,
        explicit_override=explicit_override,
    )
    return {
        "campaign_id": campaign_id.strip(),
        "agent_role": "orchestrator",
        **binding,
    }


def resolve_highest_permission_mode(
    available_modes: list[dict[str, Any]],
    *,
    configured_mode_id: str | None = None,
    explicit_mode_id: str | None = None,
) -> dict[str, str]:
    """Resolve an explicitly trusted unattended mode; never infer from prose."""
    advertised_modes = [
        mode
        for mode in available_modes
        if isinstance(mode, dict)
        and isinstance(mode.get("id"), str)
        and mode["id"].strip()
    ]
    mode_ids = [mode["id"].strip() for mode in advertised_modes]
    if len(mode_ids) != len(set(mode_ids)):
        raise ProviderPolicyError("provider advertises duplicate runtime mode IDs")
    modes = {mode_id: mode for mode_id, mode in zip(mode_ids, advertised_modes, strict=True)}
    preferred = (
        explicit_mode_id if explicit_mode_id is not None else configured_mode_id
    )
    if preferred is not None:
        if not isinstance(preferred, str) or not preferred.strip():
            raise ProviderPolicyError("unattended mode override must be nonempty text")
        preferred = preferred.strip()
        if preferred not in modes:
            raise ProviderPolicyError(f"configured unattended mode is unavailable: {preferred}")
        selected = modes[preferred]
        selected_text = " ".join(
            str(selected.get(field, "")) for field in ("id", "label", "description")
        ).casefold()
        if selected.get("isUnattended") is False or any(
            marker in selected_text
            for marker in (
                "always ask",
                "prompts for permission",
                "read-only",
                "read only",
                "plan mode",
            )
        ):
            raise ProviderPolicyError("configured mode is explicitly interactive")
        evidence = (
            "explicit-unattended-mode"
            if explicit_mode_id is not None
            else "configured-unattended-mode"
        )
    else:
        advertised = [mode for mode in modes.values() if mode.get("isUnattended") is True]
        if not advertised:
            raise ProviderPolicyError("provider advertises no unattended execution mode")
        if len(advertised) > 1:
            raise ProviderPolicyError(
                "provider advertises multiple unattended modes; configure one explicitly"
            )
        selected = advertised[0]
        evidence = "advertised-is-unattended"
    return {
        "runtime_mode_id": selected["id"].strip(),
        "runtime_mode_label": str(selected.get("label", selected["id"])).strip(),
        "evidence": evidence,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role-category", choices=sorted(ROLE_CATEGORIES), required=True)
    parser.add_argument("--campaign-id")
    parser.add_argument(
        "--preferences",
        type=Path,
        default=Path.home() / ".paseo" / "orchestration-preferences.json",
    )
    parser.add_argument("--available-provider", action="append", required=True)
    parser.add_argument("--explicit")
    parser.add_argument("--explicit-mode")
    parser.add_argument(
        "--available-modes-json",
        type=Path,
        help="optional JSON array returned by Paseo provider/agent inspection",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        preferences = load_preferences(arguments.preferences)
        available_providers = set(arguments.available_provider)
        if arguments.campaign_id is not None:
            if arguments.role_category != "planning":
                raise ProviderPolicyError(
                    "campaign_id is valid only for a planning Provider Binding"
                )
            result = resolve_campaign_orchestrator_provider(
                campaign_id=arguments.campaign_id,
                preferences=preferences,
                available_providers=available_providers,
                explicit_override=arguments.explicit,
            )
        else:
            result = resolve_provider(
                role_category=arguments.role_category,
                preferences=preferences,
                available_providers=available_providers,
                explicit_override=arguments.explicit,
            )
        mode = None
        if arguments.available_modes_json:
            unattended_modes = preferences.get("unattended_modes", {})
            if not isinstance(unattended_modes, dict):
                raise ProviderPolicyError("unattended_modes must be an object")
            configured_mode = unattended_modes.get(result["provider"])
            if configured_mode is not None and not isinstance(configured_mode, str):
                raise ProviderPolicyError("configured unattended mode must be text")
            mode = resolve_highest_permission_mode(
                json.loads(arguments.available_modes_json.read_text(encoding="utf-8")),
                configured_mode_id=configured_mode,
                explicit_mode_id=arguments.explicit_mode,
            )
    except (ProviderPolicyError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, "provider": result, "mode": mode}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
