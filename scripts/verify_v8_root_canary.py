"""Verify the read-only evidence bundle for the V8 root Canary."""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
import re
import subprocess
import sys
from typing import Mapping, Protocol, Sequence

try:
    from provision_v8_root_canary import (
        RootCanaryProvisionError,
        load_ticket_manifest,
    )
except ModuleNotFoundError:  # imported as ``scripts.verify_v8_root_canary``
    from scripts.provision_v8_root_canary import (  # type: ignore[no-redef]
        RootCanaryProvisionError,
        load_ticket_manifest,
    )


ROOT_REPOSITORY = "NOirBRight/github-work-orchestrator"
ROOT_TICKET_KEYS = ("alpha", "beta", "gamma", "delta")
STANDARD_TICKET_KEYS = ("alpha", "beta", "gamma")
STRICT_TICKET_KEY = "delta"
_TICKET_KEY_PATTERN = re.compile(r"issue:([1-9][0-9]*)\Z")
_GITHUB_REF_PATTERN = re.compile(
    rf"github://{re.escape(ROOT_REPOSITORY)}/issues/([1-9][0-9]*)\Z"
)
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_OBJECT_ID_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_TASK1_TICKET_SCHEMA = "gwo-v8-root-canary-tickets.v2"
_LOCAL_ROOT_SCHEMA = "gwo.v8.local-root-acceptance.v1"
_LOCAL_ROOT_MODE = "local-only-v1"
_LOCAL_RECEIPT_SCHEMA = "gwo-v8-root-canary-acceptance.v2"
_LOCAL_TICKET_REFS = ("issue:195", "issue:196", "issue:197", "issue:198")
_LOCAL_STANDARD_TICKET_REFS = _LOCAL_TICKET_REFS[:3]
_LOCAL_STRICT_TICKET_REF = _LOCAL_TICKET_REFS[3]
_LOCAL_READBACK_KEYS = frozenset(
    {
        "observations",
        "candidate_receipts",
        "candidate_diffs",
        "accepted_candidate_receipts",
        "delivery_proofs",
        "result_integrities",
        "git_readback",
    }
)
_LOCAL_TRANSCRIPT_SEQUENCE = (
    "start",
    "inspect",
    "advance",
    "inspect",
    "watchdog",
    "inspect",
    "advance",
    "inspect",
    "inspect",
    "advance",
    "inspect",
    "advance",
    "inspect",
)
_LOCAL_GATE_STATUS_HISTORY = {
    "issue:195": ("review_accepted",),
    "issue:196": ("repair_required", "repair_accepted"),
    "issue:197": ("ordinary_rejected", "review_accepted"),
    "issue:198": ("review_accepted",),
}
_LOCAL_REVIEW_SUBJECT_KEYS = frozenset(
    {
        "action_kind",
        "assurance_requirement_digest",
        "base_commit_oid",
        "base_tree_oid",
        "candidate_audit_digest",
        "candidate_commit_oid",
        "candidate_digest",
        "candidate_receipt_digest",
        "candidate_tree_oid",
        "check_evidence_digests",
        "diff_record_digest",
        "diff_schema_version",
        "kind",
        "parent_digest",
        "policy_witness_digest",
        "prior_review_subject_digest",
        "protocol_version",
        "repair_delta_digest",
        "repair_packet_digest",
        "runtime_subject_digest",
        "standards",
        "subject_digest",
        "ticket_contract_digest",
    }
)


class RootCanaryVerificationError(RuntimeError):
    """A named fail-closed rejection of a root-Canary evidence bundle."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class CanaryAdmissionIdentity(Protocol):
    repository: str
    campaign_key: str | None
    activation_id: str
    writer_generation: str


def _reject(code: str) -> None:
    raise RootCanaryVerificationError(code)


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if dataclasses.is_dataclass(value):
        return _json_value(dataclasses.asdict(value))
    return value


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            _json_value(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def digest_value(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _canonical_digest(value: object) -> str:
    """Digest the canonical JSON value used by the Task 1/Task 3 contracts.

    The public verifier receipt retains its historical newline-terminated
    digest contract.  The imported Task 1 and Task 3 artifacts use the shared
    GWO canonical encoder, which hashes the JSON value without that transport
    newline.
    """

    encoded = json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _candidate_diff_record_digest(value: Mapping[str, object]) -> str:
    body = {key: item for key, item in value.items() if key != "record_digest"}
    encoded = json.dumps(
        _json_value(body),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(b"gwo.candidate-diff-record.v1\x00" + encoded).hexdigest()


def _mapping(value: object, code: str) -> dict[str, object]:
    if type(value) is not dict:
        _reject(code)
    return value


def _sequence(value: object, code: str) -> tuple[object, ...]:
    if type(value) not in {list, tuple}:
        _reject(code)
    return tuple(value)


def _text(value: object, code: str) -> str:
    if type(value) is not str or not value:
        _reject(code)
    return value


def _positive_int(value: object, code: str) -> int:
    if type(value) is not int or value <= 0:
        _reject(code)
    return value


def _digest(value: object, code: str) -> str:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        _reject(code)
    return value


def _object_id(value: object, code: str) -> str:
    if type(value) is not str or _OBJECT_ID_PATTERN.fullmatch(value) is None:
        _reject(code)
    return value


def _readback_digest(value: Mapping[str, object], *names: str) -> str | None:
    for name in names:
        candidate = value.get(name)
        if type(candidate) is str and candidate:
            return candidate
    return None


def _ticket_key_from_ref(value: object) -> str | None:
    if type(value) is not str:
        return None
    if value in ROOT_TICKET_KEYS:
        return value
    if value.startswith("issue:"):
        return value
    match = _GITHUB_REF_PATTERN.fullmatch(value)
    if match is not None:
        return f"issue:{int(match.group(1))}"
    return None


def _normalise_status(value: object) -> str:
    if type(value) is not str:
        _reject("DIAGNOSTICS_STATUS_INVALID")
    return value.lower()


@dataclass(frozen=True, slots=True)
class VerifiedBatch:
    batch_id: str
    member_count: int
    pull_request_number: int | None
    hosted_run_id: int | str | None
    batch_sha: str
    target_sha: str
    receipt_digest: str


@dataclass(frozen=True, slots=True)
class RootCanaryAcceptanceReceiptV1:
    repository: str
    campaign_key: str
    plan_revision_digest: str
    activation_id: str
    writer_generation: str
    standard_ticket_keys: tuple[str, str, str]
    strict_ticket_key: str
    standard_batch: VerifiedBatch
    strict_batch: VerifiedBatch
    peak_worker_slots: int
    refill_proven: bool
    permission_same_binding: bool
    stale_diagnosis_bounded: bool
    terminal_replacement_bounded: bool
    terminal_replacement_receipt_digests: tuple[str, ...]
    duplicate_effect_ids: tuple[str, ...]
    ticket_contract_digests: tuple[tuple[str, str], ...]
    candidate_receipt_digests: tuple[tuple[str, str], ...]
    policy_witness_digest: str
    authority_root_digest: str
    runtime_selector_digest: str
    finding_ledger_digests: tuple[tuple[str, str], ...]
    batch_receipt_digests: tuple[tuple[str, str], ...]
    fault_journal_digest: str
    canary_target_sha: str
    authoritative_evidence: dict[str, object]
    receipt_digest: str
    acceptance_mode: str | None = None

    def canonical_digest_payload(self) -> dict[str, object]:
        """Return the exact mapping covered by ``receipt_digest``."""

        payload = {
            "repository": self.repository,
            "campaign_key": self.campaign_key,
            "plan_revision_digest": self.plan_revision_digest,
            "activation_id": self.activation_id,
            "writer_generation": self.writer_generation,
            "standard_ticket_keys": list(self.standard_ticket_keys),
            "strict_ticket_key": self.strict_ticket_key,
            "standard_batch_digest": self.standard_batch.receipt_digest,
            "strict_batch_digest": self.strict_batch.receipt_digest,
            "ticket_contract_digests": [
                {"key": key, "digest": digest}
                for key, digest in self.ticket_contract_digests
            ],
            "candidate_receipt_digests": [
                {"key": key, "digest": digest}
                for key, digest in self.candidate_receipt_digests
            ],
            "policy_witness_digest": self.policy_witness_digest,
            "authority_root_digest": self.authority_root_digest,
            "runtime_selector_digest": self.runtime_selector_digest,
            "finding_ledger_digests": [
                {"key": key, "digest": digest}
                for key, digest in self.finding_ledger_digests
            ],
            "batch_receipt_digests": [
                {"kind": kind, "digest": digest}
                for kind, digest in self.batch_receipt_digests
            ],
            "fault_journal_digest": self.fault_journal_digest,
            "peak_worker_slots": self.peak_worker_slots,
            "refill_ticket_order": list(
                self.standard_ticket_keys + (self.strict_ticket_key,)
            ),
            "permission_same_binding": self.permission_same_binding,
            "stale_diagnosis_bounded": self.stale_diagnosis_bounded,
            "terminal_replacement_bounded": self.terminal_replacement_bounded,
            "terminal_replacement_receipt_digests": list(
                self.terminal_replacement_receipt_digests
            ),
            "duplicate_effect_ids": list(self.duplicate_effect_ids),
            "canary_target_sha": self.canary_target_sha,
            "authoritative_evidence": self.authoritative_evidence,
        }
        if self.acceptance_mode is not None:
            payload["acceptance_mode"] = self.acceptance_mode
        return payload

    def validate_digest(self, expected: str) -> None:
        if (
            self.receipt_digest != expected
            or self.receipt_digest != digest_value(self.canonical_digest_payload())
        ):
            _reject("CANARY_RECEIPT_DIGEST_MISMATCH")

    def validate_for(self, admission: CanaryAdmissionIdentity) -> None:
        if (
            self.repository != getattr(admission, "repository", None)
            or self.campaign_key != getattr(admission, "campaign_key", None)
            or self.activation_id != getattr(admission, "activation_id", None)
            or self.writer_generation != getattr(admission, "writer_generation", None)
        ):
            _reject("CANARY_ADMISSION_IDENTITY_MISMATCH")


def _campaign_identity(
    bundle: Mapping[str, object],
) -> tuple[str, str, str, str, str, str]:
    repository = _text(bundle.get("repository"), "ROOT_REPOSITORY_MISMATCH")
    if repository != ROOT_REPOSITORY:
        _reject("ROOT_REPOSITORY_MISMATCH")

    campaign_key = _text(bundle.get("campaign_key"), "CAMPAIGN_IDENTITY_INCOMPLETE")
    plan_revision_digest = _text(
        bundle.get("plan_revision_digest"), "CAMPAIGN_IDENTITY_INCOMPLETE"
    )
    activation_id = _text(bundle.get("activation_id"), "CAMPAIGN_IDENTITY_INCOMPLETE")
    writer_generation = _text(
        bundle.get("writer_generation"), "CAMPAIGN_IDENTITY_INCOMPLETE"
    )
    canary_target_sha = _text(
        bundle.get("canary_target_sha"), "TARGET_SHA_INCOMPLETE"
    )

    campaign = bundle.get("campaign")
    if campaign is not None:
        campaign_mapping = _mapping(campaign, "CAMPAIGN_IDENTITY_INCOMPLETE")
        embedded_repository = campaign_mapping.get("repository")
        if embedded_repository is not None and embedded_repository != repository:
            _reject("ROOT_REPOSITORY_MISMATCH")
        embedded_campaign_key = campaign_mapping.get("campaign_key")
        if (
            embedded_campaign_key is not None
            and embedded_campaign_key != campaign_key
        ):
            _reject("CAMPAIGN_IDENTITY_INCOMPLETE")

    return (
        repository,
        campaign_key,
        plan_revision_digest,
        activation_id,
        writer_generation,
        canary_target_sha,
    )


def _validate_status(bundle: Mapping[str, object]) -> None:
    diagnostics = bundle.get("diagnostics")
    if diagnostics is not None:
        value = _mapping(diagnostics, "DIAGNOSTICS_INVALID")
        status = value.get("status", value.get("public_status"))
    else:
        status = bundle.get("status", bundle.get("public_status"))
    if status is None:
        _reject("DIAGNOSTICS_STATUS_REQUIRED")
    if _normalise_status(status) != "complete":
        _reject("DIAGNOSTICS_NOT_COMPLETE")


def _validate_ticket_repository(ticket: Mapping[str, object]) -> None:
    repository = ticket.get("repository")
    if repository is None:
        return
    if type(repository) is str:
        repository_name = repository
    elif type(repository) is dict:
        repository_name = repository.get("full_name")
    else:
        _reject("ROOT_TICKET_READBACK_INVALID")
    if repository_name != ROOT_REPOSITORY:
        _reject("ROOT_REPOSITORY_MISMATCH")


def _ticket_label_names(ticket: Mapping[str, object]) -> tuple[str, ...]:
    labels = _sequence(ticket.get("labels"), "ROOT_TICKET_NOT_READY")
    names: list[str] = []
    for label in labels:
        if type(label) is str:
            names.append(label)
        elif type(label) is dict:
            names.append(_text(label.get("name"), "ROOT_TICKET_READBACK_INVALID"))
        else:
            _reject("ROOT_TICKET_READBACK_INVALID")
    return tuple(names)


def _v2_repository(value: object, code: str) -> dict[str, object]:
    repository = _mapping(value, code)
    if set(repository) != {"full_name", "url"}:
        _reject(code)
    if repository.get("full_name") != ROOT_REPOSITORY:
        _reject("ROOT_REPOSITORY_MISMATCH")
    if repository.get("url") != f"https://api.github.com/repos/{ROOT_REPOSITORY}":
        _reject(code)
    return repository


def _v2_label(value: object, code: str) -> dict[str, object]:
    label = _mapping(value, code)
    if set(label) != {
        "id",
        "node_id",
        "url",
        "name",
        "color",
        "default",
        "description",
    }:
        _reject(code)
    _positive_int(label.get("id"), code)
    _text(label.get("node_id"), code)
    _text(label.get("name"), code)
    _text(label.get("url"), code)
    if type(label.get("color")) is not str or type(label.get("default")) is not bool:
        _reject(code)
    description = label.get("description")
    if description is not None and type(description) is not str:
        _reject(code)
    expected_url = (
        f"https://api.github.com/repos/{ROOT_REPOSITORY}/labels/"
        "ready-for-agent"
    )
    if label.get("url") != expected_url:
        _reject(code)
    return label


def _v2_comment(value: object, number: int, code: str) -> dict[str, object]:
    comment = _mapping(value, code)
    if set(comment) != {
        "id",
        "node_id",
        "url",
        "html_url",
        "body",
        "user",
        "created_at",
        "updated_at",
        "author_association",
    }:
        _reject(code)
    comment_id = _positive_int(comment.get("id"), code)
    _text(comment.get("node_id"), code)
    _text(comment.get("body"), code)
    for field in ("created_at", "updated_at", "author_association"):
        _text(comment.get(field), code)
    user = _mapping(comment.get("user"), code)
    if set(user) != {"login"}:
        _reject(code)
    _text(user.get("login"), code)
    expected_url = (
        f"https://api.github.com/repos/{ROOT_REPOSITORY}/issues/comments/{comment_id}"
    )
    expected_html_url = (
        f"https://github.com/{ROOT_REPOSITORY}/issues/{number}"
        f"#issuecomment-{comment_id}"
    )
    if comment.get("url") != expected_url or comment.get("html_url") != expected_html_url:
        _reject(code)
    return comment


def _v2_blocker(value: object, code: str) -> dict[str, object]:
    blocker = _mapping(value, code)
    if set(blocker) != {"key", "state", "repository", "source"}:
        _reject(code)
    key = _text(blocker.get("key"), code)
    if _TICKET_KEY_PATTERN.fullmatch(key) is None:
        _reject(code)
    state = _text(blocker.get("state"), code).lower()
    if state not in {"open", "closed"}:
        _reject(code)
    repository = _v2_repository(blocker.get("repository"), code)
    source = _mapping(blocker.get("source"), code)
    if set(source) != {"ref", "digest"} or source.get("ref") != key:
        _reject(code)
    if _digest(source.get("digest"), code) != _canonical_digest(
        {"key": key, "state": state, "repository": repository}
    ):
        _reject("TICKET_CONTRACT_DIGEST_MISMATCH")
    return blocker


def _validate_v2_ticket(value: object, expected_ref: str, code: str) -> dict[str, object]:
    ticket = _mapping(value, code)
    if set(ticket) != {"key", "labels", "source", "contract", "native_blockers"}:
        _reject(code)
    if ticket.get("key") != expected_ref:
        _reject(code)
    source = _mapping(ticket.get("source"), code)
    if set(source) != {"ref", "digest"} or source.get("ref") != expected_ref:
        _reject(code)

    contract = _mapping(ticket.get("contract"), code)
    if set(contract) != {
        "id",
        "node_id",
        "number",
        "title",
        "body",
        "state",
        "state_reason",
        "type",
        "repository",
        "labels",
        "comments",
        "updated_at",
    }:
        _reject(code)
    number_match = _TICKET_KEY_PATTERN.fullmatch(expected_ref)
    if number_match is None:
        _reject(code)
    number = int(number_match.group(1))
    if (
        _positive_int(contract.get("id"), code) <= 0
        or _text(contract.get("node_id"), code) == ""
        or contract.get("number") != number
    ):
        _reject(code)
    _text(contract.get("title"), code)
    if type(contract.get("body")) is not str:
        _reject(code)
    state = _text(contract.get("state"), code).lower()
    if state not in {"open", "closed"}:
        _reject(code)
    state_reason = contract.get("state_reason")
    if state_reason is not None and type(state_reason) is not str:
        _reject(code)
    issue_type = contract.get("type")
    if issue_type is not None:
        _mapping(issue_type, code)
    _v2_repository(contract.get("repository"), code)
    labels = _sequence(contract.get("labels"), code)
    parsed_labels = [_v2_label(item, code) for item in labels]
    if len({item["id"] for item in parsed_labels}) != len(parsed_labels):
        _reject(code)
    if len({item["name"] for item in parsed_labels}) != len(parsed_labels):
        _reject(code)
    comments = _sequence(contract.get("comments"), code)
    parsed_comments = [_v2_comment(item, number, code) for item in comments]
    if len({item["id"] for item in parsed_comments}) != len(parsed_comments):
        _reject(code)
    _text(contract.get("updated_at"), code)

    entry_labels = _sequence(ticket.get("labels"), code)
    if any(type(item) is not str or not item for item in entry_labels):
        _reject(code)
    if list(entry_labels) != [item["name"] for item in parsed_labels]:
        _reject("TICKET_READBACK_MISMATCH")
    if tuple(entry_labels) != ("ready-for-agent",):
        _reject("ROOT_TICKET_NOT_READY")

    blockers = tuple(
        _v2_blocker(item, code)
        for item in _sequence(ticket.get("native_blockers"), code)
    )
    if blockers:
        _reject("ROOT_TICKET_NOT_READY")
    projection = {
        "number": number,
        "contract": contract,
        "labels": list(entry_labels),
        "source_ref": expected_ref,
        "native_blockers": list(blockers),
    }
    if _digest(source.get("digest"), code) != _canonical_digest(projection):
        _reject("TICKET_CONTRACT_DIGEST_MISMATCH")
    if state != "open":
        _reject("ROOT_TICKET_NOT_READY")
    return ticket


def _validate_v2_manifest(
    manifest: Mapping[str, object], code: str = "ROOT_TICKET_MANIFEST_INVALID"
) -> tuple[dict[str, object], ...]:
    if set(manifest) != {"schema", "repository", "ready_refs", "tickets"}:
        _reject(code)
    if manifest.get("schema") != _TASK1_TICKET_SCHEMA:
        _reject(code)
    if manifest.get("repository") != ROOT_REPOSITORY:
        _reject("ROOT_REPOSITORY_MISMATCH")
    refs = _sequence(manifest.get("ready_refs"), code)
    if len(refs) != 4 or any(
        type(ref) is not str or _TICKET_KEY_PATTERN.fullmatch(ref) is None
        for ref in refs
    ):
        _reject(code)
    if len(set(refs)) != len(refs):
        _reject(code)
    raw_tickets = _sequence(manifest.get("tickets"), code)
    if len(raw_tickets) != 4:
        _reject(code)
    if tuple(refs) != tuple(
        _mapping(item, code).get("key") for item in raw_tickets
    ):
        _reject("TICKET_READBACK_MISMATCH")
    tickets = tuple(
        _validate_v2_ticket(item, ref, code)
        for item, ref in zip(raw_tickets, refs, strict=True)
    )
    return tickets


def _ticket_reference(ticket: Mapping[str, object]) -> str:
    if "contract" in ticket:
        return _text(ticket.get("key"), "ROOT_TICKET_READBACK_INVALID")
    return _text(ticket.get("ticket_key"), "ROOT_TICKET_READBACK_INVALID")


def _ticket_number(ticket: Mapping[str, object]) -> int:
    if "contract" in ticket:
        reference = _ticket_reference(ticket)
        match = _TICKET_KEY_PATTERN.fullmatch(reference)
        if match is None:
            _reject("ROOT_TICKET_READBACK_INVALID")
        return int(match.group(1))
    return _positive_int(ticket.get("number"), "ROOT_TICKET_READBACK_INVALID")


def _ticket_contract_digest(ticket: Mapping[str, object]) -> str:
    if "contract" in ticket:
        source = _mapping(ticket.get("source"), "ROOT_TICKET_READBACK_INVALID")
        return _digest(source.get("digest"), "ROOT_TICKET_READBACK_INVALID")
    return _text(ticket.get("contract_digest"), "ROOT_TICKET_READBACK_INVALID")


def _ticket_contract_payload(
    ticket: Mapping[str, object], code: str = "ROOT_TICKET_READBACK_INVALID"
) -> dict[str, object]:
    """Return Task 1's complete canonical Ticket contract readback."""

    number = _positive_int(ticket.get("number"), code)
    state = _text(ticket.get("state"), code)
    labels = list(_ticket_label_names(ticket))
    body = ticket.get("body")
    if type(body) is not str:
        _reject(code)

    comments = _sequence(ticket.get("comments"), code)
    comment_bodies: list[str] = []
    for comment in comments:
        if type(comment) is str:
            comment_bodies.append(comment)
        elif type(comment) is dict:
            comment_bodies.append(_text(comment.get("body"), code))
        else:
            _reject(code)

    if "blocked_by" in ticket:
        blocked_values = ticket["blocked_by"]
    elif "blockers" in ticket:
        blocked_values = ticket["blockers"]
    else:
        _reject(code)
    blocked_by = _sequence(blocked_values, code)
    blocked_numbers: list[int] = []
    for blocker in blocked_by:
        if type(blocker) is int:
            blocked_numbers.append(_positive_int(blocker, code))
        elif type(blocker) is dict:
            blocked_numbers.append(_positive_int(blocker.get("number"), code))
        else:
            _reject(code)

    blocker_states_value = ticket.get("blocker_states")
    if blocker_states_value is None:
        if blocked_numbers:
            _reject(code)
        blocker_states: list[list[object]] = []
    else:
        blocker_states_raw = _sequence(blocker_states_value, code)
        blocker_states = []
        for state_value in blocker_states_raw:
            if type(state_value) is dict:
                blocker_number = _positive_int(state_value.get("number"), code)
                blocker_state = _text(state_value.get("state"), code)
            else:
                state_pair = _sequence(state_value, code)
                if len(state_pair) != 2:
                    _reject(code)
                blocker_number = _positive_int(state_pair[0], code)
                blocker_state = _text(state_pair[1], code)
            blocker_states.append([blocker_number, blocker_state])

    return {
        "number": number,
        "state": state,
        "labels": labels,
        "body": body,
        "comments": comment_bodies,
        "blocked_by": blocked_numbers,
        "blocker_states": blocker_states,
    }


