"""Lazy, private production composition seam for V3 PlanControl."""

from __future__ import annotations

from typing import Callable, Protocol


class PlanControlService(Protocol):
    def start(self, repository: str, ready_refs: object, options: object = None): ...


_factory: Callable[[], PlanControlService] | None = None


def install_production_factory(
    factory: Callable[[], PlanControlService] | None,
) -> None:
    global _factory
    _factory = factory


def production_control() -> PlanControlService | None:
    if _factory is None:
        return None
    return _factory()
