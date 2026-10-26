"""Shared bounded HTTP/format retries; HTTP errors never expose credential bodies."""

from collections.abc import Callable
from time import sleep

import httpx

from app.config import Settings
from app.errors import LLMError
from app.llm.base import SuggestResult, parse_candidates


class HTTPProvider:
    """Own an injected or short-lived HTTP client; never log full payloads/keys."""

    name = "http"

    def __init__(
        self,
        settings: Settings,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self.settings, self.client, self.sleeper = settings, client, sleeper
        self.json_mode = settings.openrouter_json_mode
        self.request_budget: int | None = None
        self.requests_used = 0

    def _request(self, messages: list[dict]) -> dict:
        import json

        if len(json.dumps(messages).encode()) > 50000:
            raise LLMError("Prompt exceeds bounded request size")
        s = self.settings
        for attempt in range(3):
            while True:
                if self.name == "openrouter":
                    url = s.openrouter_base_url.rstrip("/") + "/chat/completions"
                    body = {
                        "model": s.openrouter_model,
                        "temperature": s.openrouter_temperature,
                        "messages": messages,
                        "max_tokens": 8192,
                    }
                    if self.json_mode:
                        body["response_format"] = {"type": "json_object"}
                    headers = {
                        "Authorization": "Bearer " + s.openrouter_api_key,
                        "HTTP-Referer": "http://localhost:8000",
                        "X-Title": "DB Performance Advisor",
                    }
                else:
                    url = s.ollama_url.rstrip("/") + "/api/chat"
                    body = {
                        "model": s.ollama_model,
                        "messages": messages,
                        "stream": False,
                        "format": "json",
                        "options": {"temperature": s.openrouter_temperature},
                    }
                    headers = {}
                if self.request_budget is not None and self.requests_used >= self.request_budget:
                    raise LLMError("HTTP request budget exhausted")
                self.requests_used += 1
                try:
                    if self.client:
                        response = self.client.post(
                            url, json=body, headers=headers, timeout=s.llm_timeout_s
                        )
                    else:
                        with httpx.Client(timeout=s.llm_timeout_s) as client:
                            response = client.post(url, json=body, headers=headers)
                except httpx.TransportError:
                    if attempt == 2:
                        raise LLMError("Provider transport failed") from None
                    self.sleeper(2**attempt)
                    break
                if (
                    self.name == "openrouter"
                    and self.json_mode
                    and response.status_code == 400
                    and "response_format" in response.text.lower()
                ):
                    self.json_mode = False
                    continue
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt == 2:
                        raise LLMError(f"Provider HTTP {response.status_code}")
                    self.sleeper(2**attempt)
                    break
                if response.is_error:
                    raise LLMError(f"Provider HTTP {response.status_code}")
                try:
                    payload = response.json()
                    if not isinstance(payload, dict) or "error" in payload:
                        raise ValueError
                    return payload
                except (ValueError, TypeError):
                    raise LLMError("Invalid provider response") from None
        raise LLMError("Provider retries exhausted")

    def complete(self, messages: list[dict]) -> tuple[str, dict]:
        """Return raw content and reported usage for proposals or question generation."""
        data = self._request(messages)
        try:
            if self.name == "openrouter":
                content = data["choices"][0]["message"]["content"]
                raw = data.get("usage") or {}
                usage = {
                    "prompt_tokens": raw.get("prompt_tokens", 0),
                    "completion_tokens": raw.get("completion_tokens", 0),
                    "cost": raw.get("cost", data.get("cost")),
                    "model": data.get("model", self.settings.openrouter_model),
                    "temperature": self.settings.openrouter_temperature,
                }
                if data.get("provider"):
                    usage["provider"] = data["provider"]
            else:
                content = data["message"]["content"]
                usage = {
                    "prompt_tokens": data.get("prompt_eval_count", 0),
                    "completion_tokens": data.get("eval_count", 0),
                    "cost": None,
                    "model": data.get("model", self.settings.ollama_model),
                    "temperature": self.settings.openrouter_temperature,
                }
            if not isinstance(content, str):
                raise ValueError
            return content, usage
        except (KeyError, TypeError, IndexError, ValueError):
            raise LLMError("Invalid provider response") from None

    def _suggest(self, messages: list[dict]) -> SuggestResult:
        usages = []
        for attempt in range(2):
            content, usage = self.complete(messages)
            usages.append(usage)
            try:
                candidates = parse_candidates(content)
                combined = dict(usage)
                combined.update(
                    prompt_tokens=sum(u["prompt_tokens"] for u in usages),
                    completion_tokens=sum(u["completion_tokens"] for u in usages),
                    cost=sum(u["cost"] for u in usages)
                    if all(u["cost"] is not None for u in usages)
                    else None,
                )
                return SuggestResult(candidates, combined)
            except LLMError:
                if attempt:
                    raise
                messages = messages + [{"role": "user", "content": "Return only JSON"}]
        raise LLMError("unparseable")