def _ticket_is_unblocked(ticket: Mapping[str, object]) -> bool:
    if "blocked_by" in ticket:
        blocked_by = _sequence(ticket["blocked_by"], "ROOT_TICKET_NOT_READY")
        if blocked_by:
            return False
    elif "blockers" in ticket:
        blockers = _sequence(ticket["blockers"], "ROOT_TICKET_NOT_READY")
        if blockers:
            return False
    else:
        _reject("ROOT_TICKET_NOT_READY")
    return True


def _ticket_aliases(bundle: Mapping[str, object]) -> tuple[dict[str, str], tuple[dict[str, object], ...]]:
    raw_tickets = _sequence(
        bundle.get("tickets"), "ROOT_TICKET_READBACK_INVALID"
    )
    if len(raw_tickets) != 4:
        _reject("ROOT_TICKET_READBACK_INVALID")

    if all(type(raw) is dict and "contract" in raw for raw in raw_tickets):
        refs = tuple(
            _text(
                _mapping(raw, "ROOT_TICKET_READBACK_INVALID").get("key"),
                "ROOT_TICKET_READBACK_INVALID",
            )
            for raw in raw_tickets
        )
        if len(set(refs)) != 4:
            _reject("ROOT_TICKET_READBACK_INVALID")
        entries = tuple(
            _validate_v2_ticket(raw, ref, "ROOT_TICKET_READBACK_INVALID")
            for raw, ref in zip(raw_tickets, refs, strict=True)
        )
        ready_refs = bundle.get("ready_refs")
        if ready_refs is not None:
            ready_values = _sequence(
                ready_refs, "ROOT_TICKET_READBACK_INVALID"
            )
            if tuple(ready_values) != refs:
                _reject("ROOT_TICKET_READBACK_INVALID")
        return (
            {ref: ROOT_TICKET_KEYS[index] for index, ref in enumerate(refs)},
            entries,
        )

    if any(type(raw) is dict and "contract" in raw for raw in raw_tickets):
        _reject("ROOT_TICKET_READBACK_INVALID")

    aliases: dict[str, str] = {}
    entries: list[dict[str, object]] = []
    seen_keys: set[str] = set()
    for raw in raw_tickets:
        ticket = _mapping(raw, "ROOT_TICKET_READBACK_INVALID")
        _validate_ticket_repository(ticket)
        key = _text(ticket.get("key"), "ROOT_TICKET_READBACK_INVALID")
        if key not in ROOT_TICKET_KEYS or key in seen_keys:
            _reject("ROOT_TICKET_READBACK_INVALID")
        ticket_key = _text(
            ticket.get("ticket_key"), "ROOT_TICKET_READBACK_INVALID"
        )
        match = _TICKET_KEY_PATTERN.fullmatch(ticket_key)
        if match is None:
            _reject("ROOT_TICKET_READBACK_INVALID")
        number = int(match.group(1))
        provided_number = ticket.get("number")
        if type(provided_number) is not int or provided_number != number:
            _reject("ROOT_TICKET_READBACK_INVALID")
        if ticket_key in aliases or number in {
            int(item.split(":", 1)[1]) for item in aliases
        }:
            _reject("ROOT_TICKET_READBACK_INVALID")
        if ticket.get("state") != "OPEN":
            _reject("ROOT_TICKET_NOT_READY")
        if _ticket_label_names(ticket) != ("ready-for-agent",):
            _reject("ROOT_TICKET_NOT_READY")
        if not _ticket_is_unblocked(ticket):
            _reject("ROOT_TICKET_NOT_READY")
        contract_digest = _text(
            ticket.get("contract_digest"), "ROOT_TICKET_READBACK_INVALID"
        )
        if digest_value(_ticket_contract_payload(ticket)) != contract_digest:
            _reject("TICKET_CONTRACT_DIGEST_MISMATCH")
        aliases[ticket_key] = key
        seen_keys.add(key)
        entries.append(ticket)

    if set(aliases.values()) != set(ROOT_TICKET_KEYS):
        _reject("ROOT_TICKET_READBACK_INVALID")

    ready_refs = bundle.get("ready_refs")
    if ready_refs is not None:
        refs = _sequence(ready_refs, "ROOT_TICKET_READBACK_INVALID")
        if len(refs) != 4:
            _reject("ROOT_TICKET_READBACK_INVALID")
        ref_numbers: list[int] = []
        for ref in refs:
            if type(ref) is not str:
                _reject("ROOT_TICKET_READBACK_INVALID")
            issue_match = _TICKET_KEY_PATTERN.fullmatch(ref)
            github_match = _GITHUB_REF_PATTERN.fullmatch(ref)
            if issue_match is None and github_match is None:
                _reject("ROOT_TICKET_READBACK_INVALID")
            ref_numbers.append(
                int((issue_match or github_match).group(1))  # type: ignore[union-attr]
            )
        if len(set(ref_numbers)) != 4 or set(ref_numbers) != {
            int(ticket_key.split(":", 1)[1]) for ticket_key in aliases
        }:
            _reject("ROOT_TICKET_READBACK_INVALID")

    return aliases, tuple(entries)


def _proof(bundle: Mapping[str, object]) -> dict[str, object]:
    diagnostics = bundle.get("diagnostics")
    if type(diagnostics) is dict and "proof" in diagnostics:
        return _mapping(diagnostics["proof"], "RECOVERY_PROOF_INCOMPLETE")
    return _mapping(bundle.get("proof"), "RECOVERY_PROOF_INCOMPLETE")


def _proof_digest(
    proof: Mapping[str, object], *names: str, code: str
) -> str | None:
    for name in names:
        value = proof.get(name)
        if value is None:
            continue
        if type(value) is str and value:
            return value
        if type(value) is dict:
            nested = _readback_digest(value, "digest", "receipt_digest")
            if nested:
                return nested
        _reject(code)
    return None


def _proof_digest_values(
    proof: Mapping[str, object], name: str, code: str
) -> tuple[str, ...] | None:
    value = proof.get(name)
    if value is None:
        return None
    values = _sequence(value, code)
    result = tuple(_digest(item, code) for item in values)
    if result != tuple(sorted(set(result))):
        _reject(code)
    return result


def _canonical_evidence_digest(
    value: object, code: str
) -> tuple[dict[str, object], str]:
    evidence = _mapping(value, code)
    digest_fields = tuple(
        field
        for field in ("digest", "receipt_digest", "subtree_digest")
        if field in evidence
    )
    if len(digest_fields) != 1:
        _reject(code)
    digest_field = digest_fields[0]
    stored = _digest(evidence.get(digest_field), code)
    body = {key: item for key, item in evidence.items() if key != digest_field}
    if not body or stored != _canonical_digest(body):
        _reject(code)
    return evidence, stored


def _evidence_digest(
    bundle: Mapping[str, object],
    proof: Mapping[str, object],
    field: str,
    evidence_name: str,
    code: str,
) -> str:
    top_digest = _digest(bundle.get(field), code)
    evidence, result = _canonical_evidence_digest(bundle.get(evidence_name), code)
    if result != top_digest:
        _reject(code)

    proof_digest = proof.get(field)
    if proof_digest is not None and _digest(proof_digest, code) != result:
        _reject(code)
    proof_evidence = proof.get(evidence_name)
    if proof_evidence is not None:
        _proof_value, proof_value_digest = _canonical_evidence_digest(
            proof_evidence, code
        )
        if proof_value_digest != result:
            _reject(code)

    # These optional identity fields are part of the evidence when the
    # producer exposes them.  They are not guessed when the Task 3 source
    # does not expose them.
    if evidence.get("repository") is not None and evidence["repository"] != ROOT_REPOSITORY:
        _reject("ROOT_REPOSITORY_MISMATCH")
    return result


def _validate_effects(proof: Mapping[str, object]) -> tuple[str, ...]:
    semantic = _sequence(proof.get("semantic_effect_ids"), "EFFECT_PROOF_INCOMPLETE")
    external = _sequence(proof.get("external_effect_ids"), "EFFECT_PROOF_INCOMPLETE")
    semantic_ids = tuple(_text(value, "EFFECT_PROOF_INCOMPLETE") for value in semantic)
    external_ids = tuple(_text(value, "EFFECT_PROOF_INCOMPLETE") for value in external)
    duplicates = _sequence(proof.get("duplicate_effect_ids"), "DUPLICATE_EFFECT")
    duplicate_ids = tuple(_text(value, "DUPLICATE_EFFECT") for value in duplicates)
    if semantic_ids != tuple(sorted(set(semantic_ids))):
        _reject("DUPLICATE_EFFECT")
    if external_ids != tuple(sorted(set(external_ids))):
        _reject("DUPLICATE_EFFECT")
    if set(semantic_ids) & set(external_ids):
        _reject("DUPLICATE_EFFECT")
    if duplicate_ids or duplicate_ids != tuple(sorted(set(duplicate_ids))):
        _reject("DUPLICATE_EFFECT")
    return tuple(sorted(duplicate_ids))


def _normalise_proof_keys(value: object, aliases: Mapping[str, str], code: str) -> tuple[str, ...]:
    values = _sequence(value, code)
    result: list[str] = []
    for item in values:
        key = _ticket_key_from_ref(item)
        if key in aliases:
            result.append(aliases[key])
        elif key in ROOT_TICKET_KEYS:
            result.append(key)  # type: ignore[arg-type]
        else:
            _reject(code)
    if len(set(result)) != len(result):
        _reject(code)
    return tuple(result)


def _normalise_count_pairs(
    value: object,
    aliases: Mapping[str, str],
    code: str,
    *,
    minimum: int,
    maximum: int | None = None,
    expected_keys: set[str] | None = None,
) -> dict[str, int]:
    pairs = _sequence(value, code)
    result: dict[str, int] = {}
    for raw in pairs:
        pair = _sequence(raw, code)
        if len(pair) != 2:
            _reject(code)
        reference = _ticket_key_from_ref(pair[0])
        key = aliases.get(reference, reference)
        if key is None and type(pair[0]) is str:
            key = pair[0]
        if key not in ROOT_TICKET_KEYS and (
            expected_keys is None or key not in expected_keys
        ):
            _reject(code)
        if key in result or type(pair[1]) is not int or isinstance(pair[1], bool):
            _reject(code)
        if pair[1] < minimum or (maximum is not None and pair[1] > maximum):
            _reject(code)
        result[key] = pair[1]
    if expected_keys is None:
        if set(result) != set(ROOT_TICKET_KEYS):
            _reject(code)
    elif set(result) != expected_keys:
        _reject(code)
    return result


def _validate_recovery_proof(
    proof: Mapping[str, object], aliases: Mapping[str, str]
) -> tuple[int, bool, bool, bool, bool, tuple[str, ...], tuple[str, ...]]:
    """Validate the exact fields exposed by Task 3 CampaignProofReadback."""

    ticket_keys = _normalise_proof_keys(
        proof.get("ticket_keys"), aliases, "RECOVERY_PROOF_INCOMPLETE"
    )
    if ticket_keys != ROOT_TICKET_KEYS:
        _reject("RECOVERY_PROOF_INCOMPLETE")

    limit = proof.get("worker_slot_limit")
    peak = proof.get("peak_worker_slots")
    if type(limit) is not int or isinstance(limit, bool) or limit != 4:
        _reject("WORKER_SLOT_PROOF_INVALID")
    if type(peak) is not int or isinstance(peak, bool) or peak != 4 or peak > limit:
        _reject("WORKER_SLOT_PROOF_INVALID")

    refill = _normalise_proof_keys(
        proof.get("refill_ticket_order"), aliases, "REFILL_PROOF_INVALID"
    )
    if refill != ROOT_TICKET_KEYS:
        _reject("REFILL_PROOF_INVALID")

    raw_pairs = _sequence(
        proof.get("permission_binding_pairs"), "PERMISSION_BINDING_MISMATCH"
    )
    if not raw_pairs:
        _reject("PERMISSION_BINDING_MISMATCH")
    permission_pairs: list[tuple[str, str]] = []
    for raw in raw_pairs:
        pair = _sequence(raw, "PERMISSION_BINDING_MISMATCH")
        if len(pair) != 2:
            _reject("PERMISSION_BINDING_MISMATCH")
        before = _text(pair[0], "PERMISSION_BINDING_MISMATCH")
        after = _text(pair[1], "PERMISSION_BINDING_MISMATCH")
        if before == after:
            same_binding = True
        else:
            before_identity = before.rsplit(":", 1)[-1]
            after_identity = after.rsplit(":", 1)[-1]
            same_binding = bool(before_identity) and before_identity == after_identity
        if not same_binding:
            _reject("PERMISSION_BINDING_MISMATCH")
        permission_pairs.append((before, after))
    if tuple(permission_pairs) != tuple(sorted(set(permission_pairs))):
        _reject("PERMISSION_BINDING_MISMATCH")

    candidate_counts = _normalise_count_pairs(
        proof.get("candidate_sha_count_by_ticket"),
        aliases,
        "RECOVERY_BOUND_INVALID",
        minimum=1,
    )
    binding_counts = _normalise_count_pairs(
        proof.get("binding_count_by_ticket"),
        aliases,
        "RECOVERY_BOUND_INVALID",
        minimum=1,
        maximum=2,
    )
    if set(candidate_counts) != set(ROOT_TICKET_KEYS):
        _reject("RECOVERY_BOUND_INVALID")

    stale_ids_value = _sequence(
        proof.get("stale_diagnosed_binding_ids"), "RECOVERY_BOUND_INVALID"
    )
    stale_ids = tuple(_text(value, "RECOVERY_BOUND_INVALID") for value in stale_ids_value)
    if stale_ids != tuple(sorted(set(stale_ids))):
        _reject("RECOVERY_BOUND_INVALID")
    stale_counts = _normalise_count_pairs(
        proof.get("stale_diagnosis_count_by_binding"),
        {},
        "RECOVERY_BOUND_INVALID",
        minimum=1,
        maximum=1,
        expected_keys=set(stale_ids),
    )
    if set(stale_counts) != set(stale_ids):
        _reject("RECOVERY_BOUND_INVALID")

    terminal_values = _sequence(
        proof.get("terminal_replacement_receipt_digests"),
        "TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE",
    )
    terminal_digests = tuple(
        _digest(value, "TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE")
        for value in terminal_values
    )
    if terminal_digests != tuple(sorted(set(terminal_digests))):
        _reject("TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE")
    replaced = {key for key, count in binding_counts.items() if count == 2}
    if bool(replaced) != bool(terminal_digests):
        _reject("TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE")

    return (
        peak,
        True,
        True,
        True,
        True,
        terminal_digests,
        tuple(sorted(binding_counts)),
    )


_CANDIDATE_RECEIPT_KEYS = frozenset(
    {
        "kind",
        "parent_digest",
        "repository",
        "campaign_key",
        "campaign_handle",
        "plan_revision_digest",
        "work_run_key",
        "ticket_key",
        "reported_reference",
        "base_commit_oid",
        "base_tree_oid",
        "candidate_commit_oid",
        "candidate_tree_oid",
        "diff_schema_version",
        "diff_record_digest",
        "authority_subtree_digest",
        "runtime_subject_digest",
        "receipt_digest",
    }
)
_ACCEPTED_CANDIDATE_RECEIPT_KEYS = frozenset(
    {
        "kind",
        "repository",
        "campaign_key",
        "plan_revision_digest",
        "target_branch",
        "ticket_key",
        "work_run_key",
        "integration_node_key",
        "accepted_sequence",
        "base_sha",
        "base_tree_oid",
        "candidate_sha",
        "candidate_tree_oid",
        "candidate_receipt_digest",
        "diff_schema_version",
        "diff_record_digest",
        "authority_subtree_digest",
        "policy_witness_digest",
        "review_subject_digest",
        "assurance",
        "assurance_requirement_digest",
        "check_environment_digest",
        "delivery_identity_digest",
        "interaction_keys",
        "protected_surfaces",
        "gitlink_change",
        "evidence_digests",
        "review_finding_ledger_digest",
        "receipt_digest",
    }
)


def _validate_candidate_receipt(value: object) -> dict[str, object]:
    receipt = _mapping(value, "CANDIDATE_RECEIPT_INCOMPLETE")
    if frozenset(receipt) != _CANDIDATE_RECEIPT_KEYS:
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    if receipt.get("kind") != "candidate_receipt.v1":
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    for field in (
        "parent_digest",
        "plan_revision_digest",
        "diff_record_digest",
        "authority_subtree_digest",
        "runtime_subject_digest",
        "receipt_digest",
    ):
        _digest(receipt.get(field), "CANDIDATE_RECEIPT_INCOMPLETE")
    for field in (
        "repository",
        "campaign_key",
        "campaign_handle",
        "work_run_key",
        "ticket_key",
        "reported_reference",
        "diff_schema_version",
    ):
        _text(receipt.get(field), "CANDIDATE_RECEIPT_INCOMPLETE")
    for field in (
        "base_commit_oid",
        "base_tree_oid",
        "candidate_commit_oid",
        "candidate_tree_oid",
    ):
        _object_id(receipt.get(field), "CANDIDATE_RECEIPT_INCOMPLETE")
    if receipt.get("diff_schema_version") != "CandidateDiffRecordV1":
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    body = {key: item for key, item in receipt.items() if key != "receipt_digest"}
    if receipt["receipt_digest"] != _canonical_digest(body):
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    return receipt


def _validate_interaction_keys(value: object) -> list[dict[str, object]]:
    values = _sequence(value, "CANDIDATE_RECEIPT_INCOMPLETE")
    result: list[dict[str, object]] = []
    for raw in values:
        item = _mapping(raw, "CANDIDATE_RECEIPT_INCOMPLETE")
        if set(item) != {"namespace", "value", "classification"}:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        namespace = _text(item.get("namespace"), "CANDIDATE_RECEIPT_INCOMPLETE")
        interaction_value = _text(item.get("value"), "CANDIDATE_RECEIPT_INCOMPLETE")
        classification = _text(
            item.get("classification"), "CANDIDATE_RECEIPT_INCOMPLETE"
        )
        if classification not in {
            "ordinary",
            "protected",
            "high_coupling",
            "non_decomposable",
        }:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        result.append(
            {
                "namespace": namespace,
                "value": interaction_value,
                "classification": classification,
            }
        )
    keys = tuple(
        (item["namespace"], item["value"], item["classification"])
        for item in result
    )
    if keys != tuple(sorted(set(keys))):
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    return result


