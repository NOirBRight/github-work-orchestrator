# GWO V8 C3 Beta3 Cutover Candidate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consume the verified C2 Beta2 handoff and deliver Issue #118 as a Beta3 cutover candidate: a read-only, fail-closed Guard proves the V6.1-to-V8 prerequisites and returns a digest-bound activation token, while the existing writer-generation/Activation Receipt protocol remains the only authority-transfer commit point.

**Architecture:** Start with a C2 closure/handoff gate, then add one host/release-control adapter module, cutover_guard.py (not a sixth domain deep module), whose ports expose reads only and whose evaluator never calls a repository, SQLite, GitHub, process, Runtime, or package-install writer. It returns a complete human-readable go/no-go report and an immutable preflight token; a guarded adapter at the existing WriterCutoverController boundary re-reads the same facts immediately before invoking the existing fenced activation. The Guard does not run the root Canary, enable V8 by default, create a Plan Revision, or implement rollback; those remain the #119 GA and existing compensating-transition responsibilities.

**Tech Stack:** Python 3.13, frozen dataclasses, typing.Protocol, canonical JSON/SHA-256 digests from gwo_v8._canonical, read-only pathlib/AST/package inspection, existing GitHub Contents and writer-generation readbacks, existing SQLite readbacks, pytest.

## Global Constraints

- Normative order is CONTEXT.md, accepted ADRs, docs/design/gwo-v8-lean-architecture.md, docs/design/gwo-v8-lean-stabilization-spec.md, then docs/design/gwo-v8-lean-roadmap.md.
- Issue #118 is blocked by #113, #117, #136, and #137; execution consumes their merged, read-backed Results and does not reopen, close, relabel, edit, or otherwise mutate GitHub Issues.
- The release train is fixed: Beta1 is metadata/tracker repair only with no production admission; Beta2 is the feature-complete preview after #113–#117, #137 revalidation, and production V3 composition; Beta3 is this Guard/cutover candidate with no default-writer change; GA is #119’s real four-Ticket root Canary plus durable success readback.
- Beta3 evidence and any cutover rehearsal use an isolated repository and an explicit controller call; they never change the default writer, the default host configuration, the installed package selection, or the GA release state. Only #119 may perform real public-API Canary admission and default-writer activation.
- The only public workflow operations remain start(repository, ready_refs, options?), advance(campaign_handle, wake_ref?), and inspect(campaign_handle); Guard check and token validation are host/release-control methods, not public workflow operations.
- Guard evaluation and token validation are read-only. They may call only the exact `read` methods defined below; no Guard port may expose or invoke stop, restore, drain, publish, compare_and_swap, write, CREATE, INSERT, UPDATE, DELETE, Runtime prepare, Runtime command, Runtime events, provider process creation, installation, or manifest generation.
- Every Guard prerequisite is evaluated even when another prerequisite fails. A reader exception becomes a named blocker and the remaining read ports are still attempted; a malformed or incomplete readback never becomes a pass by default.
- The Guard proves V6.1 authority and predecessor quiescence without stopping V6.1. It never interprets, resumes, projects, adopts, or writes V2 state and never transfers writer authority itself.
- The Guard has no CanaryAcceptance input and does not inspect a root Canary, Review, Candidate, Batch, PR, hosted check, or GA default flag. #119 remains the only plan for live GA admission.
- Guard receipts are ephemeral preflight facts, not PlanSpec fields, SQLite rows, GitHub records, or replacements for Activation Receipts. Activation re-runs the Guard readback and the existing writer CAS; after an Activation Receipt exists, recovery rolls forward and rollback is a separate human-authorized durable compensating transition.
- Every implementation task uses TDD in this order: write the named failing test, run it and record the expected RED, implement only the minimum behavior, run the named test and record GREEN, refactor while green, run the focused regression gate, and commit the task’s exact write set.
- Every commit that stages a path under `skills/orchestrator` runs the same readback sequence in this order: `py -3.13 scripts/sync_orchestrator.py`, `py -3.13 scripts/sync_orchestrator.py --check`, then the read-only manifest assertion `py -3.13 -c "from pathlib import Path; import json; from scripts.sync_orchestrator import expected_manifest; package=Path('skills/orchestrator'); assert json.loads((package/'.skill-package.json').read_text(encoding='utf-8')) == expected_manifest(package); print('manifest OK')"`; never hand-edit `.skill-package.json`.
- All Python commands use py -3.13. Do not use python, python3, pip, or an installer command in this plan.
- No task changes the primary checkout at D:\Workstation\github-work-orchestrator while it is used as the canonical local main; execute implementation tasks in fresh clean isolated worktrees from the validated C2 SHA 32ca2cfd85aec31d0215807bdad96c0f6d99361c.

