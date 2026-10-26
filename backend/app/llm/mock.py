"""Seeded fixture sampling with explicitly supplied job parameter bindings."""

import hashlib
import re

import numpy as np
from psycopg.sql import Literal

from app.errors import LLMError
from app.llm.base import SchemaContext, SuggestResult
from app.models import RawCandidate
from app.shapes import Shape


class MockProvider:
    """Deterministic provider; bad fixtures are hypotheses, never execution permission."""

    name = "mock"

    def __init__(
        self, job_seed: int = 1234, params: dict | None = None, noise: bool = False
    ) -> None:
        self.job_seed, self.params, self.noise = job_seed, params or {}, noise

    def suggest(
        self, shape: Shape, ctx: SchemaContext, sample_idx: int, rendered_sql: str
    ) -> SuggestResult:
        """Sample fixtures from stable shape/sample entropy, never Python hash()."""
        entropy = int.from_bytes(hashlib.sha256(shape.id.encode()).digest()[:8], "big")
        rng = np.random.default_rng(np.random.SeedSequence([self.job_seed, entropy, sample_idx]))
        fixtures = shape.mock_candidates
        if sample_idx == 0:
            selected = [c for c in fixtures if not c["bad"]][:3] or fixtures[:1]
        else:
            selected = [
                fixtures[int(i)]
                for i in rng.choice(
                    len(fixtures),
                    size=min(2 if sample_idx == 1 else 1, len(fixtures)),
                    replace=False,
                )
            ]
        candidates = []
        for fixture in selected:
            text = fixture["sql"]
            # Only remove the trusted fixture's known leading tag, never LLM comments.
            text = re.sub(r"^/\* dpa:shape=[a-z_]+ \*/\s*", "", text)

            def bind(match: re.Match) -> str:
                if match[1] not in self.params:
                    raise LLMError("Mock rewrite requires job sampler params")
                return Literal(self.params[match[1]]).as_string()

            text = re.sub(r"%\(([a-z_]+)\)s", bind, text)
            candidates.append(RawCandidate(fixture["kind"], text, fixture["rationale"]))
        if self.noise and rng.random() < 0.1:
            candidates.append(RawCandidate("index", "DROP INDEX x", "Planted safety rejection"))
        return SuggestResult(
            candidates, {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0, "model": "mock"}
        )
