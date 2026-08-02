from __future__ import annotations

import sys
from pathlib import Path

import pytest


pytest_plugins = ("v8_successor_test_support",)

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "orchestrator" / "scripts"
sys.path.insert(0, str(SCRIPTS))


@pytest.mark.parametrize(
    "field",
    (
        "source",
        "contract",
        "depends_on",
        "exclusive_resources",
        "capabilities",
        "authority",
        "campaign_authority",
        "policy",
        "campaign_source",
        "target_branch",
    ),
)
def test_work_subject_covers_every_semantic_fact(field):
    from gwo_v8.revision_identity import work_subject_digest
    from v8_successor_test_support import active_plan_spec, changed_plan_spec

    before = active_plan_spec()
    after = changed_plan_spec(field)
    before_item = next(item for item in before["work"] if item["key"] == "issue:108")
    after_item = next(item for item in after["work"] if item["key"] == "issue:108")
    assert work_subject_digest(before, before_item) != work_subject_digest(
        after, after_item
    )


def test_revision_digest_is_not_the_semantic_work_run_key():
    from gwo_v8._canonical import digest_value
    from gwo_v8.revision_identity import work_run_key, work_subject_digest
    from v8_successor_test_support import active_plan_spec, changed_plan_spec

    before = active_plan_spec()
    after = changed_plan_spec("contract")
    before_item = next(item for item in before["work"] if item["key"] == "issue:109")
    after_item = next(item for item in after["work"] if item["key"] == "issue:109")
    before_subject = work_subject_digest(before, before_item)
    after_subject = work_subject_digest(after, after_item)

    assert digest_value(before) != digest_value(after)
    assert before_subject == after_subject
    assert work_run_key("issue:109", before_subject) == work_run_key(
        "issue:109", after_subject
    )


def test_result_retention_requires_exact_subject_and_target():
    from gwo_v8.revision_identity import AcceptedResultBinding, can_preserve_result

    binding = AcceptedResultBinding(
        ticket_key="issue:108",
        result_digest="1" * 64,
        evidence_digests=("2" * 64,),
        work_subject_digest="3" * 64,
        target_facts_digest="4" * 64,
    )
    assert can_preserve_result(binding, "3" * 64, "4" * 64)
    assert not can_preserve_result(binding, "5" * 64, "4" * 64)
    assert not can_preserve_result(binding, "3" * 64, "6" * 64)


def test_evidence_digests_are_canonical_and_immutable():
    from gwo_v8.revision_identity import AcceptedResultBinding

    binding = AcceptedResultBinding(
        ticket_key="issue:108",
        result_digest="1" * 64,
        evidence_digests=("2" * 64, "3" * 64),
        work_subject_digest="4" * 64,
        target_facts_digest="5" * 64,
    )
    canonical = binding.canonical()
    canonical["evidence_digests"].append("6" * 64)
    assert binding.evidence_digests == ("2" * 64, "3" * 64)
    assert AcceptedResultBinding.from_canonical(binding.canonical()) == binding

    with pytest.raises(ValueError):
        AcceptedResultBinding(
            ticket_key="issue:108",
            result_digest="1" * 64,
            evidence_digests=("3" * 64, "2" * 64),
            work_subject_digest="4" * 64,
            target_facts_digest="5" * 64,
        )
    with pytest.raises(ValueError):
        AcceptedResultBinding(
            ticket_key="issue:108",
            result_digest="1" * 64,
            evidence_digests=("2" * 64, "2" * 64),
            work_subject_digest="4" * 64,
            target_facts_digest="5" * 64,
        )
    malformed = binding.canonical()
    malformed["unexpected"] = True
    with pytest.raises(ValueError):
        AcceptedResultBinding.from_canonical(malformed)
