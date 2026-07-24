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
    parser.add_argument("operation", choices=("migrate",))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--legacy", type=Path)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> dict[str, object]:
    legacy = args.legacy or args.config.with_name("providers.json")
    migrated = core.migrate_config_file(legacy, args.config)
    return {
        "status": "idle",
        "operation": "migrate",
        "config": str(args.config),
        "legacy": str(legacy),
        "schema_version": migrated["schema_version"],
    }


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
