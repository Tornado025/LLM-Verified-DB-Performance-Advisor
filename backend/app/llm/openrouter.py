"""OpenRouter chat-completions proposal provider."""

from app.llm._http import HTTPProvider
from app.llm.base import SchemaContext, SuggestResult
from app.llm.prompts import SYSTEM, render
from app.shapes import Shape


class OpenRouterProvider(HTTPProvider):
    """OpenAI-compatible OpenRouter client with bounded retries."""

    name = "openrouter"

    def suggest(
        self, shape: Shape, ctx: SchemaContext, sample_idx: int, rendered_sql: str
    ) -> SuggestResult:
        """Return untrusted parsed proposals and measured API usage."""
        return self._suggest(
            [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": render(shape, ctx, sample_idx, rendered_sql)},
            ]
        )
