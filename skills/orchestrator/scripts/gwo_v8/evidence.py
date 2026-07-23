"""Typed Evidence validation and deterministic Result Verification."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import TYPE_CHECKING, Any

from ._canonical import digest_value

if TYPE_CHECKING:
    from .runtime import RuntimeObservation


_SHA40 = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ResultClaim:
    attempt_id: str
    node_key: str
    candidate_sha: str
    assertions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TypedEvidence:
    kind: str
    subject: str
    observer_type: str
    observer_id: str
    observed_at: str
    source_ref: str
    payload: dict[str, Any]
    content_digest: str

    @classmethod
    def _capture(
        cls,
        *,
        kind: str,
        subject: str,
        observer_type: str,
        observer_id: str,
        observed_at: str,
        source_ref: str,
        payload: dict[str, Any],
    ) -> TypedEvidence:
        body = {
            "kind": kind,
            "subject": subject,
            "observer_type": observer_type,
            "observer_id": observer_id,
            "observed_at": observed_at,
            "source_ref": source_ref,
            "payload": payload,
        }
        return cls(**body, content_digest=digest_value(body))

    def has_valid_digest(self) -> bool:
        body = {
            "kind": self.kind,
            "subject": self.subject,
            "observer_type": self.observer_type,
            "observer_id": self.observer_id,
            "observed_at": self.observed_at,
            "source_ref": self.source_ref,
            "payload": self.payload,
        }
        return self.content_digest == digest_value(body)


@dataclass(frozen=True)
class VerifiedResult:
    node_key: str
    candidate_sha: str
    evidence_digests: tuple[str, ...]
    result_digest: str


@dataclass(frozen=True)
class VerificationDecision:
    status: str
    result: VerifiedResult | None
    missing_evidence: tuple[str, ...] = ()
    findings: tuple[str, ...] = ()


class EvidenceVerifier:
    """Accept Result Claims only when independently observed Evidence satisfies them."""

    def verify(
        self,
        result_claim: ResultClaim,
        output_contract: dict[str, Any],
        observation: RuntimeObservation | None,
    ) -> VerificationDecision:
        if not _SHA40.fullmatch(result_claim.candidate_sha):
            return VerificationDecision(
                status="rejected",
                result=None,
                findings=("candidate SHA is invalid",),
            )

        if observation is None:
            evidence_set: tuple[TypedEvidence, ...] = ()
            binding = None
        else:
            evidence_set = observation.evidence
            binding = observation.binding
            if observation.result_claim != result_claim:
                return VerificationDecision(
                    status="rejected",
                    result=None,
                    findings=("Runtime observation and Result Claim do not agree",),
                )
            if (
                result_claim.attempt_id != binding.attempt_id
                or result_claim.node_key != binding.node_key
            ):
                return VerificationDecision(
                    status="rejected",
                    result=None,
                    findings=("Result Claim is not bound to the active Attempt",),
                )

        valid: list[TypedEvidence] = []
        findings: list[str] = []
        for evidence in evidence_set:
            if not isinstance(evidence, TypedEvidence):
                findings.append("Evidence envelope is invalid")
                continue
            if (
                binding is None
                or evidence.observer_type != "runtime_adapter"
                or evidence.observer_id != binding.runtime_id
            ):
                findings.append(f"{evidence.kind} Evidence observer is not authoritative")
                continue
            if not evidence.has_valid_digest():
                findings.append(f"{evidence.kind} Evidence digest is invalid")
                continue
            if evidence.subject != result_claim.candidate_sha:
                findings.append(f"{evidence.kind} Evidence subject does not match Candidate")
                continue
            if evidence.kind == "check" and evidence.payload.get("outcome") != "passed":
                return VerificationDecision(
                    status="rejected",
                    result=None,
                    findings=("required check did not pass",),
                )
            valid.append(evidence)

        missing: list[str] = []
        requirements = output_contract.get("required_evidence")
        if not isinstance(requirements, list):
            return VerificationDecision(
                status="rejected",
                result=None,
                findings=("output contract has no required Evidence set",),
            )
        for requirement in requirements:
            if not isinstance(requirement, dict):
                missing.append("invalid-requirement")
                continue
            kind = requirement.get("kind")
            check_id = requirement.get("check_id")
            matched = any(
                evidence.kind == kind
                and (
                    kind != "check"
                    or evidence.payload.get("check_id") == check_id
                )
                for evidence in valid
            )
            if not matched:
                missing.append(
                    f"check:{check_id}" if kind == "check" else str(kind)
                )
        if missing:
            return VerificationDecision(
                status="waiting",
                result=None,
                missing_evidence=tuple(missing),
                findings=tuple(findings),
            )

        evidence_digests = tuple(
            sorted(evidence.content_digest for evidence in valid)
        )
        result_body = {
            "node_key": result_claim.node_key,
            "candidate_sha": result_claim.candidate_sha,
            "evidence_digests": evidence_digests,
        }
        result = VerifiedResult(
            **result_body,
            result_digest=digest_value(result_body),
        )
        return VerificationDecision(
            status="accepted",
            result=result,
            findings=tuple(findings),
        )
