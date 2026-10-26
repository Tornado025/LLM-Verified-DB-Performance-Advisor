"""Provider contracts and bounded extraction of untrusted candidate JSON."""

import json
from dataclasses import dataclass
from typing import Protocol

from app.config import Settings
from app.errors import LLMError
from app.models import Model, RawCandidate
from app.shapes import Shape


@dataclass
class SchemaContext(Model):
    """One sample's trusted schema and baseline plan context."""

    tables: list[dict]
    indexes: list[dict]
    row_counts: dict
    plan_text: str
    sample_idx: int


@dataclass
class SuggestResult(Model):
    """Parsed untrusted proposals and provider-reported usage."""

    candidates: list[RawCandidate]
    usage: dict


class LLMProvider(Protocol):
    """A provider proposes text; it never touches a database."""

    name: str

    def suggest(
        self, shape: Shape, ctx: SchemaContext, sample_idx: int, rendered_sql: str
    ) -> SuggestResult: ...


def extract_json(text: str) -> dict | list:
    """Find a JSON document in prose/fences without interpreting any text as code."""
    if not isinstance(text, str) or len(text) > 1_000_000:
        raise LLMError("unparseable")
    decoder = json.JSONDecoder()
    for i, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            result, _ = decoder.raw_decode(text[i:])
        except (ValueError, RecursionError):
            continue
        if isinstance(result, (dict, list)):
            return result
    raise LLMError("unparseable")


def parse_candidates(text: str) -> list[RawCandidate]:
    """Extract at most six valid-shaped entries; their SQL remains untrusted."""
    document = extract_json(text)
    entries = document.get("candidates") if isinstance(document, dict) else document
    if not isinstance(entries, list):
        raise LLMError("unparseable")
    result = []
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or entry.get("kind") not in {"index", "stats", "rewrite"}
            or not isinstance(entry.get("sql"), str)
            or not entry["sql"].strip()
            or not isinstance(entry.get("rationale", ""), str)
        ):
            continue
        result.append(RawCandidate(entry["kind"], entry["sql"].strip(), entry.get("rationale", "")))
        if len(result) == 6:
            break
    if entries and not result:
        raise LLMError("unparseable")
    return result


def get_provider(settings: Settings) -> LLMProvider:
    """Build the configured provider lazily; mock is the default."""
    if settings.llm_provider == "mock":
        from app.llm.mock import MockProvider

        return MockProvider()
    if settings.llm_provider == "openrouter":
        from app.llm.openrouter import OpenRouterProvider

        return OpenRouterProvider(settings)
    if settings.llm_provider == "ollama":
        from app.llm.ollama import OllamaProvider

        return OllamaProvider(settings)
    raise LLMError("Unknown provider")
