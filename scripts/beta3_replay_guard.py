"""Replay exact current-main cutover Guard inputs without external reads."""

from __future__ import annotations

from dataclasses import dataclass

from beta3_bootstrap_model import (
    AttestedCutoverBundle,
    BootstrapError,
    FrozenReadPort,
    digest_value,
)
from gwo_v8.cutover_guard import (
    CutoverBlocker,
    CutoverGuardReceipt,
    CutoverGuardReport,
    CutoverGuardSources,
    CutoverReadbackBundle,
    CutoverSubject,
    GuardCheck,
)
from gwo_v8.plan_control_host import install_cutover_guard


@dataclass(frozen=True)
class ReplayResult:
    report: CutoverGuardReport
    subject: CutoverSubject
    readback_bundle: CutoverReadbackBundle
    attestation_digest: str


_GUARD_SCHEMA = "gwo.cutover-guard.v1"
_RECEIPT_SCHEMA = "gwo.cutover-guard-receipt.v1"
_READBACK_BUNDLE_SCHEMA = "gwo.cutover-readback-bundle.v1"
_CHECK_IDS = (
    "source_writer",
    "legacy_quiescence",
    "durable_state",
    "writer_and_lease",
    "production_paths",
    "runtime_configuration",
    "package_installation",
)
_READBACKS = (
    "legacy",
    "durable_state",
    "writer_fence",
    "ownership",
    "compatibility",
    "runtime",
    "packages",
)


def _invalid(detail: str) -> None:
    raise BootstrapError("LIVE_GUARD_INVALID", detail)


def _valid_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and value == value.lower()
        and all(char in "0123456789abcdef" for char in value)
    )


def _readback_values(bundle: AttestedCutoverBundle) -> tuple[tuple[str, object], ...]:
    return (
        ("legacy", bundle.legacy),
        ("durable_state", bundle.durable_state),
        ("writer_fence", bundle.writer_fence),
        ("ownership", bundle.ownership),
        ("compatibility", bundle.compatibility),
        ("runtime", bundle.runtime),
        ("packages", bundle.packages),
    )


def _observed_digest(value: object) -> str:
    return digest_value(value.canonical())


