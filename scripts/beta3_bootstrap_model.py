"""Closed in-memory contracts for the Beta3 production bootstrap."""

from __future__ import annotations

from dataclasses import dataclass
import re
import secrets
from typing import Callable

from gwo_v8._canonical import canonical_bytes, digest_bytes, digest_value
from gwo_v8.cutover_guard import (
    CompatibilityPathReadback,
    CutoverReadbackBundle,
    CutoverSubject,
    DurableStateReadback,
    LegacyReadback,
    OwnershipReadback,
    PackageReadback,
    RuntimePreflightReadback,
    WriterFenceReadback,
    READBACK_BUNDLE_SCHEMA,
)


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX_NONCE = re.compile(r"^[0-9a-f]+$")


class BootstrapError(RuntimeError):
    code: str
    detail: str

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _invalid(code: str, detail: str) -> None:
    raise BootstrapError(code, detail)


def _require_text(value: object, name: str, code: str) -> None:
    if type(value) is not str or not value:
        _invalid(code, f"{name} must be non-empty exact text")


def _require_digest(value: object, name: str, code: str) -> None:
    if type(value) is not str or _HEX64.fullmatch(value) is None:
        _invalid(code, f"{name} must be lowercase hexadecimal SHA-256")


@dataclass(frozen=True)
class AttemptIdentity:
    run_id: str
    challenge_nonce: str
    repository: str
    evidence_root: str
    cutover_subject_digest: str
    runner_sha256: str
    attestor_sha256: str

    def __post_init__(self) -> None:
        for name in ("run_id", "repository", "evidence_root"):
            _require_text(getattr(self, name), name, "ATTEMPT_IDENTITY_INVALID")
        if (
            type(self.challenge_nonce) is not str
            or len(self.challenge_nonce) < 32
            or _HEX_NONCE.fullmatch(self.challenge_nonce) is None
        ):
            _invalid(
                "ATTEMPT_IDENTITY_INVALID",
                "challenge_nonce must contain at least 32 lowercase hexadecimal characters",
            )
        for name in (
            "cutover_subject_digest",
            "runner_sha256",
            "attestor_sha256",
        ):
            _require_digest(
                getattr(self, name), name, "ATTEMPT_IDENTITY_INVALID"
            )

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        repository: str,
        evidence_root: str,
        cutover_subject_digest: str,
        runner_sha256: str,
        attestor_sha256: str,
        nonce_factory: Callable[[int], str] = secrets.token_hex,
    ) -> "AttemptIdentity":
        return cls(
            run_id=run_id,
            challenge_nonce=nonce_factory(16),
            repository=repository,
            evidence_root=evidence_root,
            cutover_subject_digest=cutover_subject_digest,
            runner_sha256=runner_sha256,
            attestor_sha256=attestor_sha256,
        )

    @property
    def digest(self) -> str:
        return digest_value(self.canonical())

    def canonical(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "challenge_nonce": self.challenge_nonce,
            "repository": self.repository,
            "evidence_root": self.evidence_root,
            "cutover_subject_digest": self.cutover_subject_digest,
            "runner_sha256": self.runner_sha256,
            "attestor_sha256": self.attestor_sha256,
        }


