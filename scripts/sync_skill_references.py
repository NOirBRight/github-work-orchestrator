#!/usr/bin/env python3
"""Synchronize canonical shared protocols into self-contained Skill packages."""

from __future__ import annotations

import argparse
from pathlib import Path


SKILLS = (
    "github-work-orchestrator",
    "github-issue-intake",
    "github-issue-worker",
)
SHARED_REFERENCES = (
    "communication-protocol.md",
    "github-state-rules.md",
    "issue-contract.md",
    "lifecycle.md",
    "model-profiles.md",
)


def targets(root: Path):
    for skill in SKILLS:
        for filename in SHARED_REFERENCES:
            source = root / "shared" / filename
            target = root / "skills" / skill / "references" / "shared" / filename
            yield source, target


def find_drift(root: Path) -> list[str]:
    drift: list[str] = []
    for source, target in targets(root):
        if not source.is_file():
            drift.append(f"missing source: {source.relative_to(root)}")
        elif not target.is_file():
            drift.append(f"missing package copy: {target.relative_to(root)}")
        elif source.read_bytes() != target.read_bytes():
            drift.append(f"stale package copy: {target.relative_to(root)}")
    return drift


def synchronize(root: Path) -> None:
    for source, target in targets(root):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift without writing package copies",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    if not args.check:
        synchronize(root)
    drift = find_drift(root)
    if drift:
        for finding in drift:
            print(f"error: {finding}")
        return 1
    print(f"shared references synchronized across {len(SKILLS)} Skill packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