def _check_expectations(
    bundle: AttestedCutoverBundle,
) -> tuple[tuple[str, bool, str, str, object], ...]:
    subject = bundle.subject
    legacy = bundle.legacy
    durable = bundle.durable_state
    writer = bundle.writer_fence
    ownership = bundle.ownership
    compatibility = bundle.compatibility
    runtime = bundle.runtime
    packages = bundle.packages

    source_writer_ok = (
        writer.repository == subject.repository
        and writer.writer_generation == subject.source_writer_generation == "v6.1"
        and writer.authority_state == "authoritative"
        and writer.activation_id is None
        and _valid_digest(writer.control_ref_digest)
        and _valid_digest(writer.readback_digest)
    )

    legacy_state = legacy.v2_execution_state
    if legacy_state == "running":
        legacy_code = "CUTOVER_V2_ACTIVE"
    elif legacy_state in {"none", "terminal", "quiescent_read_only"}:
        legacy_code = "CUTOVER_LEGACY_NOT_QUIESCENT"
    else:
        legacy_code = "CUTOVER_LEGACY_STATE_INVALID"
    legacy_ok = (
        legacy.repository == subject.repository
        and legacy.writer_generation == subject.source_writer_generation
        and legacy.authority_state == "authoritative_quiescent"
        and not legacy.active_dispatches
        and not legacy.active_workers
        and legacy.integration_lease_owner is None
        and legacy_state in {"none", "terminal", "quiescent_read_only"}
        and legacy.original_decoder_readable
        and _valid_digest(legacy.durable_state_digest)
    )

    durable_ok = (
        durable.repository == subject.repository
        and durable.generation_id == subject.store_generation
        and durable.state_schema == "gwo.v8.store.v1"
        and durable.compatible is True
        and not durable.active_plan_digests
        and not durable.pending_activation_ids
        and not durable.predecessor_identity_refs
        and _valid_digest(durable.readback_digest)
    )

    ownership_ok = (
        ownership.repository == subject.repository
        and not ownership.active_admissions
        and not ownership.active_attempts
        and ownership.integration_lease_owner is None
        and not ownership.runtime_resource_refs
        and _valid_digest(ownership.readback_digest)
    )

    path_reachable = bool(
        compatibility.reachable_v2_projection_refs
        or compatibility.reachable_v3_compatibility_refs
        or compatibility.reachable_legacy_writer_refs
    )
    path_shape_ok = (
        compatibility.repository == subject.repository
        and compatibility.source_commit == subject.source_commit
        and compatibility.source_tree_digest == subject.source_tree_digest
        and compatibility.audit_version == "gwo.cutover-path-audit.v1"
        and compatibility.proven_unreachable_refs
        == tuple(sorted(subject.forbidden_production_refs))
        and _valid_digest(compatibility.readback_digest)
    )
    production_paths_ok = path_shape_ok and not path_reachable

    runtime_ok = (
        runtime.repository == subject.repository
        and tuple(item.selector for item in runtime.selectors)
        == subject.required_runtime_selectors
        and all(
            _valid_digest(item.profile_digest)
            and (
                item.fallback_profile_digest is None
                or _valid_digest(item.fallback_profile_digest)
            )
            for item in runtime.selectors
        )
        and _valid_digest(runtime.configuration_digest)
        and not runtime.provider_action_refs
        and not runtime.persistence_write_refs
        and _valid_digest(runtime.readback_digest)
    )

    expected_source = tuple(sorted((name, None) for name in subject.package_names))
    expected_installed = tuple(
        sorted(
            (name, surface)
            for surface in subject.install_surfaces
            for name in subject.package_names
        )
    )
    actual_source = tuple(
        sorted(
            (item.package_name, item.install_surface)
            for item in packages.source_packages
        )
    )
    actual_installed = tuple(
        sorted(
            (item.package_name, item.install_surface)
            for item in packages.installed_packages
        )
    )
    source_by_name = {item.package_name: item for item in packages.source_packages}
    installed_by_key = {
        (item.package_name, item.install_surface): item
        for item in packages.installed_packages
    }
    package_ok = (
        actual_source == expected_source
        and actual_installed == expected_installed
        and not packages.drift
        and all(
            name in source_by_name
            and (name, surface) in installed_by_key
            and source_by_name[name].version == "8.0.0"
            and _valid_digest(source_by_name[name].content_digest)
            and _valid_digest(source_by_name[name].manifest_content_digest)
            and installed_by_key[(name, surface)].version == "8.0.0"
            and installed_by_key[(name, surface)].content_digest
            == source_by_name[name].content_digest
            and installed_by_key[(name, surface)].manifest_content_digest
            == source_by_name[name].manifest_content_digest
            for name in subject.package_names
            for surface in subject.install_surfaces
        )
        and _valid_digest(packages.readback_digest)
    )

    return (
        (
            "source_writer",
            source_writer_ok,
            "CUTOVER_SOURCE_WRITER_INVALID",
            "the V6.1 writer fence is not authoritative and read-back valid",
            writer,
        ),
        (
            "legacy_quiescence",
            legacy_ok,
            legacy_code,
            "V6.1 has active work, an invalid V2 state, or an unreadable decoder",
            legacy,
        ),
        (
            "durable_state",
            durable_ok,
            "CUTOVER_DURABLE_STATE_INVALID",
            "the fresh V8 store read-back is incompatible or contains state",
            durable,
        ),
        (
            "writer_and_lease",
            ownership_ok,
            "CUTOVER_WRITER_OR_LEASE_UNAVAILABLE",
            "a Worker admission, attempt, lease, or Runtime resource is active",
            ownership,
        ),
        (
            "production_paths",
            production_paths_ok,
            (
                "CUTOVER_COMPATIBILITY_PATH_REACHABLE"
                if path_reachable
                else "CUTOVER_COMPATIBILITY_AUDIT_INVALID"
            ),
            "a forbidden predecessor path is reachable or its audit is invalid",
            compatibility,
        ),
        (
            "runtime_configuration",
            runtime_ok,
            "CUTOVER_RUNTIME_CONFIGURATION_INVALID",
            "required Runtime selector identities are incomplete or effectful",
            runtime,
        ),
        (
            "package_installation",
            package_ok,
            "CUTOVER_PACKAGE_INVALID",
            "source or installed Skill package identity has drifted",
            packages,
        ),
    )


def _expected_blockers(
    expectations: tuple[tuple[str, bool, str, str, object], ...],
) -> tuple[CutoverBlocker, ...]:
    blockers = [
        CutoverBlocker(
            code=code,
            check_id=check_id,
            observed_digest=_observed_digest(value),
            detail=detail,
        )
        for check_id, passed, code, detail, value in expectations
        if not passed
    ]
    return tuple(
        sorted(blockers, key=lambda item: (item.check_id, item.code, item.detail))
    )


def _validate_readback_bundle(
    bundle: AttestedCutoverBundle,
    readback_bundle: object,
) -> None:
    if type(readback_bundle) is not CutoverReadbackBundle:
        _invalid("Guard replay did not return the exact readback bundle type")
    if readback_bundle.schema != _READBACK_BUNDLE_SCHEMA:
        _invalid("Guard replay returned the wrong readback bundle schema")
    if readback_bundle.subject != bundle.subject:
        _invalid("Guard replay returned a different subject")
    for name in _READBACKS:
        if getattr(readback_bundle, name) != getattr(bundle, name):
            _invalid(f"Guard replay returned a different {name} readback")


