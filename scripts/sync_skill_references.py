#!/usr/bin/env python3
"""Synchronize canonical shared protocols into self-contained Skill packages."""

from __future__ import annotations

import argparse
from pathlib import Path


PACKAGES = {
    "github-work-orchestrator": (
        "communication-protocol.md",
        "github-state-rules.md",
        "issue-contract.md",
        "lifecycle.md",
        "model-profiles.md",
        "verification-policy.md",
    ),
    "github-issue-intake": (
        "communication-protocol.md",
        "github-state-rules.md",
        "issue-contract.md",
        "lifecycle.md",
        "verification-policy.md",
    ),
    "github-issue-worker": (
        "communication-protocol.md",
        "github-state-rules.md",
        "lifecycle.md",
        "model-profiles.md",
        "verification-policy.md",
    ),
}


def targets(root: Path):
    for skill, filenames in PACKAGES.items():
        for filename in filenames:
            source = root / "shared" / filename
            target = root / "skills" / skill / "references" / "shared" / filename
            yield source, target


def compatibility_skill(root: Path) -> bytes:
    canonical = (
        root / "skills" / "github-work-orchestrator" / "SKILL.md"
    ).read_text(encoding="utf-8")
    frontmatter = canonical.split("---", 2)[1].strip()
    wrapper = f"""---
{frontmatter}
---

# Compatibility entry point

Read [the packaged Orchestrator Skill](skills/github-work-orchestrator/SKILL.md)
completely and follow it. Treat `skills/github-work-orchestrator` as `<skill>`
and resolve every reference and script from that directory.
"""
    return wrapper.encode("utf-8")


def compatibility_files(root: Path):
    yield compatibility_skill(root), root / "SKILL.md"
    source = root / "skills" / "github-work-orchestrator" / "agents" / "openai.yaml"
    yield source.read_bytes(), root / "agents" / "openai.yaml"


def find_drift(root: Path) -> list[str]:
    drift: list[str] = []
    for source, target in targets(root):
        if not source.is_file():
            drift.append(f"missing source: {source.relative_to(root)}")
        elif not target.is_file():
            drift.append(f"missing package copy: {target.relative_to(root)}")
        elif source.read_bytes() != target.read_bytes():
            drift.append(f"stale package copy: {target.relative_to(root)}")
    for expected, target in compatibility_files(root):
        if not target.is_file():
            drift.append(f"missing compatibility file: {target.relative_to(root)}")
        elif expected != target.read_bytes():
            drift.append(f"stale compatibility file: {target.relative_to(root)}")
    return drift


def synchronize(root: Path) -> None:
    for skill, filenames in PACKAGES.items():
        destination = root / "skills" / skill / "references" / "shared"
        expected = set(filenames)
        if destination.is_dir():
            for packaged in destination.glob("*.md"):
                if packaged.name not in expected:
                    packaged.unlink()
    for source, target in targets(root):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    for content, target in compatibility_files(root):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


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
    print(f"shared references synchronized across {len(PACKAGES)} Skill packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
