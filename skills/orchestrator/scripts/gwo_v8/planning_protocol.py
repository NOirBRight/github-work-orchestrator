"""Closed request and output protocol for one Campaign Planning pass.

The Prompt refers only to immutable input Artifacts.  The Runtime-facing
output schema deliberately permits exactly the five semantic fields consumed
by PlanControl; provider or execution facts have no representation.
"""

from __future__ import annotations

from typing import Any


PLANNING_REQUEST_SCHEMA_VERSION = "gwo.plan.planning-request.v1"
PLANNING_ACTION_ID = "campaign.plan.v1"
PLANNING_OUTPUT_PROTOCOL_ID = "campaign.planning-output.v1"

REPLANNING_REQUEST_SCHEMA_VERSION = "gwo.plan.invalidation-replanning-request.v1"
REPLANNING_ACTION_ID = "campaign.plan-invalidation.v1"
REPLANNING_OUTPUT_PROTOCOL_ID = "campaign.plan-invalidation-output.v1"

_OUTPUT_FIELDS = (
    "admitted_work",
    "dependency_additions",
    "exclusive_resources",
    "capability_requirements",
    "decision_requirements",
)
_FORBIDDEN_FACTS = (
    "acceptance",
    "assurance",
    "binding",
    "cli",
    "lifecycle",
    "model",
    "predicted_paths",
    "profile",
    "provider",
    "risk",
    "runtime",
    "selector",
    "session",
    "steps",
    "workspace",
)
_INVARIANTS = (
    "account-for-every-selected-ticket",
    "do-not-add-or-remove-selected-work",
    "dependencies-remain-internal-and-acyclic",
    "capabilities-and-resources-remain-policy-bound",
    "decisions-do-not-expand-authority",
)

_REPLANNING_OUTPUT_FIELDS = (
    "evidence_digests",
    "disposition",
    "reason",
    "successor",
    "decision",
)
_REPLANNING_DISPOSITIONS = (
    "resume_unchanged",
    "defer_non_blocking",
    "use_approved_successor",
    "require_human_decision",
    "reject_invalid_evidence",
)
_REPLANNING_FORBIDDEN_FACTS = (
    "acceptance",
    "authority",
    "campaign_membership",
    "delegation",
    "dependency_mutation",
    "issue_edit",
    "merge",
    "model",
    "provider",
    "runtime",
    "selector",
    "session",
    "steps",
    "ticket_creation",
    "tracker_write",
    "worker_steps",
)


def planning_output_payload_schema() -> dict[str, Any]:
    """Return the exact JSON Schema embedded in the Runtime output schema."""

    ticket_facts = {
        "type": "object",
        "additionalProperties": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
    }
    return {
        "type": "object",
        "required": list(_OUTPUT_FIELDS),
        "properties": {
            "admitted_work": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "uniqueItems": True,
            },
            "dependency_additions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["from", "to", "reason"],
                    "properties": {
                        "from": {"type": "string", "minLength": 1},
                        "to": {"type": "string", "minLength": 1},
                        "reason": {"type": "string", "minLength": 1},
                    },
                    "additionalProperties": False,
                },
            },
            "exclusive_resources": ticket_facts,
            "capability_requirements": ticket_facts,
            "decision_requirements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["code", "detail"],
                    "properties": {
                        "code": {"type": "string", "minLength": 1},
                        "detail": {"type": "string", "minLength": 1},
                        "ticket_key": {"type": "string", "minLength": 1},
                    },
                    "additionalProperties": False,
                },
            },
        },
        "additionalProperties": False,
    }


