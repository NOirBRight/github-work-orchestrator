"""Private canonical encoding shared by the V8 deep modules."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


class CanonicalJsonError(ValueError):
    """A value or payload is outside the closed canonical JSON domain."""


_MAX_CANONICAL_JSON_DEPTH = 64
_MAX_CANONICAL_INTEGER_DIGITS = 4096
_MAX_CANONICAL_INTEGER_ABS = 10**_MAX_CANONICAL_INTEGER_DIGITS


def _validate_unicode_scalar_text(value: str, *, path: str) -> None:
    """Reject UTF-16 surrogate code points, which are not Unicode scalars."""

    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise CanonicalJsonError(
            f"{path} contains a non-scalar Unicode surrogate"
        )


def _validate_json_value(
    value: Any,
    *,
    path: str = "$",
    _depth: int = 0,
    _active_ids: set[int] | None = None,
) -> None:
    if _depth > _MAX_CANONICAL_JSON_DEPTH:
        raise CanonicalJsonError(
            f"{path} exceeds the maximum canonical JSON depth "
            f"of {_MAX_CANONICAL_JSON_DEPTH}"
        )

    value_type = type(value)
    if value is None or value_type is bool:
        return
    if value_type is str:
        _validate_unicode_scalar_text(value, path=path)
        return
    if value_type is int:
        if abs(value) >= _MAX_CANONICAL_INTEGER_ABS:
            raise CanonicalJsonError(
                f"{path} exceeds the maximum canonical integer size "
                f"of {_MAX_CANONICAL_INTEGER_DIGITS} digits"
            )
        return
    if value_type is float:
        if not math.isfinite(value):
            raise CanonicalJsonError(f"{path} contains a non-finite number")
        return
    if value_type is list or value_type is tuple:
        active_ids = set() if _active_ids is None else _active_ids
        identity = id(value)
        if identity in active_ids:
            raise CanonicalJsonError(f"{path} contains a reference cycle")
        active_ids.add(identity)
        try:
            for index, child in enumerate(value):
                _validate_json_value(
                    child,
                    path=f"{path}[{index}]",
                    _depth=_depth + 1,
                    _active_ids=active_ids,
                )
        finally:
            active_ids.remove(identity)
        return
    if value_type is dict:
        active_ids = set() if _active_ids is None else _active_ids
        identity = id(value)
        if identity in active_ids:
            raise CanonicalJsonError(f"{path} contains a reference cycle")
        active_ids.add(identity)
        try:
            for key, child in value.items():
                if type(key) is not str:
                    raise CanonicalJsonError(
                        f"{path} contains a non-string object key"
                    )
                _validate_unicode_scalar_text(
                    key,
                    path=f"{path} object key",
                )
                _validate_json_value(
                    child,
                    path=f"{path}.{key}",
                    _depth=_depth + 1,
                    _active_ids=active_ids,
                )
        finally:
            active_ids.remove(identity)
        return
    raise CanonicalJsonError(
        f"{path} contains a value outside the canonical JSON domain"
    )


def canonical_bytes(value: Any) -> bytes:
    """Encode one exact JSON value without Python coercions or NaN tokens."""

    _validate_json_value(value)
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (
        TypeError,
        UnicodeEncodeError,
        ValueError,
        RecursionError,
    ) as error:
        raise CanonicalJsonError("value cannot be canonically encoded") from error


def strict_json_loads(payload: str | bytes) -> Any:
    """Parse strict JSON, rejecting extensions and duplicate object names."""

    try:
        text = payload.decode("utf-8") if type(payload) is bytes else payload
    except UnicodeDecodeError as error:
        raise CanonicalJsonError("payload is not strict UTF-8") from error
    if type(text) is not str:
        raise CanonicalJsonError("payload must be exact text or bytes")

    def reject_constant(token: str) -> None:
        raise CanonicalJsonError(f"non-JSON numeric token {token} is forbidden")

    def exact_integer(token: str) -> int:
        digits = token[1:] if token.startswith("-") else token
        if len(digits) > _MAX_CANONICAL_INTEGER_DIGITS:
            raise CanonicalJsonError(
                "integer exceeds the maximum canonical size "
                f"of {_MAX_CANONICAL_INTEGER_DIGITS} digits"
            )
        return int(token)

    def exact_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                raise CanonicalJsonError(f"duplicate object name {key!r}")
            value[key] = child
        return value

    try:
        value = json.loads(
            text,
            parse_constant=reject_constant,
            parse_int=exact_integer,
            object_pairs_hook=exact_object,
        )
    except CanonicalJsonError:
        raise
    except (
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValueError,
        RecursionError,
    ) as error:
        raise CanonicalJsonError("payload is not strict JSON") from error
    _validate_json_value(value)
    return value


def load_canonical_json(payload: str | bytes) -> Any:
    """Parse and prove the exact canonical byte representation."""

    value = strict_json_loads(payload)
    try:
        observed = payload.encode("utf-8") if type(payload) is str else payload
    except UnicodeEncodeError as error:
        raise CanonicalJsonError("payload is not Unicode scalar text") from error
    if canonical_bytes(value) != observed:
        raise CanonicalJsonError("payload is valid JSON but is not canonical")
    return value


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_value(value: Any) -> str:
    return digest_bytes(canonical_bytes(value))