def _validate_accepted_candidate_receipt(
    value: object,
) -> dict[str, object]:
    receipt = _mapping(value, "CANDIDATE_RECEIPT_INCOMPLETE")
    if "assurance" not in receipt:
        _reject("ASSURANCE_SHAPE_INVALID")
    if frozenset(receipt) != _ACCEPTED_CANDIDATE_RECEIPT_KEYS:
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    if receipt.get("kind") != "accepted_candidate_receipt.v1":
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    for field in (
        "plan_revision_digest",
        "candidate_receipt_digest",
        "diff_record_digest",
        "authority_subtree_digest",
        "policy_witness_digest",
        "review_subject_digest",
        "assurance_requirement_digest",
        "check_environment_digest",
        "delivery_identity_digest",
        "review_finding_ledger_digest",
        "receipt_digest",
    ):
        _digest(receipt.get(field), "CANDIDATE_RECEIPT_INCOMPLETE")
    for field in (
        "repository",
        "campaign_key",
        "target_branch",
        "ticket_key",
        "work_run_key",
        "integration_node_key",
        "diff_schema_version",
        "assurance",
    ):
        _text(receipt.get(field), "CANDIDATE_RECEIPT_INCOMPLETE")
    if receipt["target_branch"] != "main":
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    if receipt["diff_schema_version"] != "CandidateDiffRecordV1":
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    if receipt["assurance"] not in {"standard", "strict"}:
        _reject("ASSURANCE_SHAPE_INVALID")
    if type(receipt.get("accepted_sequence")) is not int or (
        isinstance(receipt["accepted_sequence"], bool)
        or receipt["accepted_sequence"] < 1
    ):
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    for field in ("base_sha", "base_tree_oid", "candidate_sha", "candidate_tree_oid"):
        _object_id(receipt.get(field), "CANDIDATE_RECEIPT_INCOMPLETE")
    _validate_interaction_keys(receipt.get("interaction_keys"))
    protected = _sequence(
        receipt.get("protected_surfaces"), "CANDIDATE_RECEIPT_INCOMPLETE"
    )
    protected_values = tuple(
        _text(item, "CANDIDATE_RECEIPT_INCOMPLETE") for item in protected
    )
    if protected_values != tuple(sorted(set(protected_values))):
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    if type(receipt.get("gitlink_change")) is not bool:
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    evidence = _sequence(
        receipt.get("evidence_digests"), "CANDIDATE_RECEIPT_INCOMPLETE"
    )
    evidence_digests = tuple(_digest(item, "CANDIDATE_RECEIPT_INCOMPLETE") for item in evidence)
    if not evidence_digests or evidence_digests != tuple(sorted(set(evidence_digests))):
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    body = {key: item for key, item in receipt.items() if key != "receipt_digest"}
    if receipt["receipt_digest"] != _canonical_digest(body):
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    return receipt


def _candidate_records(
    bundle: Mapping[str, object], aliases: Mapping[str, str]
) -> tuple[dict[str, object], ...]:
    raw_candidates = _sequence(
        bundle.get("candidates"), "CANDIDATE_RECEIPT_INCOMPLETE"
    )
    if len(raw_candidates) != 4:
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    by_key: dict[str, dict[str, object]] = {}
    seen_receipts: set[str] = set()
    seen_work_runs: set[str] = set()
    seen_candidate_oids: set[str] = set()
    for raw in raw_candidates:
        candidate = _mapping(raw, "CANDIDATE_RECEIPT_INCOMPLETE")
        ticket_ref = _text(
            candidate.get("ticket_key"), "CANDIDATE_RECEIPT_INCOMPLETE"
        )
        reference = _ticket_key_from_ref(ticket_ref)
        key = aliases.get(reference, reference)
        if key not in ROOT_TICKET_KEYS or key in by_key:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        receipt = _validate_candidate_receipt(candidate.get("candidate_receipt"))
        receipt_key = _ticket_key_from_ref(receipt["ticket_key"])
        if aliases.get(receipt_key, receipt_key) != key:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        digest = _digest(
            candidate.get("candidate_receipt_digest"),
            "CANDIDATE_RECEIPT_INCOMPLETE",
        )
        if digest != receipt["receipt_digest"]:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        receipt_digest = receipt["receipt_digest"]
        work_run_key = receipt["work_run_key"]
        candidate_oid = receipt["candidate_commit_oid"]
        if receipt_digest in seen_receipts or work_run_key in seen_work_runs:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        if candidate_oid in seen_candidate_oids:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        seen_receipts.add(receipt_digest)
        seen_work_runs.add(work_run_key)
        seen_candidate_oids.add(candidate_oid)
        by_key[key] = candidate
    if set(by_key) != set(ROOT_TICKET_KEYS):
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    return tuple(by_key[key] for key in ROOT_TICKET_KEYS)


def _candidate_assurance(candidate: Mapping[str, object], key: str) -> str:
    """Read assurance only from the exact CandidateGate acceptance receipt."""

    accepted = _validate_accepted_candidate_receipt(
        candidate.get("accepted_candidate_receipt")
    )
    expected = "strict" if key == STRICT_TICKET_KEY else "standard"
    if accepted["assurance"] != expected:
        _reject("ASSURANCE_SHAPE_INVALID")
    wrapper_assurance = candidate.get("assurance")
    if wrapper_assurance is not None and wrapper_assurance != expected:
        _reject("ASSURANCE_SHAPE_INVALID")
    return expected


def _validate_candidate_links(
    candidates: tuple[dict[str, object], ...],
    accepted: Mapping[str, dict[str, object]],
    aliases: Mapping[str, str],
    repository: str,
    campaign_key: str,
    plan_revision_digest: str,
    policy_witness_digest: str,
) -> None:
    for candidate in candidates:
        receipt = _validate_candidate_receipt(candidate.get("candidate_receipt"))
        candidate_ticket = _text(
            candidate.get("ticket_key"), "CANDIDATE_RECEIPT_INCOMPLETE"
        )
        candidate_key = _ticket_key_from_ref(candidate_ticket)
        if candidate_key is None:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        for field, expected in (
            ("repository", repository),
            ("campaign_key", campaign_key),
            ("campaign_handle", campaign_key),
            ("plan_revision_digest", plan_revision_digest),
            ("ticket_key", candidate_ticket),
        ):
            if receipt.get(field) != expected:
                _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        key = aliases.get(candidate_key, candidate_key)
        assurance = _candidate_assurance(candidate, key)
        accepted_receipt = _validate_accepted_candidate_receipt(
            candidate.get("accepted_candidate_receipt")
        )
        if accepted_receipt["assurance"] != assurance:
            _reject("ASSURANCE_SHAPE_INVALID")
        key = aliases.get(candidate_key, candidate_key)
        if accepted.get(key) is not accepted_receipt:
            # The global collection is independently checked below; this
            # comparison is intentionally value based for JSON readbacks.
            if key not in accepted or canonical_json_bytes(accepted[key]) != canonical_json_bytes(accepted_receipt):
                _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        if accepted_receipt["policy_witness_digest"] != policy_witness_digest:
            _reject("POLICY_EVIDENCE_INCOMPLETE")
        if accepted_receipt["repository"] != repository:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        if accepted_receipt["campaign_key"] != campaign_key:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        if accepted_receipt["plan_revision_digest"] != plan_revision_digest:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        if accepted_receipt["ticket_key"] != candidate_ticket:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        for candidate_field, accepted_field in (
            ("work_run_key", "work_run_key"),
            ("base_commit_oid", "base_sha"),
            ("base_tree_oid", "base_tree_oid"),
            ("candidate_commit_oid", "candidate_sha"),
            ("candidate_tree_oid", "candidate_tree_oid"),
            ("diff_record_digest", "diff_record_digest"),
            ("authority_subtree_digest", "authority_subtree_digest"),
        ):
            if receipt[candidate_field] != accepted_receipt[accepted_field]:
                _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        if receipt["receipt_digest"] != accepted_receipt["candidate_receipt_digest"]:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        if receipt["diff_schema_version"] != accepted_receipt["diff_schema_version"]:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")

    for key, accepted_receipt in accepted.items():
        receipt = _validate_accepted_candidate_receipt(accepted_receipt)
        if receipt["policy_witness_digest"] != policy_witness_digest:
            _reject("POLICY_EVIDENCE_INCOMPLETE")
        if receipt["repository"] != repository or receipt["campaign_key"] != campaign_key:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        if receipt["plan_revision_digest"] != plan_revision_digest:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        expected_ticket = _text(
            candidates[ROOT_TICKET_KEYS.index(key)].get("ticket_key"),
            "CANDIDATE_RECEIPT_INCOMPLETE",
        )
        if receipt["ticket_key"] != expected_ticket:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        if receipt["candidate_receipt_digest"] != _digest(
            candidates[ROOT_TICKET_KEYS.index(key)].get("candidate_receipt_digest"),
            "CANDIDATE_RECEIPT_INCOMPLETE",
        ):
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")


def _validate_finding_ledger(value: object) -> dict[str, object]:
    ledger = _mapping(value, "FINDING_LEDGER_INCOMPLETE")
    if set(ledger) != {"kind", "entries", "ledger_digest"}:
        _reject("FINDING_LEDGER_INCOMPLETE")
    if ledger.get("kind") != "review_finding_ledger.v1":
        _reject("FINDING_LEDGER_INCOMPLETE")
    entries = _sequence(ledger.get("entries"), "FINDING_LEDGER_INCOMPLETE")
    for raw_entry in entries:
        entry = _mapping(raw_entry, "FINDING_LEDGER_INCOMPLETE")
        entry_digest = _digest(
            entry.get("entry_digest"), "FINDING_LEDGER_INCOMPLETE"
        )
        body = {key: item for key, item in entry.items() if key != "entry_digest"}
        if not body or entry_digest != _canonical_digest(body):
            _reject("FINDING_LEDGER_INCOMPLETE")
    ledger_digest = _digest(
        ledger.get("ledger_digest"), "FINDING_LEDGER_INCOMPLETE"
    )
    body = {key: item for key, item in ledger.items() if key != "ledger_digest"}
    if ledger_digest != _canonical_digest(body):
        _reject("FINDING_LEDGER_INCOMPLETE")
    return ledger


def _review_records(
    bundle: Mapping[str, object],
    accepted: Mapping[str, dict[str, object]],
    aliases: Mapping[str, str],
) -> tuple[dict[str, object], ...]:
    values = _sequence(bundle.get("reviews"), "FINDING_LEDGER_INCOMPLETE")
    if len(values) != 4:
        _reject("FINDING_LEDGER_INCOMPLETE")
    by_key: dict[str, dict[str, object]] = {}
    for raw in values:
        review = _mapping(raw, "FINDING_LEDGER_INCOMPLETE")
        ticket_ref = _text(review.get("ticket_key"), "FINDING_LEDGER_INCOMPLETE")
        reference = _ticket_key_from_ref(ticket_ref)
        key = aliases.get(reference, reference)
        if key not in ROOT_TICKET_KEYS or key in by_key:
            _reject("FINDING_LEDGER_INCOMPLETE")
        open_findings = review.get("open_finding_ids", review.get("open_findings", []))
        if type(open_findings) not in {list, tuple} or open_findings:
            _reject("FINDING_LEDGER_INCOMPLETE")
        finding_digest = _digest(
            review.get("finding_ledger_digest"), "FINDING_LEDGER_INCOMPLETE"
        )
        accepted_receipt = accepted.get(key)
        if accepted_receipt is None or finding_digest != accepted_receipt.get(
            "review_finding_ledger_digest"
        ):
            _reject("FINDING_LEDGER_INCOMPLETE")
        candidate_digest = _digest(
            review.get("candidate_receipt_digest"), "FINDING_LEDGER_INCOMPLETE"
        )
        if candidate_digest != accepted_receipt.get("candidate_receipt_digest"):
            _reject("FINDING_LEDGER_INCOMPLETE")
        ledger = _validate_finding_ledger(review.get("finding_ledger"))
        if ledger["ledger_digest"] != finding_digest:
            _reject("FINDING_LEDGER_INCOMPLETE")
        by_key[key] = review
    if set(by_key) != set(ROOT_TICKET_KEYS):
        _reject("FINDING_LEDGER_INCOMPLETE")
    return tuple(by_key[key] for key in ROOT_TICKET_KEYS)


def _local_readback(
    batch: Mapping[str, object], batch_sha: str
) -> None:
    local = batch.get("local_suite")
    if local is None:
        local = batch.get("local_check")
    if local is None:
        local = batch.get("local_check_receipt")
    if local is None:
        if _readback_digest(
            batch,
            "local_check_receipt_digest",
            "local_receipt_digest",
        ) is None:
            _reject("LOCAL_SUITE_INCOMPLETE")
        return
    mapping = _mapping(local, "LOCAL_SUITE_INCOMPLETE")
    status = mapping.get("status", mapping.get("conclusion"))
    if status not in {"passed", "success", "successful"}:
        _reject("LOCAL_SUITE_SHA_MISMATCH")
    head_sha = mapping.get("head_sha", mapping.get("batch_sha"))
    if head_sha != batch_sha:
        _reject("LOCAL_SUITE_SHA_MISMATCH")


def _pull_request_readback(
    batch: Mapping[str, object], batch_sha: str
) -> int:
    pull_request = batch.get("pull_request")
    if pull_request is None:
        pull_request = batch.get("pr")
    if pull_request is None:
        number = batch.get("pull_request_number")
        head_sha = batch.get("pull_request_head_sha")
        if number is None and head_sha is None:
            _reject("PULL_REQUEST_READBACK_INCOMPLETE")
        pull_request = {
            "number": number,
            "head_sha": head_sha,
        }
    mapping = _mapping(pull_request, "PULL_REQUEST_READBACK_INCOMPLETE")
    number = _positive_int(mapping.get("number"), "PULL_REQUEST_READBACK_INCOMPLETE")
    if mapping.get("head_sha") != batch_sha:
        _reject("HOSTED_SHA_MISMATCH")
    return number


def _hosted_readback(
    batch: Mapping[str, object], batch_sha: str
) -> int | str:
    hosted = batch.get("hosted_ci")
    if hosted is None:
        hosted = batch.get("hosted_check")
    if hosted is None:
        run_id = batch.get("hosted_run_id")
        head_sha = batch.get("hosted_head_sha")
        conclusion = batch.get("hosted_conclusion")
        hosted = (
            None
            if run_id is None and head_sha is None and conclusion is None
            else {
                "run_id": run_id,
                "head_sha": head_sha,
                "conclusion": conclusion,
            }
        )
    if hosted is None:
        digest = _readback_digest(
            batch,
            "hosted_result_receipt_digest",
            "hosted_receipt_digest",
        )
        if digest is None:
            _reject("HOSTED_CI_READBACK_INCOMPLETE")
        return digest
    mapping = _mapping(hosted, "HOSTED_CI_READBACK_INCOMPLETE")
    run_id = mapping.get("run_id")
    if type(run_id) is int:
        if run_id <= 0:
            _reject("HOSTED_CI_READBACK_INCOMPLETE")
    elif type(run_id) is not str or not run_id:
        _reject("HOSTED_CI_READBACK_INCOMPLETE")
    if mapping.get("head_sha") != batch_sha:
        _reject("HOSTED_SHA_MISMATCH")
    if mapping.get("conclusion") not in {"success", "passed", "successful"}:
        _reject("HOSTED_SHA_MISMATCH")
    return run_id


def _integration_readback(batch: Mapping[str, object]) -> None:
    integration = batch.get("integration_lease")
    if integration is not None:
        mapping = _mapping(integration, "INTEGRATION_NOT_SERIALIZED")
        if mapping.get("serialized") is not True:
            _reject("INTEGRATION_NOT_SERIALIZED")
        return
    serialized = batch.get("integration_lease_serialized")
    if serialized is not None:
        if serialized is not True:
            _reject("INTEGRATION_NOT_SERIALIZED")
        return
    if _readback_digest(batch, "integration_lease_digest") is None:
        _reject("INTEGRATION_NOT_SERIALIZED")


def _target_readback(
    batch: Mapping[str, object],
    batch_sha: str,
    pull_request_number: int,
    expected_target_sha: str | None,
) -> str:
    target = batch.get("target_readback")
    if target is None:
        target = batch.get("target")
    if target is None:
        direct = {
            "merge_method": batch.get("merge_method"),
            "batch_sha_is_ancestor": batch.get(
                "batch_sha_is_ancestor", batch.get("target_contains_batch_sha")
            ),
            "remote_target_sha": batch.get(
                "remote_target_sha", batch.get("target_head_sha")
            ),
            "pull_request_number": batch.get("pull_request_number"),
            "pull_request_head_sha": batch.get("pull_request_head_sha"),
            "pull_request_merge_target_sha": batch.get(
                "pull_request_merge_target_sha"
            ),
            "merge_commit_sha": batch.get("integrated_target_sha"),
        }
        target = direct
    mapping = _mapping(target, "TARGET_SHA_INCOMPLETE")
    if mapping.get("merge_method") != "merge":
        _reject("TARGET_SHA_MISMATCH")
    ancestor = mapping.get(
        "batch_sha_is_ancestor", mapping.get("target_contains_batch_sha")
    )
    if ancestor is not True:
        _reject("TARGET_SHA_MISMATCH")
    remote_target = mapping.get(
        "remote_target_sha", mapping.get("target_head_sha")
    )
    remote_target = _text(remote_target, "TARGET_SHA_INCOMPLETE")
    target_pr_number = mapping.get(
        "pull_request_number", mapping.get("pr_number")
    )
    if target_pr_number != pull_request_number:
        _reject("TARGET_SHA_MISMATCH")
    if mapping.get("pull_request_head_sha") != batch_sha:
        _reject("TARGET_SHA_MISMATCH")
    if mapping.get("pull_request_merge_target_sha") != remote_target:
        _reject("TARGET_SHA_MISMATCH")
    if mapping.get("merge_commit_sha") != remote_target:
        _reject("TARGET_SHA_MISMATCH")
    integrated = batch.get("integrated_target_sha")
    if integrated is not None and integrated != remote_target:
        _reject("TARGET_SHA_MISMATCH")
    if expected_target_sha is not None and remote_target != expected_target_sha:
        _reject("TARGET_SHA_MISMATCH")
    if batch.get("target_head_sha") is not None and batch["target_head_sha"] != remote_target:
        _reject("TARGET_SHA_MISMATCH")
    return remote_target


def _batch_records(
    bundle: Mapping[str, object],
    aliases: Mapping[str, str],
    campaign_key: str,
    plan_revision_digest: str,
    expected_target_sha: str | None,
) -> tuple[VerifiedBatch, VerifiedBatch]:
    raw_batches = _sequence(bundle.get("batches"), "BATCH_READBACK_INCOMPLETE")
    if len(raw_batches) != 2:
        _reject("BATCH_READBACK_INCOMPLETE")
    by_kind: dict[str, VerifiedBatch] = {}
    for raw in raw_batches:
        batch = _mapping(raw, "BATCH_READBACK_INCOMPLETE")
        raw_kind = _text(
            batch.get("batch_kind", batch.get("group")), "BATCH_READBACK_INCOMPLETE"
        ).lower()
        if raw_kind in {"standard", "multi"}:
            kind = "multi"
            expected = STANDARD_TICKET_KEYS
        elif raw_kind == "strict" or raw_kind == "singleton":
            kind = "singleton"
            expected = (STRICT_TICKET_KEY,)
        else:
            _reject("BATCH_MEMBERS_INVALID")
        if kind in by_kind:
            _reject("BATCH_READBACK_INCOMPLETE")
        members = _sequence(batch.get("member_ticket_keys"), "BATCH_MEMBERS_INVALID")
        normalised_members: list[str] = []
        for member in members:
            ref = _ticket_key_from_ref(member)
            if ref in aliases:
                normalised_members.append(aliases[ref])
            elif ref in ROOT_TICKET_KEYS:
                normalised_members.append(ref)  # type: ignore[arg-type]
            else:
                _reject("BATCH_MEMBERS_INVALID")
        if tuple(normalised_members) != expected:
            _reject("BATCH_MEMBERS_INVALID")
        batch_id = _text(batch.get("batch_id"), "BATCH_READBACK_INCOMPLETE")
        batch_sha = _text(batch.get("batch_sha"), "BATCH_READBACK_INCOMPLETE")
        if (
            batch.get("repository") != ROOT_REPOSITORY
            or batch.get("campaign_key") != campaign_key
            or batch.get("plan_revision_digest") != plan_revision_digest
        ):
            _reject("BATCH_READBACK_INCOMPLETE")
        for section_name in (
            "local_suite",
            "local_check",
            "local_check_receipt",
            "pull_request",
            "pr",
            "hosted_ci",
            "hosted_check",
            "target_readback",
            "target",
        ):
            section = batch.get(section_name)
            if type(section) is dict and section.get("repository") not in {
                None,
                ROOT_REPOSITORY,
            }:
                _reject("ROOT_REPOSITORY_MISMATCH")
        target_branch = batch.get("target_branch")
        if target_branch is not None and target_branch != "main":
            _reject("TARGET_SHA_MISMATCH")
        target_mapping = batch.get("target_readback")
        if type(target_mapping) is dict and target_mapping.get("target_branch") not in {
            None,
            "main",
        }:
            _reject("TARGET_SHA_MISMATCH")
        receipt_digest = _readback_digest(
            batch,
            "receipt_digest",
            "batch_receipt_digest",
            "delivery_receipt_digest",
            "proof_digest",
        )
        if receipt_digest is None:
            _reject("BATCH_READBACK_INCOMPLETE")
        _local_readback(batch, batch_sha)
        pull_request_number = _pull_request_readback(batch, batch_sha)
        hosted_run_id = _hosted_readback(batch, batch_sha)
        _integration_readback(batch)
        target_sha = _target_readback(
            batch, batch_sha, pull_request_number, expected_target_sha
        )
        by_kind[kind] = VerifiedBatch(
            batch_id=batch_id,
            member_count=len(normalised_members),
            pull_request_number=pull_request_number,
            hosted_run_id=hosted_run_id,
            batch_sha=batch_sha,
            target_sha=target_sha,
            receipt_digest=receipt_digest,
        )
    if set(by_kind) != {"multi", "singleton"}:
        _reject("BATCH_READBACK_INCOMPLETE")
    if (
        by_kind["multi"].batch_id == by_kind["singleton"].batch_id
        or by_kind["multi"].batch_sha == by_kind["singleton"].batch_sha
        or by_kind["multi"].receipt_digest == by_kind["singleton"].receipt_digest
        or by_kind["multi"].target_sha == by_kind["singleton"].target_sha
        or by_kind["multi"].pull_request_number
        == by_kind["singleton"].pull_request_number
        or by_kind["multi"].hosted_run_id == by_kind["singleton"].hosted_run_id
    ):
        _reject("BATCH_BOUNDARY_COLLAPSED")
    return by_kind["multi"], by_kind["singleton"]