@dataclass(frozen=True)
class SourceRecord:
    role: str
    locator: str
    repository: str
    read_mode: str
    identity: tuple[tuple[str, str], ...]
    content_sha256: str
    readback_digest: str | None
    producer_sha256: str

    def __post_init__(self) -> None:
        for name in ("role", "locator", "repository", "read_mode"):
            _require_text(getattr(self, name), name, "SOURCE_RECORD_INVALID")
        if type(self.identity) is not tuple or not self.identity:
            _invalid(
                "SOURCE_RECORD_INVALID",
                "identity must be a non-empty exact tuple",
            )
        keys: list[str] = []
        for item in self.identity:
            if (
                type(item) is not tuple
                or len(item) != 2
                or type(item[0]) is not str
                or type(item[1]) is not str
                or not item[0]
                or not item[1]
            ):
                _invalid(
                    "SOURCE_RECORD_INVALID",
                    "identity must contain exact non-empty key/value pairs",
                )
            keys.append(item[0])
        if len(set(keys)) != len(keys) or tuple(keys) != tuple(sorted(keys)):
            _invalid(
                "SOURCE_RECORD_INVALID",
                "identity keys must be unique and sorted",
            )
        _require_digest(self.content_sha256, "content_sha256", "SOURCE_RECORD_INVALID")
        if self.readback_digest is not None:
            _require_digest(
                self.readback_digest,
                "readback_digest",
                "SOURCE_RECORD_INVALID",
            )
        _require_digest(self.producer_sha256, "producer_sha256", "SOURCE_RECORD_INVALID")

    @property
    def digest(self) -> str:
        return digest_value(self.canonical())

    def canonical(self) -> dict[str, object]:
        return {
            "role": self.role,
            "locator": self.locator,
            "repository": self.repository,
            "read_mode": self.read_mode,
            "identity": [[key, value] for key, value in self.identity],
            "content_sha256": self.content_sha256,
            "readback_digest": self.readback_digest,
            "producer_sha256": self.producer_sha256,
        }


@dataclass(frozen=True)
class SourceObservation:
    record: SourceRecord
    canonical_payload: bytes
    complete: bool

    def __post_init__(self) -> None:
        if type(self.record) is not SourceRecord:
            _invalid("SOURCE_OBSERVATION_INVALID", "record has the wrong exact type")
        if type(self.canonical_payload) is not bytes:
            _invalid(
                "SOURCE_OBSERVATION_INVALID",
                "canonical_payload must be exact bytes",
            )
        if type(self.complete) is not bool:
            _invalid("SOURCE_OBSERVATION_INVALID", "complete must be exact bool")
        if digest_bytes(self.canonical_payload) != self.record.content_sha256:
            _invalid(
                "SOURCE_OBSERVATION_INVALID",
                "canonical_payload does not match content_sha256",
            )


@dataclass(frozen=True)
class FieldBinding:
    target: str
    source_record_digests: tuple[str, ...]
    derivation: str

    def __post_init__(self) -> None:
        _require_text(self.target, "target", "FIELD_BINDING_INVALID")
        if (
            type(self.source_record_digests) is not tuple
            or not self.source_record_digests
            or any(
                type(value) is not str
                or _HEX64.fullmatch(value) is None
                for value in self.source_record_digests
            )
            or len(set(self.source_record_digests)) != len(self.source_record_digests)
        ):
            _invalid(
                "FIELD_BINDING_INVALID",
                "source_record_digests must be a unique non-empty tuple of SHA-256 values",
            )
        _require_text(self.derivation, "derivation", "FIELD_BINDING_INVALID")

    def canonical(self) -> dict[str, object]:
        return {
            "target": self.target,
            "source_record_digests": list(self.source_record_digests),
            "derivation": self.derivation,
        }


@dataclass(frozen=True)
class WriterAuthorityObservation:
    writer_generation: str
    record_id: str
    authority_state: str
    activation_id: str | None
    legacy_stopped: bool
    source_record_digests: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("writer_generation", "record_id", "authority_state"):
            _require_text(
                getattr(self, name), name, "WRITER_AUTHORITY_INVALID"
            )
        if self.activation_id is not None:
            _require_text(
                self.activation_id, "activation_id", "WRITER_AUTHORITY_INVALID"
            )
        if type(self.legacy_stopped) is not bool:
            _invalid("WRITER_AUTHORITY_INVALID", "legacy_stopped must be exact bool")
        if (
            type(self.source_record_digests) is not tuple
            or any(
                type(value) is not str
                or _HEX64.fullmatch(value) is None
                for value in self.source_record_digests
            )
            or len(set(self.source_record_digests)) != len(self.source_record_digests)
        ):
            _invalid(
                "WRITER_AUTHORITY_INVALID",
                "source_record_digests must be a unique tuple of SHA-256 values",
            )

    def canonical(self) -> dict[str, object]:
        return {
            "writer_generation": self.writer_generation,
            "record_id": self.record_id,
            "authority_state": self.authority_state,
            "activation_id": self.activation_id,
            "legacy_stopped": self.legacy_stopped,
            "source_record_digests": list(self.source_record_digests),
        }


