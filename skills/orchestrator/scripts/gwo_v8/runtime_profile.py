"""Provider-neutral immutable Runtime Profile value."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._canonical import digest_value


@dataclass(frozen=True)
class RuntimeProfile:
    name: str
    provider: str
    model: str
    thinking: str
    mode: str
    features: dict[str, Any]

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
