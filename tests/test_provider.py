from __future__ import annotations

import pytest
from types import SimpleNamespace

from backend.provider import create_provider, default_provider, list_providers
from backend.provider.base import ProviderError
from backend.provider.ollama import OllamaProvider


def test_known_providers_include_ollama():
    names = {p["name"] for p in list_providers()}
    assert "ollama" in names


def test_create_default_provider_is_ollama():
    p = create_provider(None)
    assert isinstance(p, OllamaProvider)
    assert p.base_url.startswith("http://")


def test_create_unknown_raises():
    with pytest.raises(ProviderError):
        create_provider("nonexistent_provider")


def test_ollama_list_models_maps_context_length_not_parameter_size(monkeypatch):
    provider = OllamaProvider()
    monkeypatch.setattr(
        provider,
        "_get",
        lambda path: {
            "models": [
                {
                    "name": "qwen2.5:7b",
                    "context_length": 32768,
                    "details": {
                        "parameter_size": "7.6B",
                        "quantization_level": "Q4_K_M",
                    },
                },
                {
                    "name": "legacy:latest",
                    "details": {"parameter_size": "3B"},
                },
            ]
        },
    )

    models = provider.list_models()

    assert models[0].context_length == 32768
    assert models[1].context_length == 0
    assert all(isinstance(model.context_length, int) for model in models)


def test_default_provider_resolves_configuration_from_explicit_project_root(
    monkeypatch, tmp_path
):
    import backend.provider.registry as registry

    starts = []

    def fake_resolve(start=None):
        starts.append(start)
        return SimpleNamespace(providers={}, ollama_host="http://project-ollama:11434")

    monkeypatch.setattr(registry, "resolve_config", fake_resolve)

    provider = registry.default_provider(tmp_path)

    assert starts == [tmp_path]
    assert isinstance(provider, OllamaProvider)
    assert provider.base_url == "http://project-ollama:11434"


@pytest.mark.live
def test_ollama_list_models(ollama_available):
    if not ollama_available:
        pytest.skip("Ollama not running")
    p = default_provider()
    models = p.list_models()
    if not models:
        pytest.skip("no models installed")
    assert all(m.name for m in models)


def _smallest_installed_model(p):
    """Pick the smallest installed model so live chat tests stay fast — and
    don't hard-code a model name that may not be installed."""
    models = p.list_models()
    if not models:
        return None
    return min(models, key=lambda m: m.size or float("inf")).name


@pytest.mark.live
def test_ollama_non_stream_chat(ollama_available):
    if not ollama_available:
        pytest.skip("Ollama not running")
    p = default_provider()
    model = _smallest_installed_model(p)
    if not model:
        pytest.skip("no models installed")
    deltas = list(p.chat(model, [{"role": "user", "content": "say ok"}], stream=False))
    assert deltas[-1].done is True
    # tokens were actually generated (robust to thinking models that emit no
    # visible content).
    assert deltas[-1].raw.get("eval_count", 0) > 0


@pytest.mark.live
def test_ollama_stream_chat(ollama_available):
    if not ollama_available:
        pytest.skip("Ollama not running")
    p = default_provider()
    model = _smallest_installed_model(p)
    if not model:
        pytest.skip("no models installed")
    done_delta = None
    for d in p.chat(model, [{"role": "user", "content": "say hi"}], stream=True):
        if d.done:
            done_delta = d
    assert done_delta is not None
    assert done_delta.raw.get("eval_count", 0) > 0
