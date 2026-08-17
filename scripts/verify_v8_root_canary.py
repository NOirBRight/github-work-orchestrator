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


ROOT_REPOSITORY = "NOirBRight/github-work-orchestrator"
ROOT_TICKET_KEYS = ("alpha", "beta", "gamma", "delta")
STANDARD_TICKET_KEYS = ("alpha", "beta", "gamma")
STRICT_TICKET_KEY = "delta"
_TICKET_KEY_PATTERN = re.compile(r"issue:([1-9][0-9]*)\Z")
_GITHUB_REF_PATTERN = re.compile(
    rf"github://{re.escape(ROOT_REPOSITORY)}/issues/([1-9][0-9]*)\Z"
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
    pull_request_number: int
    hosted_run_id: int | str
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

    def canonical_digest_payload(self) -> dict[str, object]:
        """Return the exact mapping covered by ``receipt_digest``."""

        return {
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
            "refill_ticket_order": list(ROOT_TICKET_KEYS),
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
    result: list[str] = []
    for item in values:
        if type(item) is str:
            result.append(_text(item, code))
            continue
        mapping = _mapping(item, code)
        result.append(_text(_readback_digest(mapping, "digest", "receipt_digest"), code))
    return tuple(result)


def _evidence_digest(
    bundle: Mapping[str, object],
    proof: Mapping[str, object],
    field: str,
    evidence_name: str,
    code: str,
) -> str:
    value = bundle.get(field)
    top_digest: str | None = None
    if value is not None:
        if type(value) is str:
            top_digest = _text(value, code)
        elif type(value) is dict:
            top_digest = _text(
                _readback_digest(value, "digest", "receipt_digest"), code
            )
        else:
            _reject(code)

    proof_digest = _proof_digest(proof, field, evidence_name, code=code)
    if top_digest is not None and proof_digest is not None and top_digest != proof_digest:
        _reject(code)
    value_digest = top_digest or proof_digest
    evidence = bundle.get(evidence_name)
    if value_digest is None and evidence is not None:
        evidence_mapping = _mapping(evidence, code)
        value_digest = _readback_digest(evidence_mapping, "digest", "receipt_digest")
    result = _text(value_digest, code)
    if evidence is not None:
        evidence_mapping = _mapping(evidence, code)
        embedded = _readback_digest(evidence_mapping, "digest", "receipt_digest")
        if embedded is not None and embedded != result:
            _reject(code)
    proof_evidence = proof.get(evidence_name)
    if proof_evidence is not None:
        proof_evidence_mapping = _mapping(proof_evidence, code)
        embedded = _readback_digest(
            proof_evidence_mapping, "digest", "receipt_digest"
        )
        if embedded is None or embedded != result:
            _reject(code)
    return result


def _validate_effects(proof: Mapping[str, object]) -> tuple[str, ...]:
    semantic = _sequence(proof.get("semantic_effect_ids"), "EFFECT_PROOF_INCOMPLETE")
    external = _sequence(proof.get("external_effect_ids"), "EFFECT_PROOF_INCOMPLETE")
    semantic_ids = tuple(
        _text(value, "EFFECT_PROOF_INCOMPLETE") for value in semantic
    )
    external_ids = tuple(
        _text(value, "EFFECT_PROOF_INCOMPLETE") for value in external
    )
    duplicates = _sequence(proof.get("duplicate_effect_ids"), "DUPLICATE_EFFECT")
    duplicate_ids = tuple(_text(value, "DUPLICATE_EFFECT") for value in duplicates)
    if len(set(semantic_ids)) != len(semantic_ids):
        _reject("DUPLICATE_EFFECT")
    if len(set(external_ids)) != len(external_ids):
        _reject("DUPLICATE_EFFECT")
    if duplicate_ids:
        _reject("DUPLICATE_EFFECT")
    for field, ids in (
        ("semantic_effect_records", semantic_ids),
        ("external_effect_records", external_ids),
    ):
        records = _sequence(proof.get(field), "EFFECT_PROOF_INCOMPLETE")
        record_ids: list[str] = []
        for record in records:
            mapping = _mapping(record, "EFFECT_PROOF_INCOMPLETE")
            record_id = _text(
                mapping.get("stable_action_id"), "EFFECT_PROOF_INCOMPLETE"
            )
            _text(
                mapping.get("effect_digest", mapping.get("digest")),
                "EFFECT_PROOF_INCOMPLETE",
            )
            record_ids.append(record_id)
        if tuple(record_ids) != ids:
            _reject("EFFECT_PROOF_INCOMPLETE")
    history = proof.get("effect_history")
    if history is not None:
        history_values = _sequence(history, "DUPLICATE_EFFECT")
        seen: set[str] = set()
        for value in history_values:
            if type(value) is dict:
                effect_id = value.get("stable_action_id")
            else:
                effect_id = value
            effect_id = _text(effect_id, "DUPLICATE_EFFECT")
            if effect_id in seen:
                _reject("DUPLICATE_EFFECT")
            seen.add(effect_id)
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


def _validate_recovery_proof(
    proof: Mapping[str, object], aliases: Mapping[str, str]
) -> tuple[int, bool, bool, bool, bool, tuple[str, ...], tuple[str, ...]]:
    limit = proof.get("worker_slot_limit")
    if limit is not None and (type(limit) is not int or limit != 4):
        _reject("WORKER_SLOT_PROOF_INVALID")
    peak = proof.get("peak_worker_slots")
    if type(peak) is not int or peak != 4:
        _reject("WORKER_SLOT_PROOF_INVALID")

    refill = _normalise_proof_keys(
        proof.get("refill_ticket_order"), aliases, "REFILL_PROOF_INVALID"
    )
    if refill != ROOT_TICKET_KEYS:
        _reject("REFILL_PROOF_INVALID")

    pairs = _sequence(
        proof.get("permission_binding_pairs"), "PERMISSION_BINDING_MISMATCH"
    )
    if not pairs:
        _reject("PERMISSION_BINDING_MISMATCH")
    permission_pairs: list[tuple[str, str]] = []
    for pair in pairs:
        values = _sequence(pair, "PERMISSION_BINDING_MISMATCH")
        if len(values) != 2:
            _reject("PERMISSION_BINDING_MISMATCH")
        before = _text(values[0], "PERMISSION_BINDING_MISMATCH")
        after = _text(values[1], "PERMISSION_BINDING_MISMATCH")
        if before != after:
            _reject("PERMISSION_BINDING_MISMATCH")
        permission_pairs.append((before, after))

    permission_links = proof.get("permission_authorization_links")
    permission_values = _sequence(
        permission_links, "PERMISSION_AUTHORIZATION_INCOMPLETE"
    )
    if not permission_values:
        _reject("PERMISSION_AUTHORIZATION_INCOMPLETE")
    linked_permission_pairs: list[tuple[str, str]] = []
    permission_link_records: list[tuple[str, str]] = []
    permission_request_ids: set[str] = set()
    for raw in permission_values:
        link = _mapping(raw, "PERMISSION_AUTHORIZATION_INCOMPLETE")
        ticket_ref = _text(
            link.get("ticket_key"), "PERMISSION_AUTHORIZATION_INCOMPLETE"
        )
        ticket_key = _ticket_key_from_ref(ticket_ref)
        if ticket_key in aliases:
            ticket_key = aliases[ticket_key]
        if ticket_key not in ROOT_TICKET_KEYS:
            _reject("PERMISSION_AUTHORIZATION_INCOMPLETE")
        request_id = _text(
            link.get("request_id", link.get("permission_request_id")),
            "PERMISSION_AUTHORIZATION_INCOMPLETE",
        )
        if request_id in permission_request_ids:
            _reject("PERMISSION_AUTHORIZATION_INCOMPLETE")
        permission_request_ids.add(request_id)
        binding_id = _text(
            link.get("binding_id"), "PERMISSION_AUTHORIZATION_INCOMPLETE"
        )
        before = _text(
            link.get("before_binding_id", link.get("before")),
            "PERMISSION_AUTHORIZATION_INCOMPLETE",
        )
        after = _text(
            link.get("after_binding_id", link.get("after")),
            "PERMISSION_AUTHORIZATION_INCOMPLETE",
        )
        _text(link.get("request_digest"), "PERMISSION_AUTHORIZATION_INCOMPLETE")
        _text(
            link.get("authorization_digest"),
            "PERMISSION_AUTHORIZATION_INCOMPLETE",
        )
        if "authorized" in link and link["authorized"] is not True:
            _reject("PERMISSION_AUTHORIZATION_INCOMPLETE")
        if binding_id != before or before != after:
            _reject("PERMISSION_BINDING_MISMATCH")
        linked_permission_pairs.append((before, after))
        permission_link_records.append((ticket_key, binding_id))
    if (
        len(linked_permission_pairs) != len(permission_pairs)
        or sorted(linked_permission_pairs) != sorted(permission_pairs)
    ):
        _reject("PERMISSION_AUTHORIZATION_INCOMPLETE")
    permission_same = True

    stale_values = _sequence(
        proof.get("stale_diagnosis_count_by_binding"), "RECOVERY_BOUND_INVALID"
    )
    if not stale_values:
        _reject("RECOVERY_BOUND_INVALID")
    stale_ids: list[str] = []
    for item in stale_values:
        pair = _sequence(item, "RECOVERY_BOUND_INVALID")
        if len(pair) != 2 or type(pair[1]) is not int or pair[1] < 0:
            _reject("RECOVERY_BOUND_INVALID")
        binding_id = _text(pair[0], "RECOVERY_BOUND_INVALID")
        if pair[1] > 1:
            _reject("RECOVERY_BOUND_INVALID")
        stale_ids.append(binding_id)
    if len(set(stale_ids)) != len(stale_ids):
        _reject("RECOVERY_BOUND_INVALID")
    diagnosed = proof.get("stale_diagnosed_binding_ids")
    diagnosed_ids = tuple(
        _text(value, "STALE_DIAGNOSIS_AUTHORIZATION_INCOMPLETE")
        for value in _sequence(
            diagnosed, "STALE_DIAGNOSIS_AUTHORIZATION_INCOMPLETE"
        )
    )
    if (
        len(diagnosed_ids) != len(stale_ids)
        or len(set(diagnosed_ids)) != len(diagnosed_ids)
        or set(diagnosed_ids) != set(stale_ids)
    ):
        _reject("RECOVERY_BOUND_INVALID")

    stale_links = _sequence(
        proof.get("stale_diagnosis_authorization_links"),
        "STALE_DIAGNOSIS_AUTHORIZATION_INCOMPLETE",
    )
    linked_stale: set[str] = set()
    stale_link_records: list[tuple[str, str]] = []
    for raw in stale_links:
        link = _mapping(raw, "STALE_DIAGNOSIS_AUTHORIZATION_INCOMPLETE")
        ticket_ref = _text(
            link.get("ticket_key"), "STALE_DIAGNOSIS_AUTHORIZATION_INCOMPLETE"
        )
        ticket_key = _ticket_key_from_ref(ticket_ref)
        if ticket_key in aliases:
            ticket_key = aliases[ticket_key]
        if ticket_key not in ROOT_TICKET_KEYS:
            _reject("STALE_DIAGNOSIS_AUTHORIZATION_INCOMPLETE")
        binding_id = _text(
            link.get("binding_id"), "STALE_DIAGNOSIS_AUTHORIZATION_INCOMPLETE"
        )
        _text(link.get("diagnosis_id"), "STALE_DIAGNOSIS_AUTHORIZATION_INCOMPLETE")
        _text(
            link.get("diagnosis_digest"),
            "STALE_DIAGNOSIS_AUTHORIZATION_INCOMPLETE",
        )
        if link.get("authorized") is not True:
            _reject("STALE_DIAGNOSIS_AUTHORIZATION_INCOMPLETE")
        linked_stale.add(binding_id)
        stale_link_records.append((ticket_key, binding_id))
    if (
        len(stale_link_records) != len(stale_ids)
        or len(linked_stale) != len(stale_link_records)
        or linked_stale != set(stale_ids)
    ):
        _reject("STALE_DIAGNOSIS_AUTHORIZATION_INCOMPLETE")
    stale_bounded = True

    binding_values = _sequence(
        proof.get("binding_count_by_ticket"), "RECOVERY_BOUND_INVALID"
    )
    counts: dict[str, int] = {}
    for item in binding_values:
        pair = _sequence(item, "RECOVERY_BOUND_INVALID")
        if len(pair) != 2 or type(pair[1]) is not int or pair[1] < 1:
            _reject("RECOVERY_BOUND_INVALID")
        key = _ticket_key_from_ref(pair[0])
        if key in aliases:
            key = aliases[key]
        if key not in ROOT_TICKET_KEYS or key in counts:
            _reject("RECOVERY_BOUND_INVALID")
        if pair[1] > 2:
            _reject("RECOVERY_BOUND_INVALID")
        counts[key] = pair[1]
    if set(counts) != set(ROOT_TICKET_KEYS):
        _reject("RECOVERY_BOUND_INVALID")

    binding_ids_values = _sequence(
        proof.get("binding_ids_by_ticket"), "RECOVERY_BOUND_INVALID"
    )
    binding_ids_by_ticket: dict[str, tuple[str, ...]] = {}
    for item in binding_ids_values:
        pair = _sequence(item, "RECOVERY_BOUND_INVALID")
        if len(pair) != 2:
            _reject("RECOVERY_BOUND_INVALID")
        key = _ticket_key_from_ref(pair[0])
        if key in aliases:
            key = aliases[key]
        if key not in ROOT_TICKET_KEYS or key in binding_ids_by_ticket:
            _reject("RECOVERY_BOUND_INVALID")
        binding_ids = tuple(
            _text(value, "RECOVERY_BOUND_INVALID")
            for value in _sequence(pair[1], "RECOVERY_BOUND_INVALID")
        )
        if len(binding_ids) != counts[key] or len(set(binding_ids)) != len(binding_ids):
            _reject("RECOVERY_BOUND_INVALID")
        binding_ids_by_ticket[key] = binding_ids
    if set(binding_ids_by_ticket) != set(ROOT_TICKET_KEYS):
        _reject("RECOVERY_BOUND_INVALID")
    for ticket_key, binding_id in permission_link_records:
        if binding_id not in binding_ids_by_ticket[ticket_key]:
            _reject("PERMISSION_AUTHORIZATION_INCOMPLETE")
    for ticket_key, binding_id in stale_link_records:
        if binding_id not in binding_ids_by_ticket[ticket_key]:
            _reject("STALE_DIAGNOSIS_AUTHORIZATION_INCOMPLETE")
    if not set(stale_ids).issubset(
        {binding for values in binding_ids_by_ticket.values() for binding in values}
    ):
        _reject("STALE_DIAGNOSIS_AUTHORIZATION_INCOMPLETE")

    terminal = proof.get("terminal_replacement_receipt_digests")
    terminal_digests = tuple(
        _text(value, "TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE")
        for value in _sequence(
            () if terminal is None else terminal,
            "TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE",
        )
    )
    if len(terminal_digests) > 1 or len(set(terminal_digests)) != len(terminal_digests):
        _reject("TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE")

    terminal_links = _sequence(
        proof.get("terminal_replacement_authorization_links"),
        "TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE",
    )
    linked_terminal: list[str] = []
    linked_terminal_tickets: set[str] = set()
    for raw in terminal_links:
        link = _mapping(raw, "TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE")
        ticket_ref = _text(
            link.get("ticket_key"),
            "TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE",
        )
        ticket_key = _ticket_key_from_ref(ticket_ref)
        if ticket_key in aliases:
            ticket_key = aliases[ticket_key]
        if ticket_key not in ROOT_TICKET_KEYS:
            _reject("TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE")
        old_binding = _text(
            link.get("old_binding_id"),
            "TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE",
        )
        new_binding = _text(
            link.get("new_binding_id"),
            "TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE",
        )
        _text(
            link.get("terminal_binding_evidence_digest"),
            "TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE",
        )
        replacement_digest = _text(
            link.get("replacement_receipt_digest"),
            "TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE",
        )
        if link.get("authorized") is not True or old_binding == new_binding:
            _reject("TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE")
        if counts.get(ticket_key) != 2:
            _reject("TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE")
        if old_binding not in binding_ids_by_ticket[ticket_key] or new_binding not in binding_ids_by_ticket[ticket_key]:
            _reject("TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE")
        if replacement_digest in linked_terminal:
            _reject("TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE")
        linked_terminal.append(replacement_digest)
        linked_terminal_tickets.add(ticket_key)
    expected_terminal_tickets = {
        key for key, count in counts.items() if count == 2
    }
    if (
        set(linked_terminal) != set(terminal_digests)
        or linked_terminal_tickets != expected_terminal_tickets
    ):
        _reject("TERMINAL_REPLACEMENT_AUTHORIZATION_INCOMPLETE")
    terminal_bounded = True
    return (
        peak,
        set(refill) == set(ROOT_TICKET_KEYS),
        permission_same,
        stale_bounded,
        terminal_bounded,
        terminal_digests,
        tuple(sorted(counts)),
    )


def _candidate_records(
    bundle: Mapping[str, object], aliases: Mapping[str, str]
) -> tuple[dict[str, object], ...]:
    raw_candidates = _sequence(
        bundle.get("candidates"), "CANDIDATE_RECEIPT_INCOMPLETE"
    )
    if len(raw_candidates) != 4:
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    by_key: dict[str, dict[str, object]] = {}
    for raw in raw_candidates:
        candidate = _mapping(raw, "CANDIDATE_RECEIPT_INCOMPLETE")
        ticket_ref = _text(
            candidate.get("ticket_key"), "CANDIDATE_RECEIPT_INCOMPLETE"
        )
        ticket_key = _ticket_key_from_ref(ticket_ref)
        if ticket_key in aliases:
            key = aliases[ticket_key]
        elif ticket_key in ROOT_TICKET_KEYS:
            key = ticket_key
        else:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        if key in by_key:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        assurance = _candidate_assurance(candidate, key)
        expected = "strict" if key == STRICT_TICKET_KEY else "standard"
        if assurance != expected:
            _reject("ASSURANCE_SHAPE_INVALID")
        digest = _readback_digest(
            candidate,
            "candidate_receipt_digest",
            "receipt_digest",
        )
        nested = candidate.get("candidate_receipt")
        nested_mapping = _mapping(nested, "CANDIDATE_RECEIPT_INCOMPLETE")
        nested_digest = _readback_digest(
            nested_mapping, "candidate_receipt_digest", "receipt_digest"
        )
        if nested_digest is None:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        if digest is not None and digest != nested_digest:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        digest = nested_digest
        if digest is None:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        by_key[key] = candidate
    if set(by_key) != set(ROOT_TICKET_KEYS):
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    return tuple(by_key[key] for key in ROOT_TICKET_KEYS)


def _candidate_assurance(candidate: Mapping[str, object], key: str) -> str:
    """Read semantic assurance and bind it to the manifest's delta key."""

    expected = "strict" if key == STRICT_TICKET_KEY else "standard"
    records: list[Mapping[str, object]] = [candidate]
    for name in ("candidate_receipt", "accepted_candidate_receipt"):
        value = candidate.get(name)
        if value is not None:
            records.append(_mapping(value, "ASSURANCE_SHAPE_INVALID"))
    for record in records:
        value = record.get("assurance")
        if value is None:
            requirement = record.get("assurance_requirement")
            if type(requirement) is dict:
                value = requirement.get("mode")
        if value is not None:
            if _text(value, "ASSURANCE_SHAPE_INVALID").lower() != expected:
                _reject("ASSURANCE_SHAPE_INVALID")
    return expected


def _validate_candidate_links(
    candidates: tuple[dict[str, object], ...],
    accepted: Mapping[str, dict[str, object]],
    repository: str,
    campaign_key: str,
    plan_revision_digest: str,
    policy_witness_digest: str,
) -> None:
    for candidate in candidates:
        receipt = _mapping(
            candidate.get("candidate_receipt"), "CANDIDATE_RECEIPT_INCOMPLETE"
        )
        candidate_ticket = _text(
            candidate.get("ticket_key"), "CANDIDATE_RECEIPT_INCOMPLETE"
        )
        for field, expected in (
            ("repository", repository),
            ("campaign_key", campaign_key),
            ("plan_revision_digest", plan_revision_digest),
            ("ticket_key", candidate_ticket),
            ("policy_witness_digest", policy_witness_digest),
        ):
            if receipt.get(field) != expected:
                _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        candidate_digest = _readback_digest(
            candidate, "candidate_receipt_digest", "receipt_digest"
        )
        if _readback_digest(receipt, "receipt_digest") != candidate_digest:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")

    for key, accepted_receipt in accepted.items():
        for field, expected, code in (
            ("repository", repository, "CANDIDATE_RECEIPT_INCOMPLETE"),
            ("campaign_key", campaign_key, "CANDIDATE_RECEIPT_INCOMPLETE"),
            ("plan_revision_digest", plan_revision_digest, "CANDIDATE_RECEIPT_INCOMPLETE"),
            ("policy_witness_digest", policy_witness_digest, "POLICY_EVIDENCE_INCOMPLETE"),
        ):
            actual = accepted_receipt.get(field)
            if actual != expected:
                _reject(code)
        _text(
            accepted_receipt.get("authority_subtree_digest"),
            "AUTHORITY_EVIDENCE_INCOMPLETE",
        )
        expected_ticket = _text(
            candidates[ROOT_TICKET_KEYS.index(key)].get("ticket_key"),
            "CANDIDATE_RECEIPT_INCOMPLETE",
        )
        if accepted_receipt.get("ticket_key") != expected_ticket:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        candidate_digest = _readback_digest(
            accepted_receipt,
            "candidate_receipt_digest",
        )
        if candidate_digest is None:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        finding_digest = _readback_digest(
            accepted_receipt,
            "review_finding_ledger_digest",
            "finding_ledger_digest",
        )
        if finding_digest is None:
            _reject("FINDING_LEDGER_INCOMPLETE")
        if key not in ROOT_TICKET_KEYS:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        nested_accepted = candidates[ROOT_TICKET_KEYS.index(key)].get(
            "accepted_candidate_receipt"
        )
        nested_mapping = _mapping(
            nested_accepted, "CANDIDATE_RECEIPT_INCOMPLETE"
        )
        if canonical_json_bytes(nested_mapping) != canonical_json_bytes(
            accepted_receipt
        ):
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")


def _review_records(
    bundle: Mapping[str, object],
    accepted: Mapping[str, dict[str, object]],
    aliases: Mapping[str, str],
) -> tuple[dict[str, object], ...]:
    raw_reviews = bundle.get("reviews")
    if raw_reviews is None:
        _reject("FINDING_LEDGER_INCOMPLETE")
    values = _sequence(raw_reviews, "FINDING_LEDGER_INCOMPLETE")
    if len(values) != 4:
        _reject("FINDING_LEDGER_INCOMPLETE")
    by_key: dict[str, dict[str, object]] = {}
    for raw in values:
        review = _mapping(raw, "FINDING_LEDGER_INCOMPLETE")
        ticket_ref = _text(review.get("ticket_key"), "FINDING_LEDGER_INCOMPLETE")
        ticket_key = _ticket_key_from_ref(ticket_ref)
        if ticket_key in aliases:
            key = aliases[ticket_key]
        elif ticket_key in ROOT_TICKET_KEYS:
            key = ticket_key
        else:
            _reject("FINDING_LEDGER_INCOMPLETE")
        if key in by_key:
            _reject("FINDING_LEDGER_INCOMPLETE")
        open_findings = review.get("open_finding_ids")
        if open_findings is None:
            open_findings = review.get("open_findings", ())
        if type(open_findings) not in {list, tuple}:
            _reject("FINDING_LEDGER_INCOMPLETE")
        if open_findings:
            _reject("FINDING_LEDGER_INCOMPLETE")
        finding_digest = _readback_digest(
            review,
            "finding_ledger_digest",
            "review_finding_ledger_digest",
            "ledger_digest",
        )
        if finding_digest is None:
            _reject("FINDING_LEDGER_INCOMPLETE")
        expected_finding = _readback_digest(
            accepted.get(key, {}),
            "review_finding_ledger_digest",
            "finding_ledger_digest",
        )
        if expected_finding is None or finding_digest != expected_finding:
            _reject("FINDING_LEDGER_INCOMPLETE")
        candidate_digest = _readback_digest(
            review, "candidate_receipt_digest", "candidate_digest"
        )
        expected = _readback_digest(
            accepted.get(key, {}), "candidate_receipt_digest", "receipt_digest"
        )
        if candidate_digest is None or expected is None or candidate_digest != expected:
            _reject("FINDING_LEDGER_INCOMPLETE")
        ledger = _mapping(
            review.get("finding_ledger"), "FINDING_LEDGER_INCOMPLETE"
        )
        if (
            ledger.get("ticket_key") != ticket_ref
            or ledger.get("candidate_receipt_digest") != candidate_digest
            or _readback_digest(ledger, "ledger_digest", "digest") != finding_digest
        ):
            _reject("FINDING_LEDGER_INCOMPLETE")
        ledger_open = ledger.get("open_finding_ids", ledger.get("open_findings"))
        if type(ledger_open) not in {list, tuple} or ledger_open:
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
                "semantic_effect_records": proof.get("semantic_effect_records"),
                "external_effect_records": proof.get("external_effect_records"),
                "duplicate_effect_ids": proof.get("duplicate_effect_ids"),
                "effect_history": proof.get("effect_history"),
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
    raw_values = bundle.get("accepted_candidate_receipts")
    if raw_values is None:
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    values = _sequence(raw_values, "CANDIDATE_RECEIPT_INCOMPLETE")
    if len(values) != 4:
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    candidate_by_key: dict[str, dict[str, object]] = {}
    for candidate in candidates:
        ref = _text(candidate.get("ticket_key"), "CANDIDATE_RECEIPT_INCOMPLETE")
        if ref in aliases:
            key = aliases[ref]
        elif ref in ROOT_TICKET_KEYS:
            key = ref
        else:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        candidate_by_key[key] = candidate
    accepted: dict[str, dict[str, object]] = {}
    for raw in values:
        item = _mapping(raw, "CANDIDATE_RECEIPT_INCOMPLETE")
        ref = _text(item.get("ticket_key"), "CANDIDATE_RECEIPT_INCOMPLETE")
        if ref in aliases:
            key = aliases[ref]
        elif ref in ROOT_TICKET_KEYS:
            key = ref
        else:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        if key in accepted:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        candidate_digest = _readback_digest(
            candidate_by_key[key], "candidate_receipt_digest", "receipt_digest"
        )
        if item.get("candidate_receipt_digest") != candidate_digest:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        if _readback_digest(item, "receipt_digest") is None:
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        ledger = _readback_digest(
            item, "review_finding_ledger_digest", "finding_ledger_digest"
        )
        if ledger is None:
            _reject("FINDING_LEDGER_INCOMPLETE")
        if item.get("ticket_key") != candidate_by_key[key].get("ticket_key"):
            _reject("CANDIDATE_RECEIPT_INCOMPLETE")
        accepted[key] = item
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
    manifest_repository = manifest.get("repository")
    if manifest_repository != repository or manifest_repository != ROOT_REPOSITORY:
        _reject("ROOT_REPOSITORY_MISMATCH")
    if manifest.get("schema") not in {None, "gwo-v8-root-canary-tickets.v1"}:
        _reject("ROOT_TICKET_MANIFEST_INVALID")
    manifest_tickets = manifest.get("tickets")
    if manifest_tickets is None:
        _reject("ROOT_TICKET_MANIFEST_INVALID")
    manifest_values = _sequence(manifest_tickets, "ROOT_TICKET_MANIFEST_INVALID")
    merged = dict(bundle)
    existing = bundle.get("tickets")
    if existing is not None:
        existing_values = _sequence(existing, "TICKET_READBACK_MISMATCH")
        if len(existing_values) != len(manifest_values):
            _reject("TICKET_READBACK_MISMATCH")
        for left, right in zip(existing_values, manifest_values, strict=True):
            left_mapping = _mapping(left, "TICKET_READBACK_MISMATCH")
            right_mapping = _mapping(right, "TICKET_READBACK_MISMATCH")
            try:
                left_digest = digest_value(left_mapping)
                right_digest = digest_value(right_mapping)
                _ticket_contract_payload(left_mapping, "TICKET_READBACK_MISMATCH")
                _ticket_contract_payload(right_mapping, "TICKET_READBACK_MISMATCH")
            except RootCanaryVerificationError as error:
                if error.code == "TICKET_READBACK_MISMATCH":
                    raise
                _reject("TICKET_READBACK_MISMATCH")
            if left_digest != right_digest:
                _reject("TICKET_READBACK_MISMATCH")
    merged["repository"] = repository
    # Keep the already-read diagnostics projection.  The Task 1 manifest is a
    # second authoritative readback, not a lossy replacement for it.
    merged["tickets"] = list(existing_values) if existing is not None else manifest_tickets
    ready_refs = manifest.get("ready_refs")
    if ready_refs is not None:
        if "ready_refs" in bundle:
            if canonical_json_bytes(bundle["ready_refs"]) != canonical_json_bytes(
                ready_refs
            ):
                _reject("TICKET_READBACK_MISMATCH")
        merged["ready_refs"] = ready_refs
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
    return _adapt_current_readback_shape(bundle)


def _verify_root_canary(
    bundle: Mapping[str, object],
    *,
    expected_target_sha: str | None = None,
) -> RootCanaryAcceptanceReceiptV1:
    """Verify one complete, read-only root-Canary evidence bundle."""

    if type(bundle) is not dict:
        _reject("ACCEPTANCE_BUNDLE_INVALID")
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
    if proof_candidate_digests is not None and proof_candidate_digests != tuple(
        digest for _key, digest in candidate_digests
    ):
        _reject("CANDIDATE_RECEIPT_INCOMPLETE")
    proof_finding_digests = _proof_digest_values(
        proof, "review_finding_ledger_digests", "FINDING_LEDGER_INCOMPLETE"
    )
    if proof_finding_digests is not None and proof_finding_digests != tuple(
        digest for _key, digest in finding_digests
    ):
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
            ticket["key"],
            _text(ticket.get("contract_digest"), "ROOT_TICKET_READBACK_INVALID"),
        )
        for ticket in sorted(tickets, key=lambda item: ROOT_TICKET_KEYS.index(item["key"]))
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


def write_acceptance_document(
    path: Path, receipt: RootCanaryAcceptanceReceiptV1
) -> None:
    """Write a human-readable, local/read-only acceptance projection."""

    payload = {
        "schema": "gwo-v8-root-canary-acceptance.v1",
        "repository": receipt.repository,
        "campaign_key": receipt.campaign_key,
        "plan_revision_digest": receipt.plan_revision_digest,
        "activation_id": receipt.activation_id,
        "writer_generation": receipt.writer_generation,
        "standard_ticket_keys": receipt.standard_ticket_keys,
        "strict_ticket_key": receipt.strict_ticket_key,
        "standard_batch": dataclasses.asdict(receipt.standard_batch),
        "strict_batch": dataclasses.asdict(receipt.strict_batch),
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

    manifest = _load_json(args.tickets, "ROOT_TICKET_MANIFEST_INVALID")
    diagnostics = _load_json(args.diagnostics, "DIAGNOSTICS_JSON_INVALID")
    bundle = _bundle_from_diagnostics(diagnostics)
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
    args.output.write_bytes(canonical_json_bytes(dataclasses.asdict(receipt)))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return verify_main(argv)
    except RootCanaryVerificationError as error:
        print(error.code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
