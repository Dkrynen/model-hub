from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Iterator

from .base import ChatDelta, LLMProvider, ModelInfo, ProviderError

OLLAMA_DEFAULT_HOST = "http://localhost:11434"


class OllamaProvider(LLMProvider):
    type = "ollama"
    display_name = "Ollama"

    def __init__(self, base_url: str | None = None, **_: Any):
        import os

        self.base_url = (base_url or os.environ.get("OLLAMA_HOST") or OLLAMA_DEFAULT_HOST).rstrip("/")

    @property
    def name(self) -> str:
        return "ollama"

    def _request(self, method: str, path: str, body: Any = None, timeout: int = 30, stream: bool = False):
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        return urllib.request.urlopen(req, timeout=timeout)

    def _get(self, path: str) -> dict:
        try:
            with self._request("GET", path) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise ProviderError(f"HTTP {e.code}: {e.read().decode()[:200]}") from None
        except urllib.error.URLError as e:
            raise ProviderError(f"Cannot connect to Ollama at {self.base_url}: {e.reason}") from None

    def list_models(self) -> list[ModelInfo]:
        result = self._get("/api/tags")
        models = []
        for m in result.get("models", []):
            details = m.get("details", {})
            context_length = m.get("context_length", details.get("context_length", 0))
            if (
                isinstance(context_length, bool)
                or not isinstance(context_length, int)
                or context_length <= 0
            ):
                context_length = 0
            models.append(
                ModelInfo(
                    name=m.get("name", ""),
                    size=m.get("size", 0),
                    modified=m.get("modified", ""),
                    context_length=context_length,
                    quant=details.get("quantization_level", ""),
                    family=details.get("family", ""),
                    raw=m,
                )
            )
        return models

    def running(self) -> list[dict]:
        try:
            return self._get("/api/ps").get("models", [])
        except ProviderError:
            return []

    def _extract_tool_calls(self, message: dict, tools_requested: bool = False) -> list[dict]:
        if not tools_requested:
            return []
        tc = message.get("tool_calls") or []
        if tc:
            return tc
        content = message.get("content", "") or ""
        if not content:
            return []
        for strategy in (self._try_json_array, self._try_tool_calls_prefix, self._try_tool_call_xml):
            result = strategy(content)
            if result:
                return result
        return []

    @staticmethod
    def _try_json_array(content: str) -> list[dict]:
        s = content.strip()
        if not (s.startswith("[") or s.startswith("{")):
            return []
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "name" in data:
            return [data]
        return []

    @staticmethod
    def _try_tool_calls_prefix(content: str) -> list[dict]:
        import re

        m = re.search(r"\[TOOL_CALLS\]\s*(\[.*?\])", content, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    @staticmethod
    def _try_tool_call_xml(content: str) -> list[dict]:
        import re

        open_tag = chr(60) + "tool_call" + chr(62)
        close_tag = chr(60) + "/tool_call" + chr(62)
        calls = []
        for m in re.finditer(open_tag + r"\s*(\{.*?\})\s*" + close_tag, content, re.DOTALL):
            try:
                calls.append(json.loads(m.group(1)))
            except json.JSONDecodeError:
                continue
        return calls

    def chat(
        self,
        model: str,
        messages: list[dict],
        stream: bool = True,
        tools: list[dict] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatDelta]:
        msgs = list(messages)
        if system and (not msgs or msgs[0].get("role") != "system"):
            msgs.insert(0, {"role": "system", "content": system})
        body: dict = {"model": model, "messages": msgs, "stream": stream}
        if tools:
            body["tools"] = tools
            kwargs.pop("format", None)
        body.update(kwargs)

        try:
            resp = self._request("POST", "/api/chat", body, timeout=600, stream=stream)
        except urllib.error.HTTPError as e:
            raise ProviderError(f"HTTP {e.code}: {e.read().decode()[:200]}") from None
        except urllib.error.URLError as e:
            raise ProviderError(f"Cannot connect to Ollama: {e.reason}") from None

        if not stream:
            data = json.loads(resp.read().decode())
            msg = data.get("message", {})
            yield ChatDelta(
                content=msg.get("content", ""),
                tool_calls=self._extract_tool_calls(msg, tools is not None),
                done=True,
                raw=data,
            )
            return

        for raw in resp:
            line = raw.decode().strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message", {})
            delta = ChatDelta(
                content=msg.get("content", ""),
                tool_calls=self._extract_tool_calls(msg, tools is not None),
                done=obj.get("done", False),
                raw=obj,
            )
            yield delta

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        body = {"model": model, "input": texts}
        try:
            with self._request("POST", "/api/embed", body, timeout=120) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise ProviderError(f"HTTP {e.code}: {e.read().decode()[:200]}") from None
        except urllib.error.URLError as e:
            raise ProviderError(f"Cannot connect to Ollama: {e.reason}") from None
        return [e["embedding"] for e in data.get("embeddings", [])]

    def pull(self, model: str) -> Iterator[dict]:
        body = {"name": model, "stream": True}
        resp = self._request("POST", "/api/pull", body, timeout=0)
        for raw in resp:
            line = raw.decode().strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def delete(self, model: str) -> bool:
        try:
            with self._request("DELETE", "/api/delete", {"name": model}, timeout=30):
                return True
        except Exception:
            return False

    def create(self, name: str, from_model: str, parameters: dict) -> None:
        """Bake a derived Ollama model that inherits `from_model` with overridden
        parameters (e.g. {"num_ctx": 32768}). Uses the modern /api/create schema.
        Streaming is disabled and the response is consumed so the call blocks until
        the build finishes. Timeout must stay non-zero: urllib reads 0 as a
        non-blocking socket, which fails connect() outright instead of waiting."""
        body = {"model": name, "from": from_model, "parameters": parameters, "stream": False}
        with self._request("POST", "/api/create", body, timeout=600) as resp:
            resp.read()
