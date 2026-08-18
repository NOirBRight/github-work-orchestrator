from __future__ import annotations

import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.verify_v8_ga_release as verifier
from scripts.verify_v8_ga_release import (
    GaEvidenceBridge,
    GaReleaseRecord,
    LocalVerificationReadback,
    ReleaseGateError,
    canonical_json_bytes,
    digest_value,
    write_ga_release_record,
    verify_pre_tag,
)


REPOSITORY = "NOirBRight/github-work-orchestrator"
CANARY_REPOSITORY = "NOirBRight/gwo-v8-canary"
MAIN_SHA = "f81994db1bee226cd6ca429e79c9b1cdf6d02897"
MAIN_TREE_SHA = "5c97df0ecd0a267f69e80de92d4325f3a6f86743"
FINAL_MAIN_SHA = "a" * 40
FINAL_MAIN_TREE_SHA = "b" * 40
FINAL_RELEASE_SUBJECT_DIGEST = "d" * 64
EVIDENCE_BASE_SHA = "1" * 40
LOCAL_TARGET_SHA = "d31d5787df8ff53f081ed45df42389ef2e505ffb"
LOCAL_RECEIPT_DIGEST = (
    "ea642b5606efc10adaf3671174b10e3df2f1a5f2dfc8b60a86b251db5845c938"
)
PACKAGE_DIGEST = "2533a3e5f22cc0c5e8bf2e7cd7114f33f2895d394da3f0ab69a9742205069f30"
LOCAL_CAMPAIGN = "campaign:fd16e735a23425ee5071e881"
LOCAL_ACTIVATION_ID = LOCAL_CAMPAIGN
PRODUCTION_ACTIVATION_ID = "activation:47895d07122a3d9827ecdf63"
PRODUCTION_RECORD_ID = "writer-transition:ce14291c00b0c5bfe7251729"
CONTROL_REF_SHA = "5d463d2ecd3e98644fa72dce01326bd553ecbb39"
PLAN_DIGEST = "bb4c0848982c574cdce2d50241701b5920fc93caedb78a04fddcf6e5d0ad661a"
PRODUCTION_GENERATION = "v8-generation-1"
PREVIOUS_WRITER_GENERATION = PRODUCTION_GENERATION
GUARD_SOURCE_WRITER_GENERATION = "v6.1"
ACTIVATION_RECEIPT_DIGEST = (
    "98eb2d5f6a75f0e12b290836c72939c44bd03052f1d28257cae410ed30d25c06"
)
DEFAULT_WRITER_RECEIPT_DIGEST = (
    "42b595a7d4a93146200e2eaab629d804f1c0b9e383e7c7233af495e89a0c3084"
)


EVIDENCE_ROOT = Path(r"D:\gwo-release-evidence\2026-08-19-gwo-v8-ga-production-cutover")


