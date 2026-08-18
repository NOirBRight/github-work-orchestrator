from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

import scripts.render_v8_ga_metadata as renderer
from scripts.verify_v8_ga_release import ReleaseGateError
from tests.test_v8_release_metadata import (
    _renderer_bridge_fixture,
    _renderer_bridge_with_source_payload,
    _render_with_bridge,
)


def _canary_payload_with_digest(
    payload: dict[str, object], digest: str
) -> dict[str, object]:
    return {
        **payload,
        "manifest_ref": f"github://canary-manifest/{digest}",
        "manifest_sha256": digest,
        "package_digest": digest,
    }


@pytest.mark.parametrize(
    "field",
    (
        "canary_repository",
        "canary_evidence_digest",
        "canary_manifest_ref",
        "canary_evidence_refs",
    ),
)
def test_renderer_rejects_activation_canary_binding_drift(tmp_path, field):
    bridge, source_payloads = _renderer_bridge_fixture(tmp_path)
    activation_payload = json.loads(
        json.dumps(source_payloads["production_activation"][1])
    )
    transition_record = activation_payload["transition_record"]
    transition_record[field] = {
        "canary_repository": "foreign/canary",
        "canary_evidence_digest": "8" * 64,
        "canary_manifest_ref": "github://canary-manifest/foreign",
        "canary_evidence_refs": ["github://foreign/evidence"],
    }[field]
    bridge = _renderer_bridge_with_source_payload(
        tmp_path, bridge, "production_activation", activation_payload
    )

    with pytest.raises(ReleaseGateError):
        _render_with_bridge(tmp_path, bridge)


def test_renderer_rejects_canary_package_rebound_without_activation_rebound(
    tmp_path,
):
    bridge, source_payloads = _renderer_bridge_fixture(tmp_path)
    digest = "8" * 64
    package_payload = _canary_payload_with_digest(
        source_payloads["production_canary"][1], digest
    )
    bridge = json.loads(json.dumps(bridge))
    bridge["production_canary"] = {
        **bridge["production_canary"],
        "manifest_ref": package_payload["manifest_ref"],
        "package_digest": digest,
    }
    bridge = _renderer_bridge_with_source_payload(
        tmp_path, bridge, "production_canary", package_payload
    )

    with pytest.raises(ReleaseGateError):
        _render_with_bridge(tmp_path, bridge)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("canary_repository", "foreign/canary"),
        ("canary_evidence_digest", "8" * 64),
        ("canary_manifest_ref", "github://canary-manifest/foreign"),
    ),
)
def test_renderer_rejects_default_readback_canary_binding_drift(
    tmp_path, field, value
):
    bridge, source_payloads = _renderer_bridge_fixture(tmp_path)
    default_payload = json.loads(json.dumps(source_payloads["default_writer"][1]))
    default_payload.update(
        {
            "canary_evidence_digest": "2" * 64,
            "canary_manifest_ref": "github://canary-manifest/2",
            "canary_repository": "NOirBRight/gwo-v8-canary",
        }
    )
    default_payload[field] = value
    bridge = _renderer_bridge_with_source_payload(
        tmp_path, bridge, "default_writer", default_payload
    )

    with pytest.raises(ReleaseGateError):
        _render_with_bridge(tmp_path, bridge)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    (
        (
            "production_activation",
            "guard_receipt.source_writer_generation",
            "v8-generation-1",
        ),
        ("production_activation", "authorization.writer_transition", "v8 -> v8"),
        (
            "production_activation",
            "authorization.target_writer_generation",
            "v8-generation-2",
        ),
    ),
)
def test_renderer_requires_the_authorized_writer_transition(
    tmp_path, section, field, value
):
    bridge, source_payloads = _renderer_bridge_fixture(tmp_path)
    activation_payload = json.loads(
        json.dumps(source_payloads["production_activation"][1])
    )
    owner, nested_field = field.split(".")
    activation_payload[owner][nested_field] = value
    bridge = _renderer_bridge_with_source_payload(
        tmp_path, bridge, section, activation_payload
    )

    with pytest.raises(ReleaseGateError):
        _render_with_bridge(tmp_path, bridge)


def _journal(output_root: Path, staged: Path) -> dict[str, object]:
    return {
        "schema": "gwo-v8-ga-publication.v1",
        "entries": [
            {
                "backup": None,
                "staged": str(staged),
                "target": str(output_root / "CHANGELOG.md"),
            }
        ],
    }


def test_journal_recovery_rejects_output_root_as_staged_and_preserves_sentinel(
    tmp_path,
):
    output_root = tmp_path / "output"
    output_root.mkdir()
    sentinel = output_root / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(ReleaseGateError):
        renderer._rollback_publication(output_root, _journal(output_root, output_root))

    assert output_root.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_journal_recovery_only_removes_a_controlled_staging_root(tmp_path):
    output_root = tmp_path / "output"
    output_root.mkdir()
    sentinel = output_root / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    staging_root = output_root / ".ga-metadata-test"
    staging_root.mkdir()
    staged_file = staging_root / "CHANGELOG.md"
    staged_file.write_text("staged", encoding="utf-8")

    renderer._rollback_publication(output_root, _journal(output_root, staged_file))

    assert output_root.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not staging_root.exists()


def test_journal_recovery_rejects_staged_path_outside_output_root(tmp_path):
    output_root = tmp_path / "output"
    output_root.mkdir()
    sentinel = output_root / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    outside = tmp_path / ".ga-metadata-outside"
    outside.mkdir()
    staged_file = outside / "CHANGELOG.md"
    staged_file.write_text("staged", encoding="utf-8")

    with pytest.raises(ReleaseGateError):
        renderer._rollback_publication(output_root, _journal(output_root, staged_file))

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert outside.exists()


def test_journal_recovery_rejects_reparse_staged_path(tmp_path):
    output_root = tmp_path / "output"
    output_root.mkdir()
    sentinel = output_root / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    real_root = output_root / ".ga-metadata-real"
    real_root.mkdir()
    link_root = output_root / ".ga-metadata-link"
    try:
        link_root.symlink_to(real_root, target_is_directory=True)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")

    try:
        with pytest.raises(ReleaseGateError):
            renderer._rollback_publication(
                output_root, _journal(output_root, link_root / "CHANGELOG.md")
            )
    finally:
        link_root.unlink(missing_ok=True)

    assert output_root.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_direct_renderer_script_help_has_a_working_import_path():
    repository_root = Path(__file__).resolve().parents[1]
    script = repository_root / "scripts" / "render_v8_ga_metadata.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=repository_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--evidence-bridge" in result.stdout
