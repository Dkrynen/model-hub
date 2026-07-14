"""Bake and reuse a num_ctx-raised Ollama variant for the LAC agent (free tier).

The agent loop needs a large context (>=32k) or local models drop tool calls.
We create a `<base>-agent` variant once and reuse it. Pro tuning (offload sweep,
spill, iGPU) is a separate, licensed path and is NOT done here."""
from typing import Callable, Iterable


def agent_variant_name(base_model: str) -> str:
    return f"{base_model}-agent"


def ensure_agent_variant(base_model: str, num_ctx: int, *,
                         list_names: Callable[[], Iterable[str]],
                         create: Callable[[str, str, dict], None]) -> str:
    """Return the agent variant name, creating it from `base_model` with the given
    num_ctx if it does not already exist. Idempotent."""
    variant = agent_variant_name(base_model)
    if variant in set(list_names()):
        return variant
    create(variant, base_model, {"num_ctx": num_ctx})
    return variant
