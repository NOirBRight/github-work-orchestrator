"""Typed Evidence validation and deterministic Result Verification."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
from typing import TYPE_CHECKING, Any

from ._canonical import digest_value

if TYPE_CHECKING:
    from .runtime import ReviewAxisObservation, RuntimeObservation


_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_DIGEST64 = re.compile(r"^[0-9a-f]{64}$")


def _required_review_axes(requirement: dict[str, Any]) -> list[str] | None:
    axes = requirement.get("axes")
    specialists = requirement.get("specialist_requirements") or []
    if (
        not isinstance(axes, list)
        or not isinstance(specialists, list)
        or any(axis not in {"standards", "spec"} for axis in axes)
        or any(
            not isinstance(item, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", item) is None
            for item in specialists
        )
    ):
        return None
    return [
        *axes,
        *(f"specialist:{item}" for item in specialists),
    ]


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


@dataclass(frozen=True)
class PublicationEligibility:
    eligible: bool
    candidate_sha: str
    check_evidence_digests: tuple[str, ...] = ()
    review_evidence_digest: str | None = None
    missing_evidence: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewGateDecision:
    status: str
    evidence: TypedEvidence | None
    missing_axes: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


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
                or (
                    evidence.observer_type == "runtime_adapter"
                    and evidence.observer_id != binding.runtime_id
                )
                or evidence.observer_type not in {"runtime_adapter", "kernel"}
                or not evidence.observer_id
            ):
                findings.append(
                    f"{evidence.kind} Evidence observer is not authoritative"
                )
                continue
            if not evidence.has_valid_digest():
                findings.append(f"{evidence.kind} Evidence digest is invalid")
                continue
            if evidence.subject != result_claim.candidate_sha:
                findings.append(
                    f"{evidence.kind} Evidence subject does not match Candidate"
                )
                continue
            valid.append(evidence)

        candidate = next(
            (evidence for evidence in valid if evidence.kind == "candidate"),
            None,
        )
        candidate_tree = (
            None if candidate is None else candidate.payload.get("tree_sha")
        )
        definitions = {
            definition.get("check_id"): definition
            for definition in output_contract.get("checks") or ()
            if isinstance(definition, dict)
            and isinstance(definition.get("check_id"), str)
        }
        semantically_valid: list[TypedEvidence] = []
        for evidence in valid:
            if evidence.kind == "review":
                requirement = output_contract.get("review_requirement") or {
                    "mode": "none",
                    "axes": [],
                }
                payload = evidence.payload
                axes = payload.get("axes")
                required_axes = _required_review_axes(requirement)
                if (
                    payload.get("record_type") != "envelope"
                    or payload.get("attempt_id") != result_claim.attempt_id
                    or payload.get("candidate_sha") != result_claim.candidate_sha
                    or not isinstance(axes, list)
                    or required_axes is None
                    or [record.get("axis") for record in axes] != required_axes
                    or _DIGEST64.fullmatch(str(payload.get("acceptance_digest") or ""))
                    is None
                    or _DIGEST64.fullmatch(
                        str(payload.get("check_manifest_digest") or "")
                    )
                    is None
                ):
                    findings.append("Review Evidence envelope is invalid")
                    continue
                hard = [
                    finding
                    for record in axes
                    if isinstance(record, dict)
                    for finding in record.get("findings") or ()
                    if isinstance(finding, dict) and finding.get("severity") == "hard"
                ]
                if hard:
                    return VerificationDecision(
                        status="rejected",
                        result=None,
                        findings=tuple(
                            (f"{finding.get('code')}: {finding.get('message')}")
                            for finding in hard
                        ),
                    )
                semantically_valid.append(evidence)
                continue
            if evidence.kind != "check":
                semantically_valid.append(evidence)
                continue
            if evidence.payload.get("outcome") != "passed":
                return VerificationDecision(
                    status="rejected",
                    result=None,
                    findings=("required check did not pass",),
                )
            check_id = evidence.payload.get("check_id")
            definition = definitions.get(check_id)
            if definition is None:
                findings.append("check Evidence has no compiled definition")
                continue
            expected_definition_digest = definition.get("definition_digest")
            if (
                not isinstance(expected_definition_digest, str)
                or evidence.payload.get("definition_digest")
                != expected_definition_digest
            ):
                findings.append(f"check:{check_id} definition digest is stale")
                continue
            expected_command_digest = digest_value(definition.get("command"))
            if evidence.payload.get("command_digest") != expected_command_digest:
                findings.append(f"check:{check_id} command digest is invalid")
                continue
            if (
                not isinstance(candidate_tree, str)
                or _SHA40.fullmatch(candidate_tree) is None
                or evidence.payload.get("observed_tree_digest") != candidate_tree
            ):
                findings.append(f"check:{check_id} Candidate tree is invalid")
                continue
            if definition.get("base_sensitive") is True and (
                _SHA40.fullmatch(str(evidence.payload.get("base_sha") or "")) is None
                or _SHA40.fullmatch(
                    str(evidence.payload.get("observed_base_tree_digest") or "")
                )
                is None
            ):
                findings.append(
                    f"check:{check_id} base-sensitive provenance is invalid"
                )
                continue
            if any(
                _DIGEST64.fullmatch(str(evidence.payload.get(name) or "")) is None
                for name in (
                    "environment_digest",
                    "input_projection_digest",
                    "log_digest",
                )
            ):
                findings.append(f"check:{check_id} provenance digest is invalid")
                continue
            expected_environment = list(
                definition.get("environment_requirements") or ()
            )
            environment_identity = evidence.payload.get("environment_identity")
            if (
                evidence.payload.get("environment_requirements") != expected_environment
                or not isinstance(environment_identity, dict)
                or environment_identity.get("platform") is None
                or any(
                    not isinstance(environment_identity.get(requirement), dict)
                    or not environment_identity[requirement].get("executable")
                    or not environment_identity[requirement].get("version")
                    for requirement in expected_environment
                )
                or evidence.payload.get("environment_digest")
                != digest_value(environment_identity)
            ):
                findings.append(f"check:{check_id} toolchain identity is invalid")
                continue
            semantically_valid.append(evidence)
        valid = semantically_valid

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
                and (kind != "check" or evidence.payload.get("check_id") == check_id)
                for evidence in valid
            )
            if not matched:
                missing.append(f"check:{check_id}" if kind == "check" else str(kind))
        if missing:
            return VerificationDecision(
                status="waiting",
                result=None,
                missing_evidence=tuple(missing),
                findings=tuple(findings),
            )

        evidence_digests = tuple(sorted(evidence.content_digest for evidence in valid))
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

    def publication_eligibility(
        self,
        result_claim: ResultClaim,
        output_contract: dict[str, Any],
        observation: RuntimeObservation | None,
    ) -> PublicationEligibility:
        """Derive first-push eligibility without creating lifecycle state."""

        decision = self.verify(
            result_claim,
            output_contract,
            observation,
        )
        evidence_set = () if observation is None else observation.evidence
        check_digests = tuple(
            sorted(
                evidence.content_digest
                for evidence in evidence_set
                if isinstance(evidence, TypedEvidence)
                and evidence.kind == "check"
                and evidence.has_valid_digest()
            )
        )
        review_digests = tuple(
            sorted(
                evidence.content_digest
                for evidence in evidence_set
                if isinstance(evidence, TypedEvidence)
                and evidence.kind == "review"
                and evidence.has_valid_digest()
            )
        )
        return PublicationEligibility(
            eligible=decision.status == "accepted" and decision.result is not None,
            candidate_sha=result_claim.candidate_sha,
            check_evidence_digests=check_digests,
            review_evidence_digest=(review_digests[-1] if review_digests else None),
            missing_evidence=decision.missing_evidence,
            blockers=(decision.findings if decision.status == "rejected" else ()),
        )

    @staticmethod
    def verify_hosted_checks(
        candidate_sha: str,
        definitions: tuple[dict[str, Any], ...],
        evidence: tuple[TypedEvidence, ...],
    ) -> tuple[str, ...]:
        """Validate exact-SHA hosted Check Evidence independently of local checks."""

        expected = {
            str(definition.get("check_id")): definition for definition in definitions
        }
        findings: list[str] = []
        accepted: set[str] = set()
        for item in evidence:
            check_id = str(item.payload.get("check_id"))
            definition = expected.get(check_id)
            if definition is None:
                findings.append(f"unexpected hosted check Evidence: {check_id}")
                continue
            if (
                item.kind != "check"
                or item.subject != candidate_sha
                or item.observer_type != "github"
                or not item.has_valid_digest()
                or item.payload.get("candidate_sha") != candidate_sha
                or item.payload.get("outcome") != "passed"
                or item.payload.get("definition_digest")
                != definition.get("definition_digest")
                or item.payload.get("hosted_name") != definition.get("hosted_name")
            ):
                findings.append(f"hosted check Evidence is invalid: {check_id}")
                continue
            accepted.add(check_id)
        for check_id in sorted(set(expected) - accepted):
            findings.append(f"hosted check Evidence is missing: {check_id}")
        return tuple(findings)

    def assemble_review_evidence(
        self,
        result_claim: ResultClaim,
        review_requirement: dict[str, Any],
        axis_observations: tuple[ReviewAxisObservation, ...],
        *,
        acceptance_digest: str,
        check_manifest_digest: str,
        observer_id: str,
    ) -> ReviewGateDecision:
        """Mechanically preserve independent axis observations as Review Evidence."""

        mode = review_requirement.get("mode")
        required_axes = _required_review_axes(review_requirement)
        if (
            mode not in {"none", "dual_axis", "strict"}
            or required_axes is None
            or _DIGEST64.fullmatch(acceptance_digest) is None
            or _DIGEST64.fullmatch(check_manifest_digest) is None
            or not observer_id
        ):
            return ReviewGateDecision(
                status="rejected",
                evidence=None,
                blockers=("review requirement or binding is invalid",),
            )
        if mode == "none":
            return ReviewGateDecision(
                status="accepted",
                evidence=None,
            )
        by_axis: dict[str, ReviewAxisObservation] = {}
        duplicate_axes: list[str] = []
        for observation in axis_observations:
            if observation.axis in by_axis:
                duplicate_axes.append(observation.axis)
            else:
                by_axis[observation.axis] = observation
        if duplicate_axes:
            return ReviewGateDecision(
                status="rejected",
                evidence=None,
                blockers=tuple(
                    f"duplicate review axis: {axis}"
                    for axis in sorted(set(duplicate_axes))
                ),
            )
        missing: list[str] = []
        for axis in required_axes:
            observation = by_axis.get(axis)
            if (
                observation is None
                or observation.lifecycle != "completed"
                or observation.attempt_id != result_claim.attempt_id
                or observation.candidate_sha != result_claim.candidate_sha
                or observation.spec_digest != acceptance_digest
                or observation.check_manifest_digest != check_manifest_digest
                or _DIGEST64.fullmatch(str(observation.fixed_input_digest or ""))
                is None
                or _DIGEST64.fullmatch(str(observation.output_digest or "")) is None
            ):
                missing.append(axis)
        if missing:
            return ReviewGateDecision(
                status="waiting",
                evidence=None,
                missing_axes=tuple(missing),
            )
        axis_records: list[dict[str, Any]] = []
        blockers: list[str] = []
        for axis in required_axes:
            observation = by_axis[axis]
            findings = [dict(finding) for finding in observation.findings]
            for finding in findings:
                if finding.get("severity") == "hard":
                    blockers.append(
                        (
                            f"{axis}:{finding.get('code')}:"
                            f"{finding.get('location')}:"
                            f"{finding.get('message')}"
                        )
                    )
            axis_records.append(
                {
                    "axis": axis,
                    "action_key": observation.action_key,
                    "fixed_input_digest": observation.fixed_input_digest,
                    "recovery_ordinal": observation.recovery_ordinal,
                    "runtime": {
                        "runtime_id": observation.runtime_id,
                        "agent_id": observation.agent_id,
                        "session_id": observation.session_id,
                        "profile_digest": observation.profile_digest,
                        "provider": observation.provider,
                        "model": observation.model,
                        "thinking": observation.thinking,
                        "mode": observation.mode,
                    },
                    "output_digest": observation.output_digest,
                    "findings": findings,
                }
            )
        evidence = TypedEvidence._capture(
            kind="review",
            subject=result_claim.candidate_sha,
            observer_type="runtime_adapter",
            observer_id=observer_id,
            observed_at=datetime.now(timezone.utc).isoformat(),
            source_ref=(
                f"runtime://{observer_id}/attempt/"
                f"{result_claim.attempt_id}/review/{result_claim.candidate_sha}"
            ),
            payload={
                "record_type": "envelope",
                "attempt_id": result_claim.attempt_id,
                "candidate_sha": result_claim.candidate_sha,
                "acceptance_digest": acceptance_digest,
                "check_manifest_digest": check_manifest_digest,
                "axes": axis_records,
            },
        )
        return ReviewGateDecision(
            status="rejected" if blockers else "accepted",
            evidence=evidence,
            blockers=tuple(blockers),
        )
