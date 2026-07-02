# tests/test_calibration.py
from __future__ import annotations
from backend.cookbook.calibration import parse_model_tag


def test_parse_bare_tag_defaults_to_q4km():
    assert parse_model_tag("qwen3:30b-a3b") == ("qwen3:30b-a3b", "Q4_K_M")

def test_parse_quant_suffix():
    assert parse_model_tag("qwen3:30b-a3b-q8_0") == ("qwen3:30b-a3b", "Q8")
    assert parse_model_tag("qwen3:30b-a3b-q4_K_M") == ("qwen3:30b-a3b", "Q4_K_M")
    assert parse_model_tag("qwen3:30b-a3b-fp16") == ("qwen3:30b-a3b", "F16")

def test_parse_hf_sub4bit():
    assert parse_model_tag("hf.co/tiiuae/Falcon3-3B-Instruct-1.58bit") == (
        "hf.co/tiiuae/Falcon3-3B-Instruct-1.58bit", "1.58bit")

def test_parse_unknown_returns_none():
    assert parse_model_tag("totally-made-up:99b") is None