def _validate_batch_crosslinks(
    bundle: Mapping[str, object],
    aliases: Mapping[str, str],
    candidates: tuple[dict[str, object], ...],
    reviews: tuple[dict[str, object], ...],
) -> None:
    candidate_by_key: dict[str, str] = {}
    for candidate in candidates:
        ref = _text(candidate.get("ticket_key"), "BATCH_READBACK_INCOMPLETE")
        key = aliases.get(ref, ref)
        digest = _readback_digest(
            candidate, "candidate_receipt_digest", "receipt_digest"
        )
        if key not in ROOT_TICKET_KEYS or digest is None:
            _reject("BATCH_READBACK_INCOMPLETE")
        if key in candidate_by_key:
            _reject("BATCH_READBACK_INCOMPLETE")
        candidate_by_key[key] = digest
    finding_by_key: dict[str, str] = {}
    for review in reviews:
        ref = _text(review.get("ticket_key"), "BATCH_READBACK_INCOMPLETE")
        key = aliases.get(ref, ref)
        digest = _readback_digest(
            review,
            "finding_ledger_digest",
            "review_finding_ledger_digest",
            "ledger_digest",
        )
        if key not in ROOT_TICKET_KEYS or digest is None:
            _reject("BATCH_READBACK_INCOMPLETE")
        if key in finding_by_key:
            _reject("BATCH_READBACK_INCOMPLETE")
        finding_by_key[key] = digest

    raw_batches = _sequence(bundle.get("batches"), "BATCH_READBACK_INCOMPLETE")
    for raw in raw_batches:
        batch = _mapping(raw, "BATCH_READBACK_INCOMPLETE")
        members = _sequence(
            batch.get("member_ticket_keys"), "BATCH_MEMBERS_INVALID"
        )
        keys: list[str] = []
        for member in members:
            ref = _ticket_key_from_ref(member)
            key = aliases.get(ref, ref)
            if key not in ROOT_TICKET_KEYS:
                _reject("BATCH_MEMBERS_INVALID")
            keys.append(key)  # type: ignore[arg-type]
        expected_candidates = [candidate_by_key[key] for key in keys]
        actual_candidates = _sequence(
            batch.get("candidate_receipt_digests"),
            "BATCH_READBACK_INCOMPLETE",
        )
        if tuple(actual_candidates) != tuple(expected_candidates):
            _reject("BATCH_READBACK_INCOMPLETE")
        expected_findings = [finding_by_key[key] for key in keys]
        actual_findings = _sequence(
            batch.get("finding_ledger_digests"), "BATCH_READBACK_INCOMPLETE"
        )
        if tuple(actual_findings) != tuple(expected_findings):
            _reject("BATCH_READBACK_INCOMPLETE")


def _authoritative_evidence(
    bundle: Mapping[str, object],
    proof: Mapping[str, object],
    tickets: tuple[dict[str, object], ...],
    candidates: tuple[dict[str, object], ...],
    accepted: Mapping[str, dict[str, object]],
    reviews: tuple[dict[str, object], ...],
) -> dict[str, object]:
    """Capture the complete readbacks covered by the acceptance digest."""

    raw_batches = _sequence(bundle.get("batches"), "BATCH_READBACK_INCOMPLETE")
    batches = [
        _mapping(value, "BATCH_READBACK_INCOMPLETE") for value in raw_batches
    ]
    target_readbacks: list[dict[str, object]] = []
    for batch in batches:
        target = batch.get("target_readback", batch.get("target"))
        if target is None:
            target = {
                "merge_method": batch.get("merge_method"),
                "batch_sha_is_ancestor": batch.get(
                    "batch_sha_is_ancestor", batch.get("target_contains_batch_sha")
                ),
                "remote_target_sha": batch.get(
                    "remote_target_sha", batch.get("target_head_sha")
                ),
                "pull_request_number": batch.get("pull_request_number"),
                "pull_request_head_sha": batch.get("pull_request_head_sha"),
                "pull_request_merge_target_sha": batch.get(
                    "pull_request_merge_target_sha"
                ),
                "merge_commit_sha": batch.get("integrated_target_sha"),
            }
        target_readbacks.append(_mapping(target, "TARGET_SHA_INCOMPLETE"))
    return {
        "tickets": copy.deepcopy(list(tickets)),
        "candidates": copy.deepcopy(list(candidates)),
        "accepted_candidate_receipts": copy.deepcopy(
            [accepted[key] for key in ROOT_TICKET_KEYS]
        ),
        "reviews": copy.deepcopy(list(reviews)),
        "batches": copy.deepcopy(batches),
        "target_readbacks": copy.deepcopy(target_readbacks),
        "recovery": copy.deepcopy(dict(proof)),
        "effects": copy.deepcopy(
            {
                "semantic_effect_ids": proof.get("semantic_effect_ids"),
                "external_effect_ids": proof.get("external_effect_ids"),
                "duplicate_effect_ids": proof.get("duplicate_effect_ids"),
            }
        ),
        "policy_witness": copy.deepcopy(bundle.get("policy_witness")),
        "authority_root": copy.deepcopy(bundle.get("authority_root")),
        "runtime_selector": copy.deepcopy(bundle.get("runtime_selector")),
        "fault_journal": copy.deepcopy(bundle.get("fault_journal")),
        "diagnostics": copy.deepcopy(bundle.get("diagnostics")),
        "result_integrities": copy.deepcopy(bundle.get("result_integrities")),
    }


def _accepted_candidate_records(
    bundle: Mapping[str, object],
    aliases: Mapping[str, str],
    candidates: tuple[dict[str, object], ...],
) -> dict[str, dict[str, object]]:
    raw_values = _sequence(
        bundle.get("accepted_candidate_receipts"), "CANDIDATE_RECEIPT_INCOMPLETE"
    )
    if len(raw_values) != 4:
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    candidate_by_key: dict[str, dict[str, object]] = {}
    for candidate in candidates:
        reference = _ticket_key_from_ref(
            _text(candidate.get("ticket_key"), "CANDIDATE_RECEIPT_INCOMPLETE")
        )
        key = aliases.get(reference, reference)
        if key not in ROOT_TICKET_KEYS or key in candidate_by_key:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        candidate_by_key[key] = candidate

    accepted: dict[str, dict[str, object]] = {}
    seen_receipt_digests: set[str] = set()
    seen_work_runs: set[str] = set()
    for raw in raw_values:
        item = _validate_accepted_candidate_receipt(raw)
        reference = _ticket_key_from_ref(item["ticket_key"])
        key = aliases.get(reference, reference)
        if key not in ROOT_TICKET_KEYS or key in accepted:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        candidate = candidate_by_key.get(key)
        if candidate is None:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        candidate_digest = _digest(
            candidate.get("candidate_receipt_digest"),
            "CANDIDATE_RECEIPT_INCOMPLETE",
        )
        if item["candidate_receipt_digest"] != candidate_digest:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        if item["receipt_digest"] in seen_receipt_digests or item["work_run_key"] in seen_work_runs:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        nested = candidate.get("accepted_candidate_receipt")
        nested_mapping = _validate_accepted_candidate_receipt(nested)
        if canonical_json_bytes(nested_mapping) != canonical_json_bytes(item):
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        accepted[key] = item
        seen_receipt_digests.add(item["receipt_digest"])
        seen_work_runs.add(item["work_run_key"])
    if set(accepted) != set(ROOT_TICKET_KEYS):
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    return accepted


def _validate_result_integrity(
    bundle: Mapping[str, object],
    aliases: Mapping[str, str],
    batches: tuple[VerifiedBatch, VerifiedBatch],
) -> None:
    raw_values = bundle.get("result_integrities")
    if raw_values is None:
        return
    values = _sequence(raw_values, "BATCH_READBACK_INCOMPLETE")
    if len(values) != 4:
        _reject("BATCH_READBACK_INCOMPLETE")
    batch_by_id = {batch.batch_id: batch for batch in batches}
    accepted_digests = {
        _readback_digest(value, "candidate_receipt_digest", "receipt_digest")
        for value in _sequence(
            bundle.get("accepted_candidate_receipts"),
            "BATCH_READBACK_INCOMPLETE",
        )
    }
    if None in accepted_digests:
        _reject("BATCH_READBACK_INCOMPLETE")
    seen: set[str] = set()
    for raw in values:
        result = _mapping(raw, "BATCH_READBACK_INCOMPLETE")
        accepted_digest = _text(
            result.get("accepted_candidate_receipt_digest"),
            "BATCH_READBACK_INCOMPLETE",
        )
        if accepted_digest in seen:
            _reject("BATCH_READBACK_INCOMPLETE")
        if accepted_digest not in accepted_digests:
            _reject("BATCH_READBACK_INCOMPLETE")
        seen.add(accepted_digest)
        batch_id = _text(result.get("batch_id"), "BATCH_READBACK_INCOMPLETE")
        batch = batch_by_id.get(batch_id)
        if batch is None:
            _reject("BATCH_READBACK_INCOMPLETE")
        if result.get("batch_sha") != batch.batch_sha:
            _reject("TARGET_SHA_MISMATCH")
        members = _sequence(
            result.get("delivery_member_ticket_keys"),
            "BATCH_MEMBERS_INVALID",
        )
        expected = (
            STANDARD_TICKET_KEYS
            if batch.member_count == 3
            else (STRICT_TICKET_KEY,)
        )
        normalised_values: list[str] = []
        for member in members:
            ref = _ticket_key_from_ref(member)
            if ref in aliases:
                normalised_values.append(aliases[ref])
            elif ref in ROOT_TICKET_KEYS:
                normalised_values.append(ref)
            else:
                _reject("BATCH_MEMBERS_INVALID")
        normalised = tuple(normalised_values)
        if normalised != expected:
            _reject("BATCH_MEMBERS_INVALID")
        if result.get("target_contains_batch_sha") is not True:
            _reject("TARGET_SHA_MISMATCH")


def _merge_ticket_manifest(
    bundle: Mapping[str, object], manifest: Mapping[str, object], repository: str
) -> dict[str, object]:
    """Merge Task 1's v2 manifest without projecting it into a v1 shape."""

    if manifest.get("repository") != repository or repository != ROOT_REPOSITORY:
        _reject("ROOT_REPOSITORY_MISMATCH")
    if manifest.get("schema") != _TASK1_TICKET_SCHEMA:
        _reject("ROOT_TICKET_MANIFEST_INVALID")
    manifest_tickets = _validate_v2_manifest(manifest)

    merged = dict(bundle)
    existing = bundle.get("tickets")
    if existing is not None:
        existing_values = _sequence(existing, "TICKET_READBACK_MISMATCH")
        if len(existing_values) != len(manifest_tickets):
            _reject("TICKET_READBACK_MISMATCH")
        # An authoritative v2 manifest cannot be compared with, or silently
        # overwrite, a v1-only diagnostics projection.
        if any(
            type(value) is not dict or "contract" not in value
            for value in existing_values
        ):
            _reject("TICKET_READBACK_MISMATCH")
        existing_tickets = tuple(
            _validate_v2_ticket(
                value,
                _text(
                    _mapping(value, "TICKET_READBACK_MISMATCH").get("key"),
                    "TICKET_READBACK_MISMATCH",
                ),
                "TICKET_READBACK_MISMATCH",
            )
            for value in existing_values
        )
        if canonical_json_bytes(existing_tickets) != canonical_json_bytes(
            manifest_tickets
        ):
            _reject("TICKET_READBACK_MISMATCH")

    merged["repository"] = repository
    # Preserve the complete v2 objects.  Never flatten contract fields into a
    # legacy ticket record or let the diagnostics projection replace them.
    merged["tickets"] = [copy.deepcopy(value) for value in manifest_tickets]
    ready_refs = manifest.get("ready_refs")
    if ready_refs is not None:
        refs = _sequence(ready_refs, "ROOT_TICKET_MANIFEST_INVALID")
        if bundle.get("acceptance_mode") == _LOCAL_ROOT_MODE and tuple(refs) != _LOCAL_TICKET_REFS:
            _reject("ROOT_TICKET_REAL_ISSUES_REQUIRED")
        if "ready_refs" in bundle and canonical_json_bytes(bundle["ready_refs"]) != canonical_json_bytes(refs):
            _reject("TICKET_READBACK_MISMATCH")
        merged["ready_refs"] = list(refs)
    return merged


def _adapt_current_readback_shape(bundle: dict[str, object]) -> dict[str, object]:
    """Flatten the current inspect/evidence projection without inventing proof."""

    facts = bundle.get("facts")
    facts_mapping = (
        {} if facts is None else _mapping(facts, "DIAGNOSTICS_INVALID")
    )
    readback = bundle.get("readback")
    if readback is None:
        readback = facts_mapping.get("readback")
    readback_mapping = (
        {} if readback is None else _mapping(readback, "DIAGNOSTICS_INVALID")
    )

    campaign = bundle.get("campaign")
    if campaign is not None:
        campaign = _mapping(campaign, "DIAGNOSTICS_INVALID")
        bundle.setdefault("campaign_key", campaign.get("campaign_key"))
        bundle.setdefault("repository", campaign.get("repository"))
    facts_campaign = facts_mapping.get("campaign")
    if facts_campaign is not None:
        facts_campaign = _mapping(facts_campaign, "DIAGNOSTICS_INVALID")
        bundle.setdefault("campaign_key", facts_campaign.get("campaign_key"))
    plan_revision = facts_mapping.get("plan_revision")
    if plan_revision is not None:
        plan_revision = _mapping(plan_revision, "DIAGNOSTICS_INVALID")
        bundle.setdefault("plan_revision_digest", plan_revision.get("digest"))

    accepted_values = readback_mapping.get("accepted_candidate_receipts")
    accepted_by_ticket: dict[str, dict[str, object]] = {}
    if accepted_values is not None:
        for raw_value in _sequence(accepted_values, "DIAGNOSTICS_INVALID"):
            value = _mapping(raw_value, "DIAGNOSTICS_INVALID")
            ticket_key = _text(value.get("ticket_key"), "DIAGNOSTICS_INVALID")
            if ticket_key in accepted_by_ticket:
                _reject("DIAGNOSTICS_INVALID")
            accepted_by_ticket[ticket_key] = value
    candidate_values = readback_mapping.get("candidate_receipts")
    candidate_readbacks: tuple[dict[str, object], ...] | None = None
    if candidate_values is not None:
        candidate_readbacks = tuple(
            _mapping(raw_value, "DIAGNOSTICS_INVALID")
            for raw_value in _sequence(candidate_values, "DIAGNOSTICS_INVALID")
        )
    if candidate_readbacks is not None and "candidates" not in bundle:
        candidates: list[dict[str, object]] = []
        for value in candidate_readbacks:
            ticket_key = value.get("ticket_key")
            accepted = accepted_by_ticket.get(ticket_key, {})
            candidates.append(
                {
                    "ticket_key": ticket_key,
                    "candidate_receipt_digest": value.get("receipt_digest"),
                    "candidate_receipt": value,
                    **(
                        {"assurance": value["assurance"]}
                        if value.get("assurance") is not None
                        else (
                            {"assurance": accepted["assurance"]}
                            if accepted.get("assurance") is not None
                            else {}
                        )
                    ),
                    **(
                        {"diff_record_digest": value.get("diff_record_digest")}
                        if value.get("diff_record_digest") is not None
                        else {}
                    ),
                    **(
                        {"accepted_candidate_receipt": accepted}
                        if accepted
                        else {}
                    ),
                }
            )
        bundle["candidates"] = candidates
    if "accepted_candidate_receipts" not in bundle and accepted_values is not None:
        bundle["accepted_candidate_receipts"] = list(
            _sequence(accepted_values, "DIAGNOSTICS_INVALID")
        )
    if "reviews" not in bundle and accepted_by_ticket:
        bundle["reviews"] = [
            {
                "ticket_key": ticket_key,
                "open_finding_ids": [],
                "finding_ledger_digest": accepted_by_ticket[ticket_key].get(
                    "review_finding_ledger_digest"
                ),
            }
            for ticket_key in sorted(accepted_by_ticket)
        ]

    current_batches = facts_mapping.get("batches")
    delivery_values = readback_mapping.get("delivery_proofs")
    if delivery_values is not None:
        delivery_values = _sequence(delivery_values, "DIAGNOSTICS_INVALID")
    if current_batches is not None and "batches" not in bundle:
        current_batches = _sequence(current_batches, "DIAGNOSTICS_INVALID")
        proofs_by_batch: dict[object, dict[str, object]] = {}
        if delivery_values is not None:
            for raw_value in delivery_values:
                value = _mapping(raw_value, "DIAGNOSTICS_INVALID")
                batch_id = _text(value.get("batch_id"), "DIAGNOSTICS_INVALID")
                if batch_id in proofs_by_batch:
                    _reject("DIAGNOSTICS_INVALID")
                proofs_by_batch[batch_id] = value
        batches: list[dict[str, object]] = []
        for raw_value in current_batches:
            value = _mapping(raw_value, "DIAGNOSTICS_INVALID")
            batch_id = _text(value.get("batch_id"), "DIAGNOSTICS_INVALID")
            proof = proofs_by_batch.get(batch_id, {})
            member_ticket_keys = _sequence(
                value.get("member_ticket_keys"), "DIAGNOSTICS_INVALID"
            )
            finding_digests = [
                accepted_by_ticket.get(ticket_key, {}).get(
                    "review_finding_ledger_digest"
                )
                for ticket_key in member_ticket_keys
            ]
            delivery_digests = value.get("delivery_receipt_digests")
            receipt_digest = (
                delivery_digests[0]
                if type(delivery_digests) in {list, tuple} and delivery_digests
                else proof.get("batch_delivery_receipt_digest")
            )
            batch = {
                "batch_kind": value.get("group"),
                "batch_id": value.get("batch_id"),
                "repository": bundle.get("repository"),
                "campaign_key": bundle.get("campaign_key"),
                "plan_revision_digest": bundle.get("plan_revision_digest"),
                "member_ticket_keys": list(member_ticket_keys),
                "candidate_receipt_digests": value.get(
                    "candidate_receipt_digests"
                ),
                "finding_ledger_digests": value.get(
                    "finding_ledger_digests", finding_digests
                ),
                "batch_sha": value.get("batch_sha"),
                "local_check_receipt_digest": proof.get(
                    "local_check_receipt_digest"
                ),
                    "pull_request": {
                    "number": proof.get("pull_request_number"),
                    "head_sha": proof.get("pull_request_head_sha"),
                    "repository": bundle.get("repository"),
                },
                "hosted_result_receipt_digest": proof.get(
                    "hosted_result_receipt_digest"
                ),
                "integration_lease_digest": proof.get("integration_lease_digest"),
                "target_readback": {
                    "merge_method": proof.get("merge_method"),
                    "batch_sha_is_ancestor": proof.get(
                        "target_contains_batch_sha"
                    ),
                    "remote_target_sha": proof.get("target_head_sha"),
                    "target_branch": proof.get("target_branch"),
                    "pull_request_number": proof.get("pull_request_number"),
                    "pull_request_head_sha": proof.get("pull_request_head_sha"),
                    "pull_request_merge_target_sha": proof.get(
                        "pull_request_merge_target_sha"
                    ),
                    "merge_commit_sha": proof.get("target_head_sha"),
                },
                "integrated_target_sha": proof.get("target_head_sha"),
                "receipt_digest": receipt_digest,
            }
            batches.append(batch)
        bundle["batches"] = batches
    return bundle


def _bundle_from_diagnostics(raw: Mapping[str, object]) -> dict[str, object]:
    acceptance_bundle = raw.get("acceptance_bundle")
    if acceptance_bundle is not None:
        bundle = dict(_mapping(acceptance_bundle, "ACCEPTANCE_BUNDLE_INVALID"))
        if "diagnostics" not in bundle and (
            "status" in raw or "public_status" in raw or "proof" in raw
        ):
            bundle["diagnostics"] = {
                "status": raw.get("status", raw.get("public_status")),
                "proof": raw.get("proof"),
            }
    else:
        bundle = dict(raw)
    if (
        "acceptance_mode" in bundle
        or bundle.get("schema_version") == _LOCAL_ROOT_SCHEMA
        or "local_evidence" in bundle
    ):
        return bundle
    return _adapt_current_readback_shape(bundle)


