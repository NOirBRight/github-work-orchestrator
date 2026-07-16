#!/usr/bin/env python3
"""Validate or safely install the canonical GLM-5.2 Worker custom agent."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import tomllib


EXPECTED_NAME = "worker"
EXPECTED_MODEL = "ollama-cloud/glm-5.2"


class AgentConfigError(RuntimeError):
    pass


def template_path() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "worker.toml"


def default_agents_dir() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    return (Path(codex_home) if codex_home else Path.home() / ".codex") / "agents"


def validate_agent(path: Path) -> dict[str, object]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise AgentConfigError(f"cannot read valid TOML from {path}") from error
    required = ("name", "description", "developer_instructions")
    missing = [
        field
        for field in required
        if not isinstance(data.get(field), str) or not data[field].strip()
    ]
    if missing:
        raise AgentConfigError(f"missing required fields: {', '.join(missing)}")
    if data["name"] != EXPECTED_NAME:
        raise AgentConfigError("agent name must override the built-in worker")
    if data.get("model") != EXPECTED_MODEL:
        raise AgentConfigError(f"worker model must be {EXPECTED_MODEL}")
    effort = data.get("model_reasoning_effort")
    if effort != "max":
        raise AgentConfigError(
            "GLM reasoning must be explicit max"
        )
    return {
        "path": str(path.resolve()),
        "name": data["name"],
        "model": data["model"],
        "reasoning": effort,
    }


def install_agent(
    source: Path,
    agents_dir: Path,
    *,
    replace: bool = False,
) -> dict[str, object]:
    source_report = validate_agent(source)
    target = agents_dir / "worker.toml"
    content = source.read_bytes()
    if target.is_file():
        if target.read_bytes() == content:
            return source_report | {
                "target": str(target.resolve()),
                "status": "already-current",
            }
        if not replace:
            raise AgentConfigError(
                f"existing {target} differs; rerun with --replace only after review"
            )
    agents_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="worker-", suffix=".toml.tmp", dir=agents_dir
    )
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, target)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    target_report = validate_agent(target)
    return target_report | {
        "target": str(target.resolve()),
        "status": "replaced" if replace else "installed",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--install", action="store_true")
    parser.add_argument("--agents-dir", type=Path, default=default_agents_dir())
    parser.add_argument("--replace", action="store_true")
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    source = template_path()
    try:
        if arguments.install:
            report = install_agent(
                source,
                arguments.agents_dir,
                replace=arguments.replace,
            )
        else:
            report = validate_agent(source) | {"status": "template-valid"}
            installed = arguments.agents_dir / "worker.toml"
            if installed.is_file():
                report["installed"] = validate_agent(installed)
    except AgentConfigError as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, "agent": report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
