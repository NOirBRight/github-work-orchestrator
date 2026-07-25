#!/usr/bin/env python3
"""Explicit, atomic host configuration operations for GWO."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import orch_core as core


DEFAULT_CONFIG = Path.home() / ".orch" / "config.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    operations = parser.add_subparsers(dest="operation", required=True)

    migrate = operations.add_parser("migrate")
    migrate.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    migrate.add_argument("--legacy", type=Path)

    repository = operations.add_parser("set-repository-runtime")
    repository.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    repository.add_argument("--repository", required=True)
    repository.add_argument("--patch", required=True)

    global_runtime = operations.add_parser("set-global-runtime")
    global_runtime.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    global_runtime.add_argument("--patch", required=True)
    return parser.parse_args(argv)


def _parse_patch(value: str) -> dict[str, object]:
    if value == "-":
        source = sys.stdin.read()
    elif value.startswith("@"):
        try:
            source = Path(value[1:]).read_text(encoding="utf-8")
        except OSError as error:
            raise core.PolicyError("RUNTIME_CONFIG_PATCH_INVALID", str(error)) from error
    else:
        source = value
    try:
        patch = json.loads(source)
    except json.JSONDecodeError as error:
        raise core.PolicyError("RUNTIME_CONFIG_PATCH_INVALID", str(error)) from error
    return core.validate_runtime_config_patch(patch)


def _mutation_result(
    args: argparse.Namespace,
    patch: dict[str, object],
    config: dict[str, object],
) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": 1,
        "status": "updated",
        "operation": args.operation,
        "scope": (
            "repository"
            if args.operation == "set-repository-runtime"
            else "global"
        ),
        "updated": {
            section: sorted(mappings)
            for section, mappings in patch.items()
            if isinstance(mappings, dict)
        },
        "config_sha256": core.runtime_config_digest(config),
    }
    if args.operation == "set-repository-runtime":
        result["repository"] = args.repository
    return result


def run(args: argparse.Namespace) -> dict[str, object]:
    if args.operation == "migrate":
        legacy = args.legacy or args.config.with_name("providers.json")
        migrated = core.migrate_config_file(legacy, args.config)
        return {
            "status": "idle",
            "operation": "migrate",
            "config": str(args.config),
            "legacy": str(legacy),
            "schema_version": migrated["schema_version"],
        }

    patch = _parse_patch(args.patch)
    if args.operation == "set-repository-runtime":
        updated = core.set_repository_runtime_config(
            args.config,
            args.repository,
            patch,
        )
    elif args.operation == "set-global-runtime":
        updated = core.set_global_runtime_config(args.config, patch)
    else:
        raise core.PolicyError(
            "CONFIG_OPERATION_INVALID",
            f"unknown explicit config operation: {args.operation}",
        )
    return _mutation_result(args, patch, updated)


def main(argv: list[str] | None = None) -> int:
    try:
        print(json.dumps(run(parse_args(argv)), ensure_ascii=False))
        return 0
    except (core.PolicyError, OSError) as error:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "code": getattr(error, "code", "CONFIG_COMMAND_FAILED"),
                    "detail": str(error),
                },
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