def _canonical_projection(value: object, name: str) -> dict[str, object]:
    canonical = getattr(value, "canonical", None)
    if not callable(canonical):
        _invalid("COMPONENT_INVALID", f"{name} has no canonical projection")
    try:
        result = canonical()
        canonical_bytes(result)
    except Exception as error:
        if isinstance(error, BootstrapError):
            raise
        _invalid("COMPONENT_INVALID", f"{name} is not canonically encodable")
    if type(result) is not dict or any(type(key) is not str for key in result):
        _invalid("COMPONENT_INVALID", f"{name}.canonical() must return a string-keyed dict")
    return result


@dataclass(frozen=True)
class ComponentObservation:
    readbacks: tuple[tuple[str, object], ...]
    source_records: tuple[SourceRecord, ...]
    field_bindings: tuple[FieldBinding, ...]
    writer_authority: WriterAuthorityObservation | None = None

    def __post_init__(self) -> None:
        if type(self.readbacks) is not tuple:
            _invalid("COMPONENT_INVALID", "readbacks must be an exact tuple")
        names: list[str] = []
        for item in self.readbacks:
            if type(item) is not tuple or len(item) != 2:
                _invalid("COMPONENT_INVALID", "readbacks must contain pairs")
            _require_text(item[0], "readback name", "COMPONENT_INVALID")
            names.append(item[0])
        if len(set(names)) != len(names):
            _invalid("COMPONENT_INVALID", "readback names must be unique")
        if type(self.source_records) is not tuple or any(
            type(value) is not SourceRecord for value in self.source_records
        ):
            _invalid(
                "COMPONENT_INVALID",
                "source_records must be an exact tuple of SourceRecord values",
            )
        if type(self.field_bindings) is not tuple or any(
            type(value) is not FieldBinding for value in self.field_bindings
        ):
            _invalid(
                "COMPONENT_INVALID",
                "field_bindings must be an exact tuple of FieldBinding values",
            )
        if self.writer_authority is not None and type(
            self.writer_authority
        ) is not WriterAuthorityObservation:
            _invalid(
                "COMPONENT_INVALID",
                "writer_authority has the wrong exact type",
            )

    def canonical(self) -> dict[str, object]:
        return {
            "readbacks": {
                name: _canonical_projection(value, f"readbacks.{name}")
                for name, value in self.readbacks
            },
            "source_records": [value.canonical() for value in self.source_records],
            "field_bindings": [value.canonical() for value in self.field_bindings],
            "writer_authority": (
                None
                if self.writer_authority is None
                else self.writer_authority.canonical()
            ),
        }


_READBACK_TYPES: tuple[tuple[str, type], ...] = (
    ("legacy", LegacyReadback),
    ("durable_state", DurableStateReadback),
    ("writer_fence", WriterFenceReadback),
    ("ownership", OwnershipReadback),
    ("compatibility", CompatibilityPathReadback),
    ("runtime", RuntimePreflightReadback),
    ("packages", PackageReadback),
)
_READBACK_TYPE_MAP = dict(_READBACK_TYPES)


def _readback_body_digest(value: object) -> str:
    body = _canonical_projection(value, type(value).__name__)
    try:
        body = dict(body)
        observed = body.pop("readback_digest")
    except (KeyError, TypeError):
        _invalid("ATTESTATION_INVALID", "readback has no readback_digest field")
    if type(observed) is not str:
        _invalid("ATTESTATION_INVALID", "readback_digest must be exact text")
    return digest_value(body)


