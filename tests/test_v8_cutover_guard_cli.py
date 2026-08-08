from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

from conftest import load_module


ROOT = Path(__file__).resolve().parents[1]
for scripts_path in (ROOT / "scripts", ROOT / "skills" / "orchestrator" / "scripts"):
    if str(scripts_path) not in sys.path:
        sys.path.insert(0, str(scripts_path))

cutover_cli = load_module("cutover_guard_cli_test", ROOT / "scripts" / "cutover_guard.py")

from tests.cutover_guard_test_support import (  # noqa: E402
    EXPECTED_CHECK_IDS,
    GuardHarness,
    RecordingGuard,
    RecordingLiveHost,
    write_valid_bundle,
)


def test_cli_go_prints_exact_human_evidence_and_never_activates(tmp_path, capsys):
    bundle = write_valid_bundle(tmp_path / "cutover-readback.json")
    calls = []

    exit_code = cutover_cli.main(
        ["--bundle", str(bundle), "--json"],
        guard_factory=lambda sources: RecordingGuard(sources, calls),
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["decision"] == "GO"
    assert payload["evidence_mode"] == "readback_bundle"
    assert payload["activation_performed"] is False
    assert payload["receipt"]["receipt_digest"]
    assert payload["blockers"] == []
    assert calls == ["evaluate"]


def test_cli_live_invokes_composed_read_ports_without_a_bundle(tmp_path, capsys):
    subject = replace(
        GuardHarness.valid().subject,
        source_tree_digest=cutover_cli.source_tree_digest(tmp_path),
    )
    calls = []

    exit_code = cutover_cli.main(
        [
            "--live",
            "--repository", subject.repository,
            "--control-branch", subject.control_branch,
            "--target-branch", subject.target_branch,
            "--source-writer-generation", subject.source_writer_generation,
            "--target-writer-generation", subject.target_writer_generation,
            "--store-generation", subject.store_generation,
            "--source-commit", subject.source_commit,
            "--package-root", str(tmp_path),
            "--install-root", str(tmp_path / ".agents" / "skills"),
            "--install-root", str(tmp_path / ".codex" / "skills"),
            "--install-root", str(tmp_path / ".claude" / "skills"),
            "--json",
        ],
        live_host_factory=lambda request: RecordingLiveHost(subject, calls),
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["evidence_mode"] == "live_composed_ports"
    assert payload["activation_performed"] is False
    assert calls == ["check"]


def test_cli_no_go_prints_all_named_blockers_and_returns_two(tmp_path, capsys):
    bundle = write_valid_bundle(
        tmp_path / "cutover-readback.json",
        running_v2=True,
    )

    exit_code = cutover_cli.main(["--bundle", str(bundle), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["decision"] == "NO_GO"
    assert payload["receipt"] is None
    assert "CUTOVER_V2_ACTIVE" in {item["code"] for item in payload["blockers"]}
    assert payload["activation_performed"] is False


def test_cli_malformed_bundle_returns_three_and_does_not_rewrite_input(tmp_path, capsys):
    bundle = tmp_path / "cutover-readback.json"
    bundle.write_text('{"schema":"wrong"}\n', encoding="utf-8")
    original = bundle.read_bytes()

    exit_code = cutover_cli.main(["--bundle", str(bundle), "--json"])

    assert exit_code == 3
    assert json.loads(capsys.readouterr().out)["error_code"] == "CUTOVER_BUNDLE_INVALID"
    assert bundle.read_bytes() == original


def test_cli_parser_has_no_activation_or_install_option():
    options = {action.dest for action in cutover_cli.build_parser()._actions}

    assert {"activate", "install", "write", "rollback", "go"}.isdisjoint(options)


def test_cli_parser_rejects_forbidden_install_abbreviation():
    with pytest.raises(SystemExit):
        cutover_cli.parse_args(
            ["--bundle", "readback.json", "--install", "forbidden-root"]
        )


def test_cli_default_text_contains_every_check_and_digest(tmp_path, capsys):
    bundle = write_valid_bundle(tmp_path / "cutover-readback.json")

    exit_code = cutover_cli.main(["--bundle", str(bundle)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "GO" in output
    for check_id in EXPECTED_CHECK_IDS:
        assert check_id in output
    assert "receipt_digest=" in output
