"""Provider-neutral immutable Runtime Profile value."""

from __future__ import annotations

from abc import ABCMeta
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any

from ._canonical import CanonicalJsonError, canonical_bytes, digest_value


class _SealedValueMeta(ABCMeta):
    """Construct tuple-backed values through ``__new__`` only.

    Their public ``__init__`` deliberately rejects explicit re-entry.  This
    prevents a caller from mutating an already-published value by invoking its
    generated/dataclass initializer again.
    """

    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        return cls.__new__(cls, *args, **kwargs)


def _reject_reinitialization(*_args: Any, **_kwargs: Any) -> None:
    raise TypeError("sealed Runtime value cannot be reinitialized")


def _immutable_error(*_args: Any, **_kwargs: Any) -> None:
    raise TypeError("Runtime Profile features are immutable")


def _freeze_json(value: Any, *, path: str = "features") -> Any:
    """Snapshot exact JSON into composition-only immutable values."""

    if type(value) is _ImmutableJsonObject:
        value = value.to_json()
    elif type(value) is _ImmutableJsonArray:
        value = value.to_json()
    value_type = type(value)
    if value is None or value_type in {str, bool, int}:
        return value
    if value_type is float:
        if not math.isfinite(value):
            raise TypeError(f"Runtime Profile {path} contains a non-finite number")
        return value
    if value_type is dict:
        entries: list[tuple[str, Any]] = []
        for key, child in value.items():
            if type(key) is not str:
                raise TypeError(
                    "Runtime Profile feature object keys must be exact strings"
                )
            entries.append((key, _freeze_json(child, path=f"{path}.{key}")))
        return _ImmutableJsonObject(tuple(entries))
    if value_type is list:
        return _ImmutableJsonArray(
            tuple(
                _freeze_json(child, path=f"{path}[{index}]")
                for index, child in enumerate(value)
            )
        )
    raise TypeError("Runtime Profile features must contain exact JSON values")


def _project_json(value: Any) -> Any:
    if type(value) is _ImmutableJsonObject:
        return value.to_json()
    if type(value) is _ImmutableJsonArray:
        return value.to_json()
    return value


class _ImmutableJsonObject(
    frozenset, Mapping[str, Any], metaclass=_SealedValueMeta
):
    """A frozenset-backed object view with no writable Python object state."""

    __slots__ = ()

    def __new__(
        cls, entries: tuple[tuple[str, Any], ...]
    ) -> "_ImmutableJsonObject":
        if type(entries) is not tuple:
            raise TypeError("immutable JSON object entries must be a tuple")
        return frozenset.__new__(cls, entries)

    __init__ = _reject_reinitialization

    def __getitem__(self, key: str) -> Any:
        for candidate, value in frozenset.__iter__(self):
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _value in frozenset.__iter__(self))

    def __len__(self) -> int:
        return frozenset.__len__(self)

    def __contains__(self, key: object) -> bool:
        return any(
            candidate == key
            for candidate, _value in frozenset.__iter__(self)
        )

    __setitem__ = _immutable_error
    __delitem__ = _immutable_error
    clear = _immutable_error
    pop = _immutable_error
    popitem = _immutable_error
    setdefault = _immutable_error
    update = _immutable_error
    __ior__ = _immutable_error

    def to_json(self) -> dict[str, Any]:
        return {
            key: _project_json(child)
            for key, child in frozenset.__iter__(self)
        }

    def __copy__(self) -> "_ImmutableJsonObject":
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> dict[str, Any]:
        # ``dataclasses.asdict`` remains a safe legacy projection.
        return self.to_json()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            try:
                return self.to_json() == {
                    key: _project_json(value) for key, value in other.items()
                }
            except Exception:
                return False
        return False

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    __hash__ = frozenset.__hash__

    def __repr__(self) -> str:
        return repr(self.to_json())


class _ImmutableJsonArray(
    tuple, Sequence[Any], metaclass=_SealedValueMeta
):
    """A tuple-backed array view with no writable Python object state."""

    __slots__ = ()

    def __new__(cls, values: tuple[Any, ...]) -> "_ImmutableJsonArray":
        if type(values) is not tuple:
            raise TypeError("immutable JSON array values must be a tuple")
        return tuple.__new__(cls, values)

    __init__ = _reject_reinitialization

    __setitem__ = _immutable_error
    __delitem__ = _immutable_error
    __iadd__ = _immutable_error
    __imul__ = _immutable_error
    append = _immutable_error
    clear = _immutable_error
    extend = _immutable_error
    insert = _immutable_error
    pop = _immutable_error
    remove = _immutable_error
    reverse = _immutable_error
    sort = _immutable_error

    def to_json(self) -> list[Any]:
        return [_project_json(child) for child in tuple.__iter__(self)]

    def __copy__(self) -> "_ImmutableJsonArray":
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> list[Any]:
        return self.to_json()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Sequence) and not isinstance(
            other, (str, bytes, bytearray)
        ):
            try:
                return self.to_json() == [_project_json(value) for value in other]
            except Exception:
                return False
        return False

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    __hash__ = tuple.__hash__

    def __repr__(self) -> str:
        return repr(self.to_json())


@dataclass(frozen=True, init=False)
class RuntimeProfile(tuple, metaclass=_SealedValueMeta):
    """A sealed tuple-backed Profile with dataclass-compatible projection."""

    __slots__ = ()

    name: str
    provider: str
    model: str
    thinking: str
    mode: str
    features: Mapping[str, Any]

    def __new__(
        cls,
        name: str,
        provider: str,
        model: str,
        thinking: str,
        mode: str,
        features: Mapping[str, Any],
    ) -> "RuntimeProfile":
        values = (name, provider, model, thinking, mode)
        for field_name, value in zip(
            ("name", "provider", "model", "thinking", "mode"),
            values,
            strict=True,
        ):
            if type(value) is not str or not value.strip():
                raise TypeError(
                    f"Runtime Profile {field_name} must be an exact non-empty string"
                )
        if type(features) not in {dict, _ImmutableJsonObject}:
            raise TypeError("Runtime Profile features must be an exact object")
        raw_features = (
            features.to_json()
            if type(features) is _ImmutableJsonObject
            else features
        )
        try:
            canonical_bytes(
                {
                    "name": name,
                    "provider": provider,
                    "model": model,
                    "thinking": thinking,
                    "mode": mode,
                    "features": raw_features,
                }
            )
        except CanonicalJsonError as error:
            raise TypeError(
                "Runtime Profile must contain bounded Unicode-scalar exact JSON values"
            ) from error
        frozen = _freeze_json(raw_features)
        if type(frozen) is not _ImmutableJsonObject:
            raise TypeError("Runtime Profile features must be an object")
        return tuple.__new__(cls, (*values, frozen))

    __init__ = _reject_reinitialization

    @property
    def name(self) -> str:
        return tuple.__getitem__(self, 0)

    @property
    def provider(self) -> str:
        return tuple.__getitem__(self, 1)

    @property
    def model(self) -> str:
        return tuple.__getitem__(self, 2)

    @property
    def thinking(self) -> str:
        return tuple.__getitem__(self, 3)

    @property
    def mode(self) -> str:
        return tuple.__getitem__(self, 4)

    @property
    def features(self) -> _ImmutableJsonObject:
        return tuple.__getitem__(self, 5)

    def __copy__(self) -> "RuntimeProfile":
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> "RuntimeProfile":
        return self

    def canonical(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "thinking": self.thinking,
            "mode": self.mode,
            "features": self.features.to_json(),
        }

    @property
    def digest(self) -> str:
        return digest_value(self.canonical())