def _expected_binding_targets(
    subject: CutoverSubject,
    readbacks: dict[str, object],
) -> set[str]:
    targets: set[str] = set()
    for name, value in (("subject", subject), *readbacks.items()):
        targets.update(f"{name}.{field}" for field in _canonical_projection(value, name))
    return targets


@dataclass(frozen=True)
class AttestedCutoverBundle:
    schema: str
    attempt: AttemptIdentity
    subject: CutoverSubject
    legacy: LegacyReadback
    durable_state: DurableStateReadback
    writer_fence: WriterFenceReadback
    ownership: OwnershipReadback
    compatibility: CompatibilityPathReadback
    runtime: RuntimePreflightReadback
    packages: PackageReadback
    source_records: tuple[SourceRecord, ...]
    field_bindings: tuple[FieldBinding, ...]
    attestation_digest: str

    @classmethod
    def create(
        cls,
        *,
        attempt: AttemptIdentity,
        subject: CutoverSubject,
        components: tuple[ComponentObservation, ...],
    ) -> "AttestedCutoverBundle":
        try:
            if type(attempt) is not AttemptIdentity:
                _invalid("ATTESTATION_INVALID", "attempt has the wrong exact type")
            if type(subject) is not CutoverSubject:
                _invalid("ATTESTATION_INVALID", "subject has the wrong exact type")
            if type(components) is not tuple or any(
                type(value) is not ComponentObservation for value in components
            ):
                _invalid(
                    "ATTESTATION_INVALID",
                    "components must be an exact tuple of ComponentObservation values",
                )
            if attempt.repository != subject.repository:
                _invalid(
                    "ATTESTATION_INVALID",
                    "attempt and subject repositories differ",
                )
            if attempt.cutover_subject_digest != digest_value(subject.canonical()):
                _invalid(
                    "ATTESTATION_INVALID",
                    "cutover_subject_digest does not bind subject",
                )

            readbacks: dict[str, object] = {}
            source_records: list[SourceRecord] = []
            field_bindings: list[FieldBinding] = []
            for component in components:
                for name, value in component.readbacks:
                    expected_type = _READBACK_TYPE_MAP.get(name)
                    if expected_type is None or type(value) is not expected_type:
                        _invalid(
                            "ATTESTATION_INVALID",
                            f"readback {name!r} is missing or has the wrong exact type",
                        )
                    if name in readbacks:
                        _invalid(
                            "ATTESTATION_INVALID",
                            f"readback {name!r} is duplicated",
                        )
                    readbacks[name] = value
                source_records.extend(component.source_records)
                field_bindings.extend(component.field_bindings)
            if set(readbacks) != set(_READBACK_TYPE_MAP):
                _invalid("ATTESTATION_INVALID", "the seven readbacks are not complete")
            source_digests = tuple(record.digest for record in source_records)
            if (
                not source_records
                or len(set(source_digests)) != len(source_records)
                or source_digests != tuple(sorted(source_digests))
            ):
                _invalid(
                    "ATTESTATION_INVALID",
                    "source record digests must be ordered and unique",
                )
            for record in source_records:
                if record.repository != attempt.repository:
                    _invalid(
                        "ATTESTATION_INVALID",
                        "source record repository differs from attempt",
                    )
                if record.producer_sha256 != attempt.attestor_sha256:
                    _invalid(
                        "ATTESTATION_INVALID",
                        "source record producer does not match attestor",
                    )
            expected_targets = _expected_binding_targets(subject, readbacks)
            observed_targets = [binding.target for binding in field_bindings]
            if (
                len(observed_targets) != len(set(observed_targets))
                or set(observed_targets) != expected_targets
            ):
                _invalid(
                    "ATTESTATION_INVALID",
                    "field bindings do not exactly cover the canonical target set",
                )
            source_digests = set(source_digests)
            for binding in field_bindings:
                if not set(binding.source_record_digests) <= source_digests:
                    _invalid(
                        "ATTESTATION_INVALID",
                        f"field binding {binding.target!r} refers to an unknown source record",
                    )
            for name, value in readbacks.items():
                if name != "packages" and value.repository != subject.repository:
                    _invalid(
                        "ATTESTATION_INVALID",
                        f"readback {name!r} repository differs from subject",
                    )
                if _readback_body_digest(value) != value.readback_digest:
                    _invalid(
                        "ATTESTATION_INVALID",
                        f"readback {name!r} has a stale readback_digest",
                    )
            candidate = cls(
                schema=READBACK_BUNDLE_SCHEMA,
                attempt=attempt,
                subject=subject,
                legacy=readbacks["legacy"],
                durable_state=readbacks["durable_state"],
                writer_fence=readbacks["writer_fence"],
                ownership=readbacks["ownership"],
                compatibility=readbacks["compatibility"],
                runtime=readbacks["runtime"],
                packages=readbacks["packages"],
                source_records=tuple(source_records),
                field_bindings=tuple(field_bindings),
                attestation_digest="0" * 64,
            )
            result = cls(
                schema=candidate.schema,
                attempt=candidate.attempt,
                subject=candidate.subject,
                legacy=candidate.legacy,
                durable_state=candidate.durable_state,
                writer_fence=candidate.writer_fence,
                ownership=candidate.ownership,
                compatibility=candidate.compatibility,
                runtime=candidate.runtime,
                packages=candidate.packages,
                source_records=candidate.source_records,
                field_bindings=candidate.field_bindings,
                attestation_digest=digest_value(
                    candidate.canonical_without_attestation_digest()
                ),
            )
            result.validate()
            return result
        except BootstrapError:
            raise
        except Exception as error:
            raise BootstrapError("ATTESTATION_INVALID", str(error)) from error

    def canonical_without_attestation_digest(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "attempt": self.attempt.canonical(),
            "subject": self.subject.canonical(),
            "readbacks": {
                "legacy": self.legacy.canonical(),
                "durable_state": self.durable_state.canonical(),
                "writer_fence": self.writer_fence.canonical(),
                "ownership": self.ownership.canonical(),
                "compatibility": self.compatibility.canonical(),
                "runtime": self.runtime.canonical(),
                "packages": self.packages.canonical(),
            },
            "source_records": [record.canonical() for record in self.source_records],
            "field_bindings": [binding.canonical() for binding in self.field_bindings],
        }

    def canonical(self) -> dict[str, object]:
        return {
            **self.canonical_without_attestation_digest(),
            "attestation_digest": self.attestation_digest,
        }

    def validate(self) -> None:
        try:
            if type(self.schema) is not str or self.schema != READBACK_BUNDLE_SCHEMA:
                _invalid("ATTESTATION_INVALID", "schema is not the exact cutover schema")
            if type(self.attempt) is not AttemptIdentity:
                _invalid("ATTESTATION_INVALID", "attempt has the wrong exact type")
            if type(self.subject) is not CutoverSubject:
                _invalid("ATTESTATION_INVALID", "subject has the wrong exact type")
            observed_readbacks = {
                "legacy": self.legacy,
                "durable_state": self.durable_state,
                "writer_fence": self.writer_fence,
                "ownership": self.ownership,
                "compatibility": self.compatibility,
                "runtime": self.runtime,
                "packages": self.packages,
            }
            for name, expected_type in _READBACK_TYPES:
                if type(observed_readbacks[name]) is not expected_type:
                    _invalid(
                        "ATTESTATION_INVALID",
                        f"readback {name!r} has the wrong exact type",
                    )
            if type(self.source_records) is not tuple or any(
                type(value) is not SourceRecord for value in self.source_records
            ):
                _invalid("ATTESTATION_INVALID", "source_records has the wrong exact type")
            if type(self.field_bindings) is not tuple or any(
                type(value) is not FieldBinding for value in self.field_bindings
            ):
                _invalid("ATTESTATION_INVALID", "field_bindings has the wrong exact type")
            if self.attempt.repository != self.subject.repository:
                _invalid("ATTESTATION_INVALID", "attempt and subject repositories differ")
            if self.attempt.cutover_subject_digest != digest_value(
                self.subject.canonical()
            ):
                _invalid("ATTESTATION_INVALID", "cutover subject digest is not bound")
            for name, value in observed_readbacks.items():
                if name != "packages" and value.repository != self.subject.repository:
                    _invalid(
                        "ATTESTATION_INVALID",
                        f"readback {name!r} repository differs from subject",
                    )
                if _readback_body_digest(value) != value.readback_digest:
                    _invalid(
                        "ATTESTATION_INVALID",
                        f"readback {name!r} has a stale readback_digest",
                    )
            source_digests = tuple(record.digest for record in self.source_records)
            if (
                not self.source_records
                or len(set(source_digests)) != len(self.source_records)
                or source_digests != tuple(sorted(source_digests))
            ):
                _invalid(
                    "ATTESTATION_INVALID",
                    "source record digests must be ordered and unique",
                )
            source_digests = set(source_digests)
            for record in self.source_records:
                if record.repository != self.attempt.repository:
                    _invalid("ATTESTATION_INVALID", "source record repository differs")
                if record.producer_sha256 != self.attempt.attestor_sha256:
                    _invalid(
                        "ATTESTATION_INVALID",
                        "source record producer does not match attestor",
                    )
            expected_targets = _expected_binding_targets(self.subject, observed_readbacks)
            observed_targets = [binding.target for binding in self.field_bindings]
            if (
                len(observed_targets) != len(set(observed_targets))
                or set(observed_targets) != expected_targets
            ):
                _invalid(
                    "ATTESTATION_INVALID",
                    "field bindings do not exactly cover canonical targets",
                )
            for binding in self.field_bindings:
                if not set(binding.source_record_digests) <= source_digests:
                    _invalid(
                        "ATTESTATION_INVALID",
                        f"field binding {binding.target!r} refers to an unknown source",
                    )
            _require_digest(self.attestation_digest, "attestation_digest", "ATTESTATION_INVALID")
            if self.attestation_digest != digest_value(
                self.canonical_without_attestation_digest()
            ):
                _invalid("ATTESTATION_INVALID", "attestation_digest is stale")
        except BootstrapError as error:
            if error.code == "ATTESTATION_INVALID":
                raise
            raise BootstrapError("ATTESTATION_INVALID", error.detail) from error
        except Exception as error:
            raise BootstrapError("ATTESTATION_INVALID", str(error)) from error

    def cutover_bundle(self) -> CutoverReadbackBundle:
        self.validate()
        return CutoverReadbackBundle(
            schema=READBACK_BUNDLE_SCHEMA,
            subject=self.subject,
            legacy=self.legacy,
            durable_state=self.durable_state,
            writer_fence=self.writer_fence,
            ownership=self.ownership,
            compatibility=self.compatibility,
            runtime=self.runtime,
            packages=self.packages,
        )