_LOCAL_FORBIDDEN_FIELD_ALIASES = frozenset(
    {
        "ci",
        "cirunid",
        "check",
        "checkid",
        "checkurl",
        "hosted",
        "hostedci",
        "hostedcheck",
        "hostedconclusion",
        "hostedheadsha",
        "hostedrun",
        "hostedrunid",
        "hostedresultreceiptdigest",
        "hostedreceiptdigest",
        "pr",
        "publication",
        "publicationreceipt",
        "pullrequest",
        "pullrequestheadsha",
        "pullrequestmergetargetsha",
        "pullrequestnumber",
        "remote",
        "remotetarget",
        "remotetargetsha",
        "run",
        "runid",
        "runkey",
        "workflow",
        "workflowrun",
        "workflowrunid",
        "workflowurl",
        "url",
    }
)


def _reject_local_forbidden_fields(value: object) -> None:
    if type(value) is dict:
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if (
                normalized in _LOCAL_FORBIDDEN_FIELD_ALIASES
                or normalized.startswith(("hosted", "pullrequest", "publication"))
                or normalized.startswith("remote")
                or normalized.startswith("workflow")
                or normalized.startswith("check")
                or normalized.endswith("url")
            ):
                _reject("LOCAL_BATCH_HOSTED_FIELD_FORBIDDEN")
            _reject_local_forbidden_fields(item)
    elif type(value) in {list, tuple}:
        for item in value:
            _reject_local_forbidden_fields(item)


def _local_manifest_entries(
    bundle: Mapping[str, object],
) -> tuple[dict[str, object], ...]:
    raw_refs = _sequence(bundle.get("ready_refs"), "ROOT_TICKET_MANIFEST_INVALID")
    if tuple(raw_refs) != _LOCAL_TICKET_REFS:
        _reject("ROOT_TICKET_REAL_ISSUES_REQUIRED")
    raw_tickets = _sequence(
        bundle.get("tickets"), "ROOT_TICKET_MANIFEST_INVALID"
    )
    if len(raw_tickets) != len(_LOCAL_TICKET_REFS):
        _reject("ROOT_TICKET_REAL_ISSUES_REQUIRED")
    if tuple(
        _mapping(value, "ROOT_TICKET_MANIFEST_INVALID").get("key")
        for value in raw_tickets
    ) != _LOCAL_TICKET_REFS:
        _reject("ROOT_TICKET_REAL_ISSUES_REQUIRED")
    return tuple(
        _validate_v2_ticket(value, ref, "ROOT_TICKET_MANIFEST_INVALID")
        for value, ref in zip(raw_tickets, _LOCAL_TICKET_REFS, strict=True)
    )


