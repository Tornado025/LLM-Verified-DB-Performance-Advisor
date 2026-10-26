"""Provider parsing, wire format, retries and usage with no hidden network calls."""

import json
from dataclasses import replace

import httpx
import pytest

from app.config import Settings
from app.errors import LLMError
from app.llm.base import SchemaContext, parse_candidates
from app.llm.ollama import OllamaProvider
from app.llm.openrouter import OpenRouterProvider
from app.llm.prompts import render
from app.shapes import REPORT_SHAPES

CTX = SchemaContext([], [], {}, "Plan text", 0)
SHAPE = REPORT_SHAPES["recent_orders"]
CONTENT = json.dumps(
    {
        "candidates": [
            {"kind": "index", "sql": "CREATE INDEX ON orders(customer_id)", "rationale": "lookup"}
        ]
    }
)


def test_parse_candidates():
    for text in [
        CONTENT,
        "```json\n" + CONTENT + "\n```",
        "Prose " + CONTENT + " trailing",
        json.dumps(json.loads(CONTENT)["candidates"]),
    ]:
        assert len(parse_candidates(text)) == 1
    assert parse_candidates('{"candidates":[]}') == []
    with pytest.raises(LLMError):
        parse_candidates("invalid")
    entries = [{"kind": "index", "sql": "CREATE INDEX ON orders(customer_id)"}] * 10
    assert len(parse_candidates(json.dumps(entries))) == 6


@pytest.mark.parametrize("statuses", [[200], [429, 200], [500, 500, 500], [400, 200]])
def test_openrouter_http_retries(statuses):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        assert request.headers["X-Title"] == "DB Performance Advisor"
        status = statuses[min(len(seen) - 1, len(statuses) - 1)]
        if status == 400:
            return httpx.Response(400, json={"error": "response_format unsupported"})
        return httpx.Response(
            status,
            json={
                "choices": [{"message": {"content": CONTENT}}],
                "model": "served-model",
                "provider": "test-provider",
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "cost": 0.001},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenRouterProvider(Settings(), client, sleeper=lambda n: None)
        if statuses[-1] == 500:
            with pytest.raises(LLMError):
                provider.suggest(SHAPE, CTX, 0, "SELECT 1")
        else:
            result = provider.suggest(SHAPE, CTX, 0, "SELECT 1")
            assert len(result.candidates) == 1 and result.usage["provider"] == "test-provider"
    assert len(seen) == len(statuses)
    if statuses[0] == 400:
        assert "response_format" in seen[0] and "response_format" not in seen[1]


def test_parse_retry_and_ollama():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200,
            json={
                "message": {"content": "not json" if len(calls) == 1 else CONTENT},
                "prompt_eval_count": 2,
                "eval_count": 3,
                "model": "local-model",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaProvider(replace(Settings(), ollama_model="local-model"), client)
        result = provider.suggest(SHAPE, CTX, 0, "SELECT 1")
    assert result.usage["prompt_tokens"] == 4 and len(calls) == 2
    assert calls[0]["stream"] is False and calls[0]["format"] == "json"
    assert calls[1]["messages"][-1]["content"] == "Return only JSON"


def test_prompt_determinism():
    assert render(SHAPE, CTX, 1, "SELECT 1") == render(SHAPE, CTX, 1, "SELECT 1")
    assert render(SHAPE, CTX, 0, "SELECT 1") != render(SHAPE, CTX, 1, "SELECT 1")


@pytest.mark.llm
def test_real_openrouter_parses_and_screens():
    from app.catalog import get_catalog
    from app.config import get_settings
    from app.pg import connect
    from app.safety import check
    from app.sandbox import single_clone

    settings = replace(get_settings(), llm_provider="openrouter")
    with single_clone(settings) as name, connect(name) as conn:
        query = "SELECT order_id FROM orders WHERE customer_id=100"
        catalog = replace(get_catalog(conn), original_sql=query)
        context = SchemaContext(catalog.tables, catalog.indexes, catalog.row_counts, "baseline", 0)
        provider = OpenRouterProvider(settings)
        provider.request_budget = 2
        result = provider.suggest(SHAPE, context, 0, query)
        assert all(isinstance(check(c, catalog)[0], bool) for c in result.candidates)


def test_transport_retry_and_request_cap():
    calls = []

    def offline(request):
        calls.append(request)
        raise httpx.ConnectError("offline", request=request)

    with httpx.Client(transport=httpx.MockTransport(offline)) as client:
        provider = OpenRouterProvider(Settings(), client, sleeper=lambda n: None)
        provider.request_budget = 3
        with pytest.raises(LLMError, match="transport"):
            provider.suggest(SHAPE, CTX, 0, "SELECT 1")
        assert len(calls) == 3
        with pytest.raises(LLMError, match="budget"):
            provider.suggest(SHAPE, CTX, 0, "SELECT 1")
        assert len(calls) == 3


def test_seeds_have_required_coverage():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "question_generator", Path(__file__).parents[1] / "scripts/generate_questions.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert sum(len(items) for items in module.SEEDS.values()) == 60
    assert sum("flash sale" in q.lower() for items in module.SEEDS.values() for q in items) == 12
