"""Bake and reuse a num_ctx-raised Ollama variant for the LAC agent (free tier).

The agent loop needs a large context (>=32k) or local models drop tool calls.
We create a `<base>-agent` variant once and reuse it. Pro tuning (offload sweep,
spill, iGPU) is a separate, licensed path and is NOT done here.

Hard rule: never create a variant from a base that is not already installed.
Ollama's /api/create resolves an absent `from` against the registry and pulls the
whole model -- observed live as an unannounced 18.56GB download for qwen3:30b-a3b.
"""
from typing import Callable, Iterable


class BaseModelNotInstalled(RuntimeError):
    """Raised instead of letting Ollama silently pull a multi-GB base model."""


def agent_variant_name(base_model: str) -> str:
    return f"{base_model}-agent"


def normalize_model_name(name: str) -> str:
    """Ollama reports an untagged pull (`qwen3`) as `qwen3:latest`; align them."""
    return name if ":" in name else f"{name}:latest"


def is_installed(model: str, installed: Iterable[str]) -> bool:
    target = normalize_model_name(model)
    return any(normalize_model_name(n) == target for n in installed)


def ensure_agent_variant(base_model: str, num_ctx: int, *,
                         list_names: Callable[[], Iterable[str]],
                         create: Callable[[str, str, dict], None]) -> str:
    """Return the agent variant name, (re)building it from `base_model` with the
    given num_ctx.

    Always restates the parameters rather than skipping when the variant exists: a
    variant left over from an earlier run can carry a stale num_ctx, and rebuilding
    from a local base is idempotent and effectively free (~0.05s, no download).

    Raises BaseModelNotInstalled if `base_model` is not on disk, so we never
    trigger a silent registry pull.
    """
    installed = list(list_names())
    variant = agent_variant_name(base_model)
    if not is_installed(base_model, installed):
        raise BaseModelNotInstalled(
            f"{base_model} is not installed. Refusing to build the agent variant, "
            f"because Ollama would download the whole model to do it. "
            f"Run `ollama pull {base_model}` first."
        )
    create(variant, base_model, {"num_ctx": num_ctx})
    return variant
