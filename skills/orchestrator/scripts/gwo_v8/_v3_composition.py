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


def production_control(repository: str) -> PlanControlService:
    if _factory is not None:
        return _factory()
    from ._v3_production import build_production_control

    return build_production_control(repository)