def _load_evidence(name: str) -> dict[str, object]:
    path = EVIDENCE_ROOT / name
    if not path.exists():
        pytest.fail(f"real derived evidence is required: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _case(tmp_path: Path):
    canary = _load_evidence("root-canary-acceptance.json")
    activation = _load_evidence("production-activation-readback.json")
    admission = _load_evidence("default-writer-readback.json")
    bridge_payload = _load_evidence("ga-evidence-bridge.json")
    assert canary["schema"] == "gwo-v8-root-canary-acceptance.v2"
    assert canary["repository"] == REPOSITORY
    assert canary["campaign_key"] == LOCAL_CAMPAIGN
    assert canary["activation_id"] == LOCAL_ACTIVATION_ID
    assert canary["writer_generation"] == "writer:local"
    assert canary["canary_target_sha"] == LOCAL_TARGET_SHA
    assert canary["receipt_digest"] == LOCAL_RECEIPT_DIGEST
    assert activation["receipt_digest"] == ACTIVATION_RECEIPT_DIGEST
    assert activation["control_ref"]["commit_sha"] == CONTROL_REF_SHA
    assert activation["active_plan"]["active_plan_digest"] == PLAN_DIGEST
    assert (
        activation["transition_record"]["previous_writer_generation"]
        == PREVIOUS_WRITER_GENERATION
    )
    assert admission["receipt_digest"] == DEFAULT_WRITER_RECEIPT_DIGEST
    assert admission["previous_writer_generation"] == PREVIOUS_WRITER_GENERATION
    assert (
        bridge_payload["bridge_digest"]
        == "30962c93b38ae16eaaa5dd0fdb805fdd22fa4108fd6374e37abafad6cfb2dea7"
    )
    # Keep the immutable V8 transition lineage separate from the Guard's V6.1
    # source-writer proof.
    bridge_payload["activation_release_subject"] = bridge_payload.pop("release_subject")
    bridge_payload["release_subject"] = {
        "merged_main_sha": MAIN_SHA,
        "merged_main_tree": MAIN_TREE_SHA,
        "release_subject_digest": bridge_payload["activation_release_subject"][
            "release_subject_digest"
        ],
    }
    default_writer_raw = canonical_json_bytes(admission)
    bridge_payload["default_writer"] = {
        **bridge_payload["default_writer"],
        "previous_writer_generation": admission["previous_writer_generation"],
        "source_file": str(tmp_path / "default-writer-readback.json"),
        "source_file_sha256": hashlib.sha256(default_writer_raw).hexdigest(),
    }
    (tmp_path / "default-writer-readback.json").write_bytes(default_writer_raw)
    bridge_payload = _rehash_bridge(bridge_payload)
    bridge = GaEvidenceBridge.from_mapping(bridge_payload)
    assert bridge.local_root_canary["activation_id"] == LOCAL_ACTIVATION_ID
    assert (
        bridge.local_root_canary["activation_id"]
        != bridge.production_activation["activation_id"]
    )
    assert bridge.writer_family == "v8"
    assert (
        bridge.production_activation["previous_writer_generation"]
        == PREVIOUS_WRITER_GENERATION
    )
    assert bridge.default_writer["previous_writer_generation"] == PREVIOUS_WRITER_GENERATION
    assert bridge.activation_release_subject["merged_main_sha"] == MAIN_SHA
    record = GaReleaseRecord(
        version="8.0.0",
        repository=REPOSITORY,
        evidence_base_sha=EVIDENCE_BASE_SHA,
        canary_target_sha=LOCAL_TARGET_SHA,
        canary_receipt_digest=canary["receipt_digest"],
        activation_receipt_digest=activation["receipt_digest"],
        default_writer_receipt_digest=admission["receipt_digest"],
        campaign_key=LOCAL_CAMPAIGN,
        activation_id=PRODUCTION_ACTIVATION_ID,
        writer_generation=PRODUCTION_GENERATION,
    )
    local_verification = LocalVerificationReadback(
        schema="gwo-v8-c2-local-gate.v2",
        verification_mode="local-only-v1",
        subject_sha=MAIN_SHA,
        subject_tree_sha=MAIN_TREE_SHA,
        pytest_pass_count=3174,
        manifest_sha256="c" * 64,
    )
    git = SimpleNamespace(
        repository=REPOSITORY,
        current_origin_main_sha=lambda: MAIN_SHA,
        tree_sha=lambda commit: MAIN_TREE_SHA,
        is_ancestor=lambda ancestor, descendant: ancestor == EVIDENCE_BASE_SHA,
        changed_paths=lambda base, descendant: (
            "CHANGELOG.md",
            "docs/e2e/gwo-v8-root-canary.md",
            "docs/releases/v8.0.0.md",
        ),
    )
    return record, canary, activation, admission, bridge, local_verification, git


def _rehash_bridge(payload: dict[str, object]) -> dict[str, object]:
    body = {key: value for key, value in payload.items() if key != "bridge_digest"}
    return body | {"bridge_digest": digest_value(body)}


def _bridge_with_source(
    tmp_path: Path,
    bridge: GaEvidenceBridge,
    section: str,
    payload: dict[str, object],
    filename: str,
) -> GaEvidenceBridge:
    raw = canonical_json_bytes(payload)
    source = tmp_path / filename
    source.write_bytes(raw)
    bridge_payload = bridge.to_mapping()
    bridge_payload[section] = {
        **bridge_payload[section],
        "source_file": str(source),
        "source_file_sha256": hashlib.sha256(raw).hexdigest(),
    }
    return GaEvidenceBridge.from_mapping(_rehash_bridge(bridge_payload))


def test_bridge_accepts_local_root_and_external_production_canary_with_disjoint_identities(
    tmp_path,
):
    record, canary, activation, admission, bridge, local_verification, git = _case(
        tmp_path
    )

    receipt = verify_pre_tag(
        record,
        main_sha=MAIN_SHA,
        canary=canary,
        activation=activation,
        admission=admission,
        git=git,
        local_verification=local_verification,
        evidence_bridge=bridge,
    )

    assert receipt.campaign_key == LOCAL_CAMPAIGN
    assert receipt.activation_id == PRODUCTION_ACTIVATION_ID
    assert receipt.writer_generation == PRODUCTION_GENERATION


def test_bridge_rejects_a_production_canary_package_mismatch(tmp_path):
    record, canary, activation, admission, bridge, local_verification, git = _case(
        tmp_path
    )
    wrong_digest = "d" * 64
    wrong_payload = bridge.to_mapping()
    wrong_payload["production_canary"] = {
        **wrong_payload["production_canary"],
        "package_digest": wrong_digest,
        "manifest_ref": f"github://canary-manifest/{wrong_digest}",
    }
    wrong_payload = _rehash_bridge(wrong_payload)
    mismatched = GaEvidenceBridge.from_mapping(wrong_payload)

    with pytest.raises(ReleaseGateError) as error:
        verify_pre_tag(
            record,
            main_sha=MAIN_SHA,
            canary=canary,
            activation=activation,
            admission=admission,
            git=git,
            local_verification=local_verification,
            evidence_bridge=mismatched,
        )

    assert error.value.code == "GA_EVIDENCE_BRIDGE_MISMATCH"


def test_bridge_rejects_a_non_v8_generation_before_readback_is_accepted(tmp_path):
    record, canary, activation, admission, bridge, local_verification, git = _case(
        tmp_path
    )
    wrong_payload = bridge.to_mapping()
    wrong_payload["production_activation"] = {
        **wrong_payload["production_activation"],
        "writer_generation": "v8-generation-2",
    }
    wrong_payload = _rehash_bridge(wrong_payload)

    with pytest.raises(ReleaseGateError) as error:
        GaEvidenceBridge.from_mapping(wrong_payload)

    assert error.value.code == "GA_EVIDENCE_BRIDGE_WRITER_INVALID"


@pytest.mark.parametrize("section", ("production_activation", "default_writer"))
def test_bridge_rejects_a_non_v8_previous_writer_generation(tmp_path, section):
    _record, _canary, _activation, _admission, bridge, _local_verification, _git = _case(
        tmp_path
    )
    wrong_payload = bridge.to_mapping()
    wrong_payload[section] = {
        **wrong_payload[section],
        "previous_writer_generation": GUARD_SOURCE_WRITER_GENERATION,
    }
    wrong_payload = _rehash_bridge(wrong_payload)

    with pytest.raises(ReleaseGateError) as error:
        GaEvidenceBridge.from_mapping(wrong_payload)

    assert error.value.code == "GA_EVIDENCE_BRIDGE_WRITER_INVALID"


@pytest.mark.parametrize("section", ("production_activation", "default_writer"))
def test_bridge_rejects_missing_previous_writer_generation(tmp_path, section):
    _record, _canary, _activation, _admission, bridge, _local_verification, _git = _case(
        tmp_path
    )
    wrong_payload = bridge.to_mapping()
    wrong_payload[section] = {
        key: value
        for key, value in wrong_payload[section].items()
        if key != "previous_writer_generation"
    }
    wrong_payload = _rehash_bridge(wrong_payload)

    with pytest.raises(ReleaseGateError) as error:
        GaEvidenceBridge.from_mapping(wrong_payload)

    assert error.value.code == "GA_EVIDENCE_BRIDGE_FIELDS_INVALID"


@pytest.mark.parametrize("source_generation", (PRODUCTION_GENERATION, None))
def test_bridge_rejects_guard_source_generation_not_v6_1(tmp_path, source_generation):
    record, canary, activation, admission, bridge, local_verification, git = _case(
        tmp_path
    )
    wrong_activation = {
        **activation,
        "guard_receipt": {
            **activation["guard_receipt"],
            "source_writer_generation": source_generation,
        },
    }
    wrong_bridge = _bridge_with_source(
        tmp_path,
        bridge,
        "production_activation",
        wrong_activation,
        "production-activation-readback.json",
    )

    with pytest.raises(ReleaseGateError) as error:
        verify_pre_tag(
            record,
            main_sha=MAIN_SHA,
            canary=canary,
            activation=wrong_activation,
            admission=admission,
            git=git,
            local_verification=local_verification,
            evidence_bridge=wrong_bridge,
        )

    assert error.value.code == "GA_EVIDENCE_BRIDGE_MISMATCH"


@pytest.mark.parametrize(
    ("section", "missing"),
    (
        ("production_activation", False),
        ("production_activation", True),
        ("default_writer", False),
        ("default_writer", True),
    ),
)
def test_bridge_rejects_readback_previous_lineage_mismatch(tmp_path, section, missing):
    record, canary, activation, admission, bridge, local_verification, git = _case(
        tmp_path
    )
    if section == "production_activation":
        transition = {
            **activation["transition_record"],
            "previous_writer_generation": GUARD_SOURCE_WRITER_GENERATION,
        }
        if missing:
            transition.pop("previous_writer_generation")
        wrong_activation = {**activation, "transition_record": transition}
        wrong_admission = admission
        wrong_bridge = _bridge_with_source(
            tmp_path,
            bridge,
            section,
            wrong_activation,
            "production-activation-readback.json",
        )
    else:
        wrong_activation = activation
        wrong_admission = dict(admission)
        if missing:
            wrong_admission.pop("previous_writer_generation")
        else:
            wrong_admission["previous_writer_generation"] = GUARD_SOURCE_WRITER_GENERATION
        wrong_bridge = _bridge_with_source(
            tmp_path,
            bridge,
            section,
            wrong_admission,
            "default-writer-readback.json",
        )

    with pytest.raises(ReleaseGateError) as error:
        verify_pre_tag(
            record,
            main_sha=MAIN_SHA,
            canary=canary,
            activation=wrong_activation,
            admission=wrong_admission,
            git=git,
            local_verification=local_verification,
            evidence_bridge=wrong_bridge,
        )

    assert error.value.code == "GA_EVIDENCE_BRIDGE_MISMATCH"


def test_bridge_keeps_activation_subject_when_final_ga_subject_moves(tmp_path):
    record, canary, activation, admission, bridge, local_verification, git = _case(
        tmp_path
    )
    moved_payload = bridge.to_mapping()
    moved_payload["release_subject"] = {
        "merged_main_sha": FINAL_MAIN_SHA,
        "merged_main_tree": FINAL_MAIN_TREE_SHA,
        "release_subject_digest": FINAL_RELEASE_SUBJECT_DIGEST,
    }
    moved_payload = _rehash_bridge(moved_payload)
    moved_bridge = GaEvidenceBridge.from_mapping(moved_payload)
    moved_local = LocalVerificationReadback(
        schema=local_verification.schema,
        verification_mode=local_verification.verification_mode,
        subject_sha=FINAL_MAIN_SHA,
        subject_tree_sha=FINAL_MAIN_TREE_SHA,
        pytest_pass_count=local_verification.pytest_pass_count,
        manifest_sha256=local_verification.manifest_sha256,
    )
    moved_git = SimpleNamespace(
        repository=REPOSITORY,
        current_origin_main_sha=lambda: FINAL_MAIN_SHA,
        tree_sha=lambda commit: FINAL_MAIN_TREE_SHA,
        is_ancestor=lambda ancestor, descendant: ancestor == EVIDENCE_BASE_SHA,
        changed_paths=lambda base, descendant: (
            "CHANGELOG.md",
            "docs/e2e/gwo-v8-root-canary.md",
            "docs/releases/v8.0.0.md",
        ),
    )

    receipt = verify_pre_tag(
        record,
        main_sha=FINAL_MAIN_SHA,
        canary=canary,
        activation=activation,
        admission=admission,
        git=moved_git,
        local_verification=moved_local,
        evidence_bridge=moved_bridge,
    )

    assert receipt.tag_candidate_sha == FINAL_MAIN_SHA
    assert moved_bridge.activation_release_subject["merged_main_sha"] == MAIN_SHA


def test_bridge_rejects_an_activation_subject_mismatch(tmp_path):
    record, canary, activation, admission, bridge, local_verification, git = _case(
        tmp_path
    )
    wrong_payload = bridge.to_mapping()
    wrong_payload["activation_release_subject"] = {
        "merged_main_sha": FINAL_MAIN_SHA,
        "merged_main_tree": FINAL_MAIN_TREE_SHA,
        "release_subject_digest": FINAL_RELEASE_SUBJECT_DIGEST,
    }
    wrong_payload = _rehash_bridge(wrong_payload)
    mismatched = GaEvidenceBridge.from_mapping(wrong_payload)

    with pytest.raises(ReleaseGateError) as error:
        verify_pre_tag(
            record,
            main_sha=MAIN_SHA,
            canary=canary,
            activation=activation,
            admission=admission,
            git=git,
            local_verification=local_verification,
            evidence_bridge=mismatched,
        )

    assert error.value.code == "GA_EVIDENCE_BRIDGE_MISMATCH"


@pytest.mark.parametrize(
    "authorization_field",
    ("merged_main_sha", "merged_main_git_tree", "release_subject_digest"),
)
def test_bridge_rejects_activation_authorization_subject_mismatch(
    tmp_path, authorization_field
):
    record, canary, activation, admission, bridge, local_verification, git = _case(
        tmp_path
    )
    wrong_values = {
        "merged_main_sha": FINAL_MAIN_SHA,
        "merged_main_git_tree": FINAL_MAIN_TREE_SHA,
        "release_subject_digest": FINAL_RELEASE_SUBJECT_DIGEST,
    }
    wrong_activation = {
        **activation,
        "authorization": {
            **activation["authorization"],
            authorization_field: wrong_values[authorization_field],
        },
    }
    wrong_bridge = _bridge_with_source(
        tmp_path,
        bridge,
        "production_activation",
        wrong_activation,
        "production-activation-readback.json",
    )

    with pytest.raises(ReleaseGateError) as error:
        verify_pre_tag(
            record,
            main_sha=MAIN_SHA,
            canary=canary,
            activation=wrong_activation,
            admission=admission,
            git=git,
            local_verification=local_verification,
            evidence_bridge=wrong_bridge,
        )

    assert error.value.code == "GA_EVIDENCE_BRIDGE_MISMATCH"


def test_bridge_rejects_a_local_root_receipt_identity_mismatch(tmp_path):
    record, canary, activation, admission, bridge, local_verification, git = _case(
        tmp_path
    )
    wrong_payload = bridge.to_mapping()
    wrong_payload["local_root_canary"] = {
        **wrong_payload["local_root_canary"],
        "campaign_key": "campaign:foreign-local-root",
    }
    wrong_payload = _rehash_bridge(wrong_payload)
    mismatched = GaEvidenceBridge.from_mapping(wrong_payload)

    with pytest.raises(ReleaseGateError) as error:
        verify_pre_tag(
            record,
            main_sha=MAIN_SHA,
            canary=canary,
            activation=activation,
            admission=admission,
            git=git,
            local_verification=local_verification,
            evidence_bridge=mismatched,
        )

    assert error.value.code == "GA_EVIDENCE_BRIDGE_MISMATCH"


def test_bridge_cli_consumes_real_evidence_sources(tmp_path, monkeypatch):
    record, canary, activation, admission, bridge, _local_verification, _git = _case(
        tmp_path
    )
    record_path = write_ga_release_record(tmp_path / "record.json", record)
    canary_path = tmp_path / "root-canary-acceptance.json"
    activation_path = tmp_path / "production-activation-readback.json"
    admission_path = tmp_path / "default-writer-readback.json"
    bridge_path = tmp_path / "ga-evidence-bridge.json"
    canary_path.write_bytes(canonical_json_bytes(canary))
    activation_path.write_bytes(canonical_json_bytes(activation))
    admission_path.write_bytes(canonical_json_bytes(admission))
    bridge_path.write_bytes(canonical_json_bytes(bridge.to_mapping()))

    local_path = tmp_path / "local-verification.json"
    local_path.write_bytes(
        canonical_json_bytes(
            {
                "commands": [
                    {
                        "arguments": ["-m", "pytest", "-q"],
                        "exit_code": 0,
                        "name": "full",
                        "summary": "3174 passed in 1.0s",
                    }
                ],
                "final_outcome": "pass",
                "mode": "Local Verification Only",
                "schema": "gwo-c1-local-verification.v2",
                "subject_sha": MAIN_SHA,
                "subject_tree": MAIN_TREE_SHA,
                "workflow_count": 0,
            }
        )
    )

    monkeypatch.setattr(
        verifier.GitCliReadback,
        "current_origin_main_sha",
        lambda self: MAIN_SHA,
    )
    monkeypatch.setattr(
        verifier.GitCliReadback,
        "tree_sha",
        lambda self, commit: MAIN_TREE_SHA,
    )
    monkeypatch.setattr(
        verifier.GitCliReadback,
        "is_ancestor",
        lambda self, ancestor, descendant: ancestor == EVIDENCE_BASE_SHA,
    )
    monkeypatch.setattr(
        verifier.GitCliReadback,
        "changed_paths",
        lambda self, ancestor, descendant: (
            "CHANGELOG.md",
            "docs/e2e/gwo-v8-root-canary.md",
            "docs/releases/v8.0.0.md",
        ),
    )
    monkeypatch.setattr(
        verifier.subprocess,
        "check_output",
        lambda arguments, **kwargs: (
            "https://github.com/NOirBRight/github-work-orchestrator.git\n"
            if tuple(arguments) == ("git", "remote", "get-url", "origin")
            else (_ for _ in ()).throw(AssertionError(arguments))
        ),
    )

    output = tmp_path / "receipt.json"
    assert (
        verifier.verify_main(
            [
                "--pre-tag",
                "--main-sha",
                MAIN_SHA,
                "--record",
                str(record_path),
                "--canary",
                str(canary_path),
                "--activation",
                str(activation_path),
                "--default-writer",
                str(admission_path),
                "--evidence-bridge",
                str(bridge_path),
                "--local-verification",
                str(local_path),
                "--repository",
                REPOSITORY,
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert json.loads(output.read_text(encoding="utf-8"))["activation_id"] == (
        PRODUCTION_ACTIVATION_ID
    )