def _local_facts_and_readback(
    bundle: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    raw_facts = bundle.get("facts")
    if raw_facts is None:
        facts: dict[str, object] = {}
        readback = _mapping(bundle, "LOCAL_EVIDENCE_INCOMPLETE")
    else:
        facts = _mapping(raw_facts, "LOCAL_EVIDENCE_INCOMPLETE")
        readback = _mapping(
            facts.get("readback"), "LOCAL_EVIDENCE_INCOMPLETE"
        )
    if set(readback) != _LOCAL_READBACK_KEYS:
        _reject("LOCAL_READBACK_INCOMPLETE")
    return facts, readback



def _local_identity(
    bundle: Mapping[str, object],
    facts: Mapping[str, object],
    local_batches: Sequence[object],
) -> tuple[str, str, str, str, str, str]:
    campaign = bundle.get("campaign", facts.get("campaign"))
    campaign_mapping = _mapping(campaign, "LOCAL_EVIDENCE_INCOMPLETE")
    repository = campaign_mapping.get("repository", bundle.get("repository"))
    if repository != ROOT_REPOSITORY:
        _reject("ROOT_REPOSITORY_MISMATCH")
    campaign_key = bundle.get("campaign_key", campaign_mapping.get("campaign_key"))
    campaign_key = _text(campaign_key, "LOCAL_EVIDENCE_INCOMPLETE")
    plan_revision = facts.get("plan_revision")
    plan_mapping = (
        _mapping(plan_revision, "LOCAL_EVIDENCE_INCOMPLETE")
        if plan_revision is not None
        else {}
    )
    plan_digest = bundle.get(
        "plan_revision_digest", plan_mapping.get("digest")
    )
    if plan_digest is None and local_batches:
        first_batch = _mapping(local_batches[0], "LOCAL_BATCH_PROOF_INCOMPLETE")
        plan_digest = first_batch.get("plan_revision_digest")
    plan_digest = _digest(plan_digest, "LOCAL_EVIDENCE_INCOMPLETE")

    first_batch = _mapping(local_batches[0], "LOCAL_BATCH_PROOF_INCOMPLETE")
    lease = _mapping(
        first_batch.get("integration_lease"), "LOCAL_INTEGRATION_LEASE_INVALID"
    )
    activation_id = _text(lease.get("activation"), "LOCAL_INTEGRATION_LEASE_INVALID")
    writer_generation = _text(
        lease.get("writer"), "LOCAL_INTEGRATION_LEASE_INVALID"
    )
    target = _mapping(
        first_batch.get("target_readback"), "LOCAL_TARGET_READBACK_INVALID"
    )
    target_after = _mapping(
        target.get("target_after"), "LOCAL_TARGET_READBACK_INVALID"
    )
    canary_target_sha = _object_id(
        target_after.get("commit_sha"), "LOCAL_TARGET_READBACK_INVALID"
    )
    return (
        repository,
        campaign_key,
        plan_digest,
        activation_id,
        writer_generation,
        canary_target_sha,
    )


def _local_candidate_records(
    bundle: Mapping[str, object],
    readback: Mapping[str, object],
    repository: str,
    campaign_key: str,
    plan_revision_digest: str,
) -> tuple[
    tuple[dict[str, object], ...],
    dict[str, dict[str, object]],
    dict[str, dict[str, object]],
]:
    raw_candidates = readback.get("candidate_receipts")
    candidate_values = _sequence(raw_candidates, "CANDIDATE_RECEIPT_INCOMPLETE")
    raw_accepted = readback.get("accepted_candidate_receipts")
    accepted_values = _sequence(
        raw_accepted, "CANDIDATE_RECEIPT_INCOMPLETE"
    )
    if len(candidate_values) != 4 or len(accepted_values) != 4:
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")

    candidates: dict[str, dict[str, object]] = {}
    for raw in candidate_values:
        candidate_mapping = _mapping(raw, "CANDIDATE_RECEIPT_INCOMPLETE")
        candidate_receipt = candidate_mapping.get("candidate_receipt", raw)
        receipt = _validate_candidate_receipt(candidate_receipt)
        ticket_key = _text(receipt.get("ticket_key"), "CANDIDATE_RECEIPT_INCOMPLETE")
        if ticket_key not in _LOCAL_TICKET_REFS or ticket_key in candidates:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        if candidate_mapping.get("ticket_key", ticket_key) != ticket_key:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        reported_digest = candidate_mapping.get(
            "candidate_receipt_digest", receipt["receipt_digest"]
        )
        if reported_digest != receipt["receipt_digest"]:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        for field, expected in (
            ("repository", repository),
            ("campaign_key", campaign_key),
            ("campaign_handle", campaign_key),
            ("plan_revision_digest", plan_revision_digest),
            ("ticket_key", ticket_key),
        ):
            if receipt.get(field) != expected:
                _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        candidates[ticket_key] = {
            "ticket_key": ticket_key,
            "candidate_receipt_digest": receipt["receipt_digest"],
            "candidate_receipt": receipt,
        }

    accepted: dict[str, dict[str, object]] = {}
    expected_assurance = {
        **{key: "standard" for key in _LOCAL_STANDARD_TICKET_REFS},
        _LOCAL_STRICT_TICKET_REF: "strict",
    }
    for raw in accepted_values:
        receipt = _validate_accepted_candidate_receipt(raw)
        ticket_key = _text(receipt.get("ticket_key"), "CANDIDATE_RECEIPT_INCOMPLETE")
        if ticket_key not in _LOCAL_TICKET_REFS or ticket_key in accepted:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        candidate = candidates.get(ticket_key)
        if candidate is None:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        if receipt["candidate_receipt_digest"] != candidate[
            "candidate_receipt_digest"
        ]:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        if receipt["assurance"] != expected_assurance[ticket_key]:
            _reject("ASSURANCE_SHAPE_INVALID")
        if receipt["repository"] != repository or receipt["campaign_key"] != campaign_key:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        if receipt["plan_revision_digest"] != plan_revision_digest:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        candidate_receipt = candidate["candidate_receipt"]
        for candidate_field, accepted_field in (
            ("work_run_key", "work_run_key"),
            ("base_commit_oid", "base_sha"),
            ("base_tree_oid", "base_tree_oid"),
            ("candidate_commit_oid", "candidate_sha"),
            ("candidate_tree_oid", "candidate_tree_oid"),
            ("diff_record_digest", "diff_record_digest"),
            ("authority_subtree_digest", "authority_subtree_digest"),
        ):
            if candidate_receipt[candidate_field] != receipt[accepted_field]:
                _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        accepted[ticket_key] = receipt

    if tuple(candidates) != _LOCAL_TICKET_REFS or tuple(accepted) != _LOCAL_TICKET_REFS:
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    wrappers = tuple(candidates[key] for key in _LOCAL_TICKET_REFS)
    return wrappers, candidates, accepted


def _local_review_links(
    bundle: Mapping[str, object],
    facts: Mapping[str, object],
    candidates: Mapping[str, dict[str, object]],
    accepted: Mapping[str, dict[str, object]],
    manifest: Sequence[dict[str, object]],
) -> dict[str, dict[str, object]]:
    transitions_value = facts.get("candidate_gate")
    transitions_mapping = (
        _mapping(transitions_value, "LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
        if transitions_value is not None
        else {}
    )
    raw_transitions = transitions_mapping.get("transitions")
    if raw_transitions is None:
        _reject("LOCAL_CANDIDATE_REVIEW_LINK_INVALID")

    transitions = tuple(
        _mapping(raw, "LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
        for raw in _sequence(raw_transitions, "LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
    )
    expected_transition_keys = tuple(
        ticket_key
        for ticket_key, statuses in _LOCAL_GATE_STATUS_HISTORY.items()
        for _status in statuses
    )
    if tuple(
        _text(item.get("ticket_key"), "LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
        for item in transitions
    ) != expected_transition_keys:
        _reject("LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
    histories: dict[str, list[str]] = {key: [] for key in _LOCAL_TICKET_REFS}
    for transition in transitions:
        key = _text(transition.get("ticket_key"), "LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
        status = _text(transition.get("status"), "LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
        if key not in histories or transition.get("persisted") is not True:
            _reject("LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
        histories[key].append(status)
    if any(
        tuple(histories[key]) != _LOCAL_GATE_STATUS_HISTORY[key]
        for key in _LOCAL_TICKET_REFS
    ):
        _reject("LOCAL_CANDIDATE_REVIEW_LINK_INVALID")

    links = {}
    manifest_by_key = {ticket["key"]: ticket for ticket in manifest}
    for transition in transitions:
        key = _text(transition.get("ticket_key"), "LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
        status = _text(transition.get("status"), "LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
        candidate_receipt = _validate_candidate_receipt(
            transition.get("candidate_receipt")
        )
        ticket = manifest_by_key.get(key)
        if ticket is None or candidate_receipt.get("ticket_key") != key:
            _reject("LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
        if status in {"repair_required", "ordinary_rejected"}:
            if transition.get("accepted_candidate_receipt") is not None:
                _reject("LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
        else:
            if transition.get("accepted_candidate_receipt") is None:
                _reject("LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
        accepted_receipt = transition.get("accepted_candidate_receipt")
        if accepted_receipt is None:
            if transition.get("review_subject") is not None:
                _validate_local_review_subject(
                    transition.get("review_subject"),
                    candidate_receipt=candidate_receipt,
                    accepted_receipt=None,
                    ticket=ticket,
                )
            continue
        accepted_mapping = _validate_accepted_candidate_receipt(accepted_receipt)
        if key not in accepted or accepted_mapping["receipt_digest"] != accepted[key][
            "receipt_digest"
        ]:
            _reject("LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
        expected_candidate = candidates[key]["candidate_receipt"]
        if candidate_receipt.get("receipt_digest") != expected_candidate["receipt_digest"]:
            _reject("LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
        finding = _digest(
            transition.get("review_finding_ledger_digest"),
            "LOCAL_CANDIDATE_REVIEW_LINK_INVALID",
        )
        if finding != accepted[key]["review_finding_ledger_digest"]:
            _reject("LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
        subject = _mapping(
            transition.get("review_subject"),
            "LOCAL_CANDIDATE_REVIEW_LINK_INVALID",
        )
        if candidate_receipt.get("parent_digest") != expected_candidate.get("parent_digest"):
            _reject("LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
        subject_parent_digest = _digest(
            subject.get("parent_digest"), "LOCAL_REVIEW_SUBJECT_INVALID"
        )
        if subject_parent_digest != candidate_receipt["parent_digest"]:
            _reject("LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
        try:
            _validate_local_review_subject(
                subject,
                candidate_receipt=candidate_receipt,
                accepted_receipt=accepted_mapping,
                ticket=ticket,
            )
        except RootCanaryVerificationError:
            _reject("LOCAL_REVIEW_SUBJECT_INVALID")
        links[key] = {
            "ticket_key": key,
            "candidate_receipt_digest": accepted[key]["candidate_receipt_digest"],
            "finding_ledger_digest": finding,
            "ticket_contract_digest": _ticket_contract_digest(ticket),
        }
    if tuple(links) != _LOCAL_TICKET_REFS:
        _reject("LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
    return links


def _validate_local_review_subject(
    value: object,
    *,
    candidate_receipt: Mapping[str, object],
    accepted_receipt: Mapping[str, object] | None,
    ticket: Mapping[str, object],
) -> dict[str, object]:
    subject = _mapping(value, "LOCAL_REVIEW_SUBJECT_INVALID")
    if set(subject) != _LOCAL_REVIEW_SUBJECT_KEYS:
        _reject("LOCAL_REVIEW_SUBJECT_INVALID")
    for field in (
        "parent_digest",
        "candidate_receipt_digest",
        "runtime_subject_digest",
        "candidate_digest",
        "candidate_audit_digest",
        "ticket_contract_digest",
        "policy_witness_digest",
        "diff_record_digest",
        "assurance_requirement_digest",
        "subject_digest",
    ):
        _digest(subject.get(field), "LOCAL_REVIEW_SUBJECT_INVALID")
    for field in (
        "base_commit_oid",
        "base_tree_oid",
        "candidate_commit_oid",
        "candidate_tree_oid",
    ):
        _object_id(subject.get(field), "LOCAL_REVIEW_SUBJECT_INVALID")
    if (
        subject.get("kind") != "review_subject.v1"
        or subject.get("diff_schema_version") != "CandidateDiffRecordV1"
        or subject.get("protocol_version") != "gwo.formal-review.v1"
        or subject.get("action_kind") not in {"formal_review", "repair_verify"}
    ):
        _reject("LOCAL_REVIEW_SUBJECT_INVALID")
    standards = tuple(
        _text(item, "LOCAL_REVIEW_SUBJECT_INVALID")
        for item in _sequence(subject.get("standards"), "LOCAL_REVIEW_SUBJECT_INVALID")
    )
    if standards != tuple(sorted(set(standards))):
        _reject("LOCAL_REVIEW_SUBJECT_INVALID")
    check_digests = tuple(
        _digest(item, "LOCAL_REVIEW_SUBJECT_INVALID")
        for item in _sequence(
            subject.get("check_evidence_digests"), "LOCAL_REVIEW_SUBJECT_INVALID"
        )
    )
    if check_digests != tuple(sorted(set(check_digests))):
        _reject("LOCAL_REVIEW_SUBJECT_INVALID")
    repair_fields = (
        "prior_review_subject_digest",
        "repair_packet_digest",
        "repair_delta_digest",
    )
    if subject.get("action_kind") == "repair_verify":
        for field in repair_fields:
            _digest(subject.get(field), "LOCAL_REVIEW_SUBJECT_INVALID")
    elif any(subject.get(field) is not None for field in repair_fields):
        _reject("LOCAL_REVIEW_SUBJECT_INVALID")

    body = {key: item for key, item in subject.items() if key != "subject_digest"}
    if subject["subject_digest"] != _canonical_digest(body):
        _reject("LOCAL_REVIEW_SUBJECT_INVALID")
    if (
        subject["ticket_contract_digest"] != _ticket_contract_digest(ticket)
        or subject["parent_digest"] != candidate_receipt["parent_digest"]
        or subject["candidate_receipt_digest"] != candidate_receipt["receipt_digest"]
        or subject["runtime_subject_digest"] != candidate_receipt["runtime_subject_digest"]
        or subject["base_commit_oid"] != candidate_receipt["base_commit_oid"]
        or subject["base_tree_oid"] != candidate_receipt["base_tree_oid"]
        or subject["candidate_commit_oid"] != candidate_receipt["candidate_commit_oid"]
        or subject["candidate_tree_oid"] != candidate_receipt["candidate_tree_oid"]
        or subject["diff_record_digest"] != candidate_receipt["diff_record_digest"]
    ):
        _reject("LOCAL_REVIEW_SUBJECT_INVALID")
    if accepted_receipt is not None:
        if (
            subject["subject_digest"] != accepted_receipt["review_subject_digest"]
            or subject["policy_witness_digest"]
            != accepted_receipt["policy_witness_digest"]
            or subject["assurance_requirement_digest"]
            != accepted_receipt["assurance_requirement_digest"]
        ):
            _reject("LOCAL_REVIEW_SUBJECT_INVALID")
    return subject


def _validate_local_candidate_diff(
    value: object,
    *,
    candidate_receipt: Mapping[str, object],
    code: str = "LOCAL_READBACK_INCOMPLETE",
) -> dict[str, object]:
    diff = _mapping(value, code)
    if set(diff) != {
        "schema_version",
        "repository_object_format",
        "base",
        "candidate",
        "entries",
        "record_digest",
    }:
        _reject(code)
    if diff.get("schema_version") != "CandidateDiffRecordV1":
        _reject(code)
    object_format = _text(diff.get("repository_object_format"), code)
    if object_format not in {"sha1", "sha256"}:
        _reject(code)
    base = _mapping(diff.get("base"), code)
    candidate = _mapping(diff.get("candidate"), code)
    if set(base) != {"commit_oid", "tree_oid"} or set(candidate) != {
        "commit_oid",
        "tree_oid",
    }:
        _reject(code)
    for side in (base, candidate):
        for field in ("commit_oid", "tree_oid"):
            oid = _object_id(side.get(field), code)
            if len(oid) != (40 if object_format == "sha1" else 64):
                _reject(code)
    if (
        base["commit_oid"] != candidate_receipt.get("base_commit_oid")
        or base["tree_oid"] != candidate_receipt.get("base_tree_oid")
        or candidate["commit_oid"] != candidate_receipt.get("candidate_commit_oid")
        or candidate["tree_oid"] != candidate_receipt.get("candidate_tree_oid")
    ):
        _reject(code)

    entries = _sequence(diff.get("entries"), code)
    for raw_entry in entries:
        entry = _mapping(raw_entry, code)
        if set(entry) != {
            "old_path",
            "new_path",
            "change_kind",
            "old_mode",
            "new_mode",
            "old_object_type",
            "new_object_type",
            "old_oid",
            "new_oid",
        }:
            _reject(code)
        change_kind = _text(entry.get("change_kind"), code)
        if change_kind not in {"add", "delete", "modify", "type-change"}:
            _reject(code)
        old_present = entry.get("old_path") is not None
        new_present = entry.get("new_path") is not None
        if (change_kind == "add" and (old_present or not new_present)) or (
            change_kind == "delete" and (not old_present or new_present)
        ) or (
            change_kind in {"modify", "type-change"}
            and (not old_present or not new_present)
        ):
            _reject(code)
        for side in ("old", "new"):
            path = entry[f"{side}_path"]
            mode = entry[f"{side}_mode"]
            object_type = entry[f"{side}_object_type"]
            oid = entry[f"{side}_oid"]
            if path is None:
                if any(value is not None for value in (mode, object_type, oid)):
                    _reject(code)
                continue
            _text(path, code)
            if type(mode) is not str or re.fullmatch(r"[0-7]{6}", mode) is None:
                _reject(code)
            if object_type not in {"blob", "gitlink"}:
                _reject(code)
            checked_oid = _object_id(oid, code)
            if len(checked_oid) != (40 if object_format == "sha1" else 64):
                _reject(code)
    record_digest = _digest(diff.get("record_digest"), code)
    if record_digest != _candidate_diff_record_digest(diff):
        _reject(code)
    if record_digest != candidate_receipt.get("diff_record_digest"):
        _reject(code)
    return diff


def _local_candidate_diffs(
    readback: Mapping[str, object],
    candidates: Mapping[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    values = _sequence(readback.get("candidate_diffs"), "LOCAL_READBACK_INCOMPLETE")
    if len(values) != len(_LOCAL_TICKET_REFS):
        _reject("LOCAL_READBACK_INCOMPLETE")
    diffs: dict[str, dict[str, object]] = {}
    for key, value in zip(_LOCAL_TICKET_REFS, values, strict=True):
        diff = _validate_local_candidate_diff(
            value,
            candidate_receipt=candidates[key]["candidate_receipt"],
        )
        if diff["record_digest"] in {
            item["record_digest"] for item in diffs.values()
        }:
            _reject("LOCAL_READBACK_INCOMPLETE")
        diffs[key] = diff
    return diffs


_LOCAL_DELIVERY_PROOF_KEYS = frozenset(
    {
        "delivery_stable_action_id",
        "delivery_request_digest",
        "batch_id",
        "batch_sha",
        "member_ticket_keys",
        "local_check_receipt_digest",
        "publication_receipt_digest",
        "pull_request_number",
        "pull_request_head_sha",
        "hosted_result_receipt_digest",
        "integration_lease_digest",
        "target_branch",
        "target_head_sha",
        "target_readback_digest",
        "target_contains_batch_sha",
        "pull_request_merge_target_sha",
        "merge_method",
        "proof_digest",
    }
)


def _local_delivery_proofs(
    readback: Mapping[str, object],
    *,
    batches: Sequence[VerifiedBatch],
    proofs_by_id: Mapping[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    values = _sequence(readback.get("delivery_proofs"), "LOCAL_READBACK_INCOMPLETE")
    if len(values) != len(_LOCAL_TICKET_REFS):
        _reject("LOCAL_READBACK_INCOMPLETE")
    batch_by_id = {batch.batch_id: batch for batch in batches}
    result: dict[str, dict[str, object]] = {}
    for raw in values:
        proof = _mapping(raw, "LOCAL_READBACK_INCOMPLETE")
        if set(proof) != _LOCAL_DELIVERY_PROOF_KEYS:
            _reject("LOCAL_READBACK_INCOMPLETE")
        for field in (
            "delivery_request_digest",
            "local_check_receipt_digest",
            "publication_receipt_digest",
            "hosted_result_receipt_digest",
            "integration_lease_digest",
            "target_readback_digest",
            "proof_digest",
        ):
            _digest(proof.get(field), "LOCAL_READBACK_INCOMPLETE")
        for field in (
            "batch_sha",
            "pull_request_head_sha",
            "target_head_sha",
            "pull_request_merge_target_sha",
        ):
            _object_id(proof.get(field), "LOCAL_READBACK_INCOMPLETE")
        _text(proof.get("delivery_stable_action_id"), "LOCAL_READBACK_INCOMPLETE")
        _text(proof.get("batch_id"), "LOCAL_READBACK_INCOMPLETE")
        _text(proof.get("target_branch"), "LOCAL_READBACK_INCOMPLETE")
        members = tuple(
            _text(item, "LOCAL_READBACK_INCOMPLETE")
            for item in _sequence(proof.get("member_ticket_keys"), "LOCAL_READBACK_INCOMPLETE")
        )
        if members not in {
            _LOCAL_STANDARD_TICKET_REFS,
            (_LOCAL_STRICT_TICKET_REF,),
        }:
            _reject("LOCAL_READBACK_INCOMPLETE")
        if (
            type(proof.get("pull_request_number")) is not int
            or proof["pull_request_number"] <= 0
            or proof.get("target_branch") != "main"
            or proof.get("pull_request_head_sha") != proof.get("batch_sha")
            or proof.get("target_contains_batch_sha") is not True
            or proof.get("pull_request_merge_target_sha") != proof.get("target_head_sha")
            or proof.get("merge_method") != "merge"
        ):
            _reject("LOCAL_READBACK_INCOMPLETE")
        body = {key: item for key, item in proof.items() if key != "proof_digest"}
        if proof["proof_digest"] != _canonical_digest(
            {"kind": "batch-delivery-proof.v1", **body}
        ):
            _reject("LOCAL_READBACK_INCOMPLETE")
        batch = batch_by_id.get(proof["batch_id"])
        local_proof = proofs_by_id.get(proof["batch_id"])
        if batch is None or local_proof is None:
            _reject("LOCAL_BATCH_CROSSLINK_INVALID")
        if (
            proof["batch_sha"] != batch.batch_sha
            or members != tuple(local_proof["member_ticket_keys"])
            or proof["local_check_receipt_digest"]
            != local_proof["local_suite"]["receipt"]["receipt_digest"]
            or proof["integration_lease_digest"]
            != local_proof["integration_lease"]["digest"]
            or local_proof["target_readback"]["batch_sha"] != proof["batch_sha"]
            or local_proof["target_readback"]["target_ref_sha"]
            != proof["target_head_sha"]
            or local_proof["target_readback"]["target_after"]["commit_sha"]
            != proof["target_head_sha"]
        ):
            _reject("LOCAL_BATCH_CROSSLINK_INVALID")
        if proof["proof_digest"] in result:
            _reject("LOCAL_READBACK_INCOMPLETE")
        result[proof["proof_digest"]] = proof
    member_counts = sorted(
        sum(
            1
            for proof in result.values()
            if tuple(proof["member_ticket_keys"]) == members
        )
        for members in (
            _LOCAL_STANDARD_TICKET_REFS,
            (_LOCAL_STRICT_TICKET_REF,),
        )
    )
    if len(result) != len(_LOCAL_TICKET_REFS) or member_counts != [1, 3]:
        _reject("LOCAL_READBACK_INCOMPLETE")
    return result


def _local_target_readback(
    target: Mapping[str, object],
    *,
    batch_sha: str,
    batch_tree_oid: str,
    batch_ref_sha: str,
    expected_before: tuple[str, str] | None,
    expected_target_sha: str | None,
) -> tuple[str, tuple[str, str]]:
    if set(target) != {
        "repository",
        "target_branch",
        "target_before",
        "target_after",
        "batch_sha",
        "batch_ref_sha",
        "target_ref_sha",
        "cas",
        "ancestry",
        "digest",
    }:
        _reject("LOCAL_TARGET_READBACK_INVALID")
    if target.get("repository") != ROOT_REPOSITORY or target.get("target_branch") != "main":
        _reject("LOCAL_TARGET_READBACK_INVALID")
    if target.get("batch_sha") != batch_sha or target.get("batch_ref_sha") != batch_ref_sha:
        _reject("LOCAL_BATCH_SHA_MISMATCH")
    before = _mapping(target.get("target_before"), "LOCAL_TARGET_READBACK_INVALID")
    after = _mapping(target.get("target_after"), "LOCAL_TARGET_READBACK_INVALID")
    if set(before) != {"commit_sha", "tree_sha"} or set(after) != {"commit_sha", "tree_sha"}:
        _reject("LOCAL_TARGET_READBACK_INVALID")
    before_pair = (
        _object_id(before.get("commit_sha"), "LOCAL_TARGET_READBACK_INVALID"),
        _object_id(before.get("tree_sha"), "LOCAL_TARGET_READBACK_INVALID"),
    )
    after_pair = (
        _object_id(after.get("commit_sha"), "LOCAL_TARGET_READBACK_INVALID"),
        _object_id(after.get("tree_sha"), "LOCAL_TARGET_READBACK_INVALID"),
    )
    if expected_before is not None and before_pair != expected_before:
        _reject("LOCAL_TARGET_READBACK_INVALID")
    if after_pair != (batch_sha, batch_tree_oid):
        _reject("LOCAL_TARGET_READBACK_INVALID")
    target_ref_sha = _object_id(
        target.get("target_ref_sha"), "LOCAL_TARGET_READBACK_INVALID"
    )
    if target_ref_sha != after_pair[0]:
        _reject("LOCAL_TARGET_READBACK_INVALID")
    if expected_target_sha is not None and target_ref_sha != expected_target_sha:
        _reject("LOCAL_TARGET_READBACK_INVALID")

    cas = _mapping(target.get("cas"), "LOCAL_TARGET_READBACK_INVALID")
    if set(cas) != {"ref", "expected_sha", "updated_sha", "readback_sha", "updated"}:
        _reject("LOCAL_TARGET_READBACK_INVALID")
    if (
        cas.get("ref") != "refs/heads/main"
        or cas.get("expected_sha") != before_pair[0]
        or cas.get("updated_sha") != after_pair[0]
        or cas.get("readback_sha") != target_ref_sha
        or cas.get("updated") is not True
    ):
        _reject("LOCAL_TARGET_READBACK_INVALID")

    ancestry = _mapping(
        target.get("ancestry"), "LOCAL_TARGET_ANCESTRY_INVALID"
    )
    if set(ancestry) != {
        "ancestor_sha",
        "descendant_sha",
        "is_ancestor",
        "readback_digest",
    }:
        _reject("LOCAL_TARGET_ANCESTRY_INVALID")
    if (
        ancestry.get("ancestor_sha") != batch_sha
        or ancestry.get("descendant_sha") != target_ref_sha
        or ancestry.get("is_ancestor") is not True
    ):
        _reject("LOCAL_TARGET_ANCESTRY_INVALID")
    ancestry_body = {key: value for key, value in ancestry.items() if key != "readback_digest"}
    if ancestry["readback_digest"] != _canonical_digest(
        {"kind": "local-target-ancestry.v1", **ancestry_body}
    ):
        _reject("LOCAL_TARGET_ANCESTRY_INVALID")

    target_body = {key: value for key, value in target.items() if key != "digest"}
    if target["digest"] != _canonical_digest(
        {"kind": "local-target-readback.v1", **target_body}
    ):
        _reject("LOCAL_TARGET_READBACK_INVALID")
    return target_ref_sha, before_pair


def _local_batch_proofs(
    local_evidence: Mapping[str, object],
    *,
    repository: str,
    campaign_key: str,
    plan_revision_digest: str,
    candidates: Mapping[str, dict[str, object]],
    accepted: Mapping[str, dict[str, object]],
    reviews: Mapping[str, dict[str, object]],
    expected_target_sha: str | None,
) -> tuple[tuple[VerifiedBatch, VerifiedBatch], dict[str, dict[str, object]]]:
    raw_batches = _sequence(local_evidence.get("batches"), "LOCAL_BATCH_PROOF_INCOMPLETE")
    if len(raw_batches) != 2:
        _reject("LOCAL_BATCH_PROOF_INCOMPLETE")
    proof_keys = {
        "schema_version",
        "repository",
        "campaign_key",
        "plan_revision_digest",
        "batch_id",
        "batch_sha",
        "batch_tree_oid",
        "batch_ref",
        "member_ticket_keys",
        "candidate_receipt_digests",
        "accepted_candidate_receipt_digests",
        "candidate_diff_record_digests",
        "finding_ledger_digests",
        "local_suite",
        "integration_lease",
        "target_readback",
        "receipt_digest",
    }
    verified: dict[str, VerifiedBatch] = {}
    proof_by_id: dict[str, dict[str, object]] = {}
    lease_identity: tuple[str, str] | None = None
    target_before_pairs: dict[str, tuple[str, str]] = {}
    target_after_pairs: dict[str, tuple[str, str]] = {}
    for raw in raw_batches:
        proof = _mapping(raw, "LOCAL_BATCH_PROOF_INCOMPLETE")
        _reject_local_forbidden_fields(proof)
        if set(proof) != proof_keys or proof.get("schema_version") != "local_batch_proof.v1":
            _reject("LOCAL_BATCH_PROOF_INCOMPLETE")
        if (
            proof.get("repository") != repository
            or proof.get("campaign_key") != campaign_key
            or proof.get("plan_revision_digest") != plan_revision_digest
        ):
            _reject("LOCAL_BATCH_PROOF_INCOMPLETE")
        batch_id = _text(proof.get("batch_id"), "LOCAL_BATCH_PROOF_INCOMPLETE")
        if batch_id in proof_by_id:
            _reject("LOCAL_BATCH_PROOF_INCOMPLETE")
        batch_sha = _object_id(proof.get("batch_sha"), "LOCAL_BATCH_SHA_MISMATCH")
        batch_tree_oid = _object_id(
            proof.get("batch_tree_oid"), "LOCAL_BATCH_PROOF_INCOMPLETE"
        )
        batch_ref = _mapping(proof.get("batch_ref"), "LOCAL_BATCH_PROOF_INCOMPLETE")
        if set(batch_ref) != {"ref", "sha"}:
            _reject("LOCAL_BATCH_PROOF_INCOMPLETE")
        batch_ref_value = _text(batch_ref.get("ref"), "LOCAL_BATCH_PROOF_INCOMPLETE")
        batch_ref_sha = _object_id(batch_ref.get("sha"), "LOCAL_BATCH_SHA_MISMATCH")
        if (
            batch_ref_sha != batch_sha
            or batch_ref_value != f"refs/gwo-v8/integration-batches/{batch_id}"
        ):
            _reject("LOCAL_BATCH_SHA_MISMATCH")
        members = tuple(
            _text(value, "LOCAL_BATCH_PROOF_INCOMPLETE")
            for value in _sequence(
                proof.get("member_ticket_keys"), "LOCAL_BATCH_PROOF_INCOMPLETE"
            )
        )
        if members == _LOCAL_STANDARD_TICKET_REFS:
            kind = "multi"
        elif members == (_LOCAL_STRICT_TICKET_REF,):
            kind = "singleton"
        else:
            _reject("BATCH_MEMBERS_INVALID")
        if kind in verified:
            _reject("LOCAL_BATCH_PROOF_INCOMPLETE")

        candidate_digests = tuple(
            _digest(value, "LOCAL_BATCH_PROOF_INCOMPLETE")
            for value in _sequence(
                proof.get("candidate_receipt_digests"),
                "LOCAL_BATCH_PROOF_INCOMPLETE",
            )
        )
        accepted_digests = tuple(
            _digest(value, "LOCAL_BATCH_PROOF_INCOMPLETE")
            for value in _sequence(
                proof.get("accepted_candidate_receipt_digests"),
                "LOCAL_BATCH_PROOF_INCOMPLETE",
            )
        )
        diff_digests = tuple(
            _digest(value, "LOCAL_BATCH_PROOF_INCOMPLETE")
            for value in _sequence(
                proof.get("candidate_diff_record_digests"),
                "LOCAL_BATCH_PROOF_INCOMPLETE",
            )
        )
        finding_digests = tuple(
            _digest(value, "LOCAL_BATCH_PROOF_INCOMPLETE")
            for value in _sequence(
                proof.get("finding_ledger_digests"),
                "LOCAL_BATCH_PROOF_INCOMPLETE",
            )
        )
        expected_candidates = tuple(
            candidates[key]["candidate_receipt_digest"] for key in members
        )
        expected_accepted = tuple(accepted[key]["receipt_digest"] for key in members)
        expected_diffs = tuple(
            candidates[key]["candidate_receipt"]["diff_record_digest"] for key in members
        )
        expected_findings = tuple(reviews[key]["finding_ledger_digest"] for key in members)
        if (
            candidate_digests != expected_candidates
            or accepted_digests != expected_accepted
            or diff_digests != expected_diffs
            or finding_digests != expected_findings
        ):
            _reject("LOCAL_BATCH_CROSSLINK_INVALID")

        local_suite = _mapping(proof.get("local_suite"), "LOCAL_BATCH_PROOF_INCOMPLETE")
        if set(local_suite) != {
            "suite_id",
            "batch_sha",
            "status",
            "receipt_digest",
            "definition",
            "receipt",
        }:
            _reject("LOCAL_BATCH_PROOF_INCOMPLETE")
        suite_id = _text(local_suite.get("suite_id"), "LOCAL_BATCH_PROOF_INCOMPLETE")
        if local_suite.get("batch_sha") != batch_sha or local_suite.get("status") != "passed":
            _reject("LOCAL_BATCH_SHA_MISMATCH")
        receipt_digest = _digest(
            local_suite.get("receipt_digest"), "LOCAL_BATCH_PROOF_INCOMPLETE"
        )
        definition = _mapping(
            local_suite.get("definition"), "LOCAL_BATCH_PROOF_INCOMPLETE"
        )
        if set(definition) != {"suite_id", "definition_digest", "command"}:
            _reject("LOCAL_BATCH_PROOF_INCOMPLETE")
        definition_digest = _digest(
            definition.get("definition_digest"), "LOCAL_BATCH_PROOF_INCOMPLETE"
        )
        command = _sequence(definition.get("command"), "LOCAL_BATCH_PROOF_INCOMPLETE")
        if (
            definition.get("suite_id") != suite_id
            or not command
            or any(type(item) is not str or not item for item in command)
        ):
            _reject("LOCAL_BATCH_PROOF_INCOMPLETE")
        receipt = _mapping(
            local_suite.get("receipt"), "LOCAL_BATCH_PROOF_INCOMPLETE"
        )
        if set(receipt) != {
            "batch_sha",
            "suite_id",
            "definition_digest",
            "outcome",
            "observation_digest",
            "source_ref",
            "receipt_digest",
        }:
            _reject("LOCAL_BATCH_PROOF_INCOMPLETE")
        for field in ("observation_digest", "receipt_digest"):
            _digest(receipt.get(field), "LOCAL_BATCH_PROOF_INCOMPLETE")
        if (
            receipt.get("batch_sha") != batch_sha
            or receipt.get("suite_id") != suite_id
            or receipt.get("definition_digest") != definition_digest
            or receipt.get("outcome") != "passed"
            or receipt.get("source_ref")
            != f"refs/gwo-v8/integration-batches/{batch_sha}"
            or receipt.get("receipt_digest")
            != _canonical_digest(
                {
                    "kind": "local-check-receipt.v1",
                    **{
                        key: receipt[key]
                        for key in (
                            "batch_sha",
                            "suite_id",
                            "definition_digest",
                            "outcome",
                            "observation_digest",
                            "source_ref",
                        )
                    },
                }
            )
            or receipt.get("receipt_digest") != receipt_digest
        ):
            _reject("LOCAL_BATCH_PROOF_INCOMPLETE")

        lease = _mapping(
            proof.get("integration_lease"), "LOCAL_INTEGRATION_LEASE_INVALID"
        )
        if set(lease) != {
            "serialized",
            "stable_action_id",
            "writer",
            "activation",
            "digest",
            "acquisition",
            "release",
        }:
            _reject("LOCAL_INTEGRATION_LEASE_INVALID")
        serialized = _mapping(
            lease.get("serialized"), "LOCAL_INTEGRATION_LEASE_INVALID"
        )
        if set(serialized) != {
            "repository",
            "holder",
            "writer_generation",
            "activation_id",
            "lease_digest",
        }:
            _reject("LOCAL_INTEGRATION_LEASE_INVALID")
        if serialized.get("repository") != repository:
            _reject("LOCAL_INTEGRATION_LEASE_INVALID")
        for field in ("repository", "holder", "writer_generation", "activation_id"):
            _text(serialized.get(field), "LOCAL_INTEGRATION_LEASE_INVALID")
        lease_digest = _digest(lease.get("digest"), "LOCAL_INTEGRATION_LEASE_INVALID")
        if lease_digest != serialized.get("lease_digest"):
            _reject("LOCAL_INTEGRATION_LEASE_INVALID")
        lease_body = {
            key: serialized[key]
            for key in ("repository", "holder", "writer_generation", "activation_id")
        }
        if lease_digest != _canonical_digest({"kind": "integration-lease.v1", **lease_body}):
            _reject("LOCAL_INTEGRATION_LEASE_INVALID")
        if (
            lease.get("stable_action_id") != serialized.get("holder")
            or lease.get("writer") != serialized.get("writer_generation")
            or lease.get("activation") != serialized.get("activation_id")
        ):
            _reject("LOCAL_INTEGRATION_LEASE_INVALID")
        acquisition = _mapping(
            lease.get("acquisition"), "LOCAL_INTEGRATION_LEASE_INVALID"
        )
        release = _mapping(lease.get("release"), "LOCAL_INTEGRATION_LEASE_INVALID")
        if (
            set(acquisition)
            != {"status", "lease_digest", "inactive_after_release"}
            or acquisition.get("status") != "acquired"
            or acquisition.get("lease_digest") != lease_digest
            or acquisition.get("inactive_after_release") is not False
            or set(release)
            != {"status", "lease_digest", "inactive_after_release"}
            or release.get("status") != "released"
            or release.get("lease_digest") != lease_digest
            or release.get("inactive_after_release") is not True
        ):
            _reject("LOCAL_INTEGRATION_LEASE_INVALID")
        current_lease_identity = (str(lease["activation"]), str(lease["writer"]))
        if lease_identity is None:
            lease_identity = current_lease_identity
        elif lease_identity != current_lease_identity:
            _reject("LOCAL_INTEGRATION_LEASE_INVALID")

        target = _mapping(
            proof.get("target_readback"), "LOCAL_TARGET_READBACK_INVALID"
        )
        previous_target = None
        if kind == "singleton" and "multi" in target_before_pairs:
            previous_target = target_after_pairs["multi"]
        elif kind == "multi":
            first_candidate = candidates[members[0]]["candidate_receipt"]
            previous_target = (
                first_candidate["base_commit_oid"],
                first_candidate["base_tree_oid"],
            )
        target_sha, before_pair = _local_target_readback(
            target,
            batch_sha=batch_sha,
            batch_tree_oid=batch_tree_oid,
            batch_ref_sha=batch_ref_sha,
            expected_before=previous_target,
            expected_target_sha=expected_target_sha if kind == "singleton" else None,
        )
        target_before_pairs[kind] = before_pair
        target_after_pairs[kind] = (target_sha, batch_tree_oid)
        verified[kind] = VerifiedBatch(
            batch_id=batch_id,
            member_count=len(members),
            pull_request_number=None,
            hosted_run_id=None,
            batch_sha=batch_sha,
            target_sha=target_sha,
            receipt_digest="",
        )
        proof_by_id[batch_id] = proof

    if set(verified) != {"multi", "singleton"}:
        _reject("LOCAL_BATCH_PROOF_INCOMPLETE")
    if (
        verified["multi"].batch_id == verified["singleton"].batch_id
        or verified["multi"].batch_sha == verified["singleton"].batch_sha
        or verified["multi"].target_sha == verified["singleton"].target_sha
    ):
        _reject("BATCH_BOUNDARY_COLLAPSED")

    for kind, batch in tuple(verified.items()):
        proof = proof_by_id[batch.batch_id]
        receipt_digest = _digest(
            proof.get("receipt_digest"), "LOCAL_BATCH_PROOF_INCOMPLETE"
        )
        proof_body = {key: value for key, value in proof.items() if key != "receipt_digest"}
        if receipt_digest != _canonical_digest({"kind": "local_batch_proof.v1", **proof_body}):
            _reject("LOCAL_BATCH_PROOF_INCOMPLETE")
        verified[kind] = replace(batch, receipt_digest=receipt_digest)
    return (verified["multi"], verified["singleton"]), proof_by_id


def _local_result_crosslinks(
    bundle: Mapping[str, object],
    facts: Mapping[str, object],
    readback: Mapping[str, object],
    candidates: Mapping[str, dict[str, object]],
    accepted: Mapping[str, dict[str, object]],
    batches: tuple[VerifiedBatch, VerifiedBatch],
    proofs_by_id: Mapping[str, dict[str, object]],
    delivery_proofs: Mapping[str, dict[str, object]],
) -> list[dict[str, object]]:
    batch_by_id = {batch.batch_id: batch for batch in batches}
    result_values = readback.get("result_integrities")
    results = _sequence(result_values, "LOCAL_RESULT_CROSSLINK_INVALID")
    if len(results) != 4:
        _reject("LOCAL_RESULT_CROSSLINK_INVALID")
    seen: set[str] = set()
    delivery_receipts_by_ticket: dict[str, str] = {}
    evidence_by_ticket: dict[str, tuple[str, ...]] = {}
    projected: list[dict[str, object]] = []
    for raw in results:
        result = _mapping(raw, "LOCAL_RESULT_CROSSLINK_INVALID")
        if set(result) != {
            "accepted_candidate_receipt_digest",
            "candidate_commit_oid",
            "candidate_tree_oid",
            "candidate_diff_record_digest",
            "batch_delivery_receipt_digest",
            "batch_delivery_stable_action_id",
            "batch_delivery_request_digest",
            "batch_delivery_batch_id",
            "batch_delivery_batch_sha",
            "batch_delivery_proof_digest",
            "delivery_stable_action_id",
            "delivery_request_digest",
            "batch_id",
            "batch_sha",
            "delivery_member_ticket_keys",
            "local_check_receipt_digest",
            "publication_receipt_digest",
            "pull_request_number",
            "pull_request_head_sha",
            "hosted_result_receipt_digest",
            "integration_lease_digest",
            "target_branch",
            "target_head_sha",
            "target_readback_digest",
            "target_contains_batch_sha",
            "pull_request_merge_target_sha",
            "merge_method",
            "result_digest",
            "evidence_digests",
        }:
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        for field in (
            "accepted_candidate_receipt_digest",
            "candidate_diff_record_digest",
            "batch_delivery_receipt_digest",
            "batch_delivery_request_digest",
            "batch_delivery_proof_digest",
            "delivery_request_digest",
            "local_check_receipt_digest",
            "publication_receipt_digest",
            "hosted_result_receipt_digest",
            "integration_lease_digest",
            "target_readback_digest",
            "result_digest",
        ):
            _digest(result.get(field), "LOCAL_RESULT_CROSSLINK_INVALID")
        for field in (
            "candidate_commit_oid",
            "candidate_tree_oid",
            "batch_delivery_batch_sha",
            "batch_sha",
            "pull_request_head_sha",
            "target_head_sha",
            "pull_request_merge_target_sha",
        ):
            _object_id(result.get(field), "LOCAL_RESULT_CROSSLINK_INVALID")
        if type(result.get("pull_request_number")) is not int or result["pull_request_number"] <= 0:
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        _text(result.get("batch_delivery_stable_action_id"), "LOCAL_RESULT_CROSSLINK_INVALID")
        _text(result.get("delivery_stable_action_id"), "LOCAL_RESULT_CROSSLINK_INVALID")
        _text(result.get("target_branch"), "LOCAL_RESULT_CROSSLINK_INVALID")
        if result.get("merge_method") != "merge" or type(result.get("target_contains_batch_sha")) is not bool:
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        evidence = tuple(
            _digest(value, "LOCAL_RESULT_CROSSLINK_INVALID")
            for value in _sequence(result.get("evidence_digests"), "LOCAL_RESULT_CROSSLINK_INVALID")
        )
        if not evidence or evidence != tuple(sorted(evidence)):
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        accepted_digest = _digest(
            result.get("accepted_candidate_receipt_digest"),
            "LOCAL_RESULT_CROSSLINK_INVALID",
        )
        if accepted_digest in seen:
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        seen.add(accepted_digest)
        key = next(
            (
                ticket_key
                for ticket_key, value in accepted.items()
                if value["receipt_digest"] == accepted_digest
            ),
            None,
        )
        if key is None:
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        batch_id = _text(result.get("batch_id"), "LOCAL_RESULT_CROSSLINK_INVALID")
        batch = batch_by_id.get(batch_id)
        if batch is None or result.get("batch_sha") != batch.batch_sha:
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        accepted_receipt = accepted[key]
        if (
            result.get("candidate_commit_oid") != accepted_receipt.get("candidate_sha")
            or result.get("candidate_tree_oid")
            != accepted_receipt.get("candidate_tree_oid")
            or result.get("candidate_diff_record_digest")
            != accepted_receipt.get("diff_record_digest")
            or result.get("batch_delivery_batch_id") != batch_id
            or result.get("batch_delivery_batch_sha") != batch.batch_sha
            or result.get("batch_id") != batch_id
            or result.get("target_branch") != "main"
            or tuple(evidence) != tuple(sorted(accepted_receipt.get("evidence_digests", ())))
            or result.get("batch_delivery_request_digest")
            != result.get("delivery_request_digest")
            or result.get("batch_delivery_stable_action_id")
            != result.get("delivery_stable_action_id")
            or result.get("accepted_candidate_receipt_digest")
            != accepted_receipt.get("receipt_digest")
        ):
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        delivery_proof = delivery_proofs.get(result["batch_delivery_proof_digest"])
        if delivery_proof is None:
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        expected_delivery_fields = {
            "batch_delivery_stable_action_id": "delivery_stable_action_id",
            "batch_delivery_request_digest": "delivery_request_digest",
            "batch_delivery_batch_id": "batch_id",
            "batch_delivery_batch_sha": "batch_sha",
            "delivery_stable_action_id": "delivery_stable_action_id",
            "delivery_request_digest": "delivery_request_digest",
            "batch_id": "batch_id",
            "batch_sha": "batch_sha",
            "delivery_member_ticket_keys": "member_ticket_keys",
            "local_check_receipt_digest": "local_check_receipt_digest",
            "publication_receipt_digest": "publication_receipt_digest",
            "pull_request_number": "pull_request_number",
            "pull_request_head_sha": "pull_request_head_sha",
            "hosted_result_receipt_digest": "hosted_result_receipt_digest",
            "integration_lease_digest": "integration_lease_digest",
            "target_branch": "target_branch",
            "target_head_sha": "target_head_sha",
            "target_readback_digest": "target_readback_digest",
            "target_contains_batch_sha": "target_contains_batch_sha",
            "pull_request_merge_target_sha": "pull_request_merge_target_sha",
            "merge_method": "merge_method",
        }
        for result_field, proof_field in expected_delivery_fields.items():
            result_value = result[result_field]
            proof_value = delivery_proof[proof_field]
            if result_field == "delivery_member_ticket_keys":
                if tuple(result_value) != tuple(proof_value):
                    _reject("LOCAL_RESULT_CROSSLINK_INVALID")
            elif result_value != proof_value:
                _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        if result["batch_delivery_proof_digest"] != delivery_proof["proof_digest"]:
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        if result["batch_delivery_receipt_digest"] != _canonical_digest(
            {
                "kind": "root-batch-observation.v1",
                "action": result["batch_delivery_stable_action_id"],
                "proof": delivery_proof["proof_digest"],
            }
        ):
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        delivery_body = {
            key: result[key]
            for key in (
                "delivery_stable_action_id",
                "delivery_request_digest",
                "batch_id",
                "batch_sha",
                "local_check_receipt_digest",
                "publication_receipt_digest",
                "pull_request_number",
                "pull_request_head_sha",
                "hosted_result_receipt_digest",
                "integration_lease_digest",
                "target_branch",
                "target_head_sha",
                "target_readback_digest",
                "target_contains_batch_sha",
                "pull_request_merge_target_sha",
                "merge_method",
            )
        }
        delivery_body["member_ticket_keys"] = result["delivery_member_ticket_keys"]
        if result.get("batch_delivery_proof_digest") != _canonical_digest(
            {"kind": "batch-delivery-proof.v1", **delivery_body}
        ):
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        expected_result = {
            key: result[key]
            for key in result
            if key != "result_digest"
        }
        if result.get("result_digest") != _canonical_digest(
            {"kind": "gwo.result.v1", **expected_result}
        ):
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        members = tuple(
            _text(value, "LOCAL_RESULT_CROSSLINK_INVALID")
            for value in _sequence(
                result.get("delivery_member_ticket_keys"),
                "LOCAL_RESULT_CROSSLINK_INVALID",
            )
        )
        expected_members = (
            _LOCAL_STANDARD_TICKET_REFS
            if batch.member_count == 3
            else (_LOCAL_STRICT_TICKET_REF,)
        )
        if members != expected_members or result.get("target_contains_batch_sha") is not True:
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        proof = proofs_by_id[batch_id]
        proof_members = tuple(proof["member_ticket_keys"])
        if members != proof_members:
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        delivery_receipts_by_ticket[key] = result["batch_delivery_receipt_digest"]
        evidence_by_ticket[key] = evidence
        projected.append(
            {
                "ticket_key": key,
                "accepted_candidate_receipt_digest": accepted_digest,
                "batch_id": batch_id,
                "batch_sha": batch.batch_sha,
                "delivery_member_ticket_keys": list(members),
                "target_contains_batch_sha": True,
                "result_digest": result["result_digest"],
            }
        )
    if seen != {value["receipt_digest"] for value in accepted.values()}:
        _reject("LOCAL_RESULT_CROSSLINK_INVALID")

    raw_work_runs = facts.get("work_runs")
    work_runs = _sequence(raw_work_runs, "LOCAL_RESULT_CROSSLINK_INVALID")
    if len(work_runs) != 4:
        _reject("LOCAL_RESULT_CROSSLINK_INVALID")
    work_by_key: dict[str, Mapping[str, object]] = {}
    for raw in work_runs:
        run = _mapping(raw, "LOCAL_RESULT_CROSSLINK_INVALID")
        key = _text(run.get("ticket_key"), "LOCAL_RESULT_CROSSLINK_INVALID")
        if key not in accepted or key in work_by_key:
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        if run.get("phase") != "completed" or run.get("claim_state") != "released" or run.get("slot_held") is not False:
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        if run.get("candidate_receipt_digest") != candidates[key]["candidate_receipt_digest"]:
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        if run.get("accepted_candidate_receipt_digest") != accepted[key]["receipt_digest"]:
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        if run.get("candidate_diff_record_digest") != candidates[key]["candidate_receipt"][
            "diff_record_digest"
        ]:
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        if run.get("batch_id") not in batch_by_id or run.get("batch_sha") != batch_by_id[run["batch_id"]].batch_sha:
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        if run.get("result_digest") not in {
            item["result_digest"] for item in projected if item["ticket_key"] == key
        }:
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        if (
            run.get("delivery_receipt_digest") != delivery_receipts_by_ticket[key]
            or tuple(run.get("evidence_digests", ())) != evidence_by_ticket[key]
        ):
            _reject("LOCAL_RESULT_CROSSLINK_INVALID")
        work_by_key[key] = run
    if set(work_by_key) != set(_LOCAL_TICKET_REFS):
        _reject("LOCAL_RESULT_CROSSLINK_INVALID")
    return sorted(projected, key=lambda item: _LOCAL_TICKET_REFS.index(item["ticket_key"]))


def _local_authoritative_evidence(
    local_evidence: Mapping[str, object],
    candidates: Mapping[str, dict[str, object]],
    accepted: Mapping[str, dict[str, object]],
    reviews: Mapping[str, dict[str, object]],
    results: Sequence[dict[str, object]],
    manifest: Sequence[dict[str, object]],
    facts: Mapping[str, object],
) -> dict[str, object]:
    candidate_links = [
        {
            "ticket_key": key,
            "candidate_receipt_digest": candidates[key]["candidate_receipt_digest"],
            "accepted_candidate_receipt_digest": accepted[key]["receipt_digest"],
            "candidate_diff_record_digest": candidates[key]["candidate_receipt"][
                "diff_record_digest"
            ],
        }
        for key in _LOCAL_TICKET_REFS
    ]
    recovery = facts.get("concurrency")
    candidate_gate = facts.get("candidate_gate")
    replay = facts.get("replay", {})
    projected_recovery = {
        "concurrency": copy.deepcopy(recovery),
        "candidate_gate": copy.deepcopy(
            {
                key: candidate_gate.get(key)
                for key in (
                    "reviewed",
                    "repair_required",
                    "rejected",
                    "strict_specialist_review",
                    "accepted",
                )
                if isinstance(candidate_gate, dict) and key in candidate_gate
            }
        ),
        "replay": copy.deepcopy(
            {
                key: replay.get(key)
                for key in (
                    "readback_unchanged",
                    "idempotent_delivery",
                    "idempotent_effects",
                )
                if isinstance(replay, dict) and key in replay
            }
        ),
    }
    return {
        "acceptance_mode": _LOCAL_ROOT_MODE,
        "local_evidence": copy.deepcopy(dict(local_evidence)),
        "ticket_contract_digests": [
            {"key": ticket["key"], "digest": _ticket_contract_digest(ticket)}
            for ticket in manifest
        ],
        "candidate_links": candidate_links,
        "review_links": [copy.deepcopy(reviews[key]) for key in _LOCAL_TICKET_REFS],
        "result_links": copy.deepcopy(list(results)),
        "recovery": projected_recovery,
    }


def _validate_local_transcript(
    bundle: Mapping[str, object],
) -> None:
    if "transcript" not in bundle:
        _reject("LOCAL_EVIDENCE_INCOMPLETE")
    raw_values = _sequence(bundle.get("transcript"), "LOCAL_TRANSCRIPT_INVALID")
    if tuple(
        _text(
            _mapping(value, "LOCAL_TRANSCRIPT_INVALID").get("operation"),
            "LOCAL_TRANSCRIPT_INVALID",
        )
        for value in raw_values
    ) != _LOCAL_TRANSCRIPT_SEQUENCE:
        _reject("LOCAL_TRANSCRIPT_INVALID")

    campaign = _mapping(bundle.get("campaign"), "LOCAL_TRANSCRIPT_INVALID")
    campaign_key = _text(campaign.get("campaign_key"), "LOCAL_TRANSCRIPT_INVALID")
    inspect_statuses: list[str] = []
    advance_wakes: list[str] = []
    advance_statuses: list[str] = []
    for raw in raw_values:
        entry = _mapping(raw, "LOCAL_TRANSCRIPT_INVALID")
        operation = entry["operation"]
        if operation == "start":
            if set(entry) != {"operation", "campaign_key"}:
                _reject("LOCAL_TRANSCRIPT_INVALID")
            if entry.get("campaign_key") != campaign_key:
                _reject("LOCAL_TRANSCRIPT_INVALID")
        elif operation == "inspect":
            if set(entry) != {"operation", "readback"}:
                _reject("LOCAL_TRANSCRIPT_INVALID")
            readback = _mapping(entry.get("readback"), "LOCAL_TRANSCRIPT_INVALID")
            if set(readback) != {
                "status",
                "reason",
                "plan_revision_digest",
                "worker_slots",
                "work_runs",
                "outstanding_effect_ids",
            }:
                _reject("LOCAL_TRANSCRIPT_INVALID")
            status = _text(readback.get("status"), "LOCAL_TRANSCRIPT_INVALID")
            if status not in {"Blocked", "Running", "Complete"}:
                _reject("LOCAL_TRANSCRIPT_INVALID")
            inspect_statuses.append(status)
            _digest(readback.get("plan_revision_digest"), "LOCAL_TRANSCRIPT_INVALID")
            worker_slots = _mapping(
                readback.get("worker_slots"), "LOCAL_TRANSCRIPT_INVALID"
            )
            if set(worker_slots) != {"available", "held", "limit"}:
                _reject("LOCAL_TRANSCRIPT_INVALID")
            if any(
                type(worker_slots.get(field)) is not int
                for field in ("available", "held", "limit")
            ):
                _reject("LOCAL_TRANSCRIPT_INVALID")
            work_runs = _sequence(
                readback.get("work_runs"), "LOCAL_TRANSCRIPT_INVALID"
            )
            for raw_run in work_runs:
                run = _mapping(raw_run, "LOCAL_TRANSCRIPT_INVALID")
                if set(run) != {
                    "ticket_key",
                    "work_run_key",
                    "phase",
                    "claim_state",
                    "slot_held",
                    "candidate_identity",
                    "accepted_candidate_receipt_digest",
                    "candidate_diff_record_digest",
                    "delivery_receipt_digest",
                    "result_digest",
                    "evidence_digests",
                    "reason",
                    "next_check_at",
                    "exclusive_resources",
                }:
                    _reject("LOCAL_TRANSCRIPT_INVALID")
        elif operation == "advance":
            if set(entry) != {"operation", "wake_ref", "outcome"}:
                _reject("LOCAL_TRANSCRIPT_INVALID")
            wake_ref = _text(entry.get("wake_ref"), "LOCAL_TRANSCRIPT_INVALID")
            outcome = _mapping(entry.get("outcome"), "LOCAL_TRANSCRIPT_INVALID")
            if set(outcome) != {"status", "reason"}:
                _reject("LOCAL_TRANSCRIPT_INVALID")
            status = _text(outcome.get("status"), "LOCAL_TRANSCRIPT_INVALID")
            if status not in {"Running", "Complete"}:
                _reject("LOCAL_TRANSCRIPT_INVALID")
            advance_wakes.append(wake_ref)
            advance_statuses.append(status)
        elif operation == "watchdog":
            if set(entry) != {"kind", "operation", "outcomes"}:
                _reject("LOCAL_TRANSCRIPT_INVALID")
            if entry.get("kind") != "lost_wake":
                _reject("LOCAL_TRANSCRIPT_INVALID")
            outcomes = _sequence(entry.get("outcomes"), "LOCAL_TRANSCRIPT_INVALID")
            if len(outcomes) != 1:
                _reject("LOCAL_TRANSCRIPT_INVALID")
            outcome = _mapping(outcomes[0], "LOCAL_TRANSCRIPT_INVALID")
            if set(outcome) != {"status", "reason"} or outcome.get("status") != "Complete":
                _reject("LOCAL_TRANSCRIPT_INVALID")
        else:  # pragma: no cover - operation sequence rejects unknown values.
            _reject("LOCAL_TRANSCRIPT_INVALID")

    if inspect_statuses != ["Blocked", "Running", "Complete", "Complete", "Complete", "Complete", "Complete"]:
        _reject("LOCAL_TRANSCRIPT_INVALID")
    if advance_statuses != ["Running", "Complete", "Complete", "Complete"]:
        _reject("LOCAL_TRANSCRIPT_INVALID")
    if (
        advance_wakes[0] != "root:initial"
        or not advance_wakes[1].startswith("watchdog:runtime:")
        or advance_wakes[2:] != ["root:restart", "root:restart"]
    ):
        _reject("LOCAL_TRANSCRIPT_INVALID")


def _verify_local_root_canary(
    bundle: Mapping[str, object],
    *,
    expected_target_sha: str | None,
) -> RootCanaryAcceptanceReceiptV1:
    if bundle.get("acceptance_mode") != _LOCAL_ROOT_MODE:
        if "acceptance_mode" not in bundle:
            _reject("ROOT_ACCEPTANCE_MODE_REQUIRED")
        _reject("ROOT_ACCEPTANCE_MODE_INVALID")
    if bundle.get("schema_version") != _LOCAL_ROOT_SCHEMA:
        _reject("ROOT_ACCEPTANCE_SCHEMA_INVALID")
    _validate_status(bundle)
    if "record_digest" not in bundle:
        _reject("LOCAL_RECORD_DIGEST_INVALID")
    manifest = _local_manifest_entries(bundle)
    facts, readback = _local_facts_and_readback(bundle)
    required_fact_fields = {
        "tickets",
        "work_runs",
        "concurrency",
        "candidate_gate",
        "readback",
        "replay",
    }
    if not required_fact_fields.issubset(facts):
        _reject("LOCAL_EVIDENCE_INCOMPLETE")
    if "transcript" not in bundle:
        _reject("LOCAL_EVIDENCE_INCOMPLETE")
    replay = _mapping(bundle.get("replay"), "LOCAL_EVIDENCE_INCOMPLETE")
    facts_replay = _mapping(facts.get("replay"), "LOCAL_EVIDENCE_INCOMPLETE")
    if facts_replay != replay:
        _reject("LOCAL_EVIDENCE_INCOMPLETE")
    _validate_local_transcript(bundle)
    local_evidence = _mapping(
        bundle.get("local_evidence"), "LOCAL_EVIDENCE_INCOMPLETE"
    )
    if set(local_evidence) != {"schema_version", "acceptance_mode", "batches"}:
        _reject("LOCAL_EVIDENCE_INCOMPLETE")
    if (
        local_evidence.get("schema_version") != "gwo.v8.local-evidence.v1"
        or local_evidence.get("acceptance_mode") != _LOCAL_ROOT_MODE
    ):
        _reject("LOCAL_EVIDENCE_INCOMPLETE")
    _reject_local_forbidden_fields(local_evidence)
    raw_local_batches = _sequence(
        local_evidence.get("batches"), "LOCAL_BATCH_PROOF_INCOMPLETE"
    )
    (
        repository,
        campaign_key,
        plan_revision_digest,
        activation_id,
        writer_generation,
        _,
    ) = _local_identity(bundle, facts, raw_local_batches)
    candidates_tuple, candidates, accepted = _local_candidate_records(
        bundle, readback, repository, campaign_key, plan_revision_digest
    )
    del candidates_tuple
    _local_candidate_diffs(readback, candidates)
    reviews = _local_review_links(bundle, facts, candidates, accepted, manifest)
    expected_target = expected_target_sha
    batches, proofs_by_id = _local_batch_proofs(
        local_evidence,
        repository=repository,
        campaign_key=campaign_key,
        plan_revision_digest=plan_revision_digest,
        candidates=candidates,
        accepted=accepted,
        reviews=reviews,
        expected_target_sha=expected_target,
    )
    delivery_proofs = _local_delivery_proofs(
        readback,
        batches=batches,
        proofs_by_id=proofs_by_id,
    )
    results = _local_result_crosslinks(
        bundle,
        facts,
        readback,
        candidates,
        accepted,
        batches,
        proofs_by_id,
        delivery_proofs,
    )

    facts_tickets = facts.get("tickets")
    fact_values = _sequence(facts_tickets, "LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
    if tuple(
        _text(
            _mapping(value, "LOCAL_CANDIDATE_REVIEW_LINK_INVALID").get("ticket_key"),
            "LOCAL_CANDIDATE_REVIEW_LINK_INVALID",
        )
        for value in fact_values
    ) != _LOCAL_TICKET_REFS:
        _reject("LOCAL_CANDIDATE_REVIEW_LINK_INVALID")
    concurrency = _mapping(facts.get("concurrency"), "LOCAL_EVIDENCE_INCOMPLETE")
    if (
        concurrency.get("worker_slot_limit") != 4
        or concurrency.get("max_held") != 4
        or concurrency.get("final_held") != 0
        or concurrency.get("final_available") != 4
        or concurrency.get("work_run_count") != 4
    ):
        _reject("LOCAL_EVIDENCE_INCOMPLETE")
    if any(replay.get(field) is not True for field in ("readback_unchanged", "idempotent_delivery", "idempotent_effects")):
        _reject("LOCAL_EVIDENCE_INCOMPLETE")

    accepted_values = [accepted[key] for key in _LOCAL_TICKET_REFS]
    policy_digests = {_digest(value["policy_witness_digest"], "POLICY_EVIDENCE_INCOMPLETE") for value in accepted_values}
    if len(policy_digests) != 1:
        _reject("POLICY_EVIDENCE_INCOMPLETE")
    authority_digests = sorted(
        {_digest(value["authority_subtree_digest"], "AUTHORITY_EVIDENCE_INCOMPLETE") for value in accepted_values}
    )
    runtime_digests = sorted(
        {
            _digest(
                value["candidate_receipt"]["runtime_subject_digest"],
                "RUNTIME_EVIDENCE_INCOMPLETE",
            )
            for value in candidates.values()
        }
    )
    recovery_body = {
        "kind": "local-fault-journal.v1",
        "replay": facts.get("replay"),
        "candidate_gate": facts.get("candidate_gate"),
    }
    ticket_contract_digests = tuple(
        (ticket["key"], _ticket_contract_digest(ticket)) for ticket in manifest
    )
    candidate_receipt_digests = tuple(
        (key, candidates[key]["candidate_receipt_digest"]) for key in _LOCAL_TICKET_REFS
    )
    finding_ledger_digests = tuple(
        (key, reviews[key]["finding_ledger_digest"]) for key in _LOCAL_TICKET_REFS
    )
    batch_receipt_digests = (
        ("multi", batches[0].receipt_digest),
        ("singleton", batches[1].receipt_digest),
    )
    authoritative_evidence = _local_authoritative_evidence(
        local_evidence,
        candidates,
        accepted,
        reviews,
        results,
        manifest,
        facts,
    )
    record_digest = _digest(bundle.get("record_digest"), "LOCAL_RECORD_DIGEST_INVALID")
    record_body = {key: value for key, value in bundle.items() if key != "record_digest"}
    if record_digest != _canonical_digest(record_body):
        _reject("LOCAL_RECORD_DIGEST_INVALID")
    receipt = RootCanaryAcceptanceReceiptV1(
        repository=repository,
        campaign_key=campaign_key,
        plan_revision_digest=plan_revision_digest,
        activation_id=activation_id,
        writer_generation=writer_generation,
        standard_ticket_keys=_LOCAL_STANDARD_TICKET_REFS,
        strict_ticket_key=_LOCAL_STRICT_TICKET_REF,
        standard_batch=batches[0],
        strict_batch=batches[1],
        peak_worker_slots=4,
        refill_proven=True,
        permission_same_binding=True,
        stale_diagnosis_bounded=True,
        terminal_replacement_bounded=True,
        terminal_replacement_receipt_digests=(),
        duplicate_effect_ids=(),
        ticket_contract_digests=ticket_contract_digests,
        candidate_receipt_digests=candidate_receipt_digests,
        policy_witness_digest=next(iter(policy_digests)),
        authority_root_digest=_canonical_digest(
            {"kind": "local-authority-root.v1", "subtree_digests": authority_digests}
        ),
        runtime_selector_digest=_canonical_digest(
            {"kind": "local-runtime-selector.v1", "subject_digests": runtime_digests}
        ),
        finding_ledger_digests=finding_ledger_digests,
        batch_receipt_digests=batch_receipt_digests,
        fault_journal_digest=_canonical_digest(recovery_body),
        canary_target_sha=batches[1].target_sha,
        authoritative_evidence=authoritative_evidence,
        receipt_digest="",
        acceptance_mode=_LOCAL_ROOT_MODE,
    )
    return replace(receipt, receipt_digest=digest_value(receipt.canonical_digest_payload()))


def _verify_root_canary(
    bundle: Mapping[str, object],
    *,
    expected_target_sha: str | None = None,
) -> RootCanaryAcceptanceReceiptV1:
    """Verify one complete, read-only root-Canary evidence bundle."""

    if type(bundle) is not dict:
        _reject("ACCEPTANCE_BUNDLE_INVALID")
    if (
        "acceptance_mode" in bundle
        or bundle.get("schema_version") == _LOCAL_ROOT_SCHEMA
        or "local_evidence" in bundle
    ):
        return _verify_local_root_canary(
            bundle, expected_target_sha=expected_target_sha
        )
    if expected_target_sha is not None:
        _text(expected_target_sha, "TARGET_SHA_INCOMPLETE")
    _validate_status(bundle)
    (
        repository,
        campaign_key,
        plan_revision_digest,
        activation_id,
        writer_generation,
        canary_target_sha,
    ) = _campaign_identity(bundle)
    aliases, tickets = _ticket_aliases(bundle)
    proof = _proof(bundle)

    policy_witness_digest = _evidence_digest(
        bundle,
        proof,
        "policy_witness_digest",
        "policy_witness",
        "POLICY_EVIDENCE_INCOMPLETE",
    )
    authority_root_digest = _evidence_digest(
        bundle,
        proof,
        "authority_root_digest",
        "authority_root",
        "AUTHORITY_EVIDENCE_INCOMPLETE",
    )
    runtime_selector_digest = _evidence_digest(
        bundle,
        proof,
        "runtime_selector_digest",
        "runtime_selector",
        "RUNTIME_EVIDENCE_INCOMPLETE",
    )
    fault_journal_digest = _evidence_digest(
        bundle,
        proof,
        "fault_journal_digest",
        "fault_journal",
        "FAULT_EVIDENCE_INCOMPLETE",
    )
    for field, digest, code in (
        ("authority_root_digest", authority_root_digest, "AUTHORITY_EVIDENCE_INCOMPLETE"),
        ("runtime_selector_digest", runtime_selector_digest, "RUNTIME_EVIDENCE_INCOMPLETE"),
    ):
        proof_digest = proof.get(field)
        if proof_digest is not None and proof_digest != digest:
            _reject(code)

    candidates = _candidate_records(bundle, aliases)
    candidate_digests = tuple(
        (
            ROOT_TICKET_KEYS[index],
            _readback_digest(candidate, "candidate_receipt_digest", "receipt_digest")
            or "",
        )
        for index, candidate in enumerate(candidates)
    )
    if any(not digest for _key, digest in candidate_digests):
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    accepted = _accepted_candidate_records(bundle, aliases, candidates)
    _validate_candidate_links(
        candidates,
        accepted,
        aliases,
        repository,
        campaign_key,
        plan_revision_digest,
        policy_witness_digest,
    )
    reviews = _review_records(bundle, accepted, aliases)
    finding_digests = tuple(
        (
            ROOT_TICKET_KEYS[index],
            _readback_digest(
                review,
                "finding_ledger_digest",
                "review_finding_ledger_digest",
                "ledger_digest",
            )
            or "",
        )
        for index, review in enumerate(reviews)
    )
    if any(not digest for _key, digest in finding_digests):
        _reject("FINDING_LEDGER_INCOMPLETE")

    (
        peak,
        refill_proven,
        permission_same,
        stale_bounded,
        terminal_bounded,
        terminal_digests,
        _,
    ) = _validate_recovery_proof(proof, aliases)
    duplicate_effect_ids = _validate_effects(proof)
    standard_batch, strict_batch = _batch_records(
        bundle,
        aliases,
        campaign_key,
        plan_revision_digest,
        expected_target_sha,
    )
    _validate_batch_crosslinks(bundle, aliases, candidates, reviews)
    _validate_result_integrity(bundle, aliases, (standard_batch, strict_batch))

    proof_candidate_digests = _proof_digest_values(
        proof, "candidate_receipt_digests", "CANDIDATE_RECEIPT_INCOMPLETE"
    )
    expected_candidate_digests = tuple(
        sorted({digest for _key, digest in candidate_digests})
    )
    if proof_candidate_digests is not None and proof_candidate_digests != expected_candidate_digests:
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    proof_finding_digests = _proof_digest_values(
        proof, "review_finding_ledger_digests", "FINDING_LEDGER_INCOMPLETE"
    )
    expected_finding_digests = tuple(
        sorted({digest for _key, digest in finding_digests})
    )
    if proof_finding_digests is not None and proof_finding_digests != expected_finding_digests:
        _reject("FINDING_LEDGER_INCOMPLETE")
    proof_batch_digests = _proof_digest_values(
        proof, "batch_receipt_digests", "BATCH_READBACK_INCOMPLETE"
    )
    if proof_batch_digests is not None and set(proof_batch_digests) != {
        standard_batch.receipt_digest,
        strict_batch.receipt_digest,
    }:
        _reject("BATCH_READBACK_INCOMPLETE")

    ticket_contract_digests = tuple(
        (
            aliases.get(_ticket_reference(ticket), _ticket_reference(ticket)),
            _ticket_contract_digest(ticket),
        )
        for ticket in sorted(
            tickets,
            key=lambda item: ROOT_TICKET_KEYS.index(
                aliases.get(_ticket_reference(item), _ticket_reference(item))
            ),
        )
    )
    batch_receipt_digests = (
        ("multi", standard_batch.receipt_digest),
        ("singleton", strict_batch.receipt_digest),
    )
    authoritative_evidence = _authoritative_evidence(
        bundle, proof, tickets, candidates, accepted, reviews
    )

    receipt = RootCanaryAcceptanceReceiptV1(
        repository=repository,
        campaign_key=campaign_key,
        plan_revision_digest=plan_revision_digest,
        activation_id=activation_id,
        writer_generation=writer_generation,
        standard_ticket_keys=STANDARD_TICKET_KEYS,
        strict_ticket_key=STRICT_TICKET_KEY,
        standard_batch=standard_batch,
        strict_batch=strict_batch,
        peak_worker_slots=peak,
        refill_proven=refill_proven,
        permission_same_binding=permission_same,
        stale_diagnosis_bounded=stale_bounded,
        terminal_replacement_bounded=terminal_bounded,
        terminal_replacement_receipt_digests=terminal_digests,
        duplicate_effect_ids=duplicate_effect_ids,
        ticket_contract_digests=ticket_contract_digests,
        candidate_receipt_digests=candidate_digests,
        policy_witness_digest=policy_witness_digest,
        authority_root_digest=authority_root_digest,
        runtime_selector_digest=runtime_selector_digest,
        finding_ledger_digests=finding_digests,
        batch_receipt_digests=batch_receipt_digests,
        fault_journal_digest=fault_journal_digest,
        canary_target_sha=canary_target_sha,
        authoritative_evidence=authoritative_evidence,
        receipt_digest="",
    )
    return replace(receipt, receipt_digest=digest_value(receipt.canonical_digest_payload()))


def verify_root_canary(
    bundle: Mapping[str, object],
    *,
    expected_target_sha: str | None = None,
) -> RootCanaryAcceptanceReceiptV1:
    """Verify a bundle and turn malformed nested data into a named rejection."""

    try:
        return _verify_root_canary(
            bundle, expected_target_sha=expected_target_sha
        )
    except RootCanaryVerificationError:
        raise
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
        raise RootCanaryVerificationError("ACCEPTANCE_BUNDLE_INVALID") from error


def _serialized_batch(
    batch: VerifiedBatch, *, local_only: bool
) -> dict[str, object]:
    payload = dataclasses.asdict(batch)
    if local_only:
        payload.pop("pull_request_number")
        payload.pop("hosted_run_id")
    return payload


def write_acceptance_document(
    path: Path, receipt: RootCanaryAcceptanceReceiptV1
) -> None:
    """Write a human-readable, local/read-only acceptance projection."""

    local_only = receipt.acceptance_mode == _LOCAL_ROOT_MODE
    payload = {
        "schema": (
            _LOCAL_RECEIPT_SCHEMA
            if receipt.acceptance_mode == _LOCAL_ROOT_MODE
            else "gwo-v8-root-canary-acceptance.v1"
        ),
        "repository": receipt.repository,
        "campaign_key": receipt.campaign_key,
        "plan_revision_digest": receipt.plan_revision_digest,
        "activation_id": receipt.activation_id,
        "writer_generation": receipt.writer_generation,
        "standard_ticket_keys": receipt.standard_ticket_keys,
        "strict_ticket_key": receipt.strict_ticket_key,
        "standard_batch": _serialized_batch(
            receipt.standard_batch, local_only=local_only
        ),
        "strict_batch": _serialized_batch(receipt.strict_batch, local_only=local_only),
        "ticket_contract_digests": receipt.ticket_contract_digests,
        "candidate_receipt_digests": receipt.candidate_receipt_digests,
        "policy_witness_digest": receipt.policy_witness_digest,
        "authority_root_digest": receipt.authority_root_digest,
        "runtime_selector_digest": receipt.runtime_selector_digest,
        "finding_ledger_digests": receipt.finding_ledger_digests,
        "batch_receipt_digests": receipt.batch_receipt_digests,
        "fault_journal_digest": receipt.fault_journal_digest,
        "peak_worker_slots": receipt.peak_worker_slots,
        "refill_proven": receipt.refill_proven,
        "permission_same_binding": receipt.permission_same_binding,
        "stale_diagnosis_bounded": receipt.stale_diagnosis_bounded,
        "terminal_replacement_bounded": receipt.terminal_replacement_bounded,
        "terminal_replacement_receipt_digests": receipt.terminal_replacement_receipt_digests,
        "duplicate_effect_ids": receipt.duplicate_effect_ids,
        "canary_target_sha": receipt.canary_target_sha,
        "authoritative_evidence": receipt.authoritative_evidence,
        "receipt_digest": receipt.receipt_digest,
    }
    if receipt.acceptance_mode is not None:
        payload["acceptance_mode"] = receipt.acceptance_mode
    path.parent.mkdir(parents=True, exist_ok=True)
    fence = "`" * 3
    path.write_text(
        "# GWO V8 Root Canary Acceptance\n\n"
        "This is local/read-only evidence only; it does not claim live GitHub "
        "or Paseo execution.\n\n"
        + fence
        + "json\n"
        + json.dumps(_json_value(payload), ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
        + fence
        + "\n",
        encoding="utf-8",
    )


def _load_json(path: Path, code: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RootCanaryVerificationError(code) from error
    return _mapping(value, code)


def _load_ticket_manifest_for_bundle(
    path: Path, bundle: Mapping[str, object]
) -> dict[str, object]:
    local_mode = (
        bundle.get("acceptance_mode") == _LOCAL_ROOT_MODE
        or bundle.get("schema_version") == _LOCAL_ROOT_SCHEMA
        or "local_evidence" in bundle
    )
    if not local_mode:
        return _load_json(path, "ROOT_TICKET_MANIFEST_INVALID")
    try:
        return load_ticket_manifest(path, require_real_root_numbers=True)
    except RootCanaryProvisionError as error:
        raise RootCanaryVerificationError(error.code) from error
    except (OSError, TypeError, ValueError, KeyError) as error:
        raise RootCanaryVerificationError("ROOT_TICKET_MANIFEST_INVALID") from error


def _assert_live_repository(repository: str) -> None:
    try:
        raw = json.loads(
            subprocess.check_output(
                ("gh", "repo", "view", repository, "--json", "nameWithOwner"),
                text=True,
            )
        )
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise RootCanaryVerificationError("ROOT_GITHUB_READBACK_FAILED") from error
    if type(raw) is not dict or raw.get("nameWithOwner") != repository:
        _reject("ROOT_REPOSITORY_MISMATCH")


def verify_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only verification of a GWO V8 root-Canary evidence bundle."
    )
    parser.add_argument("--tickets", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--batch-receipt", type=Path, action="append")
    parser.add_argument("--repository", default=ROOT_REPOSITORY)
    parser.add_argument(
        "--target-sha",
        "--remote-target-sha",
        dest="expected_target_sha",
    )
    parser.add_argument("--github-live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.repository != ROOT_REPOSITORY:
        _reject("ROOT_REPOSITORY_MISMATCH")
    if args.github_live:
        _assert_live_repository(args.repository)

    diagnostics = _load_json(args.diagnostics, "DIAGNOSTICS_JSON_INVALID")
    bundle = _bundle_from_diagnostics(diagnostics)
    manifest = _load_ticket_manifest_for_bundle(args.tickets, bundle)
    bundle = _merge_ticket_manifest(bundle, manifest, args.repository)

    if args.batch_receipt is not None:
        if len(args.batch_receipt) != 2:
            _reject("BATCH_READBACK_INCOMPLETE")
        receipt_values = [
            _load_json(path, "BATCH_READBACK_INCOMPLETE")
            for path in args.batch_receipt
        ]
        existing = bundle.get("batches")
        if existing is not None:
            existing_values = _sequence(existing, "BATCH_READBACK_INCOMPLETE")
            if len(existing_values) != 2:
                _reject("BATCH_READBACK_INCOMPLETE")
            for left, right in zip(existing_values, receipt_values, strict=True):
                if canonical_json_bytes(left) != canonical_json_bytes(right):
                    _reject("BATCH_READBACK_MISMATCH")
        bundle["batches"] = receipt_values

    if bundle.get("repository") != args.repository:
        _reject("ROOT_REPOSITORY_MISMATCH")
    receipt = verify_root_canary(
        bundle, expected_target_sha=args.expected_target_sha
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output_payload = dataclasses.asdict(receipt)
    local_only = receipt.acceptance_mode == _LOCAL_ROOT_MODE
    output_payload["standard_batch"] = _serialized_batch(
        receipt.standard_batch, local_only=local_only
    )
    output_payload["strict_batch"] = _serialized_batch(
        receipt.strict_batch, local_only=local_only
    )
    output_payload["schema"] = (
        _LOCAL_RECEIPT_SCHEMA
        if receipt.acceptance_mode == _LOCAL_ROOT_MODE
        else "gwo-v8-root-canary-acceptance.v1"
    )
    if receipt.acceptance_mode is None:
        output_payload.pop("acceptance_mode", None)
    args.output.write_bytes(canonical_json_bytes(output_payload))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return verify_main(argv)
    except RootCanaryVerificationError as error:
        print(error.code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