- C3 cannot leave HOLD until D:\gwo-release-evidence\2026-08-06-gwo-v8-c2-beta2-feature-complete\state.json contains digest-valid gwo-v8-c2-closure.v1 and gwo-v8-c3-handoff.v1 records; the current empty closure and c3_handoff objects are an intentional failing predecessor gate.
- C2 is remotely merged at 32ca2cfd85aec31d0215807bdad96c0f6d99361c (tree b9947f69e36468b7244af1edf260de764c7038df, parent 879b04e67f059e1368e2e91fb74ce0900770b3d7). C3 implementation must branch from that merged tree, not from the stale c490d58 coordinator identity in the external C2 state.
- C2 Task 11 describes the meaning of “next scope” but does not freeze a JSON member name for it. Task 0 therefore does not guess or require `c3_handoff.next_scope`; it proves that meaning from the state-referenced tracker-after readback (#118 OPEN on `GWO V8 Beta3`, #119 OPEN on `GWO V8 GA`) and fails closed when that readback is absent or contradictory. The handoff’s exact, already-named boundary members remain `writer_activation_enabled`, `production_admission`, `default_writer_authority`, and an empty `activation_authority` object.
- GitHub Actions and GitHub-hosted acceptance are disabled. Repository acceptance in this plan is local-only: py -3.13 -m pytest, scripts/quick_validate.py, scripts/sync_orchestrator.py --check, package checks, and git diff --check; remote Issue reads are factual readbacks, not CI evidence.
- Use no GitHub Issue mutation, tag/Release publication, package installation, default-writer activation, production admission, or root Canary. Those effects remain outside C3 and belong to separately authorized release/GA work.
- At most five isolated Luna Max subagents may work concurrently. Source files may be developed in disjoint lanes, but skills/orchestrator/.skill-package.json, tests/test_orchestrator_package.py, final manifest synchronization, and final evidence are coordinator-owned serial write surfaces.
## Governing decisions and dependency handoff

- ADR-0034 establishes V6.1 as the actual predecessor writer, requires a fresh V8 store generation, and says Guard failure leaves V6.1 authoritative.
- ADR-0035 makes the durable writer-generation publication plus read-backed Activation Receipt the sole commit point; pending activation admits no Work Run, and post-receipt recovery rolls forward.
- ADR-0046 removes a separate mandatory Preflight and long-lived Shadow phase: the one activation operation performs a fail-closed read-only Guard first. Shadow is not a release gate.
- ADR-0055 requires PlanSpec v3 activation through exact Campaign compare-and-swap and a read-backed Activation Receipt; V2 is never projected into V3.
- ADR-0058 makes ExecutionKernel the only workflow driver and removes legacy driver/reconciliation entrypoints from the V3 production path.
- ADR-0059 makes RuntimeGateway the only semantic Runtime boundary; Guard Runtime validation is a host-only, read-only configuration probe and cannot materialize a Runtime action.
- ADR-0060 leaves the Integration Lease as the repository-global delivery fence; Guard proves it is available but does not acquire it.
- ADR-0061 keeps Runtime assignment explicit and outside PlanSpec; Guard checks the five required role selectors without placing provider/model/CLI facts in PlanSpec.
- ADR-0062 states that #118 cannot cut over before the #136 human gate and #137 late-discovery paths converge. #136/#137 tracker state is outside this plan’s write set.

## File and responsibility map

| File | Responsibility |
| --- | --- |
| Create: skills/orchestrator/scripts/gwo_v8/cutover_guard.py | Closed readback types, read-only Protocols, canonical Guard report/token, prerequisite evaluation, stale-token revalidation, AST production-path audit, and package/install readback. This is a host/release-control adapter, not a sixth domain deep module. |
| Create: tests/cutover_guard_test_support.py | Exact in-memory read ports, mutation tripwires, a valid subject/readback bundle, and an accepted Canary/compiled-plan fixture for activation tests. |
| Create: tests/test_v8_cutover_guard.py | Core RED/GREEN tests for every prerequisite class, named blockers, digest binding, failure aggregation, and zero mutation. |
| Create: tests/test_v8_cutover_guard_static.py | Production import/call-path and Skill-surface audit tests. |
| Create: tests/test_v8_cutover_guard_host.py | Runtime configuration probe and ProductionCutoverGuardHost composition tests. |
| Create: tests/test_v8_cutover_activation.py | Guard-token enforcement at the existing writer transition, pending activation fence, readback, and rollback boundaries. |
| Create: tests/test_v8_cutover_guard_cli.py | Read-only CLI JSON/exit-code and human go/no-go evidence tests. |
| Modify: skills/orchestrator/scripts/gwo_v8/transition.py | Require a fresh Guard token before the existing WriterCutoverController can execute any mutation; keep the existing Activation Receipt and rollback protocol unchanged. |
| Modify: skills/orchestrator/scripts/gwo_v8/plan_control_host.py | Expose a host-only ProductionCutoverGuardHost and `install_cutover_guard(sources: CutoverGuardSources)`; compose only read ports and never expose activation through the Guard host. |
| Modify: skills/orchestrator/scripts/gwo_v8/__init__.py | Stop exporting mutable cutover controllers and predecessor workflow drivers from the package root; leave the three public workflow functions as the only workflow surface. |
| Modify: skills/orchestrator/.skill-package.json | Regenerated content hash for every changed file under skills/orchestrator; update only by `py -3.13 scripts/sync_orchestrator.py`, never by hand. |
| Create: scripts/cutover_guard.py | Read-only human-evidence CLI; it can evaluate a canonical readback bundle and print a GO/NO-GO report but has no activation or install subcommand. |
| Modify: tests/test_orchestrator_package.py | Assert the package root does not expose predecessor workflow drivers or mutable cutover controls and that the CLI is syntax/package validated. |
| Verify-only dependency: skills/implement-gwo/SKILL.md | The preceding production-composition delivery must remove the PlanCompiler/LocalPlanPublication/Kernel.reconcile_once predecessor path; this plan audits it and reports a Guard blocker if it is still reachable, but does not duplicate that delivery’s write set. |
| Verify-only dependency: the V3 host created by the production-composition plan | The preceding production-composition delivery must expose the V3 start/advance/inspect path; this plan adds the Guard host seam and checks that the path audit reaches no predecessor writer. |

## Interfaces fixed by this plan

The following closed interfaces are the handoff between tasks. Every field is part of the canonical identity; unknown fields, missing fields, wrong exact builtin types, duplicate tuple members, and unsorted tuple members fail closed.

~~~python
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

GUARD_SCHEMA = "gwo.cutover-guard.v1"
RECEIPT_SCHEMA = "gwo.cutover-guard-receipt.v1"
EXPECTED_SOURCE_WRITER_GENERATION = "v6.1"
REQUIRED_RUNTIME_SELECTORS = (
    "coordinator",
    "worker",
    "recovery_worker",
    "review_primary",
    "review_strong",
)
EXPECTED_CHECK_IDS = (
    "source_writer",
    "legacy_quiescence",
    "durable_state",
    "writer_and_lease",
    "production_paths",
    "runtime_configuration",
    "package_installation",
)
DEFAULT_FORBIDDEN_PRODUCTION_REFS = (
    "gwo_v8.entry:ImplementGwoEntry",
    "gwo_v8.entry:ImplementGwoLauncher",
    "gwo_v8.goal_driver:GoalDriver",
    "gwo_v8.kernel:Kernel.reconcile_once",
    "gwo_v8.reconstruction:StoreReconstructor",
    "gwo_v8.runtime:PaseoRuntimeAdapter",
    "skills/implement-gwo:PlanCompiler",
    "skills/implement-gwo:LocalPlanPublication",
    "skills/implement-gwo:Kernel.reconcile_once",
)


def _canonical_plain(value: object) -> object:
    if is_dataclass(value):
        return {
            field.name: _canonical_plain(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, tuple):
        return [_canonical_plain(item) for item in value]
    if isinstance(value, list):
        return [_canonical_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonical_plain(child) for key, child in value.items()}
    return value


class _CanonicalValue:
    def canonical(self) -> dict[str, Any]:
        value = _canonical_plain(self)
        if type(value) is not dict:
            raise TypeError("canonical value must project to one object")
        return value


@dataclass(frozen=True)
class CutoverSubject(_CanonicalValue):
    repository: str
    control_branch: str
    target_branch: str
    source_writer_generation: str
    target_writer_generation: str
    store_generation: str
    source_commit: str
    source_tree_digest: str
    production_entry_refs: tuple[str, ...]
    forbidden_production_refs: tuple[str, ...] = DEFAULT_FORBIDDEN_PRODUCTION_REFS
    required_runtime_selectors: tuple[str, ...] = REQUIRED_RUNTIME_SELECTORS
    package_names: tuple[str, ...] = ("implement-gwo", "orchestrator")
    install_surfaces: tuple[str, ...] = (".agents", ".codex", ".claude")


@dataclass(frozen=True)
class LegacyReadback(_CanonicalValue):
    repository: str
    writer_generation: str
    authority_state: Literal["active", "authoritative_quiescent", "stopped"]
    active_dispatches: tuple[str, ...]
    active_workers: tuple[str, ...]
    integration_lease_owner: str | None
    v2_execution_refs: tuple[str, ...]
    v2_execution_state: Literal["none", "running", "terminal", "quiescent_read_only"]
    original_decoder_readable: bool
    durable_state_digest: str


@dataclass(frozen=True)
class DurableStateReadback(_CanonicalValue):
    repository: str
    generation_id: str
    state_schema: str
    compatible: bool
    active_plan_digests: tuple[str, ...]
    pending_activation_ids: tuple[str, ...]
    predecessor_identity_refs: tuple[str, ...]
    readback_digest: str


@dataclass(frozen=True)
class WriterFenceReadback(_CanonicalValue):
    repository: str
    writer_generation: str
    authority_state: Literal["authoritative", "draining", "cut_over"]
    record_id: str
    activation_id: str | None
    control_ref_digest: str
    readback_digest: str


@dataclass(frozen=True)
class OwnershipReadback(_CanonicalValue):
    repository: str
    active_admissions: tuple[str, ...]
    active_attempts: tuple[str, ...]
    integration_lease_owner: str | None
    runtime_resource_refs: tuple[str, ...]
    readback_digest: str


@dataclass(frozen=True)
class CompatibilityPathReadback(_CanonicalValue):
    repository: str
    source_commit: str
    source_tree_digest: str
    audit_version: str
    reachable_v2_projection_refs: tuple[str, ...]
    reachable_v3_compatibility_refs: tuple[str, ...]
    reachable_legacy_writer_refs: tuple[str, ...]
    proven_unreachable_refs: tuple[str, ...]
    readback_digest: str


@dataclass(frozen=True)
class RuntimeSelectorReadback(_CanonicalValue):
    selector: str
    profile_digest: str
    fallback_profile_digest: str | None
    configuration_source: Literal["campaign_start", "repository", "host_global"]


@dataclass(frozen=True)
class RuntimePreflightReadback(_CanonicalValue):
    repository: str
    selectors: tuple[RuntimeSelectorReadback, ...]
    configuration_digest: str
    provider_action_refs: tuple[str, ...]
    persistence_write_refs: tuple[str, ...]
    readback_digest: str


@dataclass(frozen=True)
class PackageIdentity(_CanonicalValue):
    package_name: str
    version: str
    content_digest: str
    manifest_content_digest: str
    install_surface: str | None


@dataclass(frozen=True)
class PackageReadback(_CanonicalValue):
    source_packages: tuple[PackageIdentity, ...]
    installed_packages: tuple[PackageIdentity, ...]
    drift: tuple[str, ...]
    readback_digest: str


READBACK_BUNDLE_SCHEMA = "gwo.cutover-readback-bundle.v1"


@dataclass(frozen=True)
class CutoverReadbackBundle(_CanonicalValue):
    schema: str
    subject: CutoverSubject
    legacy: LegacyReadback
    durable_state: DurableStateReadback
    writer_fence: WriterFenceReadback
    ownership: OwnershipReadback
    compatibility: CompatibilityPathReadback
    runtime: RuntimePreflightReadback
    packages: PackageReadback

    def canonical(self) -> dict[str, Any]:
        return _bundle_canonical(self)


@dataclass(frozen=True)
class JsonCutoverReadPorts:
    """One-read, immutable JSON replay of the seven typed read ports."""

    subject: CutoverSubject
    bundle: CutoverReadbackBundle

    @classmethod
    def load(cls, path: Path) -> "JsonCutoverReadPorts":
        return _load_json_ports(cls, path)

    def sources(self) -> CutoverGuardSources:
        return _sources_for_bundle(self.bundle)


class LegacyReadPort(Protocol):
    def read(self, repository: str) -> LegacyReadback: ...


class DurableStateReadPort(Protocol):
    def read(self, repository: str) -> DurableStateReadback: ...


class WriterFenceReadPort(Protocol):
    def read(self, repository: str) -> WriterFenceReadback: ...


class OwnershipReadPort(Protocol):
    def read(self, repository: str) -> OwnershipReadback: ...


class CompatibilityPathReadPort(Protocol):
    def read(self, subject: CutoverSubject) -> CompatibilityPathReadback: ...


class RuntimePreflightReadPort(Protocol):
    def read(
        self,
        repository: str,
        selectors: tuple[str, ...],
    ) -> RuntimePreflightReadback: ...


class PackageReadPort(Protocol):
    def read(self, subject: CutoverSubject) -> PackageReadback: ...


@dataclass(frozen=True)
class CutoverGuardSources:
    legacy: LegacyReadPort
    durable_state: DurableStateReadPort
    writer_fence: WriterFenceReadPort
    ownership: OwnershipReadPort
    compatibility: CompatibilityPathReadPort
    runtime: RuntimePreflightReadPort
    packages: PackageReadPort


class GuardActivationValidator(Protocol):
    def validate_activation(
        self,
        subject: CutoverSubject,
        receipt: CutoverGuardReceipt,
    ) -> None: ...


@dataclass(frozen=True)
class GuardCheck(_CanonicalValue):
    check_id: str
    passed: bool
    observed_digest: str | None


@dataclass(frozen=True)
class CutoverBlocker(_CanonicalValue):
    code: str
    check_id: str
    observed_digest: str | None
    detail: str


@dataclass(frozen=True)
class CutoverGuardReceipt(_CanonicalValue):
    schema: str
    repository: str
    subject_digest: str
    readback_digest: str
    source_writer_generation: str
    target_writer_generation: str
    store_generation: str
    writer_control_ref_digest: str
    runtime_configuration_digest: str
    compatibility_audit_digest: str
    package_readback_digest: str
    receipt_digest: str

    def canonical_without_digest(self) -> dict[str, Any]:
        value = self.canonical()
        value.pop("receipt_digest")
        return value


@dataclass(frozen=True)
class CutoverGuardReport(_CanonicalValue):
    schema: str
    decision: Literal["GO", "NO_GO"]
    repository: str
    subject_digest: str
    readback_digest: str
    checks: tuple[GuardCheck, ...]
    blockers: tuple[CutoverBlocker, ...]
    receipt: CutoverGuardReceipt | None


class CutoverGuardError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


~~~

`CutoverGuard` is introduced with its executable constructor,
`evaluate`, and `validate_activation_token` bodies in Task 1 Step 3 below;
the plan never asks the implementer to create a stub for it.

~~~python
from dataclasses import fields, is_dataclass

from gwo_v8._canonical import canonical_bytes, digest_value, load_canonical_json


def _plain(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(child) for key, child in value.items()}
    return value


def _bundle_canonical(bundle: CutoverReadbackBundle) -> dict[str, Any]:
    return {
        "schema": bundle.schema,
        "subject": _plain(bundle.subject),
        "readbacks": {
            "legacy": _plain(bundle.legacy),
            "durable_state": _plain(bundle.durable_state),
            "writer_fence": _plain(bundle.writer_fence),
            "ownership": _plain(bundle.ownership),
            "compatibility": _plain(bundle.compatibility),
            "runtime": _plain(bundle.runtime),
            "packages": _plain(bundle.packages),
        },
    }


class _ReplayReadPort:
    def __init__(self, value: object) -> None:
        self._value = value

    def read(self, *_args: object, **_kwargs: object) -> object:
        return self._value


def _tuple_field(data: dict[str, object], name: str) -> None:
    value = data[name]
    if type(value) is not list:
        raise CutoverGuardError("CUTOVER_BUNDLE_INVALID", f"{name} must be an array")
    data[name] = tuple(value)


def _exact_object(
    value: object,
    expected: set[str],
    label: str,
) -> dict[str, object]:
    if type(value) is not dict or set(value) != expected:
        raise CutoverGuardError(
            "CUTOVER_BUNDLE_INVALID",
            f"{label} keys are not exact",
        )
    return dict(value)


def _exact_types(
    value: dict[str, object],
    expected: dict[str, tuple[type[object], ...]],
    label: str,
) -> None:
    for name, types in expected.items():
        if type(value[name]) not in types:
            raise CutoverGuardError(
                "CUTOVER_BUNDLE_INVALID",
                f"{label}.{name} has the wrong exact type",
            )


def _decode_bundle(value: object) -> CutoverReadbackBundle:
    if type(value) is not dict or set(value) != {"schema", "subject", "readbacks"}:
        raise CutoverGuardError("CUTOVER_BUNDLE_INVALID", "bundle keys are not exact")
    if value["schema"] != READBACK_BUNDLE_SCHEMA or type(value["readbacks"]) is not dict:
        raise CutoverGuardError("CUTOVER_BUNDLE_INVALID", "bundle schema is invalid")
    readbacks = value["readbacks"]
    expected = {
        "legacy", "durable_state", "writer_fence", "ownership",
        "compatibility", "runtime", "packages",
    }
    if set(readbacks) != expected or type(value["subject"]) is not dict:
        raise CutoverGuardError("CUTOVER_BUNDLE_INVALID", "bundle readback keys are not exact")
    subject_data = _exact_object(
        value["subject"],
        {
            "repository", "control_branch", "target_branch",
            "source_writer_generation", "target_writer_generation",
            "store_generation", "source_commit", "source_tree_digest",
            "production_entry_refs", "forbidden_production_refs",
            "required_runtime_selectors", "package_names", "install_surfaces",
        },
        "subject",
    )
    _exact_types(
        subject_data,
        {
            name: (str,)
            for name in (
                "repository", "control_branch", "target_branch",
                "source_writer_generation", "target_writer_generation",
                "store_generation", "source_commit", "source_tree_digest",
            )
        },
        "subject",
    )
    for name in (
        "production_entry_refs", "forbidden_production_refs",
        "required_runtime_selectors", "package_names", "install_surfaces",
    ):
        if name in subject_data:
            _tuple_field(subject_data, name)
    subject = CutoverSubject(**subject_data)
    legacy_data = _exact_object(
        readbacks["legacy"],
        {
            "repository", "writer_generation", "authority_state",
            "active_dispatches", "active_workers", "integration_lease_owner",
            "v2_execution_refs", "v2_execution_state",
            "original_decoder_readable", "durable_state_digest",
        },
        "legacy",
    )
    for name in ("active_dispatches", "active_workers", "v2_execution_refs"):
        _tuple_field(legacy_data, name)
    durable_data = _exact_object(
        readbacks["durable_state"],
        {
            "repository", "generation_id", "state_schema", "compatible",
            "active_plan_digests", "pending_activation_ids",
            "predecessor_identity_refs", "readback_digest",
        },
        "durable_state",
    )
    for name in ("active_plan_digests", "pending_activation_ids", "predecessor_identity_refs"):
        _tuple_field(durable_data, name)
    writer_data = _exact_object(
        readbacks["writer_fence"],
        {
            "repository", "writer_generation", "authority_state", "record_id",
            "activation_id", "control_ref_digest", "readback_digest",
        },
        "writer_fence",
    )
    ownership_data = _exact_object(
        readbacks["ownership"],
        {
            "repository", "active_admissions", "active_attempts",
            "integration_lease_owner", "runtime_resource_refs", "readback_digest",
        },
        "ownership",
    )
    for name in ("active_admissions", "active_attempts", "runtime_resource_refs"):
        _tuple_field(ownership_data, name)
    compatibility_data = _exact_object(
        readbacks["compatibility"],
        {
            "repository", "source_commit", "source_tree_digest", "audit_version",
            "reachable_v2_projection_refs", "reachable_v3_compatibility_refs",
            "reachable_legacy_writer_refs", "proven_unreachable_refs",
            "readback_digest",
        },
        "compatibility",
    )
    for name in (
        "reachable_v2_projection_refs", "reachable_v3_compatibility_refs",
        "reachable_legacy_writer_refs", "proven_unreachable_refs",
    ):
        _tuple_field(compatibility_data, name)
    runtime_data = _exact_object(
        readbacks["runtime"],
        {
            "repository", "selectors", "configuration_digest",
            "provider_action_refs", "persistence_write_refs", "readback_digest",
        },
        "runtime",
    )
    if type(runtime_data["selectors"]) is not list:
        raise CutoverGuardError("CUTOVER_BUNDLE_INVALID", "runtime.selectors must be an array")
    runtime_data["selectors"] = tuple(
        RuntimeSelectorReadback(
            **_exact_object(
                item,
                {
                    "selector", "profile_digest", "fallback_profile_digest",
                    "configuration_source",
                },
                "runtime.selector",
            )
        )
        for item in runtime_data["selectors"]
    )
    package_data = _exact_object(
        readbacks["packages"],
        {"source_packages", "installed_packages", "drift", "readback_digest"},
        "packages",
    )
    if type(package_data["source_packages"]) is not list or type(package_data["installed_packages"]) is not list:
        raise CutoverGuardError("CUTOVER_BUNDLE_INVALID", "package identities must be arrays")
    package_data["source_packages"] = tuple(
        PackageIdentity(
            **_exact_object(
                item,
                {
                    "package_name", "version", "content_digest",
                    "manifest_content_digest", "install_surface",
                },
                "packages.source",
            )
        )
        for item in package_data["source_packages"]
    )
    package_data["installed_packages"] = tuple(
        PackageIdentity(
            **_exact_object(
                item,
                {
                    "package_name", "version", "content_digest",
                    "manifest_content_digest", "install_surface",
                },
                "packages.installed",
            )
        )
        for item in package_data["installed_packages"]
    )
    bundle = CutoverReadbackBundle(
        schema=value["schema"],
        subject=subject,
        legacy=LegacyReadback(**legacy_data),
        durable_state=DurableStateReadback(**durable_data),
        writer_fence=WriterFenceReadback(**writer_data),
        ownership=OwnershipReadback(**ownership_data),
        compatibility=CompatibilityPathReadback(**compatibility_data),
        runtime=RuntimePreflightReadback(**runtime_data),
        packages=PackageReadback(**package_data),
    )
    for readback in (
        bundle.legacy,
        bundle.durable_state,
        bundle.writer_fence,
        bundle.ownership,
        bundle.compatibility,
        bundle.runtime,
        bundle.packages,
    ):
        body = _plain(readback)
        observed = body.pop("readback_digest", None)
        if type(observed) is not str or observed != digest_value(body):
            raise CutoverGuardError(
                "CUTOVER_BUNDLE_INVALID",
                "nested readback digest does not match its closed value",
            )
    if canonical_bytes(bundle.canonical()) != canonical_bytes(value):
        raise CutoverGuardError("CUTOVER_BUNDLE_INVALID", "bundle is not a closed canonical object")
    return bundle


def _load_json_ports(
    cls: type[JsonCutoverReadPorts],
    path: Path,
) -> JsonCutoverReadPorts:
    raw = Path(path).read_bytes()
    try:
        value = load_canonical_json(raw)
        bundle = _decode_bundle(value)
    except (OSError, TypeError, ValueError, KeyError) as error:
        if isinstance(error, CutoverGuardError):
            raise
        raise CutoverGuardError("CUTOVER_BUNDLE_INVALID", "bundle cannot be decoded") from error
    return cls(subject=bundle.subject, bundle=bundle)


def _sources_for_bundle(bundle: CutoverReadbackBundle) -> CutoverGuardSources:
    return CutoverGuardSources(
        legacy=_ReplayReadPort(bundle.legacy),
        durable_state=_ReplayReadPort(bundle.durable_state),
        writer_fence=_ReplayReadPort(bundle.writer_fence),
        ownership=_ReplayReadPort(bundle.ownership),
        compatibility=_ReplayReadPort(bundle.compatibility),
        runtime=_ReplayReadPort(bundle.runtime),
        packages=_ReplayReadPort(bundle.packages),
    )
~~~

`source_tree_digest(package_root: Path) -> str` is the module’s read-only canonical file-tree digest helper. It hashes the same normalized relative paths and bytes that `ProductionPathScanner` audits; it never invokes Git or writes a cache. The live CLI computes `source_tree_digest` from `--package-root` instead of accepting a caller-supplied tree-digest file.

`CutoverSubject` validates `source_commit` as one 40-character lowercase hexadecimal commit ID and `source_tree_digest` as one 64-character lowercase hexadecimal digest. `PackageIdentity.install_surface` is `None` for a source package and exactly one of the subject’s three install-surface names for an installed package. `CutoverReadbackBundle.canonical()` returns the closed object `{"schema": bundle.schema, "subject": subject.canonical(), "readbacks": {"legacy": legacy.canonical(), "durable_state": durable_state.canonical(), "writer_fence": writer_fence.canonical(), "ownership": ownership.canonical(), "compatibility": compatibility.canonical(), "runtime": runtime.canonical(), "packages": packages.canonical()}}`; it never emits `{}` readback values. `JsonCutoverReadPorts.load(path)` reads the path exactly once, parses strict canonical JSON into that object, verifies every nested readback digest and the subject digest, then returns seven private immutable adapters whose only public operation is the matching `read` method. It does not write the input or create an output file.

Every value object above implements `canonical() -> dict[str, Any]`; `CutoverGuardReceipt` additionally implements `canonical_without_digest() -> dict[str, Any]`, and `CutoverGuardReport` implements `canonical() -> dict[str, Any]`. The `receipt_digest` is `digest_value` over the receipt with `receipt_digest` omitted; the report’s `readback_digest` is `digest_value` over the seven exact readback canonical values in check order. A GO report contains exactly one receipt; a NO_GO report contains `None` and at least one sorted blocker. No report contains a Canary, Plan Revision, Candidate, provider, model, process ID, or activation side effect.

### Task 0: Pass the C2 closure and C3 handoff preflight before any #118 work

**Files:**
- Read: D:\gwo-release-evidence\2026-08-06-gwo-v8-c2-beta2-feature-complete\state.json
- Read: every path and SHA-256 named by that state, including closure, c3_handoff, C2 local-verification manifests, tracker readbacks, review receipts, and merged-main identity.
- Read: docs/superpowers/plans/2026-08-06-gwo-v8-c2-beta2-feature-complete.md Task 11 and this plan’s Global Constraints.
- Verify: the merged C2 tree at 32ca2cfd85aec31d0215807bdad96c0f6d99361c.
- Create externally, only after the C2 owner completes its own release gate: closure.json, c3-handoff.json, and the corresponding state.json references under the C2 evidence root. No C3 source file is changed in this task.

**Interfaces:**
- Consumes: the C2 state schema, C2 closure/handoff schemas, current local main readback, and local-only verification policy.
- Produces: a pass/fail preflight decision. A pass supplies the exact C2 closure path/SHA, C3 handoff path/SHA, merged SHA/tree/parents, issue-state map, and non-goal flags to Tasks 1–7. A failure produces HOLD and dispatches no implementation task.

- [ ] **Step 1: Prove the current predecessor is RED and fail closed.**

Run this read-only assertion from the C2 worktree:

~~~powershell
$statePath = 'D:\gwo-release-evidence\2026-08-06-gwo-v8-c2-beta2-feature-complete\state.json'
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
if ($state.closure.schema -ne 'gwo-v8-c2-closure.v1' -or
    $state.c3_handoff.schema -ne 'gwo-v8-c3-handoff.v1') {
    throw 'C2_CLOSURE_INCOMPLETE'
}
~~~

The historical RED/HOLD is preserved at
`D:\gwo-release-evidence\2026-08-06-gwo-v8-c2-beta2-feature-complete\c3-task0-predecessor-red.log`
and `c3-task0-audit.md`. After the C2 owner gate, this assertion must now exit
zero against the digest-bound closure and handoff; do not replace the
historical RED evidence.

- [ ] **Step 2: Complete the C2 owner gate outside this implementation plan.**

The C2 owner gate has completed the exact Task 11 publication/closure procedure in docs/superpowers/plans/2026-08-06-gwo-v8-c2-beta2-feature-complete.md under Local Verification Only, deriving the subject from `refs/heads/main` at `32ca2cfd85aec31d0215807bdad96c0f6d99361c` and refreshing the state’s `coordinator_head`, `coordinator_tree`, and ordered `coordinator_parents` to that same readback. The resulting records are:

1. `D:\gwo-release-evidence\2026-08-06-gwo-v8-c2-beta2-feature-complete\closure.json` with schema gwo-v8-c2-closure.v1, exact C2 merged SHA/tree/ordered parents, all local command manifests and digests, C1 closure/handoff identities, final tracker readbacks, Beta2 publication receipts, protected-GA identity, and explicit non-goals;
2. `D:\gwo-release-evidence\2026-08-06-gwo-v8-c2-beta2-feature-complete\c3-handoff.json` with schema gwo-v8-c3-handoff.v1, the semantic next scope proven by the state-referenced tracker-after readback, #113/#114/#115/#116/#117/#136/#137 closed, #118/#119 open with Beta3/GA assignments, writer_activation_enabled=false, production_admission=false, default_writer_authority=false, and an empty activation_authority object;
3. `state.json` references whose path, schema, and sha256 values exactly match those two files.

The C2 closure procedure may read GitHub Issues, but it must not claim GitHub Actions evidence; all repository test gates are local. If that procedure cannot produce those exact records, C3 remains HOLD.

- [ ] **Step 3: Re-hash and bind every C2 artifact before dispatch.**

Run the following exact readback. It must derive the subject from the current merged main, never from a plan literal or the stale external coordinator head:

~~~powershell
function Hash-File([string]$path) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "C2_EVIDENCE_MISSING:$path"
    }
    return (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
}
$root = (git rev-parse --show-toplevel).Trim()
$statePath = 'D:\gwo-release-evidence\2026-08-06-gwo-v8-c2-beta2-feature-complete\state.json'
$state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
$closurePath = ([string]$state.closure.path).Replace('\','/')
$handoffPath = ([string]$state.c3_handoff.path).Replace('\','/')
if ((Hash-File $closurePath) -ne [string]$state.closure.sha256) {
    throw 'C2_CLOSURE_HASH_INVALID'
}
if ((Hash-File $handoffPath) -ne [string]$state.c3_handoff.sha256) {
    throw 'C3_HANDOFF_HASH_INVALID'
}
$closure = Get-Content -Raw -LiteralPath $closurePath | ConvertFrom-Json
$handoff = Get-Content -Raw -LiteralPath $handoffPath | ConvertFrom-Json
$mainSha = (git -C $root rev-parse refs/heads/main).Trim()
$mainTree = (git -C $root rev-parse "$mainSha^{tree}").Trim()
if ($closure.schema -ne 'gwo-v8-c2-closure.v1' -or
    $closure.merged_sha -ne $mainSha -or
    $closure.merged_tree -ne $mainTree) {
    throw 'C2_CLOSURE_MAIN_IDENTITY_INVALID'
}
if ($handoff.schema -ne 'gwo-v8-c3-handoff.v1' -or
    $handoff.writer_activation_enabled -ne $false -or
    $handoff.production_admission -ne $false -or
    $handoff.default_writer_authority -ne $false -or
    (($handoff.activation_authority | ConvertTo-Json -Compress) -ne '{}')) {
    throw 'C3_HANDOFF_BOUNDARY_INVALID'
}
if ($state.coordinator_head -ne $mainSha -or
    $state.coordinator_tree -ne $mainTree) {
    throw 'C2_COORDINATOR_IDENTITY_STALE'
}
if (@(git -C $root status --porcelain).Count -ne 0) {
    throw 'C2_WORKTREE_DIRTY'
}
~~~

Expected after the C2 owner gate: exit 0 and a readback of the exact C2 closure/handoff digests. Expected before that gate: HOLD with one of the named errors above. The gate performs no repository, Issue, SQLite, package, or writer mutation.

- [ ] **Step 4: Read external blocker facts without changing them.**

Run read-only commands:

~~~powershell
gh issue view 113 --repo NOirBRight/github-work-orchestrator --json state,body,comments,labels,milestone,assignees
gh issue view 114 --repo NOirBRight/github-work-orchestrator --json state,body,comments,labels,milestone,assignees
gh issue view 115 --repo NOirBRight/github-work-orchestrator --json state,body,comments,labels,milestone,assignees
gh issue view 116 --repo NOirBRight/github-work-orchestrator --json state,body,comments,labels,milestone,assignees
gh issue view 117 --repo NOirBRight/github-work-orchestrator --json state,body,comments,labels,milestone,assignees
gh issue view 136 --repo NOirBRight/github-work-orchestrator --json state,body,comments,labels,milestone,assignees
gh issue view 137 --repo NOirBRight/github-work-orchestrator --json state,body,comments,labels,milestone,assignees
gh issue view 118 --repo NOirBRight/github-work-orchestrator --json state,body,comments,labels,milestone,assignees
gh issue view 119 --repo NOirBRight/github-work-orchestrator --json state,body,comments,labels,milestone,assignees
~~~

Expected: #113–#117/#136/#137 are closed by their exact read-backed Results, #118 is OPEN with milestone `GWO V8 Beta3`, #119 is OPEN with milestone `GWO V8 GA`, and no command writes tracker state. Any mismatch is HOLD with `C3_HANDOFF_SCOPE_UNPROVEN`.

- [ ] **Step 5: Freeze the C3 implementation base and handoff payload.**

Persist the validated values for the next tasks in the coordinator log:

~~~powershell
$expected = '32ca2cfd85aec31d0215807bdad96c0f6d99361c'
if ((git rev-parse refs/heads/main).Trim() -ne $expected) {
    throw 'C2_MAIN_NOT_AT_MERGED_HEAD'
}
py -3.13 -m pytest tests/test_orchestrator_package.py -q
py -3.13 scripts/quick_validate.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
~~~

Expected: the local package/evidence gates pass and the current main is exactly the merged C2 tree. The output is evidence only; it does not publish Beta3, enable a writer, or close #118.

- [ ] **Step 6: Stop or continue using the gate result.**

If any Step 1–5 assertion fails, record HOLD, preserve the state unchanged, and dispatch no C3 implementation task. Only a zero-exit, digest-valid closure/handoff with the exact main identity allows Task 1 to begin. Task 0 has no production commit; its only durable handoff is the externally hashed C2 state and the coordinator’s read-only preflight log.

---
## Parallel execution and integration waves

0. Task 0 is a serial HOLD/PASS gate. It consumes no source write set and dispatches no implementation task on failure.
1. Task 1 defines the immutable Guard contract and pure evaluator. It is the only implementation prerequisite for the remaining lanes.
2. After Task 1 is GREEN, Task 2 (static path/package validator) and an independent read-only review of the C2 handoff may run in parallel. Task 2 owns only its scanner/validator tests and cutover_guard.py extensions.
3. After the Task 1 interfaces are fixed, Task 3 (host composition) and Task 4 (transition token fence) may be developed in separate worktrees. Task 4 uses an exact test double for ProductionCutoverGuardHost.validate_activation during RED; its GREEN merge waits for Task 3’s real host method. They never share production source files. Manifest regeneration, package tests, and integration are coordinator-owned and serial.
4. Task 5 (root API/predecessor reachability cleanup) starts only after Tasks 2–4 are GREEN because it audits the final import graph and guarded writer path. Task 6 (read-only CLI) may be developed in parallel with Task 5 after Task 3’s host factory is fixed, but updates to tests/test_orchestrator_package.py and the package manifest are applied in one serial integration lane.
5. Task 7 (Beta3 evidence/release-boundary checks) is serial after Tasks 2–6, and it is read-only. It produces no tag, Release, Issue mutation, default-writer change, or production admission.
6. At most five fresh Luna Max subagents may be active. Each subagent receives one disjoint write set and must report RED, GREEN, focused regression, manifest check, and commit SHA. The coordinator merges source lanes in dependency order, regenerates the package manifest once per integration commit, and runs the final local evidence gate once on the merged tree.

The only safe parallelism is between independent source/test write sets. No two workers touch skills/orchestrator/.skill-package.json, tests/test_orchestrator_package.py, transition.py, production_host.py, or final evidence at the same time.
---

### Task 1: Define the read-only Guard contract and pure evaluator

**Files:**
- Create: skills/orchestrator/scripts/gwo_v8/cutover_guard.py
- Create: tests/cutover_guard_test_support.py
- Create: tests/test_v8_cutover_guard.py
- Modify: skills/orchestrator/.skill-package.json (generated content hash)

**Interfaces:**
- Consumes: _canonical.digest_value, _canonical.canonical_bytes, and seven read-only ports from CutoverGuardSources.
- Produces: CutoverSubject, all exact readback/report/receipt values above, CutoverGuard.evaluate, and CutoverGuard.validate_activation_token for Tasks 2–6 and transition.py.

- [ ] **Step 1: Write the failing contract and no-write tests.**

Add these exact tests to tests/test_v8_cutover_guard.py:

~~~python
def test_guard_success_returns_digest_bound_read_only_receipt_without_writes():
    harness = GuardHarness.valid()

    report = CutoverGuard(harness.sources).evaluate(harness.subject)

    assert report.decision == "GO"
    assert report.blockers == ()
    assert report.receipt is not None
    assert report.receipt.subject_digest == digest_value(harness.subject.canonical())
    assert report.receipt.readback_digest == report.readback_digest
    assert report.receipt.source_writer_generation == "v6.1"
    assert report.receipt.target_writer_generation == "v8"
    assert report.receipt.receipt_digest == digest_value(
        report.receipt.canonical_without_digest()
    )
    assert harness.mutation_calls() == ()
    assert all(call_count > 0 for call_count in harness.read_call_counts().values())


def test_guard_collects_named_blockers_without_short_circuiting_or_writing():
    harness = GuardHarness.valid()
    harness.legacy.value = replace(
        harness.legacy.value,
        active_dispatches=("dispatch:running",),
    )
    harness.writer.value = replace(
        harness.writer.value,
        writer_generation="unexpected-writer",
    )
    harness.runtime.value = replace(
        harness.runtime.value,
        selectors=(),
    )

    report = CutoverGuard(harness.sources).evaluate(harness.subject)

    assert report.decision == "NO_GO"
    assert {blocker.code for blocker in report.blockers} == {
        "CUTOVER_LEGACY_NOT_QUIESCENT",
        "CUTOVER_SOURCE_WRITER_INVALID",
        "CUTOVER_RUNTIME_CONFIGURATION_INVALID",
    }
    assert harness.mutation_calls() == ()
    assert all(call_count > 0 for call_count in harness.read_call_counts().values())


@pytest.mark.parametrize(
    ("state", "accepted"),
    (("none", True), ("terminal", True), ("quiescent_read_only", True), ("running", False)),
)
def test_guard_accepts_only_terminal_or_quiescent_v2_readback(state, accepted):
    harness = GuardHarness.valid()
    harness.legacy.value = replace(
        harness.legacy.value,
        v2_execution_refs=("v2:one",),
        v2_execution_state=state,
    )

    report = CutoverGuard(harness.sources).evaluate(harness.subject)

    assert (report.decision == "GO") is accepted
    if not accepted:
        assert "CUTOVER_V2_ACTIVE" in {blocker.code for blocker in report.blockers}


def test_reader_exception_becomes_named_blocker_and_other_reads_continue():
    harness = GuardHarness.valid()
    harness.ownership.raise_error = RuntimeError("lease read unavailable")

    report = CutoverGuard(harness.sources).evaluate(harness.subject)

    assert report.decision == "NO_GO"
    assert "CUTOVER_OWNERSHIP_READBACK_INVALID" in {
        blocker.code for blocker in report.blockers
    }
    assert all(call_count > 0 for call_count in harness.read_call_counts().values())


def test_malformed_typed_readback_becomes_named_blocker_and_other_reads_continue():
    harness = GuardHarness.valid()
    harness.ownership.value = object()

    report = CutoverGuard(harness.sources).evaluate(harness.subject)

    assert report.decision == "NO_GO"
    assert "CUTOVER_OWNERSHIP_READBACK_INVALID" in {
        blocker.code for blocker in report.blockers
    }
    assert all(call_count > 0 for call_count in harness.read_call_counts().values())


def test_guard_go_and_no_go_never_call_repository_sqlite_github_process_or_runtime_writers():
    harness = GuardHarness.valid()

    go_report = CutoverGuard(harness.sources).evaluate(harness.subject)
    assert go_report.decision == "GO"
    harness.legacy.value = replace(
        harness.legacy.value,
        active_workers=("worker:running",),
    )

    no_go_report = CutoverGuard(harness.sources).evaluate(harness.subject)
    assert no_go_report.decision == "NO_GO"
    assert harness.external_writes == {
        "repository": 0,
        "sqlite": 0,
        "github": 0,
        "process": 0,
        "runtime": 0,
    }
    assert harness.mutation_calls() == ()


def test_guard_receipt_digest_changes_when_any_readback_changes():
    harness = GuardHarness.valid()
    first = CutoverGuard(harness.sources).evaluate(harness.subject)
    changed = replace(
        harness.durable.value,
        active_plan_digests=("plan:changed",),
    )
    body = changed.canonical()
    body.pop("readback_digest")
    harness.durable.value = replace(
        changed,
        readback_digest=digest_value(body),
    )

    second = CutoverGuard(harness.sources).evaluate(harness.subject)

    assert first.receipt is not None and second.receipt is not None
    assert first.readback_digest != second.readback_digest
    assert first.receipt.receipt_digest != second.receipt.receipt_digest


def test_activation_token_validation_re_reads_prerequisites_and_rejects_stale_readback():
    harness = GuardHarness.valid()
    first = CutoverGuard(harness.sources).evaluate(harness.subject)
    assert first.receipt is not None
    harness.writer.value = replace(
        harness.writer.value,
        control_ref_digest="c" * 64,
    )

    with pytest.raises(CutoverGuardError) as error:
        CutoverGuard(harness.sources).validate_activation_token(
            harness.subject,
            first.receipt,
        )

    assert error.value.code == "CUTOVER_GUARD_TOKEN_STALE"
    assert harness.mutation_calls() == ()
    assert all(call_count >= 2 for call_count in harness.read_call_counts().values())
~~~

Put the shared fixture bodies in `tests/cutover_guard_test_support.py`; do not leave a helper name as prose. The following is the minimum complete harness used by Tasks 1–6 (the imports are intentionally explicit so a new test module can copy them):

~~~python
from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path

from gwo_v8._canonical import digest_value

from gwo_v8.cutover_guard import (
    CompatibilityPathReadback,
    CutoverGuardSources,
    CutoverSubject,
    DurableStateReadback,
    LegacyReadback,
    OwnershipReadback,
    PackageIdentity,
    PackageReadback,
    RuntimePreflightReadback,
    RuntimeSelectorReadback,
    WriterFenceReadback,
    DEFAULT_FORBIDDEN_PRODUCTION_REFS,
    REQUIRED_RUNTIME_SELECTORS,
)


class MutationTripwire:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def record(self, operation: str) -> None:
        self.calls.append(operation)


_FORBIDDEN = {
    "stop": "repository",
    "restore": "repository",
    "drain": "repository",
    "publish": "github",
    "compare_and_swap": "github",
    "write": "sqlite",
    "prepare": "runtime",
    "command": "runtime",
    "events": "runtime",
    "install": "process",
    "start": "process",
}


class FakeReadPort:
    def __init__(
        self,
        value: object,
        *,
        category: str,
        external_writes: dict[str, int],
    ) -> None:
        self.value = value
        self.category = category
        self.external_writes = external_writes
        self.reads = 0
        self.raise_error: Exception | None = None

    def read(self, *_args: object, **_kwargs: object) -> object:
        self.reads += 1
        if self.raise_error is not None:
            raise self.raise_error
        return self.value

    def __getattr__(self, name: str) -> object:
        category = _FORBIDDEN.get(name)
        if category is not None:
            self.external_writes[category] += 1
            raise AssertionError(f"Guard attempted forbidden {name}()")
        raise AttributeError(name)


class GuardHarness:
    def __init__(self) -> None:
        self.external_writes = {
            "repository": 0,
            "sqlite": 0,
            "github": 0,
            "process": 0,
            "runtime": 0,
        }
        self._tripwire = MutationTripwire()

    @classmethod
    def valid(cls) -> "GuardHarness":
        harness = cls()
        subject = CutoverSubject(
            repository="owner/repo",
            control_branch="gwo-control",
            target_branch="main",
            source_writer_generation="v6.1",
            target_writer_generation="v8",
            store_generation="store:v8:0001",
            source_commit="a" * 40,
            source_tree_digest="b" * 64,
            production_entry_refs=(
                "gwo_v8.plan_control_host:ProductionPlanControlStartHost.start",
                "gwo_v8.execution_kernel:advance",
                "gwo_v8.execution_kernel:inspect",
            ),
        )
        source_packages = tuple(
            PackageIdentity(name, "8.0.0", "c" * 64, "d" * 64, None)
            for name in subject.package_names
        )
        installed_packages = tuple(
            PackageIdentity(name, "8.0.0", "c" * 64, "d" * 64, surface)
            for surface in subject.install_surfaces
            for name in subject.package_names
        )
        harness.subject = subject
        harness.legacy = FakeReadPort(
            LegacyReadback(
                repository="owner/repo",
                writer_generation="v6.1",
                authority_state="authoritative_quiescent",
                active_dispatches=(),
                active_workers=(),
                integration_lease_owner=None,
                v2_execution_refs=(),
                v2_execution_state="none",
                original_decoder_readable=True,
                durable_state_digest="e" * 64,
            ),
            category="repository",
            external_writes=harness.external_writes,
        )
        harness.durable = FakeReadPort(
            DurableStateReadback(
                repository="owner/repo",
                generation_id="store:v8:0001",
                state_schema="gwo.v8.store.v1",
                compatible=True,
                active_plan_digests=(),
                pending_activation_ids=(),
                predecessor_identity_refs=(),
                readback_digest="f" * 64,
            ),
            category="sqlite",
            external_writes=harness.external_writes,
        )
        harness.writer = FakeReadPort(
            WriterFenceReadback(
                repository="owner/repo",
                writer_generation="v6.1",
                authority_state="authoritative",
                record_id="writer-record:one",
                activation_id=None,
                control_ref_digest="1" * 64,
                readback_digest="2" * 64,
            ),
            category="github",
            external_writes=harness.external_writes,
        )
        harness.ownership = FakeReadPort(
            OwnershipReadback(
                repository="owner/repo",
                active_admissions=(),
                active_attempts=(),
                integration_lease_owner=None,
                runtime_resource_refs=(),
                readback_digest="3" * 64,
            ),
            category="repository",
            external_writes=harness.external_writes,
        )
        harness.compatibility = FakeReadPort(
            CompatibilityPathReadback(
                repository="owner/repo",
                source_commit="a" * 40,
                source_tree_digest="b" * 64,
                audit_version="gwo.cutover-path-audit.v1",
                reachable_v2_projection_refs=(),
                reachable_v3_compatibility_refs=(),
                reachable_legacy_writer_refs=(),
                proven_unreachable_refs=tuple(sorted(DEFAULT_FORBIDDEN_PRODUCTION_REFS)),
                readback_digest="4" * 64,
            ),
            category="process",
            external_writes=harness.external_writes,
        )
        harness.runtime = FakeReadPort(
            RuntimePreflightReadback(
                repository="owner/repo",
                selectors=tuple(
                    RuntimeSelectorReadback(
                        selector=selector,
                        profile_digest="5" * 64,
                        fallback_profile_digest=None,
                        configuration_source="host_global",
                    )
                    for selector in REQUIRED_RUNTIME_SELECTORS
                ),
                configuration_digest="6" * 64,
                provider_action_refs=(),
                persistence_write_refs=(),
                readback_digest="7" * 64,
            ),
            category="runtime",
            external_writes=harness.external_writes,
        )
        harness.packages = FakeReadPort(
            PackageReadback(
                source_packages=source_packages,
                installed_packages=installed_packages,
                drift=(),
                readback_digest="8" * 64,
            ),
            category="process",
            external_writes=harness.external_writes,
        )
        for port in (
            harness.legacy,
            harness.durable,
            harness.writer,
            harness.ownership,
            harness.compatibility,
            harness.runtime,
            harness.packages,
        ):
            body = asdict(port.value)
            body.pop("readback_digest")
            port.value = replace(
                port.value,
                readback_digest=digest_value(body),
            )
        harness.sources = CutoverGuardSources(
            legacy=harness.legacy,
            durable_state=harness.durable,
            writer_fence=harness.writer,
            ownership=harness.ownership,
            compatibility=harness.compatibility,
            runtime=harness.runtime,
            packages=harness.packages,
        )
        return harness

    def read_call_counts(self) -> dict[str, int]:
        return {
            "legacy": self.legacy.reads,
            "durable_state": self.durable.reads,
            "writer_fence": self.writer.reads,
            "ownership": self.ownership.reads,
            "compatibility": self.compatibility.reads,
            "runtime": self.runtime.reads,
            "packages": self.packages.reads,
        }

    def mutation_calls(self) -> tuple[str, ...]:
        return tuple(self._tripwire.calls)
~~~

- [ ] **Step 2: Run the focused tests to prove RED.**

~~~powershell
py -3.13 -m pytest tests/test_v8_cutover_guard.py -q
~~~

Expected: collection fails with ModuleNotFoundError: No module named gwo_v8.cutover_guard (or, after the support import is added first, ImportError for the missing CutoverGuard symbols). Do not create a compatibility stub that returns GO.

- [ ] **Step 3: Implement the minimum closed value objects and evaluator.**

Create the dataclasses and canonical projections from the contract section, then use this concrete evaluator body. It reads all seven ports in the fixed order, records a deterministic read-error blocker, and still evaluates every check:

| Check ID | Pass condition | Blocker code |
| --- | --- | --- |
| source_writer | Writer readback repository matches, generation equals subject.source_writer_generation == "v6.1", authority state is authoritative, activation is None, and control/readback digests are valid. | CUTOVER_SOURCE_WRITER_INVALID |
| legacy_quiescence | Legacy repository/generation match; no active dispatches/workers; no legacy Integration Lease owner; authority state is authoritative_quiescent; V2 state is none, terminal, or quiescent_read_only; original decoder is readable. | CUTOVER_LEGACY_NOT_QUIESCENT, CUTOVER_V2_ACTIVE, or CUTOVER_LEGACY_STATE_INVALID |
| durable_state | Generation and repository match; state_schema == "gwo.v8.store.v1"; compatible is True; active Plan digests, pending Activation IDs, and predecessor identity refs are all empty. | CUTOVER_DURABLE_STATE_INVALID |
| writer_and_lease | Ownership repository matches; admissions, attempts, Integration Lease owner, and Runtime resources are all empty. | CUTOVER_WRITER_OR_LEASE_UNAVAILABLE |
| production_paths | Compatibility repository matches; audit_version == "gwo.cutover-path-audit.v1"; all three reachable-reference tuples are empty; proven_unreachable_refs exactly equals the subject’s sorted forbidden references. | CUTOVER_COMPATIBILITY_PATH_REACHABLE or CUTOVER_COMPATIBILITY_AUDIT_INVALID |
| runtime_configuration | Runtime repository matches; selector names exactly equal subject.required_runtime_selectors; each profile/fallback digest is valid; provider action refs and persistence write refs are empty. | CUTOVER_RUNTIME_CONFIGURATION_INVALID |
| package_installation | Source and installed package identities exactly cover the subject’s package names and install surfaces; every version is 8.0.0; content and manifest digests match; drift is empty. | CUTOVER_PACKAGE_INVALID |

~~~python
import re
from typing import Any, Callable

from gwo_v8._canonical import digest_value

_GUARD_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_READ_ERROR_CODES = {
    "legacy": "CUTOVER_LEGACY_READBACK_INVALID",
    "durable_state": "CUTOVER_DURABLE_STATE_READBACK_INVALID",
    "writer_fence": "CUTOVER_WRITER_FENCE_READBACK_INVALID",
    "ownership": "CUTOVER_OWNERSHIP_READBACK_INVALID",
    "compatibility": "CUTOVER_COMPATIBILITY_READBACK_INVALID",
    "runtime": "CUTOVER_RUNTIME_READBACK_INVALID",
    "packages": "CUTOVER_PACKAGES_READBACK_INVALID",
}
_READBACK_TYPES = {
    "legacy": LegacyReadback,
    "durable_state": DurableStateReadback,
    "writer_fence": WriterFenceReadback,
    "ownership": OwnershipReadback,
    "compatibility": CompatibilityPathReadback,
    "runtime": RuntimePreflightReadback,
    "packages": PackageReadback,
}


def _valid_guard_digest(value: object) -> bool:
    return type(value) is str and _GUARD_DIGEST.fullmatch(value) is not None


class CutoverGuard:
    def __init__(self, sources: CutoverGuardSources) -> None:
        if type(sources) is not CutoverGuardSources:
            raise TypeError("Guard sources must be one exact immutable source value")
        self._sources = sources

    def evaluate(self, subject: CutoverSubject) -> CutoverGuardReport:
        if type(subject) is not CutoverSubject:
            raise CutoverGuardError(
                "CUTOVER_SUBJECT_INVALID",
                "Guard subject must be one exact CutoverSubject",
            )
        readbacks: dict[str, object] = {}
        read_errors: dict[str, str] = {}
        blockers: list[CutoverBlocker] = []
        checks: list[GuardCheck] = []

        def read(
            name: str,
            operation: Callable[[], object],
        ) -> object | None:
            try:
                value = operation()
                if type(value) is not _READBACK_TYPES[name]:
                    raise TypeError("readback has the wrong exact type")
            except Exception as error:
                code = _READ_ERROR_CODES[name]
                detail = f"{type(error).__name__}: readback unavailable"
                read_errors[name] = code
                readbacks[name] = {"error_code": code, "detail": detail}
                blockers.append(CutoverBlocker(code, name, None, detail))
                return None
            readbacks[name] = value
            return value

        legacy = read("legacy", lambda: self._sources.legacy.read(subject.repository))
        durable = read(
            "durable_state",
            lambda: self._sources.durable_state.read(subject.repository),
        )
        writer = read(
            "writer_fence",
            lambda: self._sources.writer_fence.read(subject.repository),
        )
        ownership = read(
            "ownership",
            lambda: self._sources.ownership.read(subject.repository),
        )
        compatibility = read(
            "compatibility",
            lambda: self._sources.compatibility.read(subject),
        )
        runtime = read(
            "runtime",
            lambda: self._sources.runtime.read(
                subject.repository, subject.required_runtime_selectors
            ),
        )
        packages = read(
            "packages",
            lambda: self._sources.packages.read(subject),
        )

        def observed(value: object | None) -> str | None:
            return None if value is None else digest_value(value.canonical())

        def check(
            check_id: str,
            passed: bool,
            code: str,
            detail: str,
            value: object | None,
        ) -> None:
            checks.append(GuardCheck(check_id, passed, observed(value)))
            if not passed and value is not None:
                blockers.append(CutoverBlocker(code, check_id, observed(value), detail))

        source_writer_ok = (
            writer is not None
            and writer.repository == subject.repository
            and writer.writer_generation == subject.source_writer_generation == "v6.1"
            and writer.authority_state == "authoritative"
            and writer.activation_id is None
            and _valid_guard_digest(writer.control_ref_digest)
            and _valid_guard_digest(writer.readback_digest)
        )
        check(
            "source_writer",
            source_writer_ok,
            "CUTOVER_SOURCE_WRITER_INVALID",
            "the V6.1 writer fence is not authoritative and read-back valid",
            writer,
        )

        legacy_state = None if legacy is None else legacy.v2_execution_state
        legacy_code = (
            "CUTOVER_V2_ACTIVE"
            if legacy_state == "running"
            else "CUTOVER_LEGACY_NOT_QUIESCENT"
        )
        legacy_ok = (
            legacy is not None
            and legacy.repository == subject.repository
            and legacy.writer_generation == subject.source_writer_generation
            and legacy.authority_state == "authoritative_quiescent"
            and not legacy.active_dispatches
            and not legacy.active_workers
            and legacy.integration_lease_owner is None
            and legacy_state in {"none", "terminal", "quiescent_read_only"}
            and legacy.original_decoder_readable
            and _valid_guard_digest(legacy.durable_state_digest)
        )
        if legacy is not None and legacy_state not in {
            "none",
            "running",
            "terminal",
            "quiescent_read_only",
        }:
            legacy_code = "CUTOVER_LEGACY_STATE_INVALID"
        check(
            "legacy_quiescence",
            legacy_ok,
            legacy_code,
            "V6.1 has active work, an invalid V2 state, or an unreadable decoder",
            legacy,
        )

        durable_ok = (
            durable is not None
            and durable.repository == subject.repository
            and durable.generation_id == subject.store_generation
            and durable.state_schema == "gwo.v8.store.v1"
            and durable.compatible is True
            and not durable.active_plan_digests
            and not durable.pending_activation_ids
            and not durable.predecessor_identity_refs
            and _valid_guard_digest(durable.readback_digest)
        )
        check(
            "durable_state",
            durable_ok,
            "CUTOVER_DURABLE_STATE_INVALID",
            "the fresh V8 store read-back is incompatible or contains state",
            durable,
        )

        ownership_ok = (
            ownership is not None
            and ownership.repository == subject.repository
            and not ownership.active_admissions
            and not ownership.active_attempts
            and ownership.integration_lease_owner is None
            and not ownership.runtime_resource_refs
            and _valid_guard_digest(ownership.readback_digest)
        )
        check(
            "writer_and_lease",
            ownership_ok,
            "CUTOVER_WRITER_OR_LEASE_UNAVAILABLE",
            "a Worker admission, attempt, lease, or Runtime resource is active",
            ownership,
        )

        path_reachable = (
            compatibility is not None
            and (
                compatibility.reachable_v2_projection_refs
                or compatibility.reachable_v3_compatibility_refs
                or compatibility.reachable_legacy_writer_refs
            )
        )
        path_shape_ok = (
            compatibility is not None
            and compatibility.repository == subject.repository
            and compatibility.source_commit == subject.source_commit
            and compatibility.source_tree_digest == subject.source_tree_digest
            and compatibility.audit_version == "gwo.cutover-path-audit.v1"
            and compatibility.proven_unreachable_refs
            == tuple(sorted(subject.forbidden_production_refs))
            and _valid_guard_digest(compatibility.readback_digest)
        )
        check(
            "production_paths",
            path_shape_ok and not path_reachable,
            "CUTOVER_COMPATIBILITY_PATH_REACHABLE"
            if path_reachable
            else "CUTOVER_COMPATIBILITY_AUDIT_INVALID",
            "a forbidden predecessor path is reachable or its audit is invalid",
            compatibility,
        )

        runtime_ok = (
            runtime is not None
            and runtime.repository == subject.repository
            and tuple(item.selector for item in runtime.selectors)
            == subject.required_runtime_selectors
            and all(
                _valid_guard_digest(item.profile_digest)
                and (
                    item.fallback_profile_digest is None
                    or _valid_guard_digest(item.fallback_profile_digest)
                )
                for item in runtime.selectors
            )
            and _valid_guard_digest(runtime.configuration_digest)
            and not runtime.provider_action_refs
            and not runtime.persistence_write_refs
            and _valid_guard_digest(runtime.readback_digest)
        )
        check(
            "runtime_configuration",
            runtime_ok,
            "CUTOVER_RUNTIME_CONFIGURATION_INVALID",
            "required Runtime selector identities are incomplete or effectful",
            runtime,
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
            sorted((item.package_name, item.install_surface) for item in packages.source_packages)
        ) if packages is not None else ()
        actual_installed = tuple(
            sorted((item.package_name, item.install_surface) for item in packages.installed_packages)
        ) if packages is not None else ()
        source_by_name = {
            item.package_name: item for item in packages.source_packages
        } if packages is not None else {}
        installed_by_key = {
            (item.package_name, item.install_surface): item
            for item in packages.installed_packages
        } if packages is not None else {}
        package_ok = (
            packages is not None
            and actual_source == expected_source
            and actual_installed == expected_installed
            and not packages.drift
            and all(
                source_by_name[name].version == "8.0.0"
                and _valid_guard_digest(source_by_name[name].content_digest)
                and _valid_guard_digest(source_by_name[name].manifest_content_digest)
                and installed_by_key[(name, surface)].version == "8.0.0"
                and installed_by_key[(name, surface)].content_digest
                == source_by_name[name].content_digest
                and installed_by_key[(name, surface)].manifest_content_digest
                == source_by_name[name].manifest_content_digest
                for name in subject.package_names
                for surface in subject.install_surfaces
            )
            and _valid_guard_digest(packages.readback_digest)
        )
        check(
            "package_installation",
            package_ok,
            "CUTOVER_PACKAGE_INVALID",
            "source or installed Skill package identity has drifted",
            packages,
        )

        ordered_names = (
            "legacy",
            "durable_state",
            "writer_fence",
            "ownership",
            "compatibility",
            "runtime",
            "packages",
        )
        def canonical_readback(value: object) -> object:
            if type(value) is dict:
                return value
            canonical = getattr(value, "canonical", None)
            if not callable(canonical):
                raise CutoverGuardError(
                    "CUTOVER_READBACK_INVALID",
                    "readback has no canonical projection",
                )
            return canonical()

        readback_digest = digest_value(
            {
                name: canonical_readback(readbacks[name])
                for name in ordered_names
            }
        )
        ordered_blockers = tuple(
            sorted(blockers, key=lambda item: (item.check_id, item.code, item.detail))
        )
        receipt = None
        if not ordered_blockers:
            receipt_without_digest = {
                "schema": RECEIPT_SCHEMA,
                "repository": subject.repository,
                "subject_digest": digest_value(subject.canonical()),
                "readback_digest": readback_digest,
                "source_writer_generation": subject.source_writer_generation,
                "target_writer_generation": subject.target_writer_generation,
                "store_generation": subject.store_generation,
                "writer_control_ref_digest": writer.control_ref_digest,
                "runtime_configuration_digest": runtime.configuration_digest,
                "compatibility_audit_digest": compatibility.readback_digest,
                "package_readback_digest": packages.readback_digest,
            }
            receipt = CutoverGuardReceipt(
                **receipt_without_digest,
                receipt_digest=digest_value(receipt_without_digest),
            )
        return CutoverGuardReport(
            schema=GUARD_SCHEMA,
            decision="GO" if receipt is not None else "NO_GO",
            repository=subject.repository,
            subject_digest=digest_value(subject.canonical()),
            readback_digest=readback_digest,
            checks=tuple(checks),
            blockers=ordered_blockers,
            receipt=receipt,
        )

    def validate_activation_token(
        self,
        subject: CutoverSubject,
        receipt: CutoverGuardReceipt,
    ) -> None:
        if type(receipt) is not CutoverGuardReceipt:
            raise CutoverGuardError("CUTOVER_GUARD_TOKEN_STALE", "Guard receipt type is invalid")
        if (
            receipt.schema != RECEIPT_SCHEMA
            or receipt.repository != subject.repository
            or receipt.subject_digest != digest_value(subject.canonical())
            or receipt.receipt_digest
            != digest_value(receipt.canonical_without_digest())
        ):
            raise CutoverGuardError(
                "CUTOVER_GUARD_TOKEN_STALE",
                "Guard receipt no longer matches authoritative readback",
            )
        fresh = self.evaluate(subject)
        if fresh.decision != "GO" or fresh.receipt != receipt:
            raise CutoverGuardError(
                "CUTOVER_GUARD_TOKEN_STALE",
                "Guard receipt no longer matches authoritative readback",
            )
~~~

The value-object `canonical()` implementations must return explicit plain dictionaries (not `asdict()` of an open object); the `receipt_digest` is computed only after the seven checks pass. No evaluator branch calls a writer, Runtime command, repository transaction, GitHub CAS, process, installer, or manifest generator.

- [ ] **Step 4: Run the focused tests to prove GREEN.**

~~~powershell
py -3.13 -m pytest tests/test_v8_cutover_guard.py -q
~~~

Expected: all Task 1 tests pass, including the zero-mutation and all-port-read assertions.

- [ ] **Step 5: Refactor while green and run the first regression gate.**

Keep the seven readback values in one immutable local bundle; make canonical() projections explicit plain JSON; reject dataclass subclasses and mutable aliases at the boundary; and keep the public module surface to the exact names used by later tasks. Then run:

~~~powershell
py -3.13 -m pytest tests/test_v8_cutover_guard.py tests/test_v8_runtime_gateway.py tests/test_v8_execution_kernel.py -q
py -3.13 -m py_compile skills/orchestrator/scripts/gwo_v8/cutover_guard.py
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
py -3.13 -c "from pathlib import Path; import json; from scripts.sync_orchestrator import expected_manifest; package=Path('skills/orchestrator'); assert json.loads((package/'.skill-package.json').read_text(encoding='utf-8')) == expected_manifest(package); print('manifest OK')"
git diff --check
~~~

Expected: focused Guard, RuntimeGateway, and ExecutionKernel tests pass; py_compile and git diff --check return zero.

- [ ] **Step 6: Commit the self-contained contract.**

~~~powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
py -3.13 -c "from pathlib import Path; import json; from scripts.sync_orchestrator import expected_manifest; package=Path('skills/orchestrator'); assert json.loads((package/'.skill-package.json').read_text(encoding='utf-8')) == expected_manifest(package); print('manifest OK')"
git add skills/orchestrator/scripts/gwo_v8/cutover_guard.py skills/orchestrator/.skill-package.json tests/cutover_guard_test_support.py tests/test_v8_cutover_guard.py
git commit -m "feat: define the read-only V8 cutover guard"
~~~

### Task 2: Prove legacy-path reachability and package/install integrity without writes

**Files:**
- Modify: skills/orchestrator/scripts/gwo_v8/cutover_guard.py
- Modify: skills/orchestrator/.skill-package.json (generated content hash)
- Create: tests/test_v8_cutover_guard_static.py

**Interfaces:**
- Consumes: CutoverSubject, CompatibilityPathReadback, PackageReadback, and the canonical package functions in scripts/sync_orchestrator.py used only as read helpers (package_digest, manifest_drift, install_drift).
- Produces: `ProductionPathScanner(package_root: Path).read(subject) -> CompatibilityPathReadback` and `ReadOnlyPackageValidator(source_root: Path, install_roots: Mapping[str, Path]).read(subject) -> PackageReadback`, both with no mutation method and no subprocess/provider invocation. `install_roots` must contain exactly `.agents`, `.codex`, and `.claude`; the validator emits each label into `PackageIdentity.install_surface` and each drift identifier.

- [ ] **Step 1: Write the failing static/path/package tests.**

Add these exact tests:

~~~python
def test_production_path_scanner_reports_a_reachable_predecessor_edge(tmp_path):
    package = tmp_path / "skills" / "orchestrator" / "scripts" / "gwo_v8"
    package.mkdir(parents=True)
    (package / "public.py").write_text(
        "from .legacy import LegacyWriter\n\n"
        "def start():\n    return LegacyWriter().write()\n",
        encoding="utf-8",
    )
    (package / "legacy.py").write_text(
        "class LegacyWriter:\n"
        "    def write(self):\n        return None\n",
        encoding="utf-8",
    )
    subject = scanned_subject(tmp_path, ("gwo_v8.public:start",))

    readback = ProductionPathScanner(package_root=tmp_path).read(subject)

    assert readback.reachable_legacy_writer_refs == (
        "gwo_v8.legacy:LegacyWriter.write",
    )


def test_package_validator_detects_manifest_drift_without_rewriting_any_file(tmp_path):
    source = make_two_package_tree(tmp_path / "source")
    installed = make_installed_surfaces(tmp_path)
    manifest = source / "implement-gwo" / ".skill-package.json"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "8.0.0",
            "8.0.0-drift",
        ),
        encoding="utf-8",
    )
    drifted = manifest.read_bytes()

    readback = ReadOnlyPackageValidator(
        source_root=source,
        install_roots=installed,
    ).read(static_subject(tmp_path, ()))

    assert "source:implement-gwo" in readback.drift
    assert manifest.read_bytes() == drifted
    assert not (source / "implement-gwo" / ".skill-package.json.tmp").exists()


def test_package_validator_reads_all_install_surfaces_and_never_installs(tmp_path, monkeypatch):
    source = make_two_package_tree(tmp_path / "source")
    installed = make_installed_surfaces(tmp_path)
    writes = []
    monkeypatch.setattr(
        "shutil.copytree",
        lambda *args, **kwargs: writes.append("copytree"),
    )
    monkeypatch.setattr(
        "os.replace",
        lambda *args, **kwargs: writes.append("replace"),
    )

    readback = ReadOnlyPackageValidator(
        source_root=source,
        install_roots=installed,
    ).read(static_subject(tmp_path, ()))

    assert readback.drift == ()
    assert {item.package_name for item in readback.installed_packages} == {
        "implement-gwo",
        "orchestrator",
    }
    assert writes == []
~~~

The real-root reachability checks are intentionally not in this Task. Add them to Task 5, after the production-composition Result is merged; Task 2 must never claim a pre-composition root path is GREEN. The temporary helper bodies are:

~~~python
def static_subject(root: Path, entry_refs: tuple[str, ...]) -> CutoverSubject:
    del root
    return CutoverSubject(
        repository="owner/repo",
        control_branch="gwo-control",
        target_branch="main",
        source_writer_generation="v6.1",
        target_writer_generation="v8",
        store_generation="store:v8:0001",
        source_commit="a" * 40,
        source_tree_digest="b" * 64,
        production_entry_refs=entry_refs,
    )


def scanned_subject(root: Path, entry_refs: tuple[str, ...]) -> CutoverSubject:
    from dataclasses import replace
    from gwo_v8.cutover_guard import source_tree_digest

    subject = static_subject(root, entry_refs)
    return replace(subject, source_tree_digest=source_tree_digest(root))


def make_two_package_tree(root: Path) -> Path:
    import json
    from scripts.sync_orchestrator import expected_manifest

    root.mkdir(parents=True, exist_ok=True)
    for package in ("implement-gwo", "orchestrator"):
        package_root = root / package
        package_root.mkdir()
        (package_root / "SKILL.md").write_text(
            f"# {package}\n", encoding="utf-8"
        )
        (package_root / ".skill-package.json").write_text(
            json.dumps(expected_manifest(package_root), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    return root


def make_installed_surfaces(root: Path) -> dict[str, Path]:
    surfaces = {
        ".agents": root / ".agents" / "skills",
        ".codex": root / ".codex" / "skills",
        ".claude": root / ".claude" / "skills",
    }
    for path in surfaces.values():
        path.mkdir(parents=True, exist_ok=True)
    source = root / "source"
    make_two_package_tree(source)
    for surface in surfaces.values():
        for package in ("implement-gwo", "orchestrator"):
            target = surface / package
            target.mkdir()
            for item in (source / package).iterdir():
                target.joinpath(item.name).write_bytes(item.read_bytes())
    return surfaces
~~~

The temporary manifests are created by the fixture only when a test explicitly needs a matching digest; the validator never calls `write_manifest`, `install_atomic`, `shutil.copytree`, or `os.replace`.

- [ ] **Step 2: Run RED.**

~~~powershell
py -3.13 -m pytest tests/test_v8_cutover_guard_static.py -q
~~~

Expected: FAIL because ProductionPathScanner and ReadOnlyPackageValidator are not defined.

- [ ] **Step 3: Implement the static scanner and read-only package validator.**

Implement the two read adapters with concrete bodies. The scanner never imports or executes a discovered module; the only importlib use below loads the existing package helper module, whose read helpers have no import-time effects:

~~~python
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping

from gwo_v8._canonical import digest_bytes, digest_value
from gwo_v8.cutover_guard import (
    CompatibilityPathReadback,
    CutoverGuardError,
    CutoverSubject,
    PackageIdentity,
    PackageReadback,
)


def _audited_files(root: Path) -> tuple[Path, ...]:
    candidates = [
        root / "skills" / "implement-gwo" / "SKILL.md",
        root / "skills" / "orchestrator" / "SKILL.md",
        *(root / "skills" / "orchestrator" / "scripts" / "gwo_v8").rglob("*.py"),
    ]
    return tuple(
        sorted(
            (path for path in candidates if path.is_file()),
            key=lambda path: path.relative_to(root).as_posix(),
        )
    )


def source_tree_digest(package_root: Path) -> str:
    digest = hashlib.sha256()
    root = Path(package_root).resolve()
    for path in _audited_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


class ProductionPathScanner:
    def __init__(self, package_root: Path) -> None:
        self._root = Path(package_root).resolve()

    def _module_path(self, module: str) -> Path:
        return (
            self._root / "skills" / "orchestrator" / "scripts" / "gwo_v8"
            / Path(*module.split(".")[1:])
        ).with_suffix(".py")

    @staticmethod
    def _relative_imports(module: str, tree: ast.AST) -> dict[str, str]:
        aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 1:
                continue
            parent = module.rsplit(".", 1)[0]
            imported_module = f"{parent}.{node.module}" if node.module else parent
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{imported_module}:{alias.name}"
        return aliases

    @staticmethod
    def _ast_refs(module: str, tree: ast.AST) -> tuple[str, ...]:
        aliases = ProductionPathScanner._relative_imports(module, tree)
        refs: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                value = node.func.value
                if (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id in aliases
                ):
                    refs.add(f"{aliases[value.func.id]}.{node.func.attr}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                target = aliases.get(node.func.id)
                if target is not None:
                    refs.add(target)
        return tuple(sorted(refs))

    @staticmethod
    def _bucket(ref: str) -> str:
        if ref.startswith("skills/implement-gwo:") or "PlanCompiler" in ref:
            return "v2"
        if "PaseoRuntimeAdapter" in ref or "Runtime" in ref:
            return "v3"
        return "legacy"

    def read(self, subject: CutoverSubject) -> CompatibilityPathReadback:
        observed_tree = source_tree_digest(self._root)
        if observed_tree != subject.source_tree_digest:
            raise CutoverGuardError(
                "CUTOVER_COMPATIBILITY_AUDIT_INVALID",
                "production path audit tree digest does not match the subject",
            )
        forbidden = set(subject.forbidden_production_refs)
        found: set[str] = set()
        queue = [entry.split(":", 1)[0] for entry in subject.production_entry_refs if ":" in entry]
        visited: set[str] = set()
        while queue:
            module = queue.pop()
            if module in visited:
                continue
            visited.add(module)
            path = self._module_path(module)
            if not path.is_file():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for ref in self._ast_refs(module, tree):
                if ref in forbidden:
                    found.add(ref)
                if ":" in ref and ref.split(":", 1)[0].startswith("gwo_v8."):
                    queue.append(ref.split(":", 1)[0])
        for entry in subject.production_entry_refs:
            if entry.startswith("skills/"):
                path = self._root / entry
                if path.is_file():
                    text = path.read_text(encoding="utf-8")
                    found.update(ref for ref in forbidden if ref in text)
        buckets = {
            "v2": tuple(sorted(ref for ref in found if self._bucket(ref) == "v2")),
            "v3": tuple(sorted(ref for ref in found if self._bucket(ref) == "v3")),
            "legacy": tuple(sorted(ref for ref in found if self._bucket(ref) == "legacy")),
        }
        proven = tuple(sorted(forbidden - found))
        values: dict[str, Any] = {
            "repository": subject.repository,
            "source_commit": subject.source_commit,
            "source_tree_digest": observed_tree,
            "audit_version": "gwo.cutover-path-audit.v1",
            "reachable_v2_projection_refs": buckets["v2"],
            "reachable_v3_compatibility_refs": buckets["v3"],
            "reachable_legacy_writer_refs": buckets["legacy"],
            "proven_unreachable_refs": proven,
        }
        return CompatibilityPathReadback(**values, readback_digest=digest_value(values))


class ReadOnlyPackageValidator:
    def __init__(
        self,
        source_root: Path,
        install_roots: Mapping[str, Path],
    ) -> None:
        if tuple(install_roots) != (".agents", ".codex", ".claude"):
            raise CutoverGuardError(
                "CUTOVER_PACKAGE_INVALID",
                "package validator requires the three ordered install surfaces",
            )
        self._source_root = Path(source_root).resolve()
        self._install_roots = {
            surface: Path(path).resolve() for surface, path in install_roots.items()
        }

    @staticmethod
    def _sync_module() -> Any:
        script = Path(__file__).resolve().parents[4] / "scripts" / "sync_orchestrator.py"
        spec = importlib.util.spec_from_file_location("gwo_sync_read_helpers", script)
        if spec is None or spec.loader is None:
            raise CutoverGuardError("CUTOVER_PACKAGE_INVALID", "sync helper is unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _package_path(self, name: str) -> Path:
        in_skills = self._source_root / "skills" / name
        return in_skills if in_skills.is_dir() else self._source_root / name

    @staticmethod
    def _identity(
        package: Path,
        name: str,
        surface: str | None,
        sync: Any,
    ) -> PackageIdentity:
        manifest_path = package / ".skill-package.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return PackageIdentity(
            package_name=name,
            version=manifest["version"],
            content_digest=sync.package_digest(package),
            manifest_content_digest=digest_bytes(manifest_path.read_bytes()),
            install_surface=surface,
        )

    def read(self, subject: CutoverSubject) -> PackageReadback:
        sync = self._sync_module()
        source_packages: list[PackageIdentity] = []
        installed_packages: list[PackageIdentity] = []
        drift: set[str] = set()
        for name in subject.package_names:
            source = self._package_path(name)
            try:
                source_packages.append(self._identity(source, name, None, sync))
                if sync.manifest_drift(source):
                    drift.update({f"source:{name}", f"manifest:{name}"})
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                drift.add(f"source:{name}")
            for surface, root in self._install_roots.items():
                installed = root / name
                try:
                    installed_packages.append(self._identity(installed, name, surface, sync))
                    if sync.install_drift(source, root):
                        drift.add(f"installed:{surface}:{name}")
                except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                    drift.add(f"installed:{surface}:{name}")
        values = {
            "source_packages": tuple(source_packages),
            "installed_packages": tuple(installed_packages),
            "drift": tuple(sorted(drift)),
        }
        return PackageReadback(**values, readback_digest=digest_value(values))
~~~

- [ ] **Step 4: Run GREEN and package/path regressions.**

~~~powershell
py -3.13 -m pytest tests/test_v8_cutover_guard_static.py tests/test_orchestrator_package.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
py -3.13 -c "from pathlib import Path; import json; from scripts.sync_orchestrator import expected_manifest; package=Path('skills/orchestrator'); assert json.loads((package/'.skill-package.json').read_text(encoding='utf-8')) == expected_manifest(package); print('manifest OK')"
py -3.13 scripts/quick_validate.py
~~~

Expected: the temporary AST and package tests plus existing package checks pass. No real repository path test runs in Task 2; Task 5 owns the real-root gate after the preceding production-composition Result has been read back.

- [ ] **Step 5: Refactor and commit the static proof.**

Keep the AST scanner conservative: a false reachable result is a NO_GO, never a GO. Verify every path and package failure is data-only and preserves the input files byte-for-byte. Run git diff --check, then commit:

~~~powershell
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
py -3.13 -c "from pathlib import Path; import json; from scripts.sync_orchestrator import expected_manifest; package=Path('skills/orchestrator'); assert json.loads((package/'.skill-package.json').read_text(encoding='utf-8')) == expected_manifest(package); print('manifest OK')"
git diff --check
git add skills/orchestrator/scripts/gwo_v8/cutover_guard.py skills/orchestrator/.skill-package.json tests/test_v8_cutover_guard_static.py
git commit -m "feat: audit V8 cutover paths and package integrity read-only"
~~~

### Task 3: Add host composition and a Runtime-only configuration preflight

**Files:**
- Modify: skills/orchestrator/scripts/gwo_v8/cutover_guard.py
- Modify: skills/orchestrator/scripts/gwo_v8/plan_control_host.py
- Modify: skills/orchestrator/.skill-package.json (generated content hash)
- Create: tests/test_v8_cutover_guard_host.py
- Modify: tests/cutover_guard_test_support.py
- Modify: tests/test_v8_plancontrol_production.py, tests/test_v8_plancontrol_rebuild.py, tests/test_v8_production_host.py, and tests/test_v8_successor_host.py to pass the resolver to every direct `ProductionPlanControlStartHost(...)` fixture constructor

**Interfaces:**
- Consumes: RuntimeConfiguration, its immutable Profile/selector mappings, CutoverGuardSources, and the preceding production composition’s read-only V6.1/store/ownership/path/package adapters.
- Produces: RuntimeConfigurationReader.read(repository, selectors), `CutoverGuardRequest`, `ProductionCutoverGuardHost.check(subject)`, `ProductionCutoverGuardHost.validate_activation(subject, receipt)`, `ProductionPlanControlStartHost.install_cutover_guard(sources)`, `install_cutover_guard(sources) -> ProductionCutoverGuardHost`, and the live CLI factory `load_production_cutover_guard(request) -> ProductionCutoverGuardHost`. The live factory consumes the already composed production host’s read-only adapters; it does not discover or accept a caller-supplied readback file.

The exact live factory contract is:

~~~python
class PlanControlError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class CutoverGuardRequest:
    subject: CutoverSubject
    package_root: Path
    install_roots: tuple[Path, Path, Path]


def load_production_cutover_guard(
    request: CutoverGuardRequest,
) -> ProductionCutoverGuardHost:
    if type(request) is not CutoverGuardRequest:
        raise PlanControlError(
            "CUTOVER_GUARD_COMPOSITION_INVALID",
            "the live Guard request must be one exact CutoverGuardRequest",
        )
    labels = tuple(path.parent.name for path in request.install_roots)
    if labels != (".agents", ".codex", ".claude"):
        raise PlanControlError(
            "CUTOVER_GUARD_COMPOSITION_INVALID",
            "live install roots must be .agents, .codex, .claude in order",
        )
    start_host = _installed_v3_start_host()
    sources = start_host.resolve_cutover_guard_sources(request)
    return start_host.install_cutover_guard(sources=sources)
~~~

`load_production_cutover_guard` reads the already installed V3 start host; it never discovers a JSON bundle and never constructs a reader from caller text. The host is built with one exact resolver injection. The resolver receives four existing V3 read-only adapters plus the immutable RuntimeConfiguration, and constructs the remaining three adapters from the request:

~~~python
class ProductionCutoverReadAdapterResolver:
    def __init__(
        self,
        *,
        legacy: LegacyReadPort,
        durable_state: DurableStateReadPort,
        writer_fence: WriterFenceReadPort,
        ownership: OwnershipReadPort,
        runtime_configuration: RuntimeConfiguration,
    ) -> None:
        self._legacy = legacy
        self._durable_state = durable_state
        self._writer_fence = writer_fence
        self._ownership = ownership
        self._runtime_configuration = runtime_configuration

    def resolve(
        self,
        *,
        subject: CutoverSubject,
        package_root: Path,
        install_roots: tuple[Path, Path, Path],
    ) -> CutoverGuardSources:
        surfaces = dict(zip(subject.install_surfaces, install_roots, strict=True))
        return CutoverGuardSources(
            legacy=self._legacy,
            durable_state=self._durable_state,
            writer_fence=self._writer_fence,
            ownership=self._ownership,
            compatibility=ProductionPathScanner(package_root),
            runtime=RuntimeConfigurationReader(self._runtime_configuration),
            packages=ReadOnlyPackageValidator(package_root, surfaces),
        )


def make_production_cutover_read_adapter_resolver(
    *,
    v61_legacy_read: LegacyReadPort,
    v8_store_read: DurableStateReadPort,
    writer_generation_read: WriterFenceReadPort,
    v8_ownership_read: OwnershipReadPort,
    runtime_configuration: RuntimeConfiguration,
) -> ProductionCutoverReadAdapterResolver:
    """Bind the four already-composed V3 reads to the Guard resolver."""

    return ProductionCutoverReadAdapterResolver(
        legacy=v61_legacy_read,
        durable_state=v8_store_read,
        writer_fence=writer_generation_read,
        ownership=v8_ownership_read,
        runtime_configuration=runtime_configuration,
    )


class ProductionPlanControlStartHost:
    def __init__(
        self,
        *,
        source: CampaignSnapshotSource,
        repository: PlanControlRepository,
        runtime_configuration: RuntimeConfiguration,
        repository_contexts: Mapping[str, RuntimeRepositoryContext],
        gateway_store_path: Path,
        artifact_root: Path,
        maximum_artifact_bytes: int,
        max_snapshot_bytes: int,
        human_source: object | None,
        _gateway_builder: object | None,
        cutover_read_adapter_resolver: ProductionCutoverReadAdapterResolver,
    ) -> None:
        if type(cutover_read_adapter_resolver) is not ProductionCutoverReadAdapterResolver:
            raise PlanControlError(
                "CUTOVER_GUARD_COMPOSITION_INVALID",
                "the start host requires one resolver-backed V3 read composition",
            )
        self._source = source
        self._repository = repository
        self._runtime_configuration = runtime_configuration
        self._repository_contexts = repository_contexts
        self._gateway_store_path = gateway_store_path
        self._artifact_root = artifact_root
        self._maximum_artifact_bytes = maximum_artifact_bytes
        self._max_snapshot_bytes = max_snapshot_bytes
        self._human_source = human_source
        self._gateway_builder = _gateway_builder
        self._cutover_read_adapter_resolver = cutover_read_adapter_resolver

    def resolve_cutover_guard_sources(
        self,
        request: CutoverGuardRequest,
    ) -> CutoverGuardSources:
        resolver = getattr(self, "_cutover_read_adapter_resolver", None)
        if type(resolver) is not ProductionCutoverReadAdapterResolver:
            raise PlanControlError(
                "CUTOVER_GUARD_COMPOSITION_INVALID",
                "the installed start host has no exact Guard resolver",
            )
        return resolver.resolve(
            subject=request.subject,
            package_root=request.package_root,
            install_roots=request.install_roots,
        )

    def install_cutover_guard(
        self,
        *,
        sources: CutoverGuardSources,
    ) -> ProductionCutoverGuardHost:
        return install_cutover_guard(sources=sources)


def production_start_host_constructor(
    *,
    source: CampaignSnapshotSource,
    repository: PlanControlRepository,
    runtime_configuration: RuntimeConfiguration,
    repository_contexts: Mapping[str, RuntimeRepositoryContext],
    gateway_store_path: Path,
    artifact_root: Path,
    maximum_artifact_bytes: int,
    max_snapshot_bytes: int,
    human_source: object | None,
    gateway_builder: object | None,
    cutover_read_adapter_resolver: ProductionCutoverReadAdapterResolver,
) -> ProductionPlanControlStartHost:
    host = ProductionPlanControlStartHost(
        source=source,
        repository=repository,
        runtime_configuration=runtime_configuration,
        repository_contexts=repository_contexts,
        gateway_store_path=gateway_store_path,
        artifact_root=artifact_root,
        maximum_artifact_bytes=maximum_artifact_bytes,
        max_snapshot_bytes=max_snapshot_bytes,
        human_source=human_source,
        _gateway_builder=gateway_builder,
        cutover_read_adapter_resolver=cutover_read_adapter_resolver,
    )
    return host


def install_production_start_host(
    *,
    source: CampaignSnapshotSource,
    repository: PlanControlRepository,
    runtime_configuration: RuntimeConfiguration,
    repository_contexts: Mapping[str, RuntimeRepositoryContext],
    gateway_store_path: Path,
    artifact_root: Path,
    maximum_artifact_bytes: int,
    max_snapshot_bytes: int,
    human_source: object | None,
    gateway_builder: object | None,
    v61_legacy_read: LegacyReadPort,
    v8_store_read: DurableStateReadPort,
    writer_generation_read: WriterFenceReadPort,
    v8_ownership_read: OwnershipReadPort,
) -> ProductionPlanControlStartHost:
    resolver = make_production_cutover_read_adapter_resolver(
        v61_legacy_read=v61_legacy_read,
        v8_store_read=v8_store_read,
        writer_generation_read=writer_generation_read,
        v8_ownership_read=v8_ownership_read,
        runtime_configuration=runtime_configuration,
    )
    return production_start_host_constructor(
        source=source,
        repository=repository,
        runtime_configuration=runtime_configuration,
        repository_contexts=repository_contexts,
        gateway_store_path=gateway_store_path,
        artifact_root=artifact_root,
        maximum_artifact_bytes=maximum_artifact_bytes,
        max_snapshot_bytes=max_snapshot_bytes,
        human_source=human_source,
        gateway_builder=gateway_builder,
        cutover_read_adapter_resolver=resolver,
    )


def _installed_v3_start_host() -> ProductionPlanControlStartHost:
    from .plan_control import _default_start_host

    host = _default_start_host
    if type(host) is not ProductionPlanControlStartHost:
        raise PlanControlError(
            "CUTOVER_GUARD_COMPOSITION_INVALID",
            "the installed default host is not the composed V3 start host",
        )
    return host
~~~

Add the constructor and two Guard methods shown above to the existing
`ProductionPlanControlStartHost`; its existing `start`, `advance`, `inspect`,
and readback methods remain in the class alongside these methods. Update the
existing `install_plan_control_start` and `install_github_plan_control_start`
paths so the host installed as `gwo_v8.plan_control._default_start_host` always
receives the resolver; do not create a second uninstalled production host. All
four named test modules must pass an exact resolver fixture as well, and a
direct constructor without one must fail with
`CUTOVER_GUARD_COMPOSITION_INVALID` rather than silently disabling the Guard.

`ProductionPlanControlStartHost.__init__` has the existing constructor arguments plus the required keyword `cutover_read_adapter_resolver: ProductionCutoverReadAdapterResolver`; the production installer constructs it from the V3 source/repository writer, store, ownership, and host Runtime configuration. `resolve_cutover_guard_sources` calls only that resolver, so the seven live sources are fixed as `legacy`, `durable_state`, `writer_fence`, `ownership`, `ProductionPathScanner`, `RuntimeConfigurationReader`, and `ReadOnlyPackageValidator`. A missing host, stale composition, wrong source type, or mutating surface raises `PlanControlError("CUTOVER_GUARD_COMPOSITION_INVALID", detail)` before a Guard is retained.

The existing `install_plan_control_start` and `install_github_plan_control_start` functions must add the same required keyword and forward it to `ProductionPlanControlStartHost`; their current argument order and all non-Guard behavior remain unchanged. The final `install_github_plan_control_start` call is the only production default-host installation path, so it must not leave `_default_start_host` pointing at a resolver-less host. Update every direct constructor in the four named test modules from the File list to pass a real in-memory `ProductionCutoverReadAdapterResolver`; a constructor that omits it is an intentional RED assertion for `CUTOVER_GUARD_COMPOSITION_INVALID`.

The Runtime reader has constructor `RuntimeConfigurationReader(configuration: RuntimeConfiguration)` and read method `read(repository: str, selectors: tuple[str, ...]) -> RuntimePreflightReadback`; the complete body is shown in Task 3 Step 3. It uses the existing `_runtime_configuration_canonical(configuration)` helper for the configuration digest; it does not add or assume a public `RuntimeConfiguration.canonical()` method.

Add this exact fixture helper to `tests/cutover_guard_test_support.py` and import
it in the four existing host-constructor test modules when they build a
`ProductionPlanControlStartHost`:

~~~python
from gwo_v8.plan_control_host import ProductionCutoverReadAdapterResolver


def valid_cutover_read_adapter_resolver() -> ProductionCutoverReadAdapterResolver:
    harness = GuardHarness.valid()
    return ProductionCutoverReadAdapterResolver(
        legacy=harness.legacy,
        durable_state=harness.durable,
        writer_fence=harness.writer,
        ownership=harness.ownership,
        runtime_configuration=valid_runtime_configuration(),
    )
~~~

- [ ] **Step 1: Write the failing host and Runtime tests.**

~~~python
def test_runtime_configuration_reader_resolves_exact_required_selectors_without_gateway_or_store(monkeypatch):
    configuration = valid_runtime_configuration()
    reader = RuntimeConfigurationReader(configuration)
    monkeypatch.setattr(
        RuntimeGateway,
        "__init__",
        forbidden_runtime_gateway_constructor,
    )
    monkeypatch.setattr(sqlite3, "connect", forbidden_sqlite_connect)

    readback = reader.read("owner/repo", REQUIRED_RUNTIME_SELECTORS)

    assert tuple(item.selector for item in readback.selectors) == REQUIRED_RUNTIME_SELECTORS
    assert readback.provider_action_refs == ()
    assert readback.persistence_write_refs == ()
    from gwo_v8.runtime_gateway import _runtime_configuration_canonical

    assert readback.configuration_digest == digest_value(
        _runtime_configuration_canonical(configuration)
    )


def test_runtime_configuration_reader_fails_closed_on_missing_review_strong_mapping():
    configuration = runtime_configuration_without("review_strong")

    with pytest.raises(CutoverGuardError) as error:
        RuntimeConfigurationReader(configuration).read(
            "owner/repo",
            REQUIRED_RUNTIME_SELECTORS,
        )

    assert error.value.code == "CUTOVER_RUNTIME_CONFIGURATION_INVALID"


def test_host_check_is_read_only_and_does_not_expose_activation():
    harness = GuardHarness.valid()
    host = install_cutover_guard(sources=harness.sources)

    report = host.check(harness.subject)

    assert report.decision == "GO"
    assert callable(host.validate_activation)
    assert not hasattr(host, "activate")
    assert not hasattr(host, "publish_activation")
    assert harness.mutation_calls() == ()


def test_existing_start_host_can_install_guard_without_replacing_v3_public_reader(tmp_path):
    start_host = valid_production_start_host(tmp_path)
    harness = GuardHarness.valid()

    guard_host = start_host.install_cutover_guard(sources=harness.sources)

    assert guard_host.check(harness.subject).decision == "GO"
    assert start_host.read_active is not None
    assert start_host.start is not None


def test_guard_host_rejects_a_source_object_with_mutating_surfaces():
    harness = GuardHarness.valid()
    harness.sources = replace(
        harness.sources,
        legacy=MutatingLegacyReader(),
    )

    with pytest.raises(PlanControlError) as error:
        install_cutover_guard(sources=harness.sources)

    assert error.value.code == "CUTOVER_GUARD_COMPOSITION_INVALID"
~~~

Put the remaining host-test helpers in `tests/cutover_guard_test_support.py` with these bodies:

~~~python
from types import SimpleNamespace

from gwo_v8.runtime_gateway import (
    ProfileMapping,
    RuntimeConfiguration,
    RuntimeGateway,
)
from gwo_v8.runtime_profile import RuntimeProfile


def valid_runtime_configuration() -> RuntimeConfiguration:
    profile = RuntimeProfile(
        name="test-profile",
        provider="test-provider",
        model="test-model",
        thinking="standard",
        mode="batch",
        features={},
    )
    mapping = {
        selector: ProfileMapping(profile.digest)
        for selector in REQUIRED_RUNTIME_SELECTORS
    }
    return RuntimeConfiguration(
        profiles={profile.digest: profile},
        host_mappings=mapping,
        repository_mappings={"owner/repo": mapping},
    )


def runtime_configuration_without(selector: str) -> RuntimeConfiguration:
    configuration = valid_runtime_configuration()
    host_mappings = {
        key: value
        for key, value in configuration.host_mappings.items()
        if getattr(key, "value", key) != selector
    }
    repository_mappings = {
        repository: {
            key: value
            for key, value in mappings.items()
            if getattr(key, "value", key) != selector
        }
        for repository, mappings in configuration.repository_mappings.items()
    }
    return RuntimeConfiguration(
        profiles=dict(configuration.profiles),
        host_mappings=host_mappings,
        repository_mappings=repository_mappings,
    )


def forbidden_runtime_gateway_constructor(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("RuntimeConfigurationReader constructed RuntimeGateway")


def forbidden_sqlite_connect(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("RuntimeConfigurationReader opened SQLite")


class _StartHostReadbackDouble:
    def __init__(self) -> None:
        self.read_active = object()
        self.start = object()

    def install_cutover_guard(
        self,
        *,
        sources: CutoverGuardSources,
    ) -> ProductionCutoverGuardHost:
        return install_cutover_guard(sources=sources)


def valid_production_start_host(_tmp_path: Path) -> _StartHostReadbackDouble:
    return _StartHostReadbackDouble()


class MutatingLegacyReader:
    def read(self, repository: str) -> LegacyReadback:
        return GuardHarness.valid().legacy.read(repository)

    def stop(self, repository: str) -> None:
        raise AssertionError(f"unexpected stop({repository})")

    def restore(self, repository: str) -> None:
        raise AssertionError(f"unexpected restore({repository})")
~~~

- [ ] **Step 2: Run RED.**

~~~powershell
py -3.13 -m pytest tests/test_v8_cutover_guard_host.py -q
~~~

Expected: FAIL with missing RuntimeConfigurationReader, ProductionCutoverGuardHost, or install_cutover_guard symbols.

- [ ] **Step 3: Implement the host-only Runtime probe and Guard host.**

Implement the Runtime reader, resolver-backed host, and host forwarding methods with these bodies:

~~~python
from dataclasses import fields
from gwo_v8._canonical import digest_value
from gwo_v8.cutover_guard import (
    CompatibilityPathReadPort,
    CutoverGuard,
    CutoverGuardError,
    CutoverGuardReceipt,
    CutoverGuardSources,
    CutoverSubject,
    DurableStateReadPort,
    GuardActivationValidator,
    LegacyReadPort,
    PackageReadPort,
    RuntimePreflightReadPort,
    WriterFenceReadPort,
    OwnershipReadPort,
)
from gwo_v8.runtime_gateway import (
    RuntimeSelector,
    _runtime_configuration_canonical,
)


class RuntimeConfigurationReader:
    def __init__(self, configuration: RuntimeConfiguration) -> None:
        if type(configuration) is not RuntimeConfiguration:
            raise CutoverGuardError(
                "CUTOVER_RUNTIME_CONFIGURATION_INVALID",
                "Runtime configuration is not one exact immutable host value",
            )
        self._configuration = configuration

    def read(
        self,
        repository: str,
        selectors: tuple[str, ...],
    ) -> RuntimePreflightReadback:
        try:
            configuration_digest = digest_value(
                _runtime_configuration_canonical(self._configuration)
            )
            repository_mappings = self._configuration.repository_mappings.get(
                repository, {}
            )
            resolved: list[RuntimeSelectorReadback] = []
            for selector_text in selectors:
                selector = RuntimeSelector(selector_text)
                mapping = repository_mappings.get(selector)
                source = "repository"
                if mapping is None:
                    mapping = self._configuration.host_mappings.get(selector)
                    source = "host_global"
                if mapping is None:
                    raise ValueError(f"missing Runtime mapping for {selector_text}")
                primary = self._configuration.profiles.get(mapping.primary_profile_digest)
                if primary is None or primary.digest != mapping.primary_profile_digest:
                    raise ValueError(f"invalid primary Profile for {selector_text}")
                fallback_digest = mapping.availability_fallback_profile_digest
                fallback = (
                    None
                    if fallback_digest is None
                    else self._configuration.profiles.get(fallback_digest)
                )
                if fallback_digest is not None and (
                    fallback is None or fallback.digest != fallback_digest
                ):
                    raise ValueError(f"invalid fallback Profile for {selector_text}")
                resolved.append(
                    RuntimeSelectorReadback(
                        selector=selector.value,
                        profile_digest=primary.digest,
                        fallback_profile_digest=(
                            None if fallback is None else fallback.digest
                        ),
                        configuration_source=source,
                    )
                )
            values = {
                "repository": repository,
                "selectors": tuple(resolved),
                "configuration_digest": configuration_digest,
                "provider_action_refs": (),
                "persistence_write_refs": (),
            }
            return RuntimePreflightReadback(
                **values,
                readback_digest=digest_value(values),
            )
        except Exception as error:
            if isinstance(error, CutoverGuardError):
                raise
            raise CutoverGuardError(
                "CUTOVER_RUNTIME_CONFIGURATION_INVALID",
                "required Runtime selector mapping or Profile identity is invalid",
            ) from error


class ProductionCutoverGuardHost:
    def __init__(
        self,
        *,
        guard: CutoverGuard,
        sources: CutoverGuardSources,
    ) -> None:
        self._guard = guard
        self._sources = sources

    def check(self, subject: CutoverSubject) -> CutoverGuardReport:
        return self._guard.evaluate(subject)

    def validate_activation(
        self,
        subject: CutoverSubject,
        receipt: CutoverGuardReceipt,
    ) -> None:
        self._guard.validate_activation_token(subject, receipt)


_FORBIDDEN_SOURCE_NAMES = frozenset(
    {
        "stop",
        "restore",
        "drain",
        "publish",
        "compare_and_swap",
        "write",
        "prepare",
        "command",
        "events",
        "install",
        "start",
    }
)


def _declared_surface(value: object) -> set[str]:
    names = set(vars(value)) if hasattr(value, "__dict__") else set()
    for cls in type(value).__mro__:
        names.update(vars(cls))
    return names


def install_cutover_guard(*, sources: CutoverGuardSources) -> ProductionCutoverGuardHost:
    if type(sources) is not CutoverGuardSources:
        raise PlanControlError(
            "CUTOVER_GUARD_COMPOSITION_INVALID",
            "Guard composition must use one exact source value",
        )
    for field in fields(CutoverGuardSources):
        source = getattr(sources, field.name)
        surface = _declared_surface(source)
        if "read" not in surface or _FORBIDDEN_SOURCE_NAMES.intersection(surface):
            raise PlanControlError(
                "CUTOVER_GUARD_COMPOSITION_INVALID",
                f"{field.name} is not a read-only Guard adapter",
            )
        if not callable(getattr(source, "read", None)):
            raise PlanControlError(
                "CUTOVER_GUARD_COMPOSITION_INVALID",
                f"{field.name}.read is not callable",
            )
    return ProductionCutoverGuardHost(
        guard=CutoverGuard(sources),
        sources=sources,
    )


~~~

The resolver is injected when the V3 `ProductionPlanControlStartHost` is constructed; it is not discovered by the Guard. The adapter surface check inspects declared class/instance names rather than probing forbidden names through `__getattr__`, so a valid fake cannot be mistaken for a writer. These methods do not recompose or replace PlanControl, persist a Guard record, install a package, or expose an activation method.

- [ ] **Step 4: Run GREEN and host regressions.**

~~~powershell
py -3.13 -m pytest tests/test_v8_cutover_guard_host.py tests/test_v8_successor_host.py tests/test_v8_plancontrol_production.py tests/test_v8_plancontrol_rebuild.py tests/test_v8_production_host.py -q
~~~

Expected: all host tests pass; RuntimeGateway and SQLite tripwires report zero calls from the configuration probe.

- [ ] **Step 5: Refactor and commit.**

Run:

~~~powershell
py -3.13 -m py_compile skills/orchestrator/scripts/gwo_v8/cutover_guard.py skills/orchestrator/scripts/gwo_v8/plan_control_host.py
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
py -3.13 -c "from pathlib import Path; import json; from scripts.sync_orchestrator import expected_manifest; package=Path('skills/orchestrator'); assert json.loads((package/'.skill-package.json').read_text(encoding='utf-8')) == expected_manifest(package); print('manifest OK')"
git diff --check
git add skills/orchestrator/scripts/gwo_v8/cutover_guard.py skills/orchestrator/scripts/gwo_v8/plan_control_host.py skills/orchestrator/.skill-package.json tests/cutover_guard_test_support.py tests/test_v8_cutover_guard_host.py tests/test_v8_plancontrol_production.py tests/test_v8_plancontrol_rebuild.py tests/test_v8_production_host.py tests/test_v8_successor_host.py
git commit -m "feat: expose the read-only cutover guard host"
~~~

### Task 4: Require the Guard token at the existing fenced activation point

**Files:**
- Modify: skills/orchestrator/scripts/gwo_v8/transition.py
- Modify: skills/orchestrator/.skill-package.json (generated content hash)
- Create: tests/test_v8_cutover_activation.py
- Modify: tests/test_orchestrator_v8_phase4bc.py only where existing activation fixtures call WriterCutoverController.cutover or assert its constructor

**Interfaces:**
- Consumes: GuardActivationValidator, CutoverSubject, CutoverGuardReceipt, and ProductionCutoverGuardHost.validate_activation from Tasks 1 and 3.
- Produces: a guarded WriterCutoverController whose cutover accepts exact guard_subject and guard_receipt keywords before any V6.1 stop, transition publication, local CAS, GitHub CAS, or Activation publication.

- [ ] **Step 1: Write failing activation and rollback-boundary tests.**

~~~python
def test_cutover_without_guard_token_returns_blocked_before_any_mutation(tmp_path):
    fixture = activation_fixture(tmp_path)

    outcome = fixture.controller.cutover(
        fixture.compiled_plan,
        canary=fixture.accepted_canary,
        guard_subject=fixture.subject,
        guard_receipt=None,
        writer_generation="v8",
        worker_capacity=8,
        coordinator_capacity=1,
    )

    assert outcome.status == "blocked"
    assert outcome.blockers == ("CUTOVER_GUARD_REQUIRED",)
    assert fixture.mutation_calls() == ()
    assert fixture.transitions.history(fixture.repository) == ()
    assert fixture.publication.read_active(fixture.repository) is None
    assert fixture.publication.durable.read_current_activation(fixture.repository) is None


def test_stale_guard_token_returns_blocked_before_v61_stop_or_activation(tmp_path):
    fixture = activation_fixture(tmp_path)
    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None
    fixture.writer_readback.value = replace(
        fixture.writer_readback.value,
        control_ref_digest="d" * 64,
    )

    outcome = fixture.controller.cutover(
        fixture.compiled_plan,
        canary=fixture.accepted_canary,
        guard_subject=fixture.subject,
        guard_receipt=report.receipt,
        writer_generation="v8",
        worker_capacity=8,
        coordinator_capacity=1,
    )

    assert outcome.status == "blocked"
    assert outcome.blockers == ("CUTOVER_GUARD_TOKEN_STALE",)
    assert fixture.mutation_calls() == ()
    assert fixture.legacy.readback(fixture.repository).authority_state == "authoritative_quiescent"


def test_fresh_guard_allows_existing_activation_receipt_commit_and_readback(tmp_path):
    fixture = activation_fixture(tmp_path)
    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None

    outcome = fixture.controller.cutover(
        fixture.compiled_plan,
        canary=fixture.accepted_canary,
        guard_subject=fixture.subject,
        guard_receipt=report.receipt,
        writer_generation="v8",
        worker_capacity=8,
        coordinator_capacity=1,
    )

    assert outcome.status == "cut_over"
    receipt = fixture.publication.durable.read_current_activation(fixture.repository)
    assert receipt is not None
    assert receipt.repository == fixture.repository
    assert receipt.writer_generation == "v8"
    assert receipt.plan_digest == fixture.compiled_plan.digest
    assert fixture.transitions.allows_new_work(
        fixture.repository,
        "v8",
        receipt.activation_id,
    )


def test_pending_activation_cannot_admit_work_before_activation_receipt(tmp_path):
    fixture = activation_fixture(tmp_path, fail_after={"publish_activation"})
    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None

    with pytest.raises(ActivationError) as error:
        fixture.controller.cutover(
            fixture.compiled_plan,
            canary=fixture.accepted_canary,
            guard_subject=fixture.subject,
            guard_receipt=report.receipt,
            writer_generation="v8",
            worker_capacity=8,
            coordinator_capacity=1,
        )

    assert error.value.code == "DURABLE_STATE_AMBIGUOUS"
    assert fixture.publication.read_active(fixture.repository) is None
    assert fixture.transitions.capacity_limits(
        fixture.repository,
        "v8",
        "unread-back-activation",
    ) == (0, 0)


def test_receipt_backed_rollback_is_new_compensating_record_and_preserves_receipt(tmp_path):
    fixture = activation_fixture(tmp_path)
    report = fixture.guard.check(fixture.subject)
    assert report.receipt is not None
    fixture.controller.cutover(
        fixture.compiled_plan,
        canary=fixture.accepted_canary,
        guard_subject=fixture.subject,
        guard_receipt=report.receipt,
        writer_generation="v8",
        worker_capacity=8,
        coordinator_capacity=1,
    )
    activation = fixture.publication.durable.read_current_activation(fixture.repository)
    assert activation is not None

    rollback = fixture.controller.rollback(
        repository=fixture.repository,
        ownership=fixture.ownership,
        restore_writer_generation="v6.1",
        reason="beta3 rehearsal rollback",
    )

    assert rollback.status == "rolled_back"
    assert fixture.publication.durable.read_activation(
        fixture.repository,
        activation.activation_id,
    ) == activation
    assert fixture.transitions.history(fixture.repository)[-1].kind == "rollback"
    assert fixture.legacy.readback(fixture.repository).authority_state == "authoritative_quiescent"
~~~

Define the activation fixture body in `tests/cutover_guard_test_support.py`; its only mutable state is the isolated `tmp_path` rehearsal:

~~~python
from dataclasses import dataclass
from gwo_v8.activation import ActivationReceipt, InMemoryDurablePlanControl, LocalPlanPublication
from gwo_v8.plan_control_host import ProductionCutoverGuardHost, install_cutover_guard
from gwo_v8.transition import WriterCutoverController, WriterTransitionRecord


class RecordingLegacyControl(InMemoryLegacyWriterControl):
    def __init__(self, calls: list[str]) -> None:
        super().__init__()
        self._calls = calls

    def stop(self, repository: str, *, action_key: str) -> None:
        self._calls.append("legacy.stop")
        super().stop(repository, action_key=action_key)

    def restore(self, repository: str, *, action_key: str) -> None:
        self._calls.append("legacy.restore")
        super().restore(repository, action_key=action_key)


class RecordingTransitions(InMemoryWriterTransitionControl):
    def __init__(self, calls: list[str]) -> None:
        super().__init__(initial_writer="v6.1")
        self._calls = calls

    def publish(self, record: WriterTransitionRecord) -> None:
        self._calls.append("transitions.publish")
        super().publish(record)


class RecordingPublication(LocalPlanPublication):
    def __init__(self, *args: object, calls: list[str], **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._calls = calls

    def publish_and_activate(self, *args: object, **kwargs: object) -> ActivationReceipt:
        self._calls.append("publication.publish_and_activate")
        return super().publish_and_activate(*args, **kwargs)


@dataclass
class ActivationFixture:
    repository: str
    compiled_plan: CompiledPlan
    accepted_canary: CanaryAcceptance
    subject: CutoverSubject
    guard: ProductionCutoverGuardHost
    writer_readback: FakeReadPort
    legacy: RecordingLegacyControl
    transitions: RecordingTransitions
    publication: RecordingPublication
    ownership: V8OwnershipControl
    controller: WriterCutoverController
    calls: list[str]

    def mutation_calls(self) -> tuple[str, ...]:
        return tuple(self.calls)


def activation_fixture(
    tmp_path: Path,
    *,
    fail_after: set[str] | frozenset[str] = frozenset(),
) -> ActivationFixture:
    from test_orchestrator_v8_phase4bc import _accepted_canary, _compiled, _verify_canary

    calls: list[str] = []
    harness = GuardHarness.valid()
    compiled_plan = _compiled()
    accepted_canary = _verify_canary(_accepted_canary())
    legacy = RecordingLegacyControl(calls)
    transitions = RecordingTransitions(calls)

    def checkpoint(name: str) -> None:
        if name in fail_after:
            raise RuntimeError(f"isolated rehearsal failure at {name}")

    durable = InMemoryDurablePlanControl(
        fail_once_after=(
            {"publish_activation"}
            if "publish_activation" in fail_after
            else set()
        )
    )
    publication = RecordingPublication(
        tmp_path / "v8.sqlite3",
        durable=durable,
        writer_authority=transitions,
        checkpoint=checkpoint,
        calls=calls,
    )
    guard = install_cutover_guard(sources=harness.sources)
    ownership = InMemoryV8OwnershipControl(
        V8OwnershipReadback(
            active_admissions=(),
            active_attempts=(),
            integration_lease=False,
            runtime_resources=(),
        )
    )
    controller = WriterCutoverController(
        legacy=legacy,
        transitions=transitions,
        publication=publication,
        guard=guard,
    )
    return ActivationFixture(
        repository=compiled_plan.repository,
        compiled_plan=compiled_plan,
        accepted_canary=accepted_canary,
        subject=harness.subject,
        guard=guard,
        writer_readback=harness.writer,
        legacy=legacy,
        transitions=transitions,
        publication=publication,
        ownership=ownership,
        controller=controller,
        calls=calls,
    )
~~~

The canary is passed only to `WriterCutoverController.cutover`; `ProductionCutoverGuardHost.check` receives only the subject and `validate_activation` re-reads the same seven ports. Every fixture target is beneath pytest `tmp_path`, and no fixture calls the public default host, changes the default writer, installs a package, creates a release tag, or claims GA admission.

- [ ] **Step 2: Run RED.**

~~~powershell
py -3.13 -m pytest tests/test_v8_cutover_activation.py tests/test_orchestrator_v8_phase4bc.py -q
~~~

Expected: the new tests fail because WriterCutoverController does not require a Guard and cutover does not accept/validate guard_subject and guard_receipt.

- [ ] **Step 3: Add the pre-mutation Guard fence.**

Replace the controller constructor and cutover method with this complete body. The `_blocked_before_mutation` helper is deliberately separate from the existing durable `blocked_outcome` closure: it performs no transition read or write and is used only before the old cutover body begins.

Before applying the body, add this exact private-module import to
`transition.py`; do not import `plan_control_host.py` from the transition module
because that would create a host/transition cycle:

~~~python
from .cutover_guard import (
    CutoverGuardError,
    CutoverGuardReceipt,
    CutoverSubject,
    GuardActivationValidator,
)
~~~

~~~python
class WriterCutoverController:
    def __init__(
        self,
        *,
        legacy: LegacyWriterControl,
        transitions: WriterTransitionControl,
        publication: LocalPlanPublication,
        guard: GuardActivationValidator,
    ) -> None:
        if publication.writer_authority is not transitions:
            raise ValueError(
                "cutover publication must use the transition control as writer fence"
            )
        self.legacy = legacy
        self.transitions = transitions
        self.publication = publication
        self.guard = guard

    @staticmethod
    def _blocked_before_mutation(
        *,
        repository: str,
        subject: CutoverSubject,
        writer_generation: str,
        blocker: str,
    ) -> WriterTransitionOutcome:
        return WriterTransitionOutcome(
            status="blocked",
            repository=repository,
            writer_generation=writer_generation,
            record_id=f"guard-blocked:{digest_value(subject.canonical())}",
            activation_id=None,
            worker_capacity=0,
            coordinator_capacity=0,
            blockers=(blocker,),
        )

    def cutover(
        self,
        compiled_plan: CompiledPlan,
        *,
        canary: CanaryAcceptance,
        guard_subject: CutoverSubject,
        guard_receipt: CutoverGuardReceipt | None,
        writer_generation: str,
        worker_capacity: int,
        coordinator_capacity: int,
    ) -> WriterTransitionOutcome:
        repository = compiled_plan.repository
        if guard_receipt is None:
            return self._blocked_before_mutation(
                repository=repository,
                subject=guard_subject,
                writer_generation=writer_generation,
                blocker="CUTOVER_GUARD_REQUIRED",
            )
        if (
            guard_subject.repository != repository
            or guard_subject.target_writer_generation != writer_generation
        ):
            return self._blocked_before_mutation(
                repository=repository,
                subject=guard_subject,
                writer_generation=writer_generation,
                blocker="CUTOVER_GUARD_TOKEN_STALE",
            )
        try:
            self.guard.validate_activation(guard_subject, guard_receipt)
        except CutoverGuardError:
            return self._blocked_before_mutation(
                repository=repository,
                subject=guard_subject,
                writer_generation=writer_generation,
                blocker="CUTOVER_GUARD_TOKEN_STALE",
            )

        current = self.transitions.read_current(repository)
        existing = self.transitions.read(repository, current.record_id)
        if current.writer_generation == writer_generation:
            if (
                existing is not None
                and existing.kind == "cutover"
                and existing.status == "cut_over"
                and existing.writer_generation == writer_generation
                and existing.plan_digest == compiled_plan.digest
                and existing.canary_evidence_digest == canary.evidence_package_digest
                and existing.worker_capacity == worker_capacity
                and existing.coordinator_capacity == coordinator_capacity
            ):
                active = self.publication.read_active(repository)
                if active is None or active.activation_id != existing.activation_id:
                    raise ValueError("cutover authority and Activation do not agree")
                return WriterTransitionOutcome(
                    status="cut_over",
                    repository=repository,
                    writer_generation=writer_generation,
                    record_id=existing.record_id,
                    activation_id=existing.activation_id,
                    worker_capacity=worker_capacity,
                    coordinator_capacity=coordinator_capacity,
                )

        def blocked_outcome(blockers: set[str]) -> WriterTransitionOutcome:
            ordered = tuple(sorted(blockers))
            record = _record(
                repository=repository,
                kind="cutover",
                status="blocked",
                previous_writer_generation=current.writer_generation,
                writer_generation=current.writer_generation,
                activation_id=None,
                plan_digest=compiled_plan.digest,
                canary_evidence_digest=canary.evidence_package_digest,
                canary_evidence_refs=canary.evidence_refs,
                canary_manifest_ref=canary.manifest_ref,
                worker_capacity=0,
                coordinator_capacity=0,
                reason=";".join(ordered),
            )
            self.transitions.publish(record)
            return WriterTransitionOutcome(
                status="blocked",
                repository=repository,
                writer_generation=current.writer_generation,
                record_id=record.record_id,
                activation_id=None,
                worker_capacity=0,
                coordinator_capacity=0,
                blockers=ordered,
            )

        blockers: set[str] = set()
        if (
            not canary.accepted
            or canary.evidence_package_digest is None
            or canary.manifest_ref is None
        ):
            blockers.add("CANARY_NOT_ACCEPTED")
        if worker_capacity != 8 or coordinator_capacity != 1:
            blockers.add("CUTOVER_CAPACITY_INVALID")
        resuming_pending = (
            current.writer_generation == writer_generation
            and existing is not None
            and existing.kind == "cutover_pending"
            and existing.status == "pending"
            and existing.plan_digest == compiled_plan.digest
            and existing.canary_evidence_digest == canary.evidence_package_digest
            and existing.canary_manifest_ref == canary.manifest_ref
        )
        if current.writer_generation != "v6.1" and not resuming_pending:
            blockers.add("CUTOVER_SOURCE_WRITER_INVALID")
        if blockers:
            return blocked_outcome(blockers)

        stop_action = f"stop-v61:{digest_value({'repository': repository})[:24]}"
        self.legacy.stop(repository, action_key=stop_action)
        legacy = self.legacy.readback(repository)
        if (
            not legacy.stopped
            or legacy.active_dispatches
            or legacy.integration_lease
            or legacy.active_workers
        ):
            blockers.add("V61_EXECUTION_AUTHORITY_ACTIVE")
        if blockers:
            return blocked_outcome(blockers)
        if not resuming_pending:
            pending = _record(
                repository=repository,
                kind="cutover_pending",
                status="pending",
                previous_writer_generation=current.writer_generation,
                writer_generation=writer_generation,
                activation_id=None,
                plan_digest=compiled_plan.digest,
                canary_evidence_digest=canary.evidence_package_digest,
                canary_evidence_refs=canary.evidence_refs,
                canary_manifest_ref=canary.manifest_ref,
                worker_capacity=0,
                coordinator_capacity=0,
                reason=None,
            )
            self.transitions.publish(pending)
        activation = self.publication.publish_and_activate(
            compiled_plan,
            expected_active_digest=None,
            writer_generation=writer_generation,
        )
        record = _record(
            repository=repository,
            kind="cutover",
            status="cut_over",
            previous_writer_generation=writer_generation,
            writer_generation=writer_generation,
            activation_id=activation.activation_id,
            plan_digest=compiled_plan.digest,
            canary_evidence_digest=canary.evidence_package_digest,
            canary_evidence_refs=canary.evidence_refs,
            canary_manifest_ref=canary.manifest_ref,
            worker_capacity=worker_capacity,
            coordinator_capacity=coordinator_capacity,
            reason=None,
        )
        self.transitions.publish(record)
        if self.transitions.read(repository, record.record_id) != record:
            raise ValueError("durable cutover record did not read back")
        active = self.publication.read_active(repository)
        if active is None or active.activation_id != activation.activation_id:
            raise ValueError("cutover writer fence did not authorize the Activation")
        return WriterTransitionOutcome(
            status="cut_over",
            repository=repository,
            writer_generation=writer_generation,
            record_id=record.record_id,
            activation_id=activation.activation_id,
            worker_capacity=worker_capacity,
            coordinator_capacity=coordinator_capacity,
        )
~~~

The code above runs the seven Guard read ports before any transition read that could
lead to a mutation, before `legacy.stop`, `transitions.publish`,
`publication.publish_and_activate`, any GitHub CAS, and any SQLite write. The
two subject mismatches and any `CutoverGuardError` map to
`CUTOVER_GUARD_TOKEN_STALE`; the missing-token return uses
`CUTOVER_GUARD_REQUIRED`. Do not call the existing durable `blocked_outcome`
helper for these two cases because that helper publishes a record and would
violate zero mutation. Preserve the existing `rollback` and
`begin_writer_drain` method bodies after this guarded cutover method.

Do not add a guard_receipt field to PlanSpec, Campaign state, GitHub state, SQLite, ActivationReceipt, or WriterTransitionRecord; the receipt is the pre-mutation token, while the existing Activation Receipt is the durable commit point.

- [ ] **Step 4: Run GREEN and existing transition regressions.**

~~~powershell
py -3.13 -m pytest tests/test_v8_cutover_activation.py tests/test_orchestrator_v8_phase4bc.py -q
~~~

Expected: all new guard-fence, pending-admission, readback, and rollback tests pass, including the existing restart/rollback tests updated to supply a fresh receipt.

- [ ] **Step 5: Refactor while green and commit.**

Verify that WriterCutoverController.cutover has exactly one call site for `GuardActivationValidator.validate_activation`, that it occurs before legacy.stop, transitions.publish, or publication.publish_and_activate, and that rollback never deletes an Activation Receipt. Run:

~~~powershell
py -3.13 -m pytest tests/test_v8_cutover_activation.py tests/test_v8_cutover_guard.py tests/test_v8_successor_plan_revision.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
py -3.13 -c "from pathlib import Path; import json; from scripts.sync_orchestrator import expected_manifest; package=Path('skills/orchestrator'); assert json.loads((package/'.skill-package.json').read_text(encoding='utf-8')) == expected_manifest(package); print('manifest OK')"
git diff --check
git add skills/orchestrator/scripts/gwo_v8/transition.py skills/orchestrator/.skill-package.json tests/test_v8_cutover_activation.py tests/test_orchestrator_v8_phase4bc.py
git commit -m "feat: fence writer activation with the cutover guard"
~~~

### Task 5: Remove predecessor production reachability from the package boundary

**Files:**
- Modify: skills/orchestrator/scripts/gwo_v8/__init__.py
- Modify: skills/orchestrator/scripts/orch.py (move its three legacy-writer imports to `gwo_v8.activation`/`gwo_v8.transition` after root aliases are removed)
- Modify: skills/orchestrator/.skill-package.json (generated content hash)
- Modify: tests/test_orchestrator_package.py
- Create: tests/test_v8_cutover_guard_api_boundary.py
- Modify imports and root-surface assertions: tests/test_orchestrator_v8_phase2.py, tests/test_orchestrator_v8_phase3.py, tests/test_orchestrator_v8_phase4a.py, tests/test_orchestrator_v8_phase4bc.py, tests/test_orchestrator_v8_walking_skeleton.py, tests/test_v8_canary_runner.py, tests/test_v8_runtime_gateway.py, tests/test_v8_runtime_gateway_repair.py, tests/test_v8_candidate_gate.py, tests/test_v8_candidate_gate_public.py, tests/test_v8_human_gate_public.py, tests/test_v8_plancontrol_production.py, tests/test_v8_successor_plan_revision.py, tests/test_v8_successor_planning_protocol.py, tests/v8_successor_test_support.py
- Verify-only: skills/implement-gwo/SKILL.md and the V3 host created by the production-composition plan

**Interfaces:**
- Consumes: the exact public start/advance/inspect functions, the ProductionPathScanner, and the preceding production-composition Result.
- Produces: a package root whose workflow surface is __all__ = ("advance", "inspect", "start"); release/deep-module classes remain importable from their owning modules, not as legacy root aliases.

- [ ] **Step 1: Write the failing API-boundary tests.**

~~~python
from pathlib import Path

from gwo_v8.cutover_guard import CutoverSubject, source_tree_digest


ROOT = Path(__file__).resolve().parents[1]


def real_static_subject(root: Path, entry_refs: tuple[str, ...]) -> CutoverSubject:
    return CutoverSubject(
        repository="owner/repo",
        control_branch="gwo-control",
        target_branch="main",
        source_writer_generation="v6.1",
        target_writer_generation="v8",
        store_generation="store:v8:0001",
        source_commit="a" * 40,
        source_tree_digest=source_tree_digest(root),
        production_entry_refs=entry_refs,
    )


def test_package_root_exports_only_the_three_public_workflow_operations():
    import gwo_v8

    assert gwo_v8.__all__ == ("advance", "inspect", "start")
    assert callable(gwo_v8.start)
    assert callable(gwo_v8.advance)
    assert callable(gwo_v8.inspect)
    for forbidden in (
        "ImplementGwoEntry",
        "ImplementGwoLauncher",
        "GoalDriver",
        "Kernel",
        "StoreReconstructor",
        "WriterCutoverController",
        "LegacyWriterControl",
        "V8OwnershipControl",
    ):
        assert not hasattr(gwo_v8, forbidden)


def test_v3_public_entrypoints_do_not_import_predecessor_driver_modules():
    audit = ProductionPathScanner(package_root=ROOT).read(
        real_static_subject(
            ROOT,
            (
                "gwo_v8.plan_control_host:ProductionPlanControlStartHost.start",
                "gwo_v8.execution_kernel:advance",
                "gwo_v8.execution_kernel:inspect",
            ),
        ),
    )

    assert audit.reachable_v2_projection_refs == ()
    assert audit.reachable_v3_compatibility_refs == ()
    assert audit.reachable_legacy_writer_refs == ()


def test_skill_surface_cannot_route_to_plancompiler_or_legacy_kernel():
    audit = ProductionPathScanner(package_root=ROOT).read(
        real_static_subject(ROOT, ("skills/implement-gwo/SKILL.md",))
    )

    assert audit.reachable_v2_projection_refs == ()
    assert audit.reachable_legacy_writer_refs == ()


def test_implement_skill_has_no_predecessor_execution_route():
    text = (ROOT / "skills" / "implement-gwo" / "SKILL.md").read_text("utf-8")

    for forbidden in (
        "PlanCompiler",
        "LocalPlanPublication",
        "Kernel.reconcile_once",
        "GoalDriver",
        "Matt `/implement` remains a\nseparate single-ticket workflow",
    ):
        assert forbidden not in text
    for required in (
        "start(repository, ready_refs, options?)",
        "advance(campaign_handle, wake_ref?)",
        "inspect(campaign_handle)",
    ):
        assert required in text
~~~

The preceding production-composition Result must be merged and read back before Step 2. Only then does `real_static_subject(ROOT, entry_refs)` compute the actual digest of the audited files; `static_subject` with `b * 64` is forbidden for either real-root test. The import edits move every removed root import to its owning deep module, and the root-surface assertions in `tests/test_v8_runtime_gateway_repair.py` must assert those direct-module identities instead of querying removed attributes. Use this exact mapping: predecessor Kernel, GoalDriver, and ImplementGwo* imports go to their historical direct modules only in tests that still exercise compatibility fixtures; RuntimeGateway/RuntimeConfiguration imports go to `gwo_v8.runtime_gateway`; RuntimeProfile goes to `gwo_v8.runtime_profile`; RuntimePrompt goes to `gwo_v8.runtime`; PlanCompiler goes to `gwo_v8.compiler`; CandidateGate, PlanControl, ExecutionKernel, IntegrationBatch, and successor types go to their named modules; `skills/orchestrator/scripts/orch.py` imports `ActivationError` from `gwo_v8.activation` and `GitHubLegacyWriterControl`/`LegacyWriterReadback` from `gwo_v8.transition`. Do not add a __getattr__ compatibility fallback to __init__.py.

- [ ] **Step 2: Run RED.**

~~~powershell
py -3.13 -m pytest tests/test_v8_cutover_guard_static.py tests/test_v8_cutover_guard_api_boundary.py tests/test_orchestrator_package.py -q
~~~

Expected: the dependency-gate readback is GREEN for the real-root path and Skill scan; the package-root export test is RED because the current root still exports legacy workflow and mutable transition names. Do not weaken the forbidden-reference list or substitute a fixed digest.

- [ ] **Step 3: Implement the root export boundary.**

Replace the entire root module with this concrete export boundary; do not retain imports from `.entry`, `.goal_driver`, `.kernel`, `.reconstruction`, `.runtime`, `.activation`, `.compiler`, or `.transition` and do not add `__getattr__` compatibility aliases:

~~~python
from .execution_kernel import advance, inspect
from .plan_control import start

__all__ = ("advance", "inspect", "start")
~~~

Do not edit `skills/implement-gwo/SKILL.md` in this Ticket; the dependency-gate test fails closed when its Result is absent. If the dependency changed wording while retaining the exact V3 operations, update only the test’s forbidden literal list to the exact predecessor symbols still present; never remove the path proof.

- [ ] **Step 4: Run GREEN and import regressions.**

~~~powershell
py -3.13 -m pytest tests/test_v8_cutover_guard_api_boundary.py tests/test_orchestrator_package.py tests/test_v8_runtime_gateway.py tests/test_v8_runtime_gateway_repair.py tests/test_v8_candidate_gate.py tests/test_v8_successor_host.py tests/test_v8_plancontrol_rebuild.py tests/test_v8_watchdog_production_host.py tests/test_v8_successor_plan.py -q
~~~

Expected: the root API tests pass and all affected tests import deep-module owners directly without reintroducing root aliases.

- [ ] **Step 5: Refactor and commit the boundary.**

Run the full import scan and confirm no production module imports a removed root alias:

~~~powershell
rg -n "from gwo_v8 import (.*Kernel|.*GoalDriver|.*ImplementGwo|.*WriterCutoverController)|gwo_v8\.(Kernel|GoalDriver|ImplementGwoLauncher|WriterCutoverController)" skills tests
py -3.13 -m pytest tests/test_v8_cutover_guard_static.py tests/test_v8_cutover_guard_api_boundary.py tests/test_orchestrator_package.py -q
py -3.13 scripts/sync_orchestrator.py
py -3.13 scripts/sync_orchestrator.py --check
py -3.13 -c "from pathlib import Path; import json; from scripts.sync_orchestrator import expected_manifest; package=Path('skills/orchestrator'); assert json.loads((package/'.skill-package.json').read_text(encoding='utf-8')) == expected_manifest(package); print('manifest OK')"
git diff --check
git add skills/orchestrator/scripts/gwo_v8/__init__.py skills/orchestrator/scripts/orch.py skills/orchestrator/.skill-package.json tests/test_orchestrator_package.py tests/test_v8_cutover_guard_api_boundary.py tests/test_orchestrator_v8_phase2.py tests/test_orchestrator_v8_phase3.py tests/test_orchestrator_v8_phase4a.py tests/test_orchestrator_v8_phase4bc.py tests/test_orchestrator_v8_walking_skeleton.py tests/test_v8_canary_runner.py tests/test_v8_runtime_gateway.py tests/test_v8_runtime_gateway_repair.py tests/test_v8_candidate_gate.py tests/test_v8_candidate_gate_public.py tests/test_v8_human_gate_public.py tests/test_v8_plancontrol_production.py tests/test_v8_successor_plan_revision.py tests/test_v8_successor_planning_protocol.py tests/v8_successor_test_support.py
git commit -m "refactor: close the V8 package predecessor boundary"
~~~

### Task 6: Expose canonical human go/no-go evidence through a read-only CLI

**Files:**
- Create: scripts/cutover_guard.py
- Create: tests/test_v8_cutover_guard_cli.py
- Modify: tests/test_orchestrator_package.py

**Interfaces:**
- Consumes: `CutoverSubject`, `CutoverGuard`, `CutoverGuardReport`, `CutoverReadbackBundle`, `JsonCutoverReadPorts`, `CutoverGuardRequest`, and `load_production_cutover_guard`.
- Produces: `build_parser()`, `parse_args(argv)`, `main(argv=None, *, guard_factory=None, live_host_factory=None)`, and a command with exit code 0 for GO, 2 for NO_GO, and 3 for malformed/unavailable input. The CLI has no `--activate`, `--install`, `--write`, `--go`, or `--rollback` option.

The offline bundle is not an operator-authored schema sketch. `write_valid_bundle(path: Path, *, running_v2: bool = False)` in `tests/cutover_guard_test_support.py` constructs the exact `CutoverReadbackBundle` value from the seven typed readbacks and writes `canonical_bytes(bundle.canonical())` only for a test fixture. The decoder accepts exactly this closed object:

~~~python
{
    "schema": READBACK_BUNDLE_SCHEMA,
    "subject": subject.canonical(),
    "readbacks": {
        "legacy": legacy.canonical(),
        "durable_state": durable_state.canonical(),
        "writer_fence": writer_fence.canonical(),
        "ownership": ownership.canonical(),
        "compatibility": compatibility.canonical(),
        "runtime": runtime.canonical(),
        "packages": packages.canonical(),
    },
}
~~~

`JsonCutoverReadPorts.load(path)` reads the bundle once, validates the exact schema, subject identity, seven nested readback digests, and canonical key sets, then exposes only the seven typed `read` methods through `sources()`. It does not write the bundle, repair manifests, call GitHub, open SQLite, launch a process, or call Runtime. The offline command is explicitly labeled `evidence_mode="readback_bundle"`; its receipt cannot be passed to activation without `validate_activation_token` re-reading the live ports.

The production command does not consume a mystery bundle. With the V3 composition installed, `--live` builds the `CutoverSubject`, calls the exact `load_production_cutover_guard(CutoverGuardRequest(subject=subject, package_root=package_root, install_roots=install_roots))` factory from `gwo_v8.plan_control_host`, and invokes `ProductionCutoverGuardHost.check(subject)` directly:

~~~powershell
$c2State = Get-Content -Raw -LiteralPath 'D:\gwo-release-evidence\2026-08-06-gwo-v8-c2-beta2-feature-complete\state.json' | ConvertFrom-Json
$c2Sha = ([string]$c2State.closure.merged_sha).Trim()
$sourceCommit = (git rev-parse HEAD).Trim()
git merge-base --is-ancestor $c2Sha $sourceCommit
if ($LASTEXITCODE -ne 0) { throw 'C2_SOURCE_IDENTITY_INVALID' }
py -3.13 scripts/cutover_guard.py --live `
  --repository owner/repo `
  --control-branch gwo-control `
  --target-branch main `
  --source-writer-generation v6.1 `
  --target-writer-generation v8 `
  --store-generation store:v8:0001 `
  --source-commit $sourceCommit `
  --package-root . `
  --install-root "$HOME/.agents/skills" `
  --install-root "$HOME/.codex/skills" `
  --install-root "$HOME/.claude/skills" `
  --json
~~~

`--live` requires exactly three `--install-root` values in `.agents`, `.codex`, `.claude` order, computes the source tree digest from `--package-root`, performs no file output, and emits `evidence_mode="live_composed_ports"`. It returns 3 when the V3 host is not installed or any live read port is unavailable.

- [ ] **Step 1: Write failing CLI tests.**

~~~python
def test_cli_go_prints_exact_human_evidence_and_never_activates(tmp_path, capsys):
    bundle = write_valid_bundle(tmp_path / "cutover-readback.json")
    calls = []

    exit_code = cutover_cli.main(
        ["--bundle", str(bundle), "--json"],
        guard_factory=lambda sources: RecordingGuard(sources, calls),
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["decision"] == "GO"
    assert payload["evidence_mode"] == "readback_bundle"
    assert payload["activation_performed"] is False
    assert payload["receipt"]["receipt_digest"]
    assert payload["blockers"] == []
    assert calls == ["evaluate"]


def test_cli_live_invokes_composed_read_ports_without_a_bundle(tmp_path, capsys):
    subject = GuardHarness.valid().subject
    calls = []

    exit_code = cutover_cli.main(
        [
            "--live",
            "--repository", subject.repository,
            "--control-branch", subject.control_branch,
            "--target-branch", subject.target_branch,
            "--source-writer-generation", subject.source_writer_generation,
            "--target-writer-generation", subject.target_writer_generation,
            "--store-generation", subject.store_generation,
            "--source-commit", subject.source_commit,
            "--package-root", str(tmp_path),
            "--install-root", str(tmp_path / ".agents" / "skills"),
            "--install-root", str(tmp_path / ".codex" / "skills"),
            "--install-root", str(tmp_path / ".claude" / "skills"),
            "--json",
        ],
        live_host_factory=lambda request: RecordingLiveHost(subject, calls),
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["evidence_mode"] == "live_composed_ports"
    assert payload["activation_performed"] is False
    assert calls == ["check"]


def test_cli_no_go_prints_all_named_blockers_and_returns_two(tmp_path, capsys):
    bundle = write_valid_bundle(
        tmp_path / "cutover-readback.json",
        running_v2=True,
    )

    exit_code = cutover_cli.main(["--bundle", str(bundle), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["decision"] == "NO_GO"
    assert payload["receipt"] is None
    assert "CUTOVER_V2_ACTIVE" in {item["code"] for item in payload["blockers"]}
    assert payload["activation_performed"] is False


def test_cli_malformed_bundle_returns_three_and_does_not_rewrite_input(tmp_path, capsys):
    bundle = tmp_path / "cutover-readback.json"
    bundle.write_text('{"schema":"wrong"}\n', encoding="utf-8")
    original = bundle.read_bytes()

    exit_code = cutover_cli.main(["--bundle", str(bundle), "--json"])

    assert exit_code == 3
    assert json.loads(capsys.readouterr().out)["error_code"] == "CUTOVER_BUNDLE_INVALID"
    assert bundle.read_bytes() == original


def test_cli_parser_has_no_activation_or_install_option():
    options = {action.dest for action in cutover_cli.build_parser()._actions}

    assert {"activate", "install", "write", "rollback", "go"}.isdisjoint(options)


def test_cli_default_text_contains_every_check_and_digest(tmp_path, capsys):
    bundle = write_valid_bundle(tmp_path / "cutover-readback.json")

    exit_code = cutover_cli.main(["--bundle", str(bundle)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "GO" in output
    for check_id in EXPECTED_CHECK_IDS:
        assert check_id in output
    assert "receipt_digest=" in output
~~~

Add the remaining CLI fixture bodies to `tests/cutover_guard_test_support.py`:

~~~python
EXPECTED_CHECK_IDS = (
    "source_writer",
    "legacy_quiescence",
    "durable_state",
    "writer_and_lease",
    "production_paths",
    "runtime_configuration",
    "package_installation",
)


def write_valid_bundle(path: Path, *, running_v2: bool = False) -> Path:
    from dataclasses import asdict, replace
    from gwo_v8._canonical import canonical_bytes, digest_value
    from gwo_v8.cutover_guard import (
        CutoverReadbackBundle,
        READBACK_BUNDLE_SCHEMA,
    )

    harness = GuardHarness.valid()
    if running_v2:
        harness.legacy.value = replace(
            harness.legacy.value,
            v2_execution_refs=("v2:running",),
            v2_execution_state="running",
        )
        legacy_body = asdict(harness.legacy.value)
        legacy_body.pop("readback_digest")
        harness.legacy.value = replace(
            harness.legacy.value,
            readback_digest=digest_value(legacy_body),
        )
    bundle = CutoverReadbackBundle(
        schema=READBACK_BUNDLE_SCHEMA,
        subject=harness.subject,
        legacy=harness.legacy.value,
        durable_state=harness.durable.value,
        writer_fence=harness.writer.value,
        ownership=harness.ownership.value,
        compatibility=harness.compatibility.value,
        runtime=harness.runtime.value,
        packages=harness.packages.value,
    )
    path.write_bytes(canonical_bytes(bundle.canonical()))
    return path


class RecordingGuard:
    def __init__(self, sources: CutoverGuardSources, calls: list[str]) -> None:
        self._sources = sources
        self._calls = calls

    def evaluate(self, subject: CutoverSubject) -> CutoverGuardReport:
        self._calls.append("evaluate")
        return CutoverGuard(self._sources).evaluate(subject)


class RecordingLiveHost:
    def __init__(self, subject: CutoverSubject, calls: list[str]) -> None:
        self._subject = subject
        self._calls = calls

    def check(self, subject: CutoverSubject) -> CutoverGuardReport:
        self._calls.append("check")
        if subject != self._subject:
            raise AssertionError("live factory received a different subject")
        return CutoverGuard(GuardHarness.valid().sources).evaluate(subject)
~~~

- [ ] **Step 2: Run RED.**

~~~powershell
py -3.13 -m pytest tests/test_v8_cutover_guard_cli.py -q
~~~

Expected: collection fails because scripts/cutover_guard.py does not exist.

- [ ] **Step 3: Implement the no-side-effect CLI.**

Implement the CLI module with these complete parser, rendering, and `main` bodies. The only production factory is the resolver-backed `load_production_cutover_guard`; the injected factory arguments exist solely for tests:

~~~python
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable, Sequence

from gwo_v8.cutover_guard import (
    CutoverGuard,
    CutoverGuardError,
    CutoverGuardReport,
    CutoverGuardSources,
    CutoverSubject,
    JsonCutoverReadPorts,
    source_tree_digest,
)
from gwo_v8.plan_control_host import (
    CutoverGuardRequest,
    load_production_cutover_guard,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only GWO V8 cutover evidence")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--bundle", type=Path)
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--repository")
    parser.add_argument("--control-branch")
    parser.add_argument("--target-branch")
    parser.add_argument("--source-writer-generation")
    parser.add_argument("--target-writer-generation")
    parser.add_argument("--store-generation")
    parser.add_argument("--source-commit")
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--install-root", dest="install_roots", type=Path, action="append", default=[])
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _live_subject(args: argparse.Namespace) -> CutoverSubject:
    required = (
        "repository",
        "control_branch",
        "target_branch",
        "source_writer_generation",
        "target_writer_generation",
        "store_generation",
        "source_commit",
        "package_root",
    )
    if any(getattr(args, name) in (None, "") for name in required):
        raise CutoverGuardError(
            "CUTOVER_GUARD_UNAVAILABLE",
            "--live requires every subject and package-root option",
        )
    roots = tuple(args.install_roots)
    if len(roots) != 3 or tuple(root.parent.name for root in roots) != (
        ".agents",
        ".codex",
        ".claude",
    ):
        raise CutoverGuardError(
            "CUTOVER_GUARD_UNAVAILABLE",
            "--live requires exactly three ordered .agents/.codex/.claude roots",
        )
    return CutoverSubject(
        repository=args.repository,
        control_branch=args.control_branch,
        target_branch=args.target_branch,
        source_writer_generation=args.source_writer_generation,
        target_writer_generation=args.target_writer_generation,
        store_generation=args.store_generation,
        source_commit=args.source_commit,
        source_tree_digest=source_tree_digest(args.package_root),
        production_entry_refs=(
            "gwo_v8.plan_control_host:ProductionPlanControlStartHost.start",
            "gwo_v8.execution_kernel:advance",
            "gwo_v8.execution_kernel:inspect",
        ),
    )


def _payload(report: CutoverGuardReport, evidence_mode: str) -> dict[str, object]:
    payload = dict(report.canonical())
    payload["evidence_mode"] = evidence_mode
    payload["activation_performed"] = False
    return payload


def _print_report(
    report: CutoverGuardReport,
    *,
    evidence_mode: str,
    json_output: bool,
) -> None:
    payload = _payload(report, evidence_mode)
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return
    print(
        " ".join(
            (
                f"decision={payload['decision']}",
                f"evidence_mode={payload['evidence_mode']}",
                f"repository={payload['repository']}",
                f"subject_digest={payload['subject_digest']}",
                f"readback_digest={payload['readback_digest']}",
                "activation_performed=false",
            )
        )
    )
    for check in payload["checks"]:
        print(
            f"check={check['check_id']} passed={check['passed']} "
            f"observed_digest={check['observed_digest']}"
        )
    for blocker in payload["blockers"]:
        print(
            f"blocker={blocker['code']} check_id={blocker['check_id']} "
            f"detail={blocker['detail']}"
        )
    receipt = payload["receipt"]
    print(
        "receipt_digest="
        + ("none" if receipt is None else str(receipt["receipt_digest"]))
    )


def _print_error(code: str, detail: str, *, json_output: bool) -> None:
    payload = {"error_code": code, "detail": detail}
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        print(f"error_code={code} detail={detail}")


def main(
    argv: Sequence[str] | None = None,
    *,
    guard_factory: Callable[[CutoverGuardSources], CutoverGuard] | None = None,
    live_host_factory: Callable[[CutoverGuardRequest], object] | None = None,
) -> int:
    try:
        args = parse_args(argv)
    except SystemExit:
        _print_error("CUTOVER_GUARD_UNAVAILABLE", "command-line arguments are invalid", json_output=True)
        return 3
    if args.bundle is not None:
        try:
            ports = JsonCutoverReadPorts.load(args.bundle)
            guard = (guard_factory or CutoverGuard)(ports.sources())
            report = guard.evaluate(ports.subject)
        except Exception as error:
            _print_error(
                "CUTOVER_BUNDLE_INVALID",
                "offline readback bundle is malformed or unavailable",
                json_output=args.json_output,
            )
            return 3
        _print_report(report, evidence_mode="readback_bundle", json_output=args.json_output)
        return 0 if report.decision == "GO" else 2
    try:
        subject = _live_subject(args)
        roots = tuple(args.install_roots)
        request = CutoverGuardRequest(
            subject=subject,
            package_root=args.package_root,
            install_roots=(roots[0], roots[1], roots[2]),
        )
        host = (live_host_factory or load_production_cutover_guard)(request)
        report = host.check(subject)
    except Exception as error:
        _print_error(
            "CUTOVER_GUARD_UNAVAILABLE",
            "live composed read ports are unavailable",
            json_output=args.json_output,
        )
        return 3
    _print_report(report, evidence_mode="live_composed_ports", json_output=args.json_output)
    return 0 if report.decision == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
~~~

- [ ] **Step 4: Run GREEN and package validation.**

~~~powershell
py -3.13 -m pytest tests/test_v8_cutover_guard_cli.py tests/test_orchestrator_package.py -q
py -3.13 scripts/quick_validate.py
~~~

Expected: all CLI tests pass, the package syntax/manifest checks pass, and no command creates an install backup or modifies a manifest.

- [ ] **Step 5: Refactor and commit.**

Keep the CLI report stable and canonical; a human must be able to compare subject_digest, every check digest, all blocker codes, and the receipt digest without a model transcript. Commit:

~~~powershell
git diff --check
git add scripts/cutover_guard.py tests/test_v8_cutover_guard_cli.py tests/test_orchestrator_package.py
git commit -m "feat: expose read-only cutover evidence CLI"
~~~

### Task 7: Run the Beta3 gate and record exact human go/no-go evidence

**Files:**
- No production file changes.
- Read-only verification of docs/design/gwo-v8-lean-architecture.md, docs/design/gwo-v8-lean-roadmap.md, docs/design/gwo-v8-lean-stabilization-spec.md, accepted ADRs, skills/implement-gwo/SKILL.md, skills/orchestrator/scripts/gwo_v8/__init__.py, and all Task 1–6 files.

**Interfaces:**
- Consumes: merged #113, #117, #136, #137, production V3 composition, the read-only Guard host, and the exact Beta2 tree.
- Produces: a reproducible Beta3 candidate evidence bundle and a human decision point. It does not publish a release, activate the writer, change the default, or mutate GitHub.

- [ ] **Step 1: Run the focused #118 suite.**

~~~powershell
py -3.13 -m pytest tests/test_v8_cutover_guard.py tests/test_v8_cutover_guard_static.py tests/test_v8_cutover_guard_host.py tests/test_v8_cutover_activation.py tests/test_v8_cutover_guard_api_boundary.py tests/test_v8_cutover_guard_cli.py -q
~~~

Expected: all Guard, static-path, host, token, activation-boundary, package-boundary, and CLI tests pass. The focused suite must include tests proving zero repository/SQLite/GitHub/process/Runtime writes on both GO and NO_GO paths.

- [ ] **Step 2: Run V3 regressions and the repository gates.**

~~~powershell
py -3.13 -m pytest tests/test_v8_successor_host.py tests/test_v8_successor_execution_kernel.py tests/test_v8_successor_plan_revision.py tests/test_v8_plancontrol_production.py tests/test_v8_plancontrol_rebuild.py tests/test_v8_runtime_gateway.py tests/test_v8_runtime_gateway_repair.py tests/test_v8_candidate_gate.py tests/test_v8_candidate_gate_public.py tests/test_orchestrator_package.py -q
py -3.13 -m pytest -q
py -3.13 scripts/quick_validate.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
~~~

Expected: focused regressions, full pytest, quick validation, package synchronization, and diff checks all pass. The tree contains no generated SQLite, ArtifactStore, package-install, or temporary manifest files.

- [ ] **Step 3: Read back the exact external blockers without changing them.**

~~~powershell
gh issue view 113 --repo NOirBRight/github-work-orchestrator --json state,body,comments
gh issue view 117 --repo NOirBRight/github-work-orchestrator --json state,body,comments
gh issue view 136 --repo NOirBRight/github-work-orchestrator --json state,body,comments
gh issue view 137 --repo NOirBRight/github-work-orchestrator --json state,body,comments
gh issue view 118 --repo NOirBRight/github-work-orchestrator --json state,body,comments
~~~

Expected: #113, #117, #136, and #137 are closed by read-backed merged Results, #118 remains the current cutover Ticket until its Result is accepted, and the command performs no mutation. If the tracker still shows #137 closed while its native #114/#115 blockers are open, stop and return to the master-plan human tracker-repair checkpoint; do not repair it in this plan.

- [ ] **Step 4: Produce the exact no-mutation human evidence.**

Run the composed production host’s read-only check directly; this is the authoritative Beta3 evidence command and does not require an operator-authored bundle:

~~~powershell
$c2State = Get-Content -Raw -LiteralPath 'D:\gwo-release-evidence\2026-08-06-gwo-v8-c2-beta2-feature-complete\state.json' | ConvertFrom-Json
$c2Sha = ([string]$c2State.closure.merged_sha).Trim()
$sourceCommit = (git rev-parse HEAD).Trim()
git merge-base --is-ancestor $c2Sha $sourceCommit
if ($LASTEXITCODE -ne 0) { throw 'C2_SOURCE_IDENTITY_INVALID' }
py -3.13 scripts/cutover_guard.py --live `
  --repository owner/repo `
  --control-branch gwo-control `
  --target-branch main `
  --source-writer-generation v6.1 `
  --target-writer-generation v8 `
  --store-generation store:v8:0001 `
  --source-commit $sourceCommit `
  --package-root . `
  --install-root "$HOME/.agents/skills" `
  --install-root "$HOME/.codex/skills" `
  --install-root "$HOME/.claude/skills" `
  --json
~~~

The captured report is acceptable only when it contains:

1. decision == "GO";
2. all seven check IDs with passed == true and non-empty observed digests;
3. source_writer_generation == "v6.1", an empty predecessor active set, an empty Integration Lease, a compatible fresh V8 store generation, and an empty V8 ownership set;
4. empty reachable V2-projection, V3-compatibility, and legacy-writer reference tuples, with the exact forbidden-reference list proven unreachable;
5. exactly the five required Runtime selectors, no provider action refs, and no persistence write refs;
6. source/installed implement-gwo and orchestrator package identities at version 8.0.0 with empty drift; and
7. a receipt.receipt_digest bound to the subject/readback digests plus activation_performed == false; the report is evidence only and does not change the default writer or invoke the rehearsal controller.

The human go/no-go record is the complete canonical JSON report, the exact Beta2 source SHA, the seven readback digests, the report digest, and the operator’s explicit decision outside the Guard. No chat text, model output, or CLI flag is treated as activation authority.

- [ ] **Step 5: Prove the Guard is not GA activation.**

~~~powershell
rg -n "CanaryAcceptance|root Canary|default.*V8|activate|publish_activation|compare_and_swap|install_atomic|Kernel\.reconcile_once" skills/orchestrator/scripts/gwo_v8/cutover_guard.py scripts/cutover_guard.py
~~~

Expected: no Guard/CLI code invokes a Canary, default switch, activation writer, install writer, or predecessor reconciliation driver. `--live` invokes only the composed read ports and `ProductionCutoverGuardHost.check`; `transition.py` is the only activation boundary and can reach mutation only after `validate_activation_token` in an isolated rehearsal. Beta3 keeps the default writer unchanged; #119 alone supplies the real four-Ticket Canary and default-writer readback.

- [ ] **Step 6: Self-review the plan and implementation boundary.**

Run the exact plan-content checks before declaring the plan complete:

~~~powershell
$plan = "docs/superpowers/plans/2026-08-08-gwo-v8-c3-beta3-cutover-candidate.md"
$patterns = @(
    (@("T", "B", "D") -join ""),
    (@("T", "O", "D", "O") -join ""),
    (@("as", "appropriate") -join " "),
    (@("implement", "later") -join " "),
    (@("fill", "in", "details") -join " "),
    (@("Similar", "to", "Task") -join " "),
    (@("write", "tests", "for", "the", "above") -join " ")
)
$hits = foreach ($pattern in $patterns) {
    Select-String -Path $plan -Pattern $pattern -SimpleMatch
}
if ($hits) { $hits | Format-Table; throw "plan placeholder scan failed" }
py -3.13 -m pytest -q
py -3.13 scripts/quick_validate.py
py -3.13 scripts/sync_orchestrator.py --check
git diff --check
git status --short
~~~

Expected: no placeholder hits, all tests/validation pass, and the only intended implementation changes are the Task 1–6 write sets. A successful Guard is still only Beta3 evidence; do not mark #119 or GA complete from this plan.

## Issue #118 acceptance coverage

| Issue #118 acceptance item | Plan proof |
| --- | --- |
| One-time read-only, fail-closed Guard proves V6.1 authority, predecessor quiescence/durable state, writer fence/lease, and required selectors; named failure diagnostics; zero mutation. | Task 1 exact read ports/check matrix; Task 3 host surface; Task 7 focused zero-write suite and seven-digest evidence. |
| Every V3-composition/V2-projection adapter, caller, and write path absent or unreachable; V2 finishes through original decoder or is terminal/quiescent read-only; V8 never resumes/interprets/writes/projects V2. | Task 2 AST audit; Task 5 package/Skill boundary; Task 1 LegacyReadback V2-state matrix; Task 7 path scan. |
| Guard success does not transfer authority; existing fencing publishes writer generation and read-backed Activation Receipt with repository/Campaign/Plan/expected previous authority/generation identity. | Task 4 pre-mutation token fence; existing LocalPlanPublication/WriterCutoverController Activation protocol remains the only commit; Task 7 proves activation_performed == false for Guard evidence. |
| Pending activation admits no work; only PlanSpec v3 writes after receipt; predecessor/successor cannot write simultaneously. | Task 4 pending Activation and capacity_limits == (0, 0) test; existing writer-generation allows_new_work readback; Task 5 V3 public boundary/path audit. |
| Post-receipt recovery rolls forward; rollback is a new human-authorized durable compensating transition preserving receipt/diagnostics. | Task 4 receipt-backed rollback test asserts immutable Activation Receipt and final rollback record; Task 7 excludes automatic rollback/default change. |

## C3 exit boundary

C3 is complete only when the local Beta3 Guard evidence is reproducible and the transition fence is covered by tests. A GO report is a release-candidate fact, not authority transfer. The branch remains eligible for a later owner-approved Beta3 publication step, but this plan itself does not publish or activate anything.

## Explicit non-goals

- No root-Canary execution, real Ticket selection, CandidateGate/Batch delivery, hosted CI, or GA release; those belong to #119.
- No new public workflow operation and no sixth domain module; Guard is release-control infrastructure with read-only ports.
- No V6.1 stop inside Guard, no V8 Activation Receipt inside Guard, no PlanSpec/SQLite/GitHub Guard persistence, and no long-lived Shadow service.
- No automatic tracker repair for the currently closed #137, no reopening of #136/#137, no release-tag creation, and no package installation.
