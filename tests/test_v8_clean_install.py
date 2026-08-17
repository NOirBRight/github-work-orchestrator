from __future__ import annotations

from pathlib import Path

from scripts.verify_v8_ga_release import clean_install_and_smoke


ROOT = Path(__file__).resolve().parents[1]


def test_clean_install_uses_agents_codex_claude_and_public_smoke(tmp_path):
    result = clean_install_and_smoke(ROOT, tmp_path)

    assert result.surfaces == (".agents", ".codex", ".claude")
    assert result.public_names == ("advance", "inspect", "start")
    assert result.source_checkout_imported is False
