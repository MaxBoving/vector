from __future__ import annotations

from src.runtime.config import RuntimeStageHandlerConfig, RuntimeStageHandlerProvider, StageHandlerBinding
from src.runtime.stage_handlers import StageFamily, StageHandlerRegistry


DEFAULT_STAGE_HANDLER_CONFIG = RuntimeStageHandlerConfig(
    bindings=(
        StageHandlerBinding(StageFamily.ROUTER, "handle_router_stage"),
        StageHandlerBinding(StageFamily.CONTEXT, "handle_context_stage"),
        StageHandlerBinding(StageFamily.AGENT, "handle_agent_stage"),
        StageHandlerBinding(StageFamily.NOOP, "handle_noop_stage"),
    )
)


def build_default_stage_handler_registry(
    *,
    handlers: object,
    provider: RuntimeStageHandlerProvider | None = None,
    config: RuntimeStageHandlerConfig = DEFAULT_STAGE_HANDLER_CONFIG,
) -> StageHandlerRegistry:
    return (provider or RuntimeStageHandlerProvider()).build_registry(handlers=handlers, config=config)
