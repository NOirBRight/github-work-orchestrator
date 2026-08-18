"""CLI wiring for one explicitly authorized V8 production activation.

The CLI owns parsing and assembling the immutable activation inputs.  A live
composition factory owns infrastructure-specific construction of the durable
``WriterCutoverController`` and Canary read control; no test double is selected
by this production entry point.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from dataclasses import asdict, dataclass, fields
import importlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GWO_SCRIPTS = REPOSITORY_ROOT / "skills" / "orchestrator" / "scripts"
if str(GWO_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(GWO_SCRIPTS))

from gwo_v8._canonical import strict_json_loads  # noqa: E402
from gwo_v8.compiler import CompiledPlan  # noqa: E402
from gwo_v8.cutover_guard import (  # noqa: E402
    CutoverGuardReceipt,
    CutoverSubject,
)
from gwo_v8.production_activation import (  # noqa: E402
    ProductionActivationAuthorization,
    ProductionActivationAuthorizationReceipt,
    ProductionActivationComposition,
    ProductionActivationCompositionFactory,
    ProductionActivationError,
    ProductionActivationFacade,
    ProductionActivationPreflight,
    ProductionActivationRequest,
)
from gwo_v8.transition import (  # noqa: E402
    CanaryAcceptance,
    WriterTransitionOutcome,
)


_BUNDLE_KEYS = frozenset(
    {
        "authorization",
        "authorization_receipt",
        "compiled_plan",
        "canary",
        "guard_subject",
        "guard_receipt",
        "worker_capacity",
        "coordinator_capacity",
    }
)
_AUTHORIZATION_KEYS = frozenset(
    {
        "run_id",
        "repository",
        "merged_main_sha",
        "merged_main_git_tree",
        "release_subject_digest",
        "evidence_root",
        "target_repository",
        "canary_repository",
        "writer_transition",
        "target_writer_generation",
    }
)
_AUTHORIZATION_RECEIPT_KEYS = _AUTHORIZATION_KEYS | {
    "approval_ref",
    "receipt_digest",
}
_PLAN_KEYS = frozenset(
    {"repository", "canonical_bytes_base64", "digest", "compilation_record"}
)
_CANARY_KEYS = frozenset(
    {
        "accepted",
        "repository",
        "evidence_package_digest",
        "manifest_ref",
        "blockers",
        "evidence_refs",
    }
)
_GUARD_RECEIPT_KEYS = frozenset(
    {
        "schema",
        "repository",
        "subject_digest",
        "readback_digest",
        "source_writer_generation",
        "target_writer_generation",
        "store_generation",
        "writer_control_ref_digest",
        "runtime_configuration_digest",
        "compatibility_audit_digest",
        "package_readback_digest",
        "receipt_digest",
    }
)


def _mapping(value: object, label: str, keys: frozenset[str]) -> dict[str, Any]:
    if type(value) is not dict or frozenset(value) != keys:
        raise ProductionActivationError(
            "ACTIVATION_INPUT_INVALID",
            f"{label} must use the exact closed activation schema",
        )
    return dict(value)


def _text(value: object, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise ProductionActivationError(
            "ACTIVATION_INPUT_INVALID",
            f"{label} must be non-empty text",
        )
    return value


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if type(value) not in (list, tuple) or any(type(item) is not str for item in value):
        raise ProductionActivationError(
            "ACTIVATION_INPUT_INVALID",
            f"{label} must be a string list",
        )
    return tuple(value)


def _bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ProductionActivationError(
            "ACTIVATION_INPUT_INVALID",
            f"{label} must be a JSON boolean",
        )
    return value


def _int(value: object, label: str) -> int:
    if type(value) is not int:
        raise ProductionActivationError(
            "ACTIVATION_INPUT_INVALID",
            f"{label} must be a JSON integer",
        )
    return value


def _base64_bytes(value: object, label: str) -> bytes:
    encoded = _text(value, label)
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ProductionActivationError(
            "ACTIVATION_INPUT_INVALID",
            f"{label} is not valid base64",
        ) from error


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = strict_json_loads(path.read_bytes())
    except (OSError, TypeError, ValueError) as error:
        raise ProductionActivationError(
            "ACTIVATION_INPUT_INVALID",
            f"activation input is not canonical JSON: {path}",
        ) from error
    if type(value) is not dict:
        raise ProductionActivationError(
            "ACTIVATION_INPUT_INVALID",
            "activation input root must be one JSON object",
        )
    return dict(value)


def _authorization(value: object) -> ProductionActivationAuthorization:
    raw = _mapping(value, "authorization", _AUTHORIZATION_KEYS)
    try:
        return ProductionActivationAuthorization(**raw)
    except (TypeError, ValueError) as error:
        raise ProductionActivationError(
            "AUTHORIZATION_IDENTITY_INVALID",
            "owner authorization identity is invalid",
        ) from error


def _authorization_receipt(
    value: object,
) -> ProductionActivationAuthorizationReceipt:
    raw = _mapping(value, "authorization_receipt", _AUTHORIZATION_RECEIPT_KEYS)
    try:
        receipt = ProductionActivationAuthorizationReceipt(**raw)
    except (TypeError, ValueError) as error:
        raise ProductionActivationError(
            "AUTHORIZATION_PROVENANCE_INVALID",
            "owner authorization receipt is invalid",
        ) from error
    if not receipt.has_valid_digest():
        raise ProductionActivationError(
            "AUTHORIZATION_PROVENANCE_INVALID",
            "owner authorization receipt digest is invalid",
        )
    return receipt


def _compiled_plan(value: object) -> CompiledPlan:
    raw = _mapping(value, "compiled_plan", _PLAN_KEYS)
    compilation_record = raw["compilation_record"]
    if type(compilation_record) is not dict:
        raise ProductionActivationError(
            "ACTIVATION_INPUT_INVALID",
            "compiled_plan.compilation_record must be an object",
        )
    try:
        return CompiledPlan(
            repository=_text(raw["repository"], "compiled_plan.repository"),
            canonical_bytes=_base64_bytes(
                raw["canonical_bytes_base64"],
                "compiled_plan.canonical_bytes_base64",
            ),
            digest=_text(raw["digest"], "compiled_plan.digest"),
            compilation_record=compilation_record,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ProductionActivationError(
            "ACTIVATION_INPUT_INVALID",
            "compiled Plan input is invalid",
        ) from error


def _canary(value: object) -> CanaryAcceptance:
    raw = _mapping(value, "canary", _CANARY_KEYS)
    return CanaryAcceptance(
        accepted=_bool(raw["accepted"], "canary.accepted"),
        repository=_text(raw["repository"], "canary.repository"),
        evidence_package_digest=(
            None
            if raw["evidence_package_digest"] is None
            else _text(raw["evidence_package_digest"], "canary.evidence_package_digest")
        ),
        manifest_ref=(
            None
            if raw["manifest_ref"] is None
            else _text(raw["manifest_ref"], "canary.manifest_ref")
        ),
        blockers=_string_list(raw["blockers"], "canary.blockers"),
        evidence_refs=_string_list(raw["evidence_refs"], "canary.evidence_refs"),
    )


def _subject(value: object) -> CutoverSubject:
    raw = _mapping(
        value,
        "guard_subject",
        frozenset(field.name for field in fields(CutoverSubject)),
    )
    try:
        return CutoverSubject(
            repository=_text(raw["repository"], "guard_subject.repository"),
            control_branch=_text(raw["control_branch"], "guard_subject.control_branch"),
            target_branch=_text(raw["target_branch"], "guard_subject.target_branch"),
            source_writer_generation=_text(
                raw["source_writer_generation"],
                "guard_subject.source_writer_generation",
            ),
            target_writer_generation=_text(
                raw["target_writer_generation"],
                "guard_subject.target_writer_generation",
            ),
            store_generation=_text(raw["store_generation"], "guard_subject.store_generation"),
            source_commit=_text(raw["source_commit"], "guard_subject.source_commit"),
            source_tree_digest=_text(
                raw["source_tree_digest"],
                "guard_subject.source_tree_digest",
            ),
            production_entry_refs=_string_list(
                raw["production_entry_refs"],
                "guard_subject.production_entry_refs",
            ),
            forbidden_production_refs=_string_list(
                raw["forbidden_production_refs"],
                "guard_subject.forbidden_production_refs",
            ),
            required_runtime_selectors=_string_list(
                raw["required_runtime_selectors"],
                "guard_subject.required_runtime_selectors",
            ),
            package_names=_string_list(raw["package_names"], "guard_subject.package_names"),
            install_surfaces=_string_list(
                raw["install_surfaces"],
                "guard_subject.install_surfaces",
            ),
        )
    except (TypeError, ValueError) as error:
        raise ProductionActivationError(
            "ACTIVATION_INPUT_INVALID",
            "CutoverSubject input is invalid",
        ) from error


def _guard_receipt(value: object) -> CutoverGuardReceipt:
    raw = _mapping(value, "guard_receipt", _GUARD_RECEIPT_KEYS)
    try:
        return CutoverGuardReceipt(**raw)
    except (TypeError, ValueError) as error:
        raise ProductionActivationError(
            "ACTIVATION_INPUT_INVALID",
            "Guard receipt input is invalid",
        ) from error


@dataclass(frozen=True)
class ProductionActivationBundle:
    """Typed, closed input set consumed by the production activation path."""

    request: ProductionActivationRequest
    authorization_receipt: ProductionActivationAuthorizationReceipt


@dataclass(frozen=True)
class ProductionActivationResult:
    """Read-only preflight plus optional durable activation outcome."""

    bundle: ProductionActivationBundle
    preflight: ProductionActivationPreflight
    outcome: WriterTransitionOutcome | None
    composition: ProductionActivationComposition

    def as_dict(self) -> dict[str, object]:
        preflight = self.preflight
        payload: dict[str, object] = {
            "schema": "gwo.v8.production-activation.v1",
            "mode": "execute" if self.outcome is not None else "preflight",
            "authorization": self.bundle.request.authorization.canonical(),
            "authorization_receipt_digest": (
                self.bundle.authorization_receipt.receipt_digest
            ),
            "preflight": {
                "plan_digest": preflight.plan_digest,
                "canary_evidence_digest": preflight.canary_evidence_digest,
                "guard_receipt_digest": preflight.guard_receipt_digest,
                "current_writer": asdict(preflight.current_writer),
                "checks": list(preflight.checks),
            },
            "outcome": None if self.outcome is None else asdict(self.outcome),
        }
        return payload


class _StaticAuthorizationSource:
    def __init__(self, receipt: ProductionActivationAuthorizationReceipt):
        self._receipt = receipt

    def read(
        self,
        authorization: ProductionActivationAuthorization,
    ) -> ProductionActivationAuthorizationReceipt:
        del authorization
        return self._receipt


def _validate_authorization_receipt_identity(
    authorization: ProductionActivationAuthorization,
    receipt: ProductionActivationAuthorizationReceipt,
) -> None:
    if (
        not receipt.has_valid_digest()
        or receipt.repository != authorization.repository
        or receipt.run_id != authorization.run_id
        or receipt.merged_main_sha != authorization.merged_main_sha
        or receipt.merged_main_git_tree != authorization.merged_main_git_tree
        or receipt.release_subject_digest != authorization.release_subject_digest
        or receipt.evidence_root != authorization.evidence_root
        or receipt.target_repository != authorization.target_repository
        or receipt.canary_repository != authorization.canary_repository
        or receipt.writer_transition != authorization.writer_transition
        or receipt.target_writer_generation
        != authorization.target_writer_generation
    ):
        raise ProductionActivationError(
            "AUTHORIZATION_IDENTITY_INVALID",
            "owner approval and its durable receipt are not the same identity",
        )


def _validate_bundle_identity(
    authorization: ProductionActivationAuthorization,
    receipt: ProductionActivationAuthorizationReceipt,
    request: ProductionActivationRequest,
) -> None:
    if (
        not receipt.has_valid_digest()
        or receipt.repository != authorization.repository
        or receipt.run_id != authorization.run_id
        or receipt.merged_main_sha != authorization.merged_main_sha
        or receipt.merged_main_git_tree != authorization.merged_main_git_tree
        or receipt.release_subject_digest != authorization.release_subject_digest
        or receipt.evidence_root != authorization.evidence_root
        or receipt.target_repository != authorization.target_repository
        or receipt.canary_repository != authorization.canary_repository
        or receipt.writer_transition != authorization.writer_transition
        or receipt.target_writer_generation
        != authorization.target_writer_generation
        or request.compiled_plan.repository != authorization.target_repository
        or request.guard_subject.repository != authorization.target_repository
        or request.guard_subject.source_commit != authorization.merged_main_sha
        or request.source_main_sha != authorization.merged_main_sha
        or request.source_main_tree != authorization.merged_main_git_tree
    ):
        raise ProductionActivationError(
            "AUTHORIZATION_IDENTITY_INVALID",
            "activation inputs are not bound to the exact owner approval identity",
        )


def build_activation_bundle(value: Mapping[str, object]) -> ProductionActivationBundle:
    """Build every typed activation input from one closed canonical mapping."""

    raw = _mapping(value, "activation bundle", _BUNDLE_KEYS)
    authorization = _authorization(raw["authorization"])
    receipt = _authorization_receipt(raw["authorization_receipt"])
    _validate_authorization_receipt_identity(authorization, receipt)
    compiled_plan = _compiled_plan(raw["compiled_plan"])
    canary = _canary(raw["canary"])
    subject = _subject(raw["guard_subject"])
    guard_receipt = _guard_receipt(raw["guard_receipt"])
    worker_capacity = _int(raw["worker_capacity"], "worker_capacity")
    coordinator_capacity = _int(raw["coordinator_capacity"], "coordinator_capacity")
    try:
        request = ProductionActivationRequest(
            authorization=authorization,
            source_main_sha=authorization.merged_main_sha,
            source_main_tree=authorization.merged_main_git_tree,
            compiled_plan=compiled_plan,
            canary=canary,
            guard_subject=subject,
            guard_receipt=guard_receipt,
            worker_capacity=worker_capacity,
            coordinator_capacity=coordinator_capacity,
        )
    except (TypeError, ValueError) as error:
        raise ProductionActivationError(
            "ACTIVATION_INPUT_INVALID",
            "activation request input is invalid",
        ) from error
    _validate_bundle_identity(authorization, receipt, request)
    return ProductionActivationBundle(request=request, authorization_receipt=receipt)


def run_production_activation(
    bundle: ProductionActivationBundle,
    *,
    factory: ProductionActivationCompositionFactory,
    execute: bool,
) -> ProductionActivationResult:
    """Run zero-write preflight and, only when requested, the cutover path."""

    if type(bundle) is not ProductionActivationBundle:
        raise ProductionActivationError(
            "ACTIVATION_INPUT_INVALID",
            "activation bundle has the wrong exact type",
        )
    compose = getattr(factory, "compose", None)
    if not callable(compose):
        raise ProductionActivationError(
            "ACTIVATION_COMPOSITION_INVALID",
            "production composition factory has no compose operation",
        )
    try:
        composition = compose(
            authorization=bundle.request.authorization,
            compiled_plan=bundle.request.compiled_plan,
            canary=bundle.request.canary,
            guard_subject=bundle.request.guard_subject,
            guard_receipt=bundle.request.guard_receipt,
        )
    except ProductionActivationError:
        raise
    except Exception as error:
        raise ProductionActivationError(
            "ACTIVATION_COMPOSITION_INVALID",
            "production composition factory could not assemble live controls",
        ) from error
    if type(composition) is not ProductionActivationComposition:
        raise ProductionActivationError(
            "ACTIVATION_COMPOSITION_INVALID",
            "production factory did not return one exact activation composition",
        )
    facade = ProductionActivationFacade(
        composition.controller,
        authorization_source=_StaticAuthorizationSource(
            bundle.authorization_receipt,
        ),
        canary_evidence_control=composition.canary_evidence_control,
        expected_canary_repository=composition.expected_canary_repository,
    )
    preflight = facade.preflight(bundle.request)
    outcome = None
    if execute:
        outcome = facade.execute(
            bundle.request,
            authorization=bundle.request.authorization,
            preflight=preflight,
        )
    return ProductionActivationResult(
        bundle=bundle,
        preflight=preflight,
        outcome=outcome,
        composition=composition,
    )


def _load_factory(spec: str) -> ProductionActivationCompositionFactory:
    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise ProductionActivationError(
            "ACTIVATION_COMPOSITION_INVALID",
            "--composition-factory must be module:attribute",
        )
    try:
        factory = getattr(importlib.import_module(module_name), attribute)
        if isinstance(factory, type):
            factory = factory()
    except (AttributeError, ImportError, TypeError) as error:
        raise ProductionActivationError(
            "ACTIVATION_COMPOSITION_INVALID",
            "production composition factory could not be loaded",
        ) from error
    if not callable(getattr(factory, "compose", None)):
        raise ProductionActivationError(
            "ACTIVATION_COMPOSITION_INVALID",
            "production composition factory has no compose operation",
        )
    return factory


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the explicitly authorized GWO V8 production activation"
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--composition-factory", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="perform the guarded v6.1 -> v8 mutation after preflight",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        bundle = build_activation_bundle(_load_json(args.input))
        factory = _load_factory(args.composition_factory)
        result = run_production_activation(
            bundle,
            factory=factory,
            execute=args.execute,
        )
        _write_json(args.output, result.as_dict())
    except ProductionActivationError as error:
        print(f"{error.code}: {error.detail}", file=sys.stderr)
        return 2
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(f"ACTIVATION_INPUT_INVALID: {error}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
