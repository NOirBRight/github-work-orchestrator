#!/usr/bin/env python3
"""Build, check, and atomically install the sole Orchestrator Skill package."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid


SKILL = "orchestrator"
VERSION = "6.1.0"
MANIFEST = ".skill-package.json"
TEXT_SUFFIXES = {".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"}


def package_digest(package: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        path
        for path in package.rglob("*")
        if path.is_file()
        and path.name != MANIFEST
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    )
    for path in files:
        relative = path.relative_to(package).as_posix().encode("utf-8")
        content = path.read_bytes()
        if path.suffix.lower() in TEXT_SUFFIXES:
            content = content.replace(b"\r\n", b"\n")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def expected_manifest(package: Path) -> dict[str, object]:
    return {
        "content_sha256": package_digest(package),
        "schema_version": 1,
        "skill": SKILL,
        "version": VERSION,
    }


def write_manifest(package: Path) -> None:
    content = json.dumps(expected_manifest(package), indent=2, sort_keys=True) + "\n"
    temporary = package / f"{MANIFEST}.tmp"
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, package / MANIFEST)


def manifest_drift(package: Path) -> list[str]:
    manifest = package / MANIFEST
    if not manifest.is_file():
        return [f"missing manifest: {manifest}"]
    try:
        actual = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return [f"invalid manifest: {manifest}: {error}"]
    expected = expected_manifest(package)
    return [] if actual == expected else [f"stale manifest: {manifest}"]


def install_drift(source: Path, install_root: Path) -> list[str]:
    installed = install_root / SKILL
    if not installed.is_dir():
        return [f"missing installed Skill: {installed}"]
    source_manifest = json.loads((source / MANIFEST).read_text(encoding="utf-8"))
    installed_manifest = installed / MANIFEST
    if not installed_manifest.is_file():
        return [f"missing installed manifest: {installed_manifest}"]
    actual_manifest = json.loads(installed_manifest.read_text(encoding="utf-8"))
    if actual_manifest != source_manifest:
        return [f"installed manifest mismatch: {installed_manifest}"]
    if package_digest(installed) != source_manifest["content_sha256"]:
        return [f"installed content mismatch: {installed}"]
    return []


def install_atomic(source: Path, install_root: Path, backup_root: Path) -> None:
    install_root.mkdir(parents=True, exist_ok=True)
    backup_root.mkdir(parents=True, exist_ok=True)
    target = install_root / SKILL
    temporary = install_root / f".{SKILL}.tmp-{uuid.uuid4().hex}"
    shutil.copytree(
        source, temporary, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    if package_digest(temporary) != expected_manifest(source)["content_sha256"]:
        shutil.rmtree(temporary)
        raise RuntimeError(f"temporary install digest mismatch: {temporary}")
    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        surface = install_root.parent.name or "skills"
        backup = backup_root / f"{surface}-{SKILL}-{stamp}-{uuid.uuid4().hex[:8]}"
        shutil.move(str(target), str(backup))
    os.replace(temporary, target)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--install-root", type=Path, action="append", default=[])
    parser.add_argument("--install", action="store_true")
    parser.add_argument(
        "--backup-root", type=Path, default=Path.home() / ".orch" / "install-backups"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.root.resolve() / "skills" / SKILL
    if not args.check:
        write_manifest(source)
    findings = manifest_drift(source)
    if args.install:
        if findings:
            raise SystemExit("refusing to install a stale source manifest")
        for root in args.install_root:
            install_atomic(
                source,
                root.expanduser().resolve(),
                args.backup_root.expanduser().resolve(),
            )
    for root in args.install_root:
        findings.extend(install_drift(source, root.expanduser().resolve()))
    if findings:
        for finding in findings:
            print(f"error: {finding}")
        return 1
    print(f"{SKILL} {VERSION} package synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
