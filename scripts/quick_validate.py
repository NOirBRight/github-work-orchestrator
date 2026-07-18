#!/usr/bin/env python3
"""Run fast syntax, package-drift, and Skill metadata validation."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import py_compile
import sys


def _load_sync(root: Path):
    path = root / "scripts" / "sync_skill_references.py"
    spec = importlib.util.spec_from_file_location("gwo_sync_skill_references", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def findings(root: Path) -> list[str]:
    errors: list[str] = []
    for script in sorted((root / "skills").rglob("*.py")):
        try:
            py_compile.compile(str(script), doraise=True)
        except py_compile.PyCompileError as error:
            errors.append(f"syntax: {script.relative_to(root)}: {error.msg}")
    sync = _load_sync(root)
    errors.extend(sync.find_drift(root))
    for skill in sync.PACKAGES:
        path = root / "skills" / skill / "SKILL.md"
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if (
            not text.startswith("---\n")
            or "\nname:" not in text
            or "\ndescription:" not in text
        ):
            errors.append(f"invalid Skill frontmatter: {path.relative_to(root)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    errors = findings(root)
    for error in errors:
        print(f"error: {error}")
    if errors:
        return 1
    print("quick validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
