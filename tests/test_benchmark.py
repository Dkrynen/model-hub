from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli import _benchmark_log, _benchmark_history


def test_benchmark_log_and_history(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    entry = {
        "model": "test:1b",
        "prompt": "hello",
        "eval_count": 50,
        "eval_duration_ns": 5_000_000_000,
        "tokens_per_second": 10.0,
    }
    log_path = _benchmark_log(entry)
    assert log_path is not None
    assert log_path.exists()

    history = _benchmark_history()
    assert len(history) == 1
    assert history[0]["model"] == "test:1b"
    assert history[0]["tokens_per_second"] == 10.0
    assert "timestamp" in history[0]


def test_benchmark_log_multiple_entries(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    for i in range(3):
        _benchmark_log({"model": f"test:{i}b", "eval_count": 10, "tokens_per_second": float(i + 1)})

    history = _benchmark_history()
    assert len(history) == 3
    tps = [e["tokens_per_second"] for e in history]
    assert sorted(tps) == [1.0, 2.0, 3.0]


def test_benchmark_history_empty_when_no_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    assert _benchmark_history() == []


def test_benchmark_history_skips_corrupt_lines(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    log_dir = home / ".model-hub" / "benchmarks"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "results.jsonl"
    log_file.write_text(
        '{"model": "good", "tokens_per_second": 10.0}\n'
        "not-json\n"
        '{"model": "also-good", "tokens_per_second": 20.0}\n'
    )

    history = _benchmark_history()
    assert len(history) == 2
    assert history[0]["model"] == "good"
    assert history[1]["model"] == "also-good"


def test_benchmark_metrics_ttft_is_load_plus_prompt_not_eval_duration():
    from cli import _benchmark_metrics
    # Ollama /api/generate durations are in nanoseconds.
    result = {
        "eval_count": 100,
        "eval_duration": 5_000_000_000,          # 5s generating 100 tokens
        "load_duration": 3_000_000_000,          # 3s loading the model
        "prompt_eval_duration": 1_000_000_000,   # 1s prefilling the prompt
        "total_duration": 9_000_000_000,
        "response": "ok",
    }
    entry = _benchmark_metrics(result, "m:1b", "hello", 100, 0.0)
    # TTFT = time before the first generated token = load + prompt prefill =
    # 4000 ms. It must NOT be the generation (eval) duration of 5000 ms.
    assert entry["time_to_first_token_ms"] == 4000.0
    # tok/s = 100 tokens / 5.0 s = 20.
    assert entry["tokens_per_second"] == 20.0


def test_benchmark_entry_can_carry_fingerprint():
    from cli import _benchmark_metrics
    result = {"eval_count": 100, "eval_duration": 5_000_000_000,
              "load_duration": 1_000_000_000, "prompt_eval_duration": 1_000_000_000,
              "total_duration": 7_000_000_000, "response": "ok"}
    entry = _benchmark_metrics(result, "m:1b", "hi", 100, 0.0, fingerprint="abc123", stack={"ollama_version": "0.31.1"})
    assert entry["fingerprint"] == "abc123"
    assert entry["stack"]["ollama_version"] == "0.31.1"


def test_cli_help_includes_benchmark():
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-m", "cli", "benchmark", "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0
    assert "benchmark" in r.stdout
    assert "--prompt PROMPT" in r.stdout
    assert "--num-predict" in r.stdout
    assert "--temperature" in r.stdout
    assert "--list" in r.stdout
    assert "--export FILE" in r.stdout
