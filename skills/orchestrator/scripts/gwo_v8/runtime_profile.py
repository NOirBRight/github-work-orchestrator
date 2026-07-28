"""Provider-neutral immutable Runtime Profile value."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._canonical import digest_value


def _immutable_error(*_args: Any, **_kwargs: Any) -> None:
    raise TypeError("Runtime Profile features are immutable")


class _FrozenJsonObject(dict[str, Any]):
    """JSON-compatible object that cannot retain or expose mutable children."""

    __setitem__ = _immutable_error
    __delitem__ = _immutable_error
    clear = _immutable_error
    pop = _immutable_error
    popitem = _immutable_error
    setdefault = _immutable_error
    update = _immutable_error
    __ior__ = _immutable_error

    def __copy__(self) -> "_FrozenJsonObject":
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> "_FrozenJsonObject":
        return self


class _FrozenJsonArray(list[Any]):
    """JSON-compatible array whose sequence operations all fail closed."""

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

    def __copy__(self) -> "_FrozenJsonArray":
        return self

    def __deepcopy__(self, _memo: dict[int, Any]) -> "_FrozenJsonArray":
        return self


def _freeze_json(value: Any) -> Any:
    if isinstance(value, _FrozenJsonObject | _FrozenJsonArray):
        return value
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Runtime Profile feature object keys must be strings")
        return _FrozenJsonObject(
            (key, _freeze_json(child)) for key, child in value.items()
        )
    if isinstance(value, (list, tuple)):
        return _FrozenJsonArray(_freeze_json(child) for child in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("Runtime Profile features must contain JSON-compatible values")


@dataclass(frozen=True)
class RuntimeProfile:
    name: str
    provider: str
    model: str
    thinking: str
    mode: str
    features: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.features, dict):
            raise TypeError("Runtime Profile features must be an object")
        object.__setattr__(self, "features", _freeze_json(self.features))

    @property
    def digest(self) -> str:
        return digest_value(
            {
                "name": self.name,
                "provider": self.provider,
                "model": self.model,
                "thinking": self.thinking,
                "mode": self.mode,
                "features": self.features,
            }
        )