FORBIDDEN_SOURCE_SURFACES = {
    "start",
    "stop",
    "restore",
    "drain",
    "write",
    "publish",
    "compare_and_swap",
    "compare_and_swap_ref",
    "activate",
    "advance",
    "install",
    "prepare",
    "command",
    "events",
    "put",
    "delete",
    "unlink",
}


def require_read_only_surface(source: object, *, required_method: str) -> None:
    """Reject any source whose public callable surface is not exactly {required_method}.

    Inspect class and instance namespaces directly so that ``__dir__`` cannot
    hide mutators. Dynamic attribute resolution is rejected because its
    callable surface cannot be enumerated exactly.
    """
    if type(required_method) is not str or not required_method:
        raise BootstrapError(
            "UNSAFE_SOURCE_CAPABILITY",
            "required_method must be non-empty exact text",
        )
    try:
        import inspect

        source_type = type(source)
        source_mro = source_type.__mro__
        if any("__getattr__" in vars(cls) for cls in source_mro):
            raise BootstrapError(
                "UNSAFE_SOURCE_CAPABILITY",
                "source exposes dynamic attribute resolution",
            )

        exposed = {
            name
            for cls in source_mro
            for name in vars(cls)
            if not name.startswith("_")
        }
        try:
            instance_namespace = object.__getattribute__(source, "__dict__")
        except AttributeError:
            instance_namespace = {}
        if isinstance(instance_namespace, dict):
            exposed.update(
                name
                for name in instance_namespace
                if type(name) is str and not name.startswith("_")
            )
        for name in sorted(exposed):
            attr = inspect.getattr_static(source, name)
            if name != required_method and (
                callable(attr) or callable(getattr(source, name))
            ):
                raise BootstrapError(
                    "UNSAFE_SOURCE_CAPABILITY",
                    f"source exposes an unlisted public callable: {name}",
                )
        if not callable(getattr(source, required_method, None)):
            raise BootstrapError(
                "UNSAFE_SOURCE_CAPABILITY",
                "source does not expose the required read method",
            )
    except BootstrapError:
        raise
    except Exception as error:
        raise BootstrapError(
            "UNSAFE_SOURCE_CAPABILITY",
            "source capability could not be inspected",
        ) from error


