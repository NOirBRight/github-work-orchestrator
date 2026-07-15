#!/usr/bin/env python3
"""Synchronize canonical shared protocols into self-contained Skill packages."""

from __future__ import annotations

import argparse
import hashlib
import json
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
        "worker-execution.md",
    ),
}
PACKAGE_VERSION = "2.1.0"
PACKAGE_MANIFEST = ".skill-package.json"
TEXT_SUFFIXES = {".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"}


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


def package_digest(package: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        path
        for path in package.rglob("*")
        if path.is_file()
        and path.name != PACKAGE_MANIFEST
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    )
    for path in files:
        relative = path.relative_to(package).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        content = path.read_bytes()
        if path.suffix.lower() in TEXT_SUFFIXES:
            # Git may materialize the same tracked text as LF or CRLF. Hash the
            # repository content, not the host checkout convention.
            content = content.replace(b"\r\n", b"\n")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def package_manifest(root: Path, skill: str) -> bytes:
    payload = {
        "schema_version": 1,
        "skill": skill,
        "version": PACKAGE_VERSION,
        "content_sha256": package_digest(root / "skills" / skill),
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def read_manifest(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


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
    for skill, filenames in PACKAGES.items():
        destination = root / "skills" / skill / "references" / "shared"
        packaged = {path.name for path in destination.glob("*") if path.is_file()}
        for stale in sorted(packaged - set(filenames)):
            drift.append(
                f"unexpected package copy: {(destination / stale).relative_to(root)}"
            )
        manifest = root / "skills" / skill / PACKAGE_MANIFEST
        expected = json.loads(package_manifest(root, skill))
        if not manifest.is_file():
            drift.append(f"missing package manifest: {manifest.relative_to(root)}")
        elif read_manifest(manifest) != expected:
            drift.append(f"stale package manifest: {manifest.relative_to(root)}")
    return drift


def find_install_drift(root: Path, install_root: Path) -> list[str]:
    drift: list[str] = []
    for skill in PACKAGES:
        source = root / "skills" / skill
        installed = install_root / skill
        if not installed.is_dir():
            drift.append(f"missing installed Skill: {installed}")
            continue
        source_manifest = source / PACKAGE_MANIFEST
        installed_manifest = installed / PACKAGE_MANIFEST
        if not installed_manifest.is_file():
            drift.append(f"missing installed manifest: {installed_manifest}")
            continue
        if read_manifest(source_manifest) != read_manifest(installed_manifest):
            drift.append(f"installed manifest mismatch: {installed_manifest}")
            continue
        expected_digest = read_manifest(source_manifest)["content_sha256"]
        if package_digest(installed) != expected_digest:
            drift.append(f"installed content mismatch: {installed}")
    return drift


def synchronize(root: Path) -> None:
    for skill, filenames in PACKAGES.items():
        destination = root / "skills" / skill / "references" / "shared"
        expected = set(filenames)
        if destination.is_dir():
            for packaged in destination.glob("*"):
                if not packaged.is_file():
                    continue
                if packaged.name not in expected:
                    packaged.unlink()
    for source, target in targets(root):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    for content, target in compatibility_files(root):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    for skill in PACKAGES:
        manifest = root / "skills" / skill / PACKAGE_MANIFEST
        manifest.write_bytes(package_manifest(root, skill))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root",
    )
    parser.add_argument(
        "--install-root",
        action="append",
        type=Path,
        default=[],
        help="optional Codex skills directory whose package digests must match",
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
    for install_root in args.install_root:
        drift.extend(find_install_drift(root, install_root.resolve()))
    if drift:
        for finding in drift:
            print(f"error: {finding}")
        return 1
    print(f"shared references synchronized across {len(PACKAGES)} Skill packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
