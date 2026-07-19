#!/usr/bin/env python3
"""Fast syntax, package, metadata, link, and line-budget checks for V6."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import py_compile
import re
import sys


SKILL = "orchestrator"
REQUIRED = (
    ".skill-package.json",
    "SKILL.md",
    "agents/openai.yaml",
    "references/runtime-config.md",
    "scripts/orch.py",
    "scripts/orch_core.py",
    "templates/config.example.json",
    "templates/worker-prompt.md",
    "templates/reviewer-prompt.md",
)
LINE_BUDGETS = {
    "SKILL.md": 220,
    "templates/worker-prompt.md": 60,
    "templates/reviewer-prompt.md": 40,
}


def _sync_module(root: Path):
    path = root / "scripts" / "sync_orchestrator.py"
    spec = importlib.util.spec_from_file_location("sync_orchestrator_validation", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def findings(root: Path) -> list[str]:
    errors: list[str] = []
    package = root / "skills" / SKILL
    for relative in REQUIRED:
        if not (package / relative).is_file():
            errors.append(f"missing package file: skills/{SKILL}/{relative}")
    skill_dirs = sorted(
        path.name
        for path in (root / "skills").iterdir()
        if (path / "SKILL.md").is_file()
    )
    if skill_dirs != [SKILL]:
        errors.append(f"expected sole Skill {SKILL}, found {skill_dirs}")
    if (root / "SKILL.md").exists():
        errors.append("root compatibility SKILL.md must not exist")
    for script in sorted((root / "skills").rglob("*.py")):
        try:
            py_compile.compile(str(script), doraise=True)
        except py_compile.PyCompileError as error:
            errors.append(f"syntax: {script.relative_to(root)}: {error.msg}")
    skill_text = (
        (package / "SKILL.md").read_text(encoding="utf-8") if package.is_dir() else ""
    )
    if not (
        skill_text.startswith("---\n")
        and "\nname: orchestrator\n" in skill_text
        and "\ndescription:" in skill_text
        and "V6.0" in skill_text
    ):
        errors.append("invalid Orchestrator Skill frontmatter/version")
    for relative, maximum in LINE_BUDGETS.items():
        path = package / relative
        if (
            path.is_file()
            and len(path.read_text(encoding="utf-8").splitlines()) > maximum
        ):
            errors.append(f"{relative} exceeds {maximum} lines")
    design = root / "docs" / "orchestrator-v6-living-design.md"
    if design.is_file() and len(design.read_text(encoding="utf-8").splitlines()) > 220:
        errors.append("living design exceeds 220 lines")
    for relative in ("templates/config.example.json", ".skill-package.json"):
        path = package / relative
        if path.is_file():
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                errors.append(f"invalid JSON {relative}: {error}")
    yaml = package / "agents" / "openai.yaml"
    if yaml.is_file():
        text = yaml.read_text(encoding="utf-8")
        for required in (
            "display_name:",
            "short_description:",
            "default_prompt:",
            "$orchestrator",
        ):
            if required not in text:
                errors.append(f"agents/openai.yaml missing {required}")
    markdown = [
        package / "SKILL.md",
        *package.rglob("*.md"),
        *root.joinpath("docs").rglob("*.md"),
    ]
    link_pattern = re.compile(r"\[[^]]+\]\((?!https?://|#)([^)]+)\)")
    for path in set(markdown):
        if not path.is_file():
            continue
        for target in link_pattern.findall(path.read_text(encoding="utf-8")):
            clean = target.split("#", 1)[0]
            if clean and not (path.parent / clean).resolve().exists():
                errors.append(f"broken link: {path.relative_to(root)} -> {target}")
    if package.is_dir():
        errors.extend(_sync_module(root).manifest_drift(package))
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = findings(root)
    for error in errors:
        print(f"error: {error}")
    if errors:
        return 1
    print("quick validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
