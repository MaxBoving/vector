from __future__ import annotations

from dataclasses import dataclass

from .stage_handlers import StageFamily, StageHandlerRegistry


@dataclass(frozen=True)
class StageHandlerBinding:
    family: StageFamily
    handler_name: str


@dataclass(frozen=True)
class RuntimeStageHandlerConfig:
    bindings: tuple[StageHandlerBinding, ...]


class RuntimeStageHandlerProvider:
    def build_registry(
        self,
        *,
        handlers: object,
        config: RuntimeStageHandlerConfig,
    ) -> StageHandlerRegistry:
        registry = StageHandlerRegistry()
        for binding in config.bindings:
            handler = getattr(handlers, binding.handler_name, None)
            if handler is None:
                raise AttributeError(
                    f"Handler object is missing configured stage handler '{binding.handler_name}'."
                )
            registry.register(binding.family, handler)
        return registry
