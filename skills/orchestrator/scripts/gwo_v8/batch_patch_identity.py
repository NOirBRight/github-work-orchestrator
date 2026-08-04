"""Pure PatchIdentityV1 and Clean Base Advance proof values."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ._canonical import digest_value
from .candidate_gate import AcceptedCandidateReceipt, InteractionKey

if TYPE_CHECKING:
    from .batch_integrator import AncestorReadback, TargetDeltaReadback


_CHANGE_KINDS = {"add", "delete", "modify", "type-change"}
_OBJECT_TYPES = {"blob", "gitlink"}
_OBJECT_FORMAT_LENGTHS = {"sha1": 40, "sha256": 64}


def _batch_integrator_error(code: str, detail: str) -> Exception:
    from .batch_integrator import BatchIntegratorError

    return BatchIntegratorError(code, detail)


def _require_digest(name: str, value: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _batch_integrator_error(
            "BATCH_DIGEST_INVALID",
            f"{name} must be a lowercase SHA-256 digest",
        )
    return value


def _require_object_id(name: str, value: str) -> str:
    if (
        type(value) is not str
        or len(value) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise _batch_integrator_error(
            "BATCH_OBJECT_ID_INVALID",
            f"{name} must be a lowercase Git object ID",
        )
    return value


def _validate_path(path: str) -> None:
    if type(path) is not str or not path:
        raise ValueError(
            "PatchIdentityV1 paths must be safe repository-relative paths"
        )
    if (
        "\x00" in path
        or path.startswith("/")
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise ValueError(
            "PatchIdentityV1 paths must be safe repository-relative paths"
        )
    try:
        path.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(
            "PatchIdentityV1 paths must be valid UTF-8"
        ) from error


def _validate_object_id(value: str) -> None:
    if (
        type(value) is not str
        or len(value) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(
            "PatchIdentityV1 object IDs must be lowercase hexadecimal"
        )


@dataclass(frozen=True)
class PatchIdentityEntry:
    old_path: str | None
    new_path: str | None
    change_kind: Literal["add", "delete", "modify", "type-change"]
    old_mode: str | None
    new_mode: str | None
    old_object_type: Literal["blob", "gitlink"] | None
    new_object_type: Literal["blob", "gitlink"] | None
    old_oid: str | None
    new_oid: str | None

    def __post_init__(self) -> None:
        if self.change_kind not in _CHANGE_KINDS:
            raise ValueError("unsupported PatchIdentityV1 change kind")
        old_values = (
            self.old_path,
            self.old_mode,
            self.old_object_type,
            self.old_oid,
        )
        new_values = (
            self.new_path,
            self.new_mode,
            self.new_object_type,
            self.new_oid,
        )
        for side, values in (("old", old_values), ("new", new_values)):
            present = values[0] is not None
            if present and any(value is None for value in values):
                raise ValueError(
                    f"PatchIdentityV1 {side} entry is only partially present"
                )
            if not present and any(value is not None for value in values):
                raise ValueError(
                    f"PatchIdentityV1 {side} entry has fields without a path"
                )
            if not present:
                continue
            path, mode, object_type, oid = values
            assert isinstance(path, str)
            assert isinstance(mode, str)
            assert isinstance(object_type, str)
            assert isinstance(oid, str)
            _validate_path(path)
            if len(mode) != 6 or not mode.isdigit():
                raise ValueError("PatchIdentityV1 modes must be six-digit Git modes")
            if object_type not in _OBJECT_TYPES:
                raise ValueError(
                    "PatchIdentityV1 object types must be blob or gitlink"
                )
            _validate_object_id(oid)
        if self.change_kind == "add" and (
            self.old_path is not None or self.new_path is None
        ):
            raise ValueError("PatchIdentityV1 add entry has invalid sides")
        if self.change_kind == "delete" and (
            self.old_path is None or self.new_path is not None
        ):
            raise ValueError("PatchIdentityV1 delete entry has invalid sides")
        if self.change_kind in {"modify", "type-change"} and (
            self.old_path is None or self.new_path is None
        ):
            raise ValueError("PatchIdentityV1 change entry has invalid sides")
        if self.change_kind == "type-change" and (
            self.old_mode == self.new_mode
            and self.old_object_type == self.new_object_type
        ):
            raise ValueError(
                "PatchIdentityV1 type-change must change mode or object type"
            )

    def encoded(self) -> bytes:
        fields = (
            b"" if self.old_path is None else self.old_path.encode("utf-8"),
            b"" if self.new_path is None else self.new_path.encode("utf-8"),
            self.change_kind.encode("ascii"),
            b"" if self.old_mode is None else self.old_mode.encode("ascii"),
            b"" if self.new_mode is None else self.new_mode.encode("ascii"),
            b""
            if self.old_object_type is None
            else self.old_object_type.encode("ascii"),
            b""
            if self.new_object_type is None
            else self.new_object_type.encode("ascii"),
            b"" if self.old_oid is None else bytes.fromhex(self.old_oid),
            b"" if self.new_oid is None else bytes.fromhex(self.new_oid),
        )
        return b"".join(length_prefix(field) for field in fields)


@dataclass(frozen=True)
class PatchIdentityV1:
    repository_object_format: Literal["sha1", "sha256"]
    entries: tuple[PatchIdentityEntry, ...]

    def __post_init__(self) -> None:
        if self.repository_object_format not in _OBJECT_FORMAT_LENGTHS:
            raise ValueError("PatchIdentityV1 object format must be sha1 or sha256")
        entries = tuple(self.entries)
        if any(type(entry) is not PatchIdentityEntry for entry in entries):
            raise ValueError("PatchIdentityV1 entries must be exact tree entries")
        object.__setattr__(self, "entries", entries)

    @property
    def digest(self) -> str:
        return patch_identity_v1(self.repository_object_format, self.entries)


def length_prefix(value: bytes) -> bytes:
    if type(value) is not bytes:
        raise TypeError("PatchIdentityV1 LP values must be bytes")
    return len(value).to_bytes(8, "big", signed=False) + value


def _validate_entries(
    repository_object_format: Literal["sha1", "sha256"],
    entries: tuple[PatchIdentityEntry, ...],
) -> None:
    expected_oid_length = _OBJECT_FORMAT_LENGTHS[repository_object_format]
    path_by_casefold: dict[str, str] = {}
    seen_paths: set[tuple[str, str]] = set()
    for entry in entries:
        if type(entry) is not PatchIdentityEntry:
            raise ValueError("PatchIdentityV1 entries must be exact tree entries")
        for path in (entry.old_path, entry.new_path):
            if path is None:
                continue
            folded = path.casefold()
            existing = path_by_casefold.setdefault(folded, path)
            if existing != path:
                raise ValueError(
                    "PatchIdentityV1 paths have case-folding ambiguity"
                )
        path_pair = (entry.old_path, entry.new_path)
        if path_pair in seen_paths:
            raise ValueError("PatchIdentityV1 entries must not duplicate paths")
        seen_paths.add(path_pair)
        for name, oid in (
            ("old_oid", entry.old_oid),
            ("new_oid", entry.new_oid),
        ):
            if oid is None:
                continue
            _validate_object_id(oid)
            if len(oid) != expected_oid_length:
                raise ValueError(
                    f"PatchIdentityV1 {name} does not match {repository_object_format}"
                )


def patch_identity_v1(
    repository_object_format: Literal["sha1", "sha256"],
    entries: tuple[PatchIdentityEntry, ...],
) -> str:
    if repository_object_format not in _OBJECT_FORMAT_LENGTHS:
        raise ValueError("PatchIdentityV1 object format must be sha1 or sha256")
    frozen_entries = tuple(entries)
    _validate_entries(repository_object_format, frozen_entries)
    encoded = sorted(entry.encoded() for entry in frozen_entries)
    payload = (
        b"gwo.patch-identity.v1\x00"
        + length_prefix(repository_object_format.encode("ascii"))
        + b"".join(length_prefix(entry) for entry in encoded)
    )
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class CleanBaseAdvanceProof:
    base_sha: str
    base_tree_oid: str
    candidate_sha: str
    candidate_tree_oid: str
    target_head_sha: str
    target_tree_oid: str
    original_base_is_ancestor: bool
    ancestor_readback_digest: str
    target_delta_interaction_keys: tuple[InteractionKey, ...]
    target_delta_protected_interaction_keys: tuple[InteractionKey, ...]
    target_delta_facts_digest: str
    advanced_member_tree_oid: str
    original_patch_digest: str
    recomputed_patch_digest: str
    proof_digest: str


def require_clean_base_advance(
    *,
    member: AcceptedCandidateReceipt,
    original_patch_digest: str,
    recomputed_patch_digest: str,
    ancestor: AncestorReadback,
    target_delta: TargetDeltaReadback,
    target_tree_oid: str = "8" * 40,
    advanced_member_tree_oid: str | None = None,
) -> CleanBaseAdvanceProof:
    ancestor.validate()
    if (
        ancestor.ancestor_sha != member.base_sha
        or ancestor.descendant_sha != target_delta.target_head_sha
    ):
        raise _batch_integrator_error(
            "CLEAN_BASE_ANCESTOR_READBACK_MISMATCH",
            "ancestor readback does not name the member base and current target",
        )
    if not ancestor.is_ancestor:
        raise _batch_integrator_error(
            "CLEAN_BASE_ANCESTOR_REQUIRED",
            "original Candidate base is not an authoritative target ancestor",
        )
    target_delta.canonical()
    if target_delta.base_sha != member.base_sha:
        raise _batch_integrator_error(
            "TARGET_DELTA_BASE_MISMATCH",
            "target delta facts do not start at the Candidate base",
        )
    if target_delta.protected_interaction_keys:
        raise _batch_integrator_error(
            "TARGET_DELTA_PROTECTED_INTERACTION",
            "target delta shares a protected Interaction Key with the Candidate",
        )
    if original_patch_digest != recomputed_patch_digest:
        raise _batch_integrator_error(
            "CLEAN_BASE_PATCH_IDENTITY_MISMATCH",
            "PatchIdentityV1 changed across Clean Base Advance",
        )
    _require_digest("original_patch_digest", original_patch_digest)
    _require_digest("recomputed_patch_digest", recomputed_patch_digest)
    _require_object_id("target_tree_oid", target_tree_oid)
    advanced_tree = advanced_member_tree_oid or member.candidate_tree_oid
    _require_object_id("advanced_member_tree_oid", advanced_tree)
    body = {
        "base_sha": member.base_sha,
        "base_tree_oid": member.base_tree_oid,
        "candidate_sha": member.candidate_sha,
        "candidate_tree_oid": member.candidate_tree_oid,
        "target_head_sha": target_delta.target_head_sha,
        "target_tree_oid": target_tree_oid,
        "original_base_is_ancestor": ancestor.is_ancestor,
        "ancestor_readback_digest": ancestor.readback_digest,
        "target_delta_interaction_keys": [
            key.canonical() for key in target_delta.interaction_keys
        ],
        "target_delta_protected_interaction_keys": [
            key.canonical() for key in target_delta.protected_interaction_keys
        ],
        "target_delta_facts_digest": target_delta.facts_digest,
        "advanced_member_tree_oid": advanced_tree,
        "original_patch_digest": original_patch_digest,
        "recomputed_patch_digest": recomputed_patch_digest,
    }
    return CleanBaseAdvanceProof(**body, proof_digest=digest_value(body))
