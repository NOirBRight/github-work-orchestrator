from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_v8_ga_evidence_bridge import _case
from tests.test_v8_release_metadata import _canonical_pre_tag_case
import scripts.verify_v8_ga_release as verifier
from scripts.verify_v8_ga_release import GaEvidenceBridge, ReleaseGateError


def _rehash_bridge(payload: dict[str, object]) -> dict[str, object]:
    body = {key: value for key, value in payload.items() if key != "bridge_digest"}
    return body | {"bridge_digest": verifier.digest_value(body)}


def _local_source_case(tmp_path: Path, mutate) -> tuple[object, ...]:
    record, canary, activation, admission, bridge, local, git = _case(tmp_path)
    mutated = deepcopy(canary)
    mutate(mutated)
    raw = verifier.canonical_json_bytes(mutated)
    source = tmp_path / "root-canary-acceptance.json"
    source.write_bytes(raw)

    bridge_payload = bridge.to_mapping()
    bridge_payload["local_root_canary"] = {
        **bridge_payload["local_root_canary"],
        "source_file": str(source),
        "source_file_sha256": hashlib.sha256(raw).hexdigest(),
    }
    mutated_bridge = GaEvidenceBridge.from_mapping(_rehash_bridge(bridge_payload))
    return record, mutated, activation, admission, mutated_bridge, local, git


def _producer_source_case(
    tmp_path: Path, section: str, mutate
) -> tuple[object, ...]:
    record, canary, activation, admission, bridge, local, git = _case(tmp_path)
    payloads = {
        "production_activation": deepcopy(activation),
        "default_writer": deepcopy(admission),
        "production_canary": json.loads(
            Path(bridge.production_canary["source_file"]).read_text(encoding="utf-8")
        ),
    }
    payload = payloads[section]
    mutate(payload)
    filename = {
        "production_activation": "production-activation-readback.json",
        "default_writer": "default-writer-readback.json",
        "production_canary": "production-canary-readback.json",
    }[section]
    raw = verifier.canonical_json_bytes(payload)
    source = tmp_path / filename
    source.write_bytes(raw)

    bridge_payload = bridge.to_mapping()
    bridge_payload[section] = {
        **bridge_payload[section],
        "source_file": str(source),
        "source_file_sha256": hashlib.sha256(raw).hexdigest(),
    }
    mutated_bridge = GaEvidenceBridge.from_mapping(_rehash_bridge(bridge_payload))
    if section == "production_activation":
        activation = payload
    elif section == "default_writer":
        admission = payload
    return record, canary, activation, admission, mutated_bridge, local, git


def _verify_bridge(case: tuple[object, ...]):
    record, canary, activation, admission, bridge, local, git = case
    return verifier.verify_pre_tag(
        record,
        main_sha=git.current_origin_main_sha(),
        canary=canary,
        activation=activation,
        admission=admission,
        git=git,
        local_verification=local,
        evidence_bridge=bridge,
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.__setitem__("peak_worker_slots", 999),
        lambda payload: payload.__setitem__("refill_proven", False),
        lambda payload: payload["authoritative_evidence"].update(
            {"integrity_mutation": {"value": "changed"}}
        ),
    ],
)
def test_bridge_recomputes_the_complete_local_root_receipt_digest(tmp_path, mutate):
    case = _local_source_case(tmp_path, mutate)

    with pytest.raises(ReleaseGateError) as error:
        _verify_bridge(case)

    assert error.value.code == "GA_EVIDENCE_BRIDGE_MISMATCH"


@pytest.mark.parametrize(
    ("section", "mutate"),
    [
        (
            "production_activation",
            lambda payload: payload["execute_outcome"].update(
                {"unbound_field": "tampered"}
            ),
        ),
        (
            "default_writer",
            lambda payload: payload.update({"unbound_field": "tampered"}),
        ),
        (
            "production_canary",
            lambda payload: payload["node_keys"].append("unbound-node"),
        ),
    ],
)
def test_bridge_recomputes_each_producer_receipt_digest(
    tmp_path, section, mutate
):
    case = _producer_source_case(tmp_path, section, mutate)

    with pytest.raises(ReleaseGateError) as error:
        _verify_bridge(case)

    assert error.value.code == "GA_EVIDENCE_BRIDGE_MISMATCH"


def test_bridge_pre_tag_accepts_only_the_canonical_local_verification_mode(tmp_path):
    case = list(_case(tmp_path))
    case[5] = replace(case[5], verification_mode="local-only")

    with pytest.raises(ReleaseGateError) as error:
        _verify_bridge(tuple(case))

    assert error.value.code == "GA_EVIDENCE_BRIDGE_VERIFICATION_INVALID"


def test_loader_retains_historical_local_verification_mode_alias(tmp_path):
    manifest = {
        "schema": "gwo-c1-local-verification.v2",
        "mode": "local-only",
        "subject_sha": "3" * 40,
        "subject_tree": "a" * 40,
        "workflow_count": 0,
        "final_outcome": "pass",
        "commands": [
            {
                "name": "full",
                "exit_code": 0,
                "status": "passed",
                "passed": 42,
                "summary": "42 passed in 1.0s",
            }
        ],
    }
    path = tmp_path / "local-verification.json"
    path.write_bytes(verifier.canonical_json_bytes(manifest))

    readback = verifier.load_local_verification(path)

    assert readback.verification_mode == "local-only"


