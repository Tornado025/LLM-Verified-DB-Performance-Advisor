"""Ollama non-streaming JSON proposal provider."""

from app.llm._http import HTTPProvider
from app.llm.base import SchemaContext, SuggestResult
from app.llm.prompts import SYSTEM, render
from app.shapes import Shape


class OllamaProvider(HTTPProvider):
    """Local provider with the same parsing and bounded retry policy."""

    name = "ollama"

    def suggest(
        self, shape: Shape, ctx: SchemaContext, sample_idx: int, rendered_sql: str
    ) -> SuggestResult:
        """Request untrusted tuning proposals without executing SQL."""
        return self._suggest(
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": render(shape, ctx, sample_idx, rendered_sql)},
            ]
        )
