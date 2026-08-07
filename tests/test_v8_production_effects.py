from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_production_effects_requires_the_merged_candidate_and_batch_ports(tmp_path):
    from gwo_v8.production_effects import (
        ProductionCompositionError,
        ProductionWorkRunEffects,
    )

    with pytest.raises(ProductionCompositionError) as raised:
        ProductionWorkRunEffects(
            store_path=tmp_path / "effects.sqlite3",
            runtime_gateways=object(),
            runtime_stale_readbacks=object(),
            work_run_subjects=object(),
            candidate_references=object(),
            candidate_parents=object(),
            candidate_gate=object(),
            batch_requests=object(),
            batch_integrator=object(),
        )
    assert raised.value.code == "PRODUCTION_COMPOSITION_INPUT_INVALID"
