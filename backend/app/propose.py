"""Safety-first sampling until candidate plan novelty saturates."""

from collections.abc import Callable

import sqlglot

from app.errors import LLMError
from app.llm.base import LLMProvider, SchemaContext
from app.models import Candidate, ProposeResult, RawCandidate
from app.plans import _index_definition, _normalized_sql
from app.sandbox import InvalidDDL
from app.shapes import Shape
from app.store import Store


def _index_key(statement: str) -> str:
    tree = sqlglot.parse_one(_index_definition(statement), read="postgres")
    index = tree.this
    index.set("this", None)
    params = index.args.get("params")
    if params:
        params.set("include", None)
        params.set("using", None)
    tree.set("concurrently", False)
    tree.set("exists", False)
    return _normalized_sql(tree.sql(dialect="postgres"))


def propose(
    shape: Shape,
    rendered_sql: str,
    provider: LLMProvider,
    ctx_builder: Callable[[int], SchemaContext],
    signature_fn: Callable[[Candidate], str],
    safety_fn: Callable[[RawCandidate], tuple[bool, str]],
    *,
    k_max: int,
    patience: int,
    job_id: str,
    store: Store | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ProposeResult:
    """Keep new signatures only after safety; preserve all rejection evidence."""
    if k_max < 1 or patience < 1:
        raise ValueError("Sampling budget and patience must be positive")
    kept, rejected, curve = [], [], []
    seen: dict[str, str] = {}
    texts: dict[str, str] = {}
    index_keys: dict[str, str] = {}
    usage: dict = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0, "calls": []}
    stale, samples, number = 0, 0, 0
    saturated = False
    for i in range(k_max):
        if should_stop and should_stop():
            break
        context = ctx_builder(i)
        result = None
        for retry in range(2):
            try:
                result = provider.suggest(shape, context, i, rendered_sql)
                break
            except LLMError as exc:
                if retry:
                    rejected.append(
                        {"status": "provider_error", "sample_idx": i, "reason": str(exc)}
                    )
        samples += 1
        if result is None:
            # Provider failure is not evidence that plan space is saturated.
            break
        usage["calls"].append(result.usage)
        for key in ("prompt_tokens", "completion_tokens"):
            usage[key] += result.usage.get(key, 0)
        cost = result.usage.get("cost")
        usage["cost"] = None if cost is None or usage["cost"] is None else usage["cost"] + cost
        usage["model"] = result.usage.get("model", provider.name)
        new, ok_count = 0, 0
        for raw in result.candidates:
            number += 1
            candidate = Candidate(
                f"{job_id}:{shape.id}:{number:02d}",
                job_id,
                shape.id,
                raw.kind,
                raw.sql,
                raw.rationale,
                i,
            )
            ok, reason = safety_fn(raw)
            if not ok:
                candidate.status, candidate.reason = "rejected_safety", reason
            else:
                ok_count += 1
                candidate.sql = raw.sql  # safety may remove CONCURRENTLY
                try:
                    text = _normalized_sql(raw.sql)
                    index_key = _index_key(raw.sql) if raw.kind == "index" else None
                    duplicate = texts.get(text) or (
                        index_keys.get(index_key) if index_key else None
                    )
                    if duplicate:
                        candidate.status, candidate.reason = (
                            "skipped_duplicate",
                            "duplicate_of:" + duplicate,
                        )
                    else:
                        texts[text] = candidate.id
                        if index_key:
                            index_keys[index_key] = candidate.id
                        candidate.signature = signature_fn(candidate)
                        signature_key = candidate.signature
                        if signature_key in seen:
                            candidate.status, candidate.reason = (
                                "skipped_duplicate",
                                "duplicate_of:" + seen[signature_key],
                            )
                        else:
                            seen[signature_key] = candidate.id
                            kept.append(candidate)
                            new += 1
                except (InvalidDDL, sqlglot.errors.SqlglotError) as exc:
                    candidate.status, candidate.reason = "rejected_invalid_ddl", str(exc)
            if candidate.status != "proposed":
                rejected.append(candidate.to_dict())
            if store:
                store.save_candidate(candidate)
        curve.append(len(seen))
        if store:
            store.emit(
                job_id,
                "propose",
                "info",
                "Proposal sample completed",
                {"sample_idx": i, "raw": len(result.candidates), "ok": ok_count, "new": new},
            )
        stale = 0 if new else stale + 1
        if stale >= patience:
            saturated = True
            break
    if store:
        store.emit(
            job_id,
            "propose",
            "result",
            "Proposal sampling finished",
            {"samples": samples, "saturated": saturated, "distinct_plan_curve": curve},
        )
    return ProposeResult(kept, samples, saturated, usage, rejected, curve)
