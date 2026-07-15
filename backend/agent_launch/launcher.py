"""`lac agent [dir]` -- LAC's local-model coding agent: scan the box, pick an
agent-capable local model, bake a num_ctx-raised variant, point stock OpenCode at
it, and launch. The hardware brain is the moat; OpenCode is wrapped, never edited."""
from pathlib import Path

from .variant import ensure_agent_variant, is_installed
from .config_writer import write_opencode_config, write_agent_commands
from .opencode_bin import resolve_opencode_binary

# Consider the whole catalog when ranking, then filter to what is actually on disk.
_CANDIDATE_DEPTH = 100


def _default_recommend(info, use_case, top_k):
    from backend.cookbook.recommend import recommend
    return recommend(info, use_case=use_case, top_k=top_k)


def _default_detect():
    from backend.cookbook.hardware import detect
    return detect()


def _default_provider_factory():
    from backend.provider.registry import create_provider
    return create_provider("ollama")


def _default_config(start=None):
    from backend.config import resolve_config
    return resolve_config(start)


def _default_launch(cmd, **kwargs):
    """Route the interactive OpenCode launch through the guarded proc wrapper
    (inherits the console; never hides it)."""
    from backend.cookbook.proc import run_interactive
    return run_interactive(cmd, **kwargs)


def _installed_names(provider) -> list[str]:
    """Normalize provider.list_models() (objects or dicts) to model names."""
    out = []
    for m in provider.list_models():
        if isinstance(m, dict):
            out.append(m.get("name") or m.get("model") or "")
        else:
            out.append(getattr(m, "name", getattr(m, "model", "")) or "")
    return [n for n in out if n]


def launch_agent(project_dir, *,
                 detect_fn=_default_detect,
                 recommend_fn=_default_recommend,
                 ensure_variant_fn=ensure_agent_variant,
                 write_config_fn=write_opencode_config,
                 write_commands_fn=write_agent_commands,
                 resolve_bin_fn=resolve_opencode_binary,
                 provider_factory=_default_provider_factory,
                 config_fn=_default_config,
                 launch_fn=_default_launch,
                 out=print) -> int:
    from backend.cookbook.recommend import AGENT_MIN_CONTEXT

    project_dir = Path(project_dir).resolve()
    cfg = config_fn(project_dir)
    host = cfg.ollama_host

    info = detect_fn()
    recs = recommend_fn(info, use_case="agent", top_k=_CANDIDATE_DEPTH)
    if not recs:
        out("No agent-capable local model fits this machine. "
            "Try installing a 7B+ tool-calling model (e.g. `ollama pull qwen3:8b`) "
            "and re-run `lac agent`.")
        return 1

    provider = provider_factory()
    installed = _installed_names(provider)

    # Rank by fit for the box, but only ever run a model the user already has:
    # building a variant from an absent base makes Ollama pull it (GBs, unasked).
    rec = next((r for r in recs if is_installed(r.model.id, installed)), None)
    if rec is None:
        best = recs[0]
        out("None of the agent-capable models for this machine are installed.")
        out("LAC's best fit for your box is %s. To use it:" % best.model.id)
        out("    ollama pull %s" % best.model.id)
        alternatives = [r.model.id for r in recs[1:4]]
        if alternatives:
            out("Or a lighter option: %s" % ", ".join(alternatives))
        out("Then re-run `lac agent`.")
        return 1

    base = rec.model.id
    num_ctx = max(int(rec.context_used), AGENT_MIN_CONTEXT)

    # Rank-0 is the best fit for the box; if it isn't installed, say so rather than
    # quietly running something worse.
    if rec is not recs[0]:
        out("Using %s (installed). A better fit for your box is %s - "
            "`ollama pull %s` to use it." % (base, recs[0].model.id, recs[0].model.id))

    variant = ensure_variant_fn(base, num_ctx,
                                list_names=lambda: installed,
                                create=provider.create)

    write_config_fn(project_dir, variant, host)
    write_commands_fn(project_dir)
    binary = resolve_bin_fn()

    out("LAC picked + prepared %s for your box (%s score %s, num_ctx %s). "
        "Launching OpenCode..." % (base, getattr(rec, "speed_source", "estimated"),
                                   getattr(rec, "score", "?"), num_ctx))
    warning = (rec.details or {}).get("agent_warning")
    if warning:
        out("  [!] " + warning)

    result = launch_fn([str(binary)], cwd=str(project_dir))
    return getattr(result, "returncode", 0)
