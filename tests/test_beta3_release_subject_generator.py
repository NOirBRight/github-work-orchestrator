from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import generate_beta3_release_subject as generator  # noqa: E402
import beta3_release_subject as release_subject  # noqa: E402
from beta3_release_subject import (  # noqa: E402
    ReleaseFileIdentity,
    ReviewedProvenanceIdentity,
)


def test_generator_main_rejects_options_without_reading_or_writing(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        generator,
        "generate_production_subject",
        lambda: pytest.fail("options must be rejected before generation"),
    )
    monkeypatch.setattr(
        generator,
        "write_production_subject_exclusive",
        lambda _subject: pytest.fail("options must be rejected before writing"),
    )
    assert generator.main(["--path", "elsewhere"]) == 1


def test_generator_main_uses_only_fixed_generation_and_writer_seams(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    calls: list[str] = []
    subject = SimpleNamespace(subject_digest="a" * 64)

    def generate():
        calls.append("generate")
        return subject

    class Binding:
        def assert_stable(self) -> None:
            calls.append("assert_stable")

        def close(self) -> None:
            calls.append("close")

    def write(observed):
        assert observed is subject
        calls.append("write")
        return Binding()

    monkeypatch.setattr(generator, "generate_production_subject", generate)
    monkeypatch.setattr(generator, "write_production_subject_exclusive", write)

    assert generator.main([]) == 0
    assert calls == ["generate", "write", "assert_stable", "close"]
    assert capsys.readouterr().out == f"{'a' * 64}\n"


def test_generate_production_subject_binds_fixed_git_and_observer_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    repository_root = (tmp_path / "repository").resolve()
    evidence_root = (tmp_path / "evidence").resolve()
    repository_root.mkdir()
    evidence_root.mkdir()
    runner = ReleaseFileIdentity(
        "run_beta3_live_guard",
        str(repository_root / "scripts" / "run_beta3_live_guard.py"),
        "d" * 64,
    )
    attestors = tuple(
        ReleaseFileIdentity(
            name.removesuffix(".py"),
            str(repository_root / "scripts" / name),
            digest,
        )
        for name, digest in zip(
            release_subject.ATTESTOR_FILENAMES,
            ("e" * 64, "f" * 64, "1" * 64, "2" * 64),
            strict=True,
        )
    )
    reviewed = ReviewedProvenanceIdentity(
        str(repository_root / "scripts" / "beta3_reviewed_provenance.json"),
        "4" * 64,
    )
    monkeypatch.setattr(release_subject, "REPOSITORY_ROOT", repository_root)
    monkeypatch.setattr(release_subject, "EVIDENCE_ROOT", evidence_root)
    monkeypatch.setattr(
        release_subject,
        "_git_snapshot",
        lambda: ("a" * 40, "b" * 40),
    )
    monkeypatch.setattr(
        release_subject,
        "source_tree_digest",
        lambda root: "c" * 64,
    )
    monkeypatch.setattr(
        release_subject,
        "_observer_snapshot",
        lambda root: (runner, attestors, "3" * 64, reviewed),
    )

    subject = release_subject.generate_production_subject()

    assert subject.repository_root == str(repository_root)
    assert subject.evidence_root == str(evidence_root)
    assert subject.merged_main_sha == "a" * 40
    assert subject.merged_main_git_tree == "b" * 40
    assert subject.audited_source_tree_digest == "c" * 64
    assert subject.attestor_bundle_sha256 == "3" * 64