@pytest.mark.parametrize("field", ("hosted_ci", "pr", "remote", "publication"))
def test_bridge_rejects_hosted_or_remote_fields_recursively_in_local_root_source(
    tmp_path, field
):
    def mutate(payload):
        payload["authoritative_evidence"] = {
            "nested": [{"deeper": {field: {"value": "forbidden"}}}]
        }

    case = _local_source_case(tmp_path, mutate)

    with pytest.raises(ReleaseGateError) as error:
        _verify_bridge(case)

    assert error.value.code == "GA_LOCAL_VERIFICATION_HOSTED_EVIDENCE"


def test_bridge_local_field_rejection_does_not_apply_to_activation_run_id(tmp_path):
    record, canary, activation, admission, bridge, local, git = _case(tmp_path)

    receipt = verifier.verify_pre_tag(
        record,
        main_sha=git.current_origin_main_sha(),
        canary=canary,
        activation=activation,
        admission=admission,
        git=git,
        local_verification=local,
        evidence_bridge=bridge,
    )

    assert receipt.activation_id == record.activation_id


def test_bridge_rejects_a_symlink_ancestor_for_a_source_file(tmp_path):
    record, canary, activation, admission, bridge, local, git = _case(tmp_path)
    raw = verifier.canonical_json_bytes(canary)
    real_dir = tmp_path / "real-source"
    real_dir.mkdir()
    target = real_dir / "root-canary-acceptance.json"
    target.write_bytes(raw)
    linked_dir = tmp_path / "linked-source"
    try:
        linked_dir.symlink_to(real_dir, target_is_directory=True)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"source symlinks are unavailable: {error}")

    bridge_payload = bridge.to_mapping()
    bridge_payload["local_root_canary"] = {
        **bridge_payload["local_root_canary"],
        "source_file": str(linked_dir / target.name),
        "source_file_sha256": hashlib.sha256(raw).hexdigest(),
    }
    linked_bridge = GaEvidenceBridge.from_mapping(_rehash_bridge(bridge_payload))

    with pytest.raises(ReleaseGateError) as error:
        verifier.verify_pre_tag(
            record,
            main_sha=git.current_origin_main_sha(),
            canary=canary,
            activation=activation,
            admission=admission,
            git=git,
            local_verification=local,
            evidence_bridge=linked_bridge,
        )

    assert error.value.code == "GA_EVIDENCE_BRIDGE_MISMATCH"


def test_load_local_verification_rejects_noncanonical_json(tmp_path):
    manifest = {
        "schema": "gwo-c1-local-verification.v2",
        "mode": "local-only-v1",
        "subject_sha": "3" * 40,
        "subject_tree": "a" * 40,
        "workflow_count": 0,
        "actions_enabled": False,
        "final_outcome": "pass",
        "commands": [
            {
                "name": "full",
                "arguments": ["-m", "pytest", "-q"],
                "exit_code": 0,
                "status": "passed",
                "passed": 42,
                "summary": "42 passed in 1.0s",
            }
        ],
    }
    path = tmp_path / "local-verification.json"
    path.write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))

    with pytest.raises(ReleaseGateError) as error:
        verifier.load_local_verification(path)

    assert error.value.code == "GA_LOCAL_VERIFICATION_UNREADABLE"


def test_legacy_pre_tag_rejects_a_non_v8_writer_generation():
    record, canary, activation, admission, ci, git = _canonical_pre_tag_case()
    generation = "legacy-not-v8"

    activation_payload = vars(activation).copy()
    activation_payload.pop("receipt_digest")
    activation_payload["writer_generation"] = generation
    activation = SimpleNamespace(
        **activation_payload, receipt_digest=verifier.digest_value(activation_payload)
    )

    admission_payload = vars(admission).copy()
    admission_payload.pop("receipt_digest")
    admission_payload["writer_generation"] = generation
    admission = SimpleNamespace(
        **admission_payload, receipt_digest=verifier.digest_value(admission_payload)
    )
    record = replace(
        record,
        activation_receipt_digest=activation.receipt_digest,
        default_writer_receipt_digest=admission.receipt_digest,
        writer_generation=generation,
    )

    with pytest.raises(ReleaseGateError) as error:
        verifier.verify_pre_tag(
            record,
            main_sha=ci.head_sha,
            canary=canary,
            activation=activation,
            admission=admission,
            ci=ci,
            git=git,
        )

    assert error.value.code == "GA_ACTIVATION_READBACK_INVALID"


@pytest.mark.parametrize("generation", ("v8", "v8-generation-1"))
def test_legacy_pre_tag_accepts_historical_and_real_v8_generations(generation):
    record, canary, activation, admission, ci, git = _canonical_pre_tag_case()

    activation_payload = vars(activation).copy()
    activation_payload.pop("receipt_digest")
    activation_payload["writer_generation"] = generation
    activation = SimpleNamespace(
        **activation_payload, receipt_digest=verifier.digest_value(activation_payload)
    )

    admission_payload = vars(admission).copy()
    admission_payload.pop("receipt_digest")
    admission_payload["writer_generation"] = generation
    admission = SimpleNamespace(
        **admission_payload, receipt_digest=verifier.digest_value(admission_payload)
    )
    record = replace(
        record,
        activation_receipt_digest=activation.receipt_digest,
        default_writer_receipt_digest=admission.receipt_digest,
        writer_generation=generation,
    )

    receipt = verifier.verify_pre_tag(
        record,
        main_sha=ci.head_sha,
        canary=canary,
        activation=activation,
        admission=admission,
        ci=ci,
        git=git,
    )

    assert receipt.writer_generation == generation
