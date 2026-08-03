"""Read-only authoritative tracker and Policy Witness readback."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ._canonical import (
    CanonicalJsonError,
    canonical_bytes,
    digest_bytes,
    digest_value,
    load_canonical_json,
)
from .activation import GitHubContentClient
from .github_snapshot import GitHubHumanApprovalReadClient
from .human_gate import (
    HUMAN_REQUIRED_CHANGES,
    HUMAN_SOURCE_STATES,
    HumanDecisionRecord,
    HumanGateError,
    HumanSourceReadback,
    ReplanBudgetPolicy,
)
from .plan_control import CampaignHandle, PlanControlError, _normalize_ticket


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_OID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_ISSUE_KEY = re.compile(r"^issue:[1-9][0-9]*$")
_DISALLOWED_SCHEMES = ("chat:", "model:", "webhook:", "local:", "file:")
_TRUSTED_ACTOR_SCHEMES = ("workflow://", "github-actions://", "github://")
_DEFAULT_APPROVAL_ACTOR_REF = "workflow://gwo-human-gate"

_APPROVAL_FIELDS = {
    "kind",
    "decision_id",
    "classification_action_id",
    "predecessor_revision_digest",
    "evidence_digests",
    "required_change",
    "approval_state",
    "approval_record_ref",
    "approval_actor_ref",
    "source_change_digest",
}
_SOURCE_REF_FIELDS = {
    "tracker_source_ref",
    "source_ref",
    "human_source_ref",
}
_POLICY_REF_FIELDS = {
    "policy_witness_ref",
    "policy_ref",
}
_SOURCE_FIELDS = {
    "kind",
    "repository",
    "campaign_key",
    "target_branch",
    "campaign_source",
    "membership",
    "tickets",
    "product_release",
    "source_change_digest",
}
_POLICY_FIELDS = {
    "kind",
    "replan",
    "digest",
    "schema_version",
    "ref",
    "authority_grants",
    "allowed_capabilities",
    "exclusive_resources",
}

_STATE_CODES = {
    "pending": "HUMAN_SOURCE_READBACK_PENDING",
    "approved": "HUMAN_SOURCE_APPROVED",
    "rejected": "HUMAN_SOURCE_REJECTED",
    "incomplete": "HUMAN_SOURCE_READBACK_INCOMPLETE",
    "ambiguous": "HUMAN_SOURCE_AMBIGUOUS",
    "reverted": "HUMAN_SOURCE_REVERTED",
    "out_of_policy": "HUMAN_SOURCE_OUT_OF_POLICY",
}


class _IncompleteSource(Exception):
    """The durable source is visible but does not contain the full projection."""


@dataclass(frozen=True)
class _ReadRound:
    approval_bytes: bytes | None
    approval: dict[str, Any] | None
    tracker_bytes: bytes | None
    policy_bytes: bytes | None
    tracker_blob_sha: str | None = None
    policy_blob_sha: str | None = None


def _fail(code: str, detail: str) -> None:
    raise HumanGateError(code, detail)


def _text(value: Any, label: str, *, code: str) -> str:
    if type(value) is not str or not value:
        _fail(code, f"{label} must be non-empty exact text")
    return value


def _digest(value: Any, label: str, *, code: str) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        _fail(code, f"{label} must be a lowercase SHA-256 digest")
    return value


def _closed(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != expected:
        _fail("HUMAN_SOURCE_READBACK_INVALID", f"{label} schema is not closed")
    return value


def _content_ref(
    value: Any,
    *,
    default_branch: str,
    default_path: str,
    label: str,
) -> tuple[str, str]:
    if value is None:
        return default_branch, default_path
    if type(value) is dict:
        if set(value) != {"branch", "path"}:
            _fail("HUMAN_SOURCE_READBACK_INVALID", f"{label} is not an exact content reference")
        branch = value["branch"]
        path = value["path"]
    else:
        branch = default_branch
        path = value
    _text(branch, f"{label}.branch", code="HUMAN_SOURCE_READBACK_INVALID")
    _text(path, f"{label}.path", code="HUMAN_SOURCE_READBACK_INVALID")
    if (
        _BRANCH.fullmatch(branch) is None
        or not path.startswith(".gwo-v8/")
        or path != path.strip("/")
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or "://" in path
    ):
        if any(path.casefold().startswith(scheme) for scheme in _DISALLOWED_SCHEMES):
            _fail("HUMAN_APPROVAL_UNAUTHORIZED", f"{label} is not an authoritative GitHub content reference")
        _fail("HUMAN_SOURCE_READBACK_INVALID", f"{label} is not a safe GitHub content reference")
    return branch, path


def _canonical_content(
    content_client: GitHubContentClient,
    *,
    repository: str,
    branch: str,
    path: str,
    label: str,
) -> tuple[bytes | None, Any | None, str | None]:
    try:
        content = content_client.read(repository, branch, path)
    except Exception as error:
        raise HumanGateError(
            "HUMAN_SOURCE_READBACK_INVALID",
            f"{label} could not be read from the authoritative GitHub content source",
        ) from error
    if content is None:
        return None, None, None
    payload = getattr(content, "content", None)
    if type(payload) is not bytes:
        _fail("HUMAN_SOURCE_READBACK_INVALID", f"{label} did not return exact bytes")
    try:
        value = load_canonical_json(payload)
    except CanonicalJsonError as error:
        raise HumanGateError(
            "HUMAN_SOURCE_READBACK_INVALID",
            f"{label} is not exact canonical JSON",
        ) from error
    blob_sha = getattr(content, "blob_sha", None)
    if blob_sha is not None and (type(blob_sha) is not str or not blob_sha):
        _fail("HUMAN_SOURCE_READBACK_INVALID", f"{label} blob identity is invalid")
    return payload, value, blob_sha


def _approval_state(value: Any) -> str:
    if type(value) is not str or value not in HUMAN_SOURCE_STATES:
        _fail("HUMAN_SOURCE_READBACK_INVALID", "approval_state is outside the closed union")
    return value


def _approval_refs(value: Mapping[str, Any]) -> tuple[Any, Any]:
    source_keys = [key for key in _SOURCE_REF_FIELDS if key in value]
    policy_keys = [key for key in _POLICY_REF_FIELDS if key in value]
    if len(source_keys) > 1 or len(policy_keys) > 1:
        _fail("HUMAN_SOURCE_READBACK_INVALID", "approval contains competing source references")
    source_ref = value[source_keys[0]] if source_keys else None
    policy_ref = value[policy_keys[0]] if policy_keys else None
    return source_ref, policy_ref


def _readback_digest(
    *,
    decision_id: str,
    state: str,
    approval_digest: str | None,
    tracker_digest: str | None,
    policy_digest: str | None,
    source_change_digest: str | None,
    code: str,
) -> str:
    return digest_value(
        {
            "decision_id": decision_id,
            "state": state,
            "approval_record_digest": approval_digest,
            "tracker_source_digest": tracker_digest,
            "policy_witness_digest": policy_digest,
            "source_change_digest": source_change_digest,
            "code": code,
        }
    )


class GitHubHumanApprovalSource:
    """Read a durable human approval and its exact GitHub source projection.

    This class deliberately receives only read ports.  It has no GitHub
    mutation method and never receives a PlanControl or activation object.
    """

    def __init__(
        self,
        approval_client: GitHubHumanApprovalReadClient,
        content_client: GitHubContentClient,
        control_branch: str,
        target_branch: str,
        policy_path: str = ".gwo-v8/policy-witness.json",
        tracker_source_path: str = ".gwo-v8/human-tracker-source.json",
        *,
        approval_actor_ref: str | None = None,
        allowed_approval_actor_refs: tuple[str, ...] | None = None,
    ):
        if type(control_branch) is not str or _BRANCH.fullmatch(control_branch) is None:
            raise HumanGateError("PLAN_CONTROL_COMPOSITION_INVALID", "control branch is invalid")
        if type(target_branch) is not str or _BRANCH.fullmatch(target_branch) is None:
            raise HumanGateError("PLAN_CONTROL_COMPOSITION_INVALID", "target branch is invalid")
        for path, label in (
            (policy_path, "policy path"),
            (tracker_source_path, "tracker source path"),
        ):
            _content_ref(
                {"branch": control_branch, "path": path},
                default_branch=control_branch,
                default_path=path,
                label=label,
            )
        if approval_actor_ref is not None:
            _text(
                approval_actor_ref,
                "approval_actor_ref",
                code="PLAN_CONTROL_COMPOSITION_INVALID",
            )
        if allowed_approval_actor_refs is not None and (
            type(allowed_approval_actor_refs) is not tuple
            or not allowed_approval_actor_refs
            or any(type(value) is not str or not value for value in allowed_approval_actor_refs)
            or len(set(allowed_approval_actor_refs)) != len(allowed_approval_actor_refs)
        ):
            raise HumanGateError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "allowed approval actors are invalid",
            )
        if approval_actor_ref is not None and allowed_approval_actor_refs is not None:
            raise HumanGateError(
                "PLAN_CONTROL_COMPOSITION_INVALID",
                "approval actor identity and approval actor allowlist are ambiguous",
            )
        self.approval_client = approval_client
        self.content_client = content_client
        self.control_branch = control_branch
        self.target_branch = target_branch
        self.policy_path = policy_path
        self.tracker_source_path = tracker_source_path
        self.approval_actor_ref = approval_actor_ref
        self.allowed_approval_actor_refs = allowed_approval_actor_refs

    def _validate_input(
        self,
        handle: CampaignHandle,
        decision: HumanDecisionRecord,
        readback_ref: str,
    ) -> None:
        if type(handle) is not CampaignHandle or type(decision) is not HumanDecisionRecord:
            _fail("HUMAN_APPROVAL_INPUT_INVALID", "human source read identity is invalid")
        if decision.campaign != handle:
            _fail("HUMAN_APPROVAL_INPUT_INVALID", "Decision is bound to another Campaign")
        _text(readback_ref, "readback_ref", code="HUMAN_APPROVAL_INPUT_INVALID")
        lowered = readback_ref.casefold()
        if any(lowered.startswith(scheme) for scheme in _DISALLOWED_SCHEMES):
            _fail(
                "HUMAN_APPROVAL_UNAUTHORIZED",
                "chat, model, webhook, and local approval references are not authoritative",
            )

    def _validate_actor(self, actor: Any) -> str:
        _text(actor, "approval_actor_ref", code="HUMAN_APPROVAL_UNAUTHORIZED")
        if self.allowed_approval_actor_refs is not None:
            if actor not in self.allowed_approval_actor_refs:
                _fail("HUMAN_APPROVAL_UNAUTHORIZED", "approval actor is not the configured workflow")
            return actor
        if self.approval_actor_ref is not None:
            if actor != self.approval_actor_ref:
                _fail("HUMAN_APPROVAL_UNAUTHORIZED", "approval actor is not the configured workflow")
            return actor
        # A URI scheme is not an identity.  When a host does not provide an
        # explicit allowlist, use one exact built-in workflow identity rather
        # than accepting an arbitrary actor under a trusted-looking prefix.
        if actor != _DEFAULT_APPROVAL_ACTOR_REF:
            _fail("HUMAN_APPROVAL_UNAUTHORIZED", "approval actor is not a durable workflow identity")
        return actor

    def _validate_approval(
        self,
        value: Any,
        *,
        handle: CampaignHandle,
        decision: HumanDecisionRecord,
        readback_ref: str,
    ) -> tuple[dict[str, Any], bytes, str]:
        if type(value) is not dict:
            _fail("HUMAN_SOURCE_READBACK_INVALID", "approval record is not an object")
        unknown = set(value) - (_APPROVAL_FIELDS | _SOURCE_REF_FIELDS | _POLICY_REF_FIELDS)
        if unknown:
            _fail("HUMAN_SOURCE_READBACK_INVALID", "approval record contains unknown fields")
        if not _APPROVAL_FIELDS.issubset(value):
            _fail("HUMAN_SOURCE_READBACK_INVALID", "approval record is incomplete")
        if value["kind"] != "gwo.human-approval.v1":
            _fail("HUMAN_SOURCE_READBACK_INVALID", "approval record kind is invalid")
        if value["decision_id"] != decision.decision_id:
            _fail("HUMAN_SOURCE_DIGEST_MISMATCH", "approval Decision ID differs from the requested Decision")
        if value["classification_action_id"] != decision.classification_action_id:
            _fail("HUMAN_SOURCE_DIGEST_MISMATCH", "approval action differs from the Decision")
        if value["predecessor_revision_digest"] != decision.plan_revision_digest:
            _fail("HUMAN_SOURCE_DIGEST_MISMATCH", "approval predecessor differs from the Decision")
        if value["required_change"] not in HUMAN_REQUIRED_CHANGES:
            _fail("HUMAN_SOURCE_READBACK_INVALID", "approval required_change is invalid")
        if value["required_change"] != decision.required_change:
            _fail("HUMAN_REQUIRED_CHANGE_MISMATCH", "approval required_change differs from the Decision")
        evidence = value["evidence_digests"]
        if (
            type(evidence) is not list
            or any(type(item) is not str or _DIGEST.fullmatch(item) is None for item in evidence)
            or evidence != sorted(set(evidence))
            or tuple(evidence) != decision.evidence_digests
        ):
            _fail("HUMAN_SOURCE_DIGEST_MISMATCH", "approval Evidence digests differ from the Decision")
        _digest(
            value["predecessor_revision_digest"],
            "approval predecessor_revision_digest",
            code="HUMAN_SOURCE_READBACK_INVALID",
        )
        _digest(
            value["source_change_digest"],
            "approval source_change_digest",
            code="HUMAN_SOURCE_READBACK_INVALID",
        )
        if value["approval_record_ref"] != readback_ref:
            _fail("HUMAN_SOURCE_DIGEST_MISMATCH", "approval record reference differs from the readback reference")
        self._validate_actor(value["approval_actor_ref"])
        state = _approval_state(value["approval_state"])
        source_ref, policy_ref = _approval_refs(value)
        if state == "approved":
            if source_ref is None or policy_ref is None:
                _fail("HUMAN_SOURCE_READBACK_INCOMPLETE", "approved source references are missing")
        approval_bytes = canonical_bytes(value)
        return value, approval_bytes, state

    def _read_round(
        self,
        handle: CampaignHandle,
        decision: HumanDecisionRecord,
        readback_ref: str,
    ) -> _ReadRound:
        try:
            approval_value = self.approval_client.read_human_approval(
                handle.repository,
                readback_ref,
            )
        except HumanGateError:
            raise
        except Exception as error:
            raise HumanGateError(
                "HUMAN_SOURCE_READBACK_INVALID",
                "durable human approval could not be read",
            ) from error
        if approval_value is None:
            return _ReadRound(None, None, None, None)
        approval, approval_bytes, state = self._validate_approval(
            approval_value,
            handle=handle,
            decision=decision,
            readback_ref=readback_ref,
        )
        if state != "approved":
            return _ReadRound(approval_bytes, approval, None, None)
        source_ref, policy_ref = _approval_refs(approval)
        source_branch, source_path = _content_ref(
            source_ref,
            default_branch=self.control_branch,
            default_path=self.tracker_source_path,
            label="tracker_source_ref",
        )
        policy_branch, policy_path = _content_ref(
            policy_ref,
            default_branch=self.control_branch,
            default_path=self.policy_path,
            label="policy_witness_ref",
        )
        tracker_bytes, _, tracker_blob_sha = _canonical_content(
            self.content_client,
            repository=handle.repository,
            branch=source_branch,
            path=source_path,
            label="tracker source",
        )
        policy_bytes, _, policy_blob_sha = _canonical_content(
            self.content_client,
            repository=handle.repository,
            branch=policy_branch,
            path=policy_path,
            label="Policy Witness",
        )
        return _ReadRound(
            approval_bytes,
            approval,
            tracker_bytes,
            policy_bytes,
            tracker_blob_sha,
            policy_blob_sha,
        )

    @staticmethod
    def _validate_campaign_source(value: Any, *, target_branch: str) -> None:
        source = _closed(value, {"input_ref", "resolved_commit_oid", "tree_oid", "digest"}, "campaign_source")
        if (
            source["input_ref"] != f"refs/heads/{target_branch}"
            or type(source["resolved_commit_oid"]) is not str
            or _OID.fullmatch(source["resolved_commit_oid"]) is None
            or type(source["tree_oid"]) is not str
            or _OID.fullmatch(source["tree_oid"]) is None
        ):
            _fail("HUMAN_SOURCE_READBACK_INVALID", "campaign source identity is invalid")
        observed = {key: source[key] for key in ("input_ref", "resolved_commit_oid", "tree_oid")}
        if source["digest"] != digest_value(observed):
            _fail("HUMAN_SOURCE_DIGEST_MISMATCH", "campaign source digest does not bind its exact identity")
        _digest(source["digest"], "campaign_source.digest", code="HUMAN_SOURCE_READBACK_INVALID")

    @staticmethod
    def _validate_membership(value: Any) -> tuple[str, ...]:
        membership = _closed(value, {"ticket_keys", "digest"}, "membership")
        keys = membership["ticket_keys"]
        if (
            type(keys) is not list
            or any(type(key) is not str or _ISSUE_KEY.fullmatch(key) is None for key in keys)
            or keys != sorted(set(keys))
        ):
            raise _IncompleteSource("membership is not complete")
        core = {"ticket_keys": keys}
        _digest(membership["digest"], "membership.digest", code="HUMAN_SOURCE_READBACK_INVALID")
        if membership["digest"] != digest_value(core):
            _fail("HUMAN_SOURCE_DIGEST_MISMATCH", "membership digest does not bind its exact keys")
        if not keys:
            raise _IncompleteSource("membership is empty")
        return tuple(keys)

    @staticmethod
    def _validate_tickets(
        value: Any,
        *,
        repository: str,
        membership: tuple[str, ...],
    ) -> None:
        if type(value) is not list:
            raise _IncompleteSource("tickets are missing")
        try:
            tickets = sorted(value, key=lambda item: item.get("key") if type(item) is dict else "")
        except AttributeError as error:
            raise _IncompleteSource("tickets are malformed") from error
        if tickets != value:
            raise _IncompleteSource("tickets are not in canonical membership order")
        if [ticket.get("key") for ticket in tickets if type(ticket) is dict] != list(membership):
            raise _IncompleteSource("membership and ticket set differ")
        for ticket in tickets:
            if type(ticket) is not dict:
                raise _IncompleteSource("ticket is not an object")
            try:
                normalized = _normalize_ticket(ticket, repository=repository)
            except PlanControlError as error:
                raise _IncompleteSource("ticket contract or blocker graph is incomplete") from error
            if normalized != ticket:
                raise _IncompleteSource("ticket contract is not canonical")

    def _validate_tracker_source(
        self,
        payload: bytes,
        *,
        handle: CampaignHandle,
        decision: HumanDecisionRecord,
        approval: Mapping[str, Any],
    ) -> str:
        try:
            source = load_canonical_json(payload)
        except CanonicalJsonError as error:
            raise HumanGateError("HUMAN_SOURCE_READBACK_INVALID", "tracker source is not canonical") from error
        if type(source) is not dict:
            _fail("HUMAN_SOURCE_READBACK_INVALID", "tracker source is not an object")
        if set(source) != _SOURCE_FIELDS:
            _fail("HUMAN_SOURCE_READBACK_INVALID", "tracker source schema is not closed")
        if source["kind"] != "gwo.human-tracker-source.v1":
            _fail("HUMAN_SOURCE_READBACK_INVALID", "tracker source kind is invalid")
        if source["repository"] != handle.repository or source["campaign_key"] != handle.campaign_key:
            _fail("HUMAN_SOURCE_DIGEST_MISMATCH", "tracker source Campaign identity differs")
        if source["target_branch"] != self.target_branch:
            _fail("HUMAN_SOURCE_DIGEST_MISMATCH", "tracker source target differs")
        _digest(
            source["source_change_digest"],
            "tracker source_change_digest",
            code="HUMAN_SOURCE_READBACK_INVALID",
        )
        source_core = {
            key: source[key]
            for key in _SOURCE_FIELDS
            if key != "source_change_digest"
        }
        if source["source_change_digest"] != digest_value(source_core):
            _fail(
                "HUMAN_SOURCE_DIGEST_MISMATCH",
                "tracker source_change_digest does not bind its complete projection",
            )
        self._validate_campaign_source(source["campaign_source"], target_branch=self.target_branch)
        try:
            membership = self._validate_membership(source["membership"])
            self._validate_tickets(
                source["tickets"],
                repository=handle.repository,
                membership=membership,
            )
        except _IncompleteSource:
            raise
        product_release = source["product_release"]
        if decision.required_change == "product" and product_release is None:
            raise _IncompleteSource("product/release facts are missing")
        # Authority approvals use the Policy Witness digest domain.  Do not
        # compare that digest with the tracker projection merely because the
        # tracker is also read as part of the complete source bundle.
        # Durable records written before the source-domain split omitted the
        # predecessor snapshot identity.  Keep those records readable as the
        # historical tracker-domain shape; every newly generated Decision has
        # the snapshot identity and therefore uses the strict policy domain.
        policy_domain = (
            decision.required_source.source_kind == "policy"
            and decision.required_source.predecessor_snapshot_digest is not None
        )
        if not policy_domain:
            # Upstream workflows commonly bind approval to the complete source
            # bytes, while the source projection also carries its own semantic
            # change digest.  Accept either exact binding; anything else means
            # the authoritative source changed after approval.
            if approval["source_change_digest"] not in {
                digest_bytes(payload),
                source["source_change_digest"],
            }:
                _fail(
                    "HUMAN_SOURCE_CHANGED_DURING_READBACK",
                    "approval does not bind the authoritative tracker bytes",
                )
            if (
                decision.required_source.predecessor_source_digest
                == source["source_change_digest"]
            ):
                return "reverted"
        return "approved"

    @staticmethod
    def _validate_policy(payload: bytes) -> dict[str, Any]:
        try:
            policy = load_canonical_json(payload)
        except CanonicalJsonError as error:
            raise HumanGateError("HUMAN_SOURCE_READBACK_INVALID", "Policy Witness is not canonical") from error
        compiler_fields = {
            "schema_version",
            "ref",
            "digest",
            "authority_grants",
            "allowed_capabilities",
            "exclusive_resources",
        }
        if type(policy) is not dict:
            _fail("REPLAN_BUDGET_POLICY_INVALID", "Policy Witness lacks the closed replan budget")
        if set(policy) - _POLICY_FIELDS:
            _fail("HUMAN_SOURCE_READBACK_INVALID", "Policy Witness contains unknown fields")
        complete = compiler_fields | {"replan"}
        complete_with_kind = complete | {"kind"}
        if set(policy) not in (complete, complete_with_kind):
            _fail(
                "REPLAN_BUDGET_POLICY_INVALID",
                "approved Policy Witness lacks the complete compiler authority projection",
            )
        if "kind" in policy and policy["kind"] != "gwo.policy-witness.v1":
            _fail("HUMAN_SOURCE_READBACK_INVALID", "Policy Witness kind is invalid")
        _digest(policy["digest"], "Policy Witness digest", code="HUMAN_SOURCE_READBACK_INVALID")
        core = {key: value for key, value in policy.items() if key != "digest"}
        if policy["digest"] != digest_value(core):
            _fail("HUMAN_SOURCE_DIGEST_MISMATCH", "Policy Witness digest does not bind its exact bytes")
        # Reuse the strict PlanControl policy normalizer here, before the
        # readback can be persisted.  The successor compiler's historical
        # helper only checked shape and digest and would allow a recomputed,
        # unauthorized role grant to reach durable state first.
        try:
            from .plan_control import _normalize_policy

            normalized = _normalize_policy(policy)
        except PlanControlError as error:
            raise HumanGateError(
                "REPLAN_BUDGET_POLICY_INVALID",
                "complete Policy Witness replan budget is invalid",
            ) from error
        except Exception as error:
            raise HumanGateError(
                "REPLAN_BUDGET_POLICY_INVALID",
                "complete Policy Witness replan budget is invalid",
            ) from error
        if normalized != policy:
            _fail(
                "HUMAN_SOURCE_DIGEST_MISMATCH",
                "Policy Witness is not the exact canonical authority projection",
            )
        return policy

    def _result(
        self,
        *,
        decision: HumanDecisionRecord,
        state: str,
        approval_bytes: bytes | None = None,
        tracker_bytes: bytes | None = None,
        policy_bytes: bytes | None = None,
        source_change_digest: str | None = None,
    ) -> HumanSourceReadback:
        code = _STATE_CODES[state]
        approval_digest = None if approval_bytes is None else digest_bytes(approval_bytes)
        tracker_digest = None if tracker_bytes is None else digest_bytes(tracker_bytes)
        policy_digest = None if policy_bytes is None else digest_bytes(policy_bytes)
        return HumanSourceReadback(
            decision_id=decision.decision_id,
            state=state,
            approval_record_bytes=None if state != "approved" else approval_bytes,
            tracker_source_bytes=None if state != "approved" else tracker_bytes,
            policy_witness_bytes=None if state != "approved" else policy_bytes,
            approval_record_digest=approval_digest if state == "approved" else None,
            tracker_source_digest=tracker_digest if state == "approved" else None,
            policy_witness_digest=policy_digest if state == "approved" else None,
            source_change_digest=source_change_digest if state == "approved" else None,
            readback_digest=_readback_digest(
                decision_id=decision.decision_id,
                state=state,
                approval_digest=approval_digest,
                tracker_digest=tracker_digest,
                policy_digest=policy_digest,
                source_change_digest=source_change_digest if state == "approved" else None,
                code=code,
            ),
            code=code,
        )

    def read(
        self,
        handle: CampaignHandle,
        decision: HumanDecisionRecord,
        readback_ref: str,
    ) -> HumanSourceReadback:
        self._validate_input(handle, decision, readback_ref)
        first = self._read_round(handle, decision, readback_ref)
        second = self._read_round(handle, decision, readback_ref)
        if (
            first.approval_bytes != second.approval_bytes
            or first.tracker_bytes != second.tracker_bytes
            or first.policy_bytes != second.policy_bytes
            or first.tracker_blob_sha != second.tracker_blob_sha
            or first.policy_blob_sha != second.policy_blob_sha
        ):
            _fail(
                "HUMAN_SOURCE_CHANGED_DURING_READBACK",
                "authoritative approval, tracker, or Policy Witness bytes changed during readback",
            )
        if first.approval is None:
            return self._result(decision=decision, state="pending")
        state = first.approval["approval_state"]
        if state != "approved":
            return self._result(decision=decision, state=state)
        if first.tracker_bytes is None or first.policy_bytes is None:
            return self._result(decision=decision, state="incomplete")
        try:
            source_state = self._validate_tracker_source(
                first.tracker_bytes,
                handle=handle,
                decision=decision,
                approval=first.approval,
            )
        except _IncompleteSource:
            return self._result(decision=decision, state="incomplete")
        if source_state == "reverted":
            return self._result(decision=decision, state="reverted")
        policy = self._validate_policy(first.policy_bytes)
        policy_domain = (
            decision.required_source.source_kind == "policy"
            and decision.required_source.predecessor_snapshot_digest is not None
        )
        if policy_domain:
            if first.approval["source_change_digest"] not in {
                digest_bytes(first.policy_bytes),
                policy["digest"],
            }:
                _fail(
                    "HUMAN_SOURCE_CHANGED_DURING_READBACK",
                    "approval does not bind the authoritative Policy Witness",
                )
            if policy["digest"] == decision.required_source.predecessor_source_digest:
                return self._result(decision=decision, state="reverted")
            source_change_digest = policy["digest"]
        else:
            source_change_digest = load_canonical_json(first.tracker_bytes)[
                "source_change_digest"
            ]
        if first.approval["approval_state"] == "out_of_policy":
            return self._result(decision=decision, state="out_of_policy")
        return self._result(
            decision=decision,
            state="approved",
            approval_bytes=first.approval_bytes,
            tracker_bytes=first.tracker_bytes,
            policy_bytes=first.policy_bytes,
            source_change_digest=source_change_digest,
        )


__all__ = ["GitHubHumanApprovalSource"]
