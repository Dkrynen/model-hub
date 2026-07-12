from __future__ import annotations

from backend.provider.ollama import OllamaProvider


def _xml(content_json: str) -> str:
    return chr(60) + "tool_call" + chr(62) + content_json + chr(60) + "/tool_call" + chr(62)


def test_structured_passthrough():
    p = OllamaProvider()
    msg = {"tool_calls": [{"function": {"name": "x", "arguments": "{}"}}], "content": ""}
    assert p._extract_tool_calls(msg, tools_requested=True) == msg["tool_calls"]


def test_json_array_strategy():
    p = OllamaProvider()
    msg = {"content": '[{"name": "foo", "arguments": "{}"}]'}
    out = p._extract_tool_calls(msg, tools_requested=True)
    assert len(out) == 1
    assert out[0]["name"] == "foo"


def test_json_single_object_strategy():
    p = OllamaProvider()
    msg = {"content": '{"name": "bar", "arguments": "{}"}'}
    out = p._extract_tool_calls(msg, tools_requested=True)
    assert len(out) == 1
    assert out[0]["name"] == "bar"


def test_tool_calls_prefix_strategy():
    p = OllamaProvider()
    msg = {"content": '[TOOL_CALLS] [{"name": "baz", "arguments": "{}"}]'}
    out = p._extract_tool_calls(msg, tools_requested=True)
    assert len(out) == 1
    assert out[0]["name"] == "baz"


def test_xml_strategy():
    p = OllamaProvider()
    msg = {"content": _xml('{"name": "qux", "arguments": "{}"}')}
    out = p._extract_tool_calls(msg, tools_requested=True)
    assert len(out) == 1
    assert out[0]["name"] == "qux"


def test_false_positive_guard_no_tools():
    p = OllamaProvider()
    msg = {"content": '[{"name": "foo", "arguments": "{}"}]'}
    assert p._extract_tool_calls(msg, tools_requested=False) == []


def test_structured_tool_calls_are_ignored_when_no_tools_were_requested():
    p = OllamaProvider()
    msg = {
        "tool_calls": [{"function": {"name": "write_file", "arguments": "{}"}}],
        "content": "plain answer",
    }
    assert p._extract_tool_calls(msg, tools_requested=False) == []


def test_plain_text_returns_empty():
    p = OllamaProvider()
    assert p._extract_tool_calls({"content": "just a normal reply"}, tools_requested=True) == []


def test_empty_content():
    p = OllamaProvider()
    assert p._extract_tool_calls({"content": ""}, tools_requested=True) == []
    assert p._extract_tool_calls({}, tools_requested=True) == []
