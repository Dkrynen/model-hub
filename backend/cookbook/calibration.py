# backend/cookbook/calibration.py
"""Per-machine self-calibration: turn real apt-benchmark results into
corrected recommendations. Pure/Ollama-free except detect_stack()."""
from __future__ import annotations

from .recommend import load_models, QUANTS, SUB4BIT_QUANT

# ollama quant-suffix (lowercased) -> catalog quant name. Most catalog quant
# names (e.g. "Q4_K_M", "Q6_K") already match the ollama tag suffix once
# lowercased. A couple of ollama's real suffixes are irregular relative to
# the catalog name ("q8_0" for "Q8", "fp16" for "F16") and need an alias.
_QUANT_ALIASES = {"Q8": "q8_0", "F16": "fp16"}
_SUFFIX_TO_QUANT: dict[str, str] = {}
for _q in QUANTS:
    _SUFFIX_TO_QUANT[_q.name.lower()] = _q.name
    if _q.name in _QUANT_ALIASES:
        _SUFFIX_TO_QUANT[_QUANT_ALIASES[_q.name]] = _q.name


def parse_model_tag(tag: str):
    """Map an ollama tag to (catalog_id, quant_name), or None if unknown."""
    ids = {m.id: m for m in load_models()}
    # exact catalog id (incl. hf.co sub-4bit) -> its single/default quant
    if tag in ids:
        m = ids[tag]
        return (tag, SUB4BIT_QUANT.name if m.sub4bit else "Q4_K_M")
    # try stripping a quant suffix: "<id>-<quant>"
    for suffix, qname in _SUFFIX_TO_QUANT.items():
        needle = "-" + suffix
        if tag.lower().endswith(needle):
            base = tag[: -len(needle)]
            if base in ids:
                return (base, qname)
    return None