class FrozenReadPort:
    def __init__(self, value: object, *, expected_args: tuple[object, ...]) -> None:
        if type(expected_args) is not tuple:
            raise BootstrapError(
                "FROZEN_PORT_INVALID",
                "expected_args must be an exact tuple",
            )
        self._value = value
        self._expected_args = expected_args

    def read(self, *args: object, **kwargs: object) -> object:
        if kwargs or args != self._expected_args:
            raise BootstrapError(
                "FROZEN_PORT_ARGUMENTS",
                "frozen read port received unexpected arguments",
            )
        return self._value


class BootstrapLease:
    def __init__(
        self,
        *,
        expected_records: tuple[SourceRecord, ...],
        probes: tuple[Callable[[], SourceRecord], ...],
        local_assertions: tuple[Callable[[], None], ...],
        closers: tuple[Callable[[], None], ...],
    ) -> None:
        if type(expected_records) is not tuple or any(
            type(value) is not SourceRecord for value in expected_records
        ):
            raise BootstrapError(
                "LEASE_INVALID",
                "expected_records must be an exact tuple of SourceRecord values",
            )
        for name, values in (
            ("probes", probes),
            ("local_assertions", local_assertions),
            ("closers", closers),
        ):
            if type(values) is not tuple or any(
                not callable(value) for value in values
            ):
                raise BootstrapError(
                    "LEASE_INVALID",
                    f"{name} must be an exact tuple of callables",
                )
        self._expected_records = expected_records
        self._probes = probes
        self._local_assertions = local_assertions
        self._closers = closers
        self._closed = False

    def assert_stable(self) -> None:
        if len(self._probes) != len(self._expected_records):
            raise BootstrapError(
                "LIVE_INPUT_DRIFT",
                "source probe count differs from expected source records",
            )
        for index, (expected, probe) in enumerate(
            zip(self._expected_records, self._probes)
        ):
            try:
                observed = probe()
                if type(observed) is not SourceRecord:
                    raise TypeError("probe did not return SourceRecord")
                if canonical_bytes(observed.canonical()) != canonical_bytes(
                    expected.canonical()
                ):
                    raise ValueError("source record changed")
            except Exception as error:
                raise BootstrapError(
                    "LIVE_INPUT_DRIFT",
                    f"source record probe {index} changed or failed",
                ) from error
        for index, assertion in enumerate(self._local_assertions):
            try:
                assertion()
            except Exception as error:
                raise BootstrapError(
                    "LIVE_INPUT_DRIFT",
                    f"local input assertion {index} failed",
                ) from error

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: Exception | None = None
        for closer in reversed(self._closers):
            try:
                closer()
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise BootstrapError(
                "LEASE_CLOSE_FAILED",
                "one or more lease closers failed",
            ) from first_error