def replanning_output_payload_schema() -> dict[str, Any]:
    """Return the closed output schema for one invalidation classification."""

    digest = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    successor = {
        "type": ["object", "null"],
        "properties": {
            "approved_ticket_keys": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 1,
                "uniqueItems": True,
            },
            "dependency_additions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["from", "to", "reason"],
                    "properties": {
                        "from": {"type": "string", "minLength": 1},
                        "to": {"type": "string", "minLength": 1},
                        "reason": {"type": "string", "minLength": 1},
                    },
                    "additionalProperties": False,
                },
                "uniqueItems": True,
            },
            "exclusive_resource_additions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["ticket_key", "resource_id", "reason"],
                    "properties": {
                        "ticket_key": {"type": "string", "minLength": 1},
                        "resource_id": {"type": "string", "minLength": 1},
                        "reason": {"type": "string", "minLength": 1},
                    },
                    "additionalProperties": False,
                },
                "uniqueItems": True,
            },
        },
        "required": [
            "approved_ticket_keys",
            "dependency_additions",
            "exclusive_resource_additions",
        ],
        "additionalProperties": False,
    }
    decision = {
        "type": ["object", "null"],
        "properties": {
            "code": {"type": "string", "minLength": 1},
            "detail": {"type": "string", "minLength": 1},
            "required_change": {
                "type": "string",
                "enum": [
                    "new_ticket",
                    "acceptance",
                    "campaign_membership",
                    "authority",
                    "product",
                    "replan_budget",
                ],
            },
        },
        "required": ["code", "detail", "required_change"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "required": list(_REPLANNING_OUTPUT_FIELDS),
        "properties": {
            "evidence_digests": {
                "type": "array",
                "items": digest,
                "minItems": 1,
                "uniqueItems": True,
            },
            "disposition": {
                "type": "string",
                "enum": list(_REPLANNING_DISPOSITIONS),
            },
            "reason": {"type": "string", "minLength": 1},
            "successor": successor,
            "decision": decision,
        },
        "additionalProperties": False,
        "oneOf": [
            {
                "required": ["disposition", "successor", "decision"],
                "properties": {
                    "disposition": {"const": disposition},
                    "successor": {"type": "null"},
                    "decision": {"type": "null"},
                },
            }
            for disposition in (
                "resume_unchanged",
                "defer_non_blocking",
                "reject_invalid_evidence",
            )
        ]
        + [
            {
                "required": ["disposition", "successor", "decision"],
                "properties": {
                    "disposition": {"const": "use_approved_successor"},
                    "successor": {"type": "object"},
                    "decision": {"type": "null"},
                },
            },
            {
                "required": ["disposition", "successor", "decision"],
                "properties": {
                    "disposition": {"const": "require_human_decision"},
                    "successor": {"type": "null"},
                    "decision": {"type": "object"},
                },
            },
        ],
    }


def planning_request_payload(
    snapshot_artifact_digest: str,
    policy_witness_artifact_digest: str,
) -> dict[str, Any]:
    """Build the versioned, Artifact-only Campaign Planning request."""

    return {
        "schema_version": PLANNING_REQUEST_SCHEMA_VERSION,
        "action_id": PLANNING_ACTION_ID,
        "protocol_id": PLANNING_OUTPUT_PROTOCOL_ID,
        "input_artifacts": {
            "snapshot_artifact_digest": snapshot_artifact_digest,
            "policy_witness_artifact_digest": policy_witness_artifact_digest,
        },
        "output_contract": {
            "allowed_fields": list(_OUTPUT_FIELDS),
            "forbidden_facts": list(_FORBIDDEN_FACTS),
            "invariants": list(_INVARIANTS),
            "payload_schema": planning_output_payload_schema(),
        },
    }


def replanning_request_payload(
    snapshot_artifact_digest: str,
    policy_witness_artifact_digest: str,
) -> dict[str, Any]:
    """Build the versioned request for one Campaign invalidation pass."""

    return {
        "schema_version": REPLANNING_REQUEST_SCHEMA_VERSION,
        "action_id": REPLANNING_ACTION_ID,
        "protocol_id": REPLANNING_OUTPUT_PROTOCOL_ID,
        "input_artifacts": {
            "snapshot_artifact_digest": snapshot_artifact_digest,
            "policy_witness_artifact_digest": policy_witness_artifact_digest,
        },
        "output_contract": {
            "allowed_fields": list(_REPLANNING_OUTPUT_FIELDS),
            "forbidden_facts": list(_REPLANNING_FORBIDDEN_FACTS),
            "invariants": [
                "account-for-every-pending-invalidation-evidence",
                "do-not-admit-external-tickets",
                "do-not-change-frozen-contract-or-authority",
                "do-not-delegate-or-write-repository-or-tracker",
                "resume-only-after-execution-readback",
            ],
            "payload_schema": replanning_output_payload_schema(),
        },
    }


def planning_prompt(
    *,
    subject_digest: str,
    authority_digest: str,
    snapshot_artifact_digest: str,
    policy_witness_artifact_digest: str,
) -> dict[str, Any]:
    """Build the complete immutable Runtime Prompt Artifact."""

    return {
        "schema_version": "gwo.runtime.prompt.v1",
        "subject_digest": subject_digest,
        "authority_digest": authority_digest,
        "payload": planning_request_payload(
            snapshot_artifact_digest,
            policy_witness_artifact_digest,
        ),
    }


def replanning_prompt(
    *,
    subject_digest: str,
    authority_digest: str,
    snapshot_artifact_digest: str,
    policy_witness_artifact_digest: str,
) -> dict[str, Any]:
    """Build the complete immutable invalidation-classification Prompt."""

    return {
        "schema_version": "gwo.runtime.prompt.v1",
        "subject_digest": subject_digest,
        "authority_digest": authority_digest,
        "payload": replanning_request_payload(
            snapshot_artifact_digest,
            policy_witness_artifact_digest,
        ),
    }


def planning_output_schema_from_prompt(value: object) -> dict[str, Any] | None:
    """Recognize only the exact Planning protocol and return its payload schema."""

    if type(value) is not dict or set(value) != {
        "schema_version",
        "subject_digest",
        "authority_digest",
        "payload",
    }:
        return None
    payload = value.get("payload")
    if type(payload) is not dict:
        return None
    inputs = payload.get("input_artifacts")
    if type(inputs) is not dict or set(inputs) != {
        "snapshot_artifact_digest",
        "policy_witness_artifact_digest",
    }:
        return None
    expected_payload = planning_request_payload(
        inputs["snapshot_artifact_digest"],
        inputs["policy_witness_artifact_digest"],
    )
    if (
        value.get("schema_version") != "gwo.runtime.prompt.v1"
        or payload != expected_payload
    ):
        return None
    return planning_output_payload_schema()


def replanning_output_schema_from_prompt(value: object) -> dict[str, Any] | None:
    """Recognize only the exact invalidation-classification protocol."""

    if type(value) is not dict or set(value) != {
        "schema_version",
        "subject_digest",
        "authority_digest",
        "payload",
    }:
        return None
    payload = value.get("payload")
    if type(payload) is not dict:
        return None
    inputs = payload.get("input_artifacts")
    if type(inputs) is not dict or set(inputs) != {
        "snapshot_artifact_digest",
        "policy_witness_artifact_digest",
    }:
        return None
    expected_payload = replanning_request_payload(
        inputs["snapshot_artifact_digest"],
        inputs["policy_witness_artifact_digest"],
    )
    if (
        value.get("schema_version") != "gwo.runtime.prompt.v1"
        or payload != expected_payload
    ):
        return None
    return replanning_output_payload_schema()


def planning_protocol_kind_from_prompt(value: object) -> str | None:
    """Return ``initial`` or ``invalidation`` for one exact planning prompt."""

    if planning_output_schema_from_prompt(value) is not None:
        return "initial"
    if replanning_output_schema_from_prompt(value) is not None:
        return "invalidation"
    return None
