"""Canonical semantic identities used by the V8 execution boundary."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ._canonical import digest_value


_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")


def _require_text(value: object, label: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{label} must be non-empty text")
    return value


def _require_digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be a SHA-256 digest")
    return value


@dataclass(frozen=True)
class AcceptedResultBinding:
    """The exact identities that make a completed Result retainable."""

    ticket_key: str
    result_digest: str
    evidence_digests: tuple[str, ...]
    work_subject_digest: str
    target_facts_digest: str

    def __post_init__(self) -> None:
        _require_text(self.ticket_key, "Ticket key")
        _require_digest(self.result_digest, "Result digest")
        _require_digest(self.work_subject_digest, "Work subject digest")
        _require_digest(self.target_facts_digest, "Target facts digest")
        if type(self.evidence_digests) is not tuple:
            raise ValueError("Evidence digests must be an immutable tuple")
        for digest in self.evidence_digests:
            _require_digest(digest, "Evidence digest")
        canonical = tuple(sorted(self.evidence_digests))
        if canonical != self.evidence_digests or len(set(canonical)) != len(canonical):
            raise ValueError("Evidence digests must be sorted and unique")
        object.__setattr__(self, "evidence_digests", tuple(list(self.evidence_digests)))

    def canonical(self) -> dict[str, Any]:
        return {
            "kind": "accepted_result_binding.v1",
            "ticket_key": self.ticket_key,
            "result_digest": self.result_digest,
            "evidence_digests": list(self.evidence_digests),
            "work_subject_digest": self.work_subject_digest,
            "target_facts_digest": self.target_facts_digest,
        }

    @classmethod
    def from_canonical(cls, value: Mapping[str, Any]) -> "AcceptedResultBinding":
        expected = {
            "kind",
            "ticket_key",
            "result_digest",
            "evidence_digests",
            "work_subject_digest",
            "target_facts_digest",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) != expected
            or value.get("kind") != "accepted_result_binding.v1"
        ):
            raise ValueError("Accepted Result binding schema is not exact")
        evidence = value.get("evidence_digests")
        if type(evidence) is not list:
            raise ValueError("Accepted Result evidence digests must be a list")
        return cls(
            ticket_key=value["ticket_key"],
            result_digest=value["result_digest"],
            evidence_digests=tuple(evidence),
            work_subject_digest=value["work_subject_digest"],
            target_facts_digest=value["target_facts_digest"],
        )


def target_facts_digest(plan_spec: Mapping[str, Any]) -> str:
    return digest_value(
        {
            "kind": "gwo.target-facts.v1",
            "repository": plan_spec["repository"],
            "target_branch": plan_spec["target_branch"],
            "campaign_source": plan_spec["campaign"]["source"],
        }
    )


def work_subject_digest(
    plan_spec: Mapping[str, Any], work_item: Mapping[str, Any]
) -> str:
    return digest_value(
        {
            "kind": "gwo.work-subject.v1",
            "repository": plan_spec["repository"],
            "campaign_key": plan_spec["campaign"]["key"],
            "target_branch": plan_spec["target_branch"],
            "campaign_source": plan_spec["campaign"]["source"],
            "campaign_authority": plan_spec["campaign"]["authority"],
            "policy": plan_spec["policy"],
            "ticket_key": work_item["key"],
            "source": work_item["source"],
            "contract": work_item["contract"],
            "depends_on": list(work_item["depends_on"]),
            "exclusive_resources": list(work_item["exclusive_resources"]),
            "capabilities": list(work_item["capabilities"]),
            "authority": work_item["authority"],
        }
    )


def work_run_key(ticket_key: str, subject_digest: str) -> str:
    _require_text(ticket_key, "Ticket key")
    _require_digest(subject_digest, "Work subject digest")
    return "work-run:" + digest_value(
        {
            "kind": "gwo.work-run-key.v1",
            "ticket_key": ticket_key,
            "work_subject_digest": subject_digest,
        }
    )


def can_preserve_result(
    binding: AcceptedResultBinding,
    successor_work_subject_digest: str,
    successor_target_facts_digest: str,
) -> bool:
    if type(binding) is not AcceptedResultBinding:
        return False
    if (
        type(successor_work_subject_digest) is not str
        or _DIGEST_PATTERN.fullmatch(successor_work_subject_digest) is None
        or type(successor_target_facts_digest) is not str
        or _DIGEST_PATTERN.fullmatch(successor_target_facts_digest) is None
    ):
        return False
    return (
        binding.work_subject_digest == successor_work_subject_digest
        and binding.target_facts_digest == successor_target_facts_digest
    )