def _validate_report(
    bundle: AttestedCutoverBundle,
    report: object,
    readback_bundle: object,
) -> None:
    _validate_readback_bundle(bundle, readback_bundle)
    if type(report) is not CutoverGuardReport:
        _invalid("Guard replay did not return the exact report type")

    subject_digest = digest_value(bundle.subject.canonical())
    readback_digest = digest_value(
        {name: value.canonical() for name, value in _readback_values(bundle)}
    )
    if report.schema != _GUARD_SCHEMA:
        _invalid("Guard report schema is not exact")
    if report.repository != bundle.subject.repository:
        _invalid("Guard report repository is not bound to the subject")
    if report.subject_digest != subject_digest:
        _invalid("Guard report subject digest is not exact")
    if report.readback_digest != readback_digest:
        _invalid("Guard report readback digest is not exact")

    expectations = _check_expectations(bundle)
    expected_checks = tuple(
        GuardCheck(
            check_id=check_id,
            passed=passed,
            observed_digest=_observed_digest(value),
        )
        for check_id, passed, _code, _detail, value in expectations
    )
    if type(report.checks) is not tuple or any(
        type(value) is not GuardCheck for value in report.checks
    ):
        _invalid("Guard report checks are not exact")
    if tuple(value.check_id for value in report.checks) != _CHECK_IDS:
        _invalid("Guard report check order is not exact")
    if report.checks != expected_checks:
        _invalid("Guard report checks do not match the attested readbacks")

    expected_blockers = _expected_blockers(expectations)
    if type(report.blockers) is not tuple or any(
        type(value) is not CutoverBlocker for value in report.blockers
    ):
        _invalid("Guard report blockers are not exact")
    if report.blockers != expected_blockers:
        _invalid("Guard report blockers do not match the attested readbacks")

    expected_decision = "GO" if not expected_blockers else "NO_GO"
    if report.decision != expected_decision:
        _invalid("Guard report decision does not match its checks")
    if expected_decision == "NO_GO":
        if report.receipt is not None:
            _invalid("Guard report has a receipt for NO_GO")
        return

    if type(report.receipt) is not CutoverGuardReceipt:
        _invalid("Guard report GO receipt is not exact")
    receipt = report.receipt
    expected_receipt = {
        "schema": _RECEIPT_SCHEMA,
        "repository": bundle.subject.repository,
        "subject_digest": subject_digest,
        "readback_digest": readback_digest,
        "source_writer_generation": bundle.subject.source_writer_generation,
        "target_writer_generation": bundle.subject.target_writer_generation,
        "store_generation": bundle.subject.store_generation,
        "writer_control_ref_digest": bundle.writer_fence.control_ref_digest,
        "runtime_configuration_digest": bundle.runtime.configuration_digest,
        "compatibility_audit_digest": bundle.compatibility.readback_digest,
        "package_readback_digest": bundle.packages.readback_digest,
    }
    if receipt.canonical_without_digest() != expected_receipt:
        _invalid("Guard receipt does not match the attested readbacks")
    if receipt.receipt_digest != digest_value(expected_receipt):
        _invalid("Guard receipt digest is not exact")


def evaluate_attested_bundle(bundle: AttestedCutoverBundle) -> ReplayResult:
    if type(bundle) is not AttestedCutoverBundle:
        raise BootstrapError(
            "ATTESTATION_INVALID",
            "replay requires one exact AttestedCutoverBundle",
        )
    bundle.validate()
    try:
        sources = CutoverGuardSources(
            legacy=FrozenReadPort(
                bundle.legacy,
                expected_args=(bundle.subject.repository,),
            ),
            durable_state=FrozenReadPort(
                bundle.durable_state,
                expected_args=(bundle.subject.repository,),
            ),
            writer_fence=FrozenReadPort(
                bundle.writer_fence,
                expected_args=(bundle.subject.repository,),
            ),
            ownership=FrozenReadPort(
                bundle.ownership,
                expected_args=(bundle.subject.repository,),
            ),
            compatibility=FrozenReadPort(
                bundle.compatibility,
                expected_args=(bundle.subject,),
            ),
            runtime=FrozenReadPort(
                bundle.runtime,
                expected_args=(
                    bundle.subject.repository,
                    bundle.subject.required_runtime_selectors,
                ),
            ),
            packages=FrozenReadPort(
                bundle.packages,
                expected_args=(bundle.subject,),
            ),
        )
        host = install_cutover_guard(sources=sources)
        report = host.check(bundle.subject)
        readback_bundle = bundle.cutover_bundle()
        _validate_report(bundle, report, readback_bundle)
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError(
            "LIVE_GUARD_INVALID",
            "exact-main Guard replay failed",
        ) from error
    return ReplayResult(
        report=report,
        subject=bundle.subject,
        readback_bundle=readback_bundle,
        attestation_digest=bundle.attestation_digest,
    )
