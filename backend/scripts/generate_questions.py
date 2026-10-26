"""Generate a bounded-cost, safety-screened text-to-SQL workload via OpenRouter.

Run explicitly; no API calls occur on import or during ordinary unit tests.
The questions are model-generated workload material, not equivalence ground truth.
"""

import argparse
import json
import logging
import os
import sys
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.catalog import get_catalog
from app.config import get_settings
from app.errors import LLMError
from app.llm.base import extract_json
from app.llm.openrouter import OpenRouterProvider
from app.models import RawCandidate
from app.pg import connect
from app.safety import check

_LOG = logging.getLogger(__name__)
SEEDS = {
    "stock": [
        "Which products are out of stock at each store?",
        "Which stock rows changed in the last fifteen minutes?",
        "Which stores have fewer than ten units of a product?",
        "How much stock is reserved in each store?",
        "Which active products have no store stock row?",
        "Which categories have the lowest available stock?",
        "Which stores stock the most distinct products?",
        "Which products have not had a stock update for a day?",
        "During a flash sale, which hot products are sold out?",
        "During a flash sale, which stores have stock below reserved quantities?",
    ],
    "orders": [
        "What are the latest twenty orders for a customer?",
        "How many orders were delivered yesterday per store?",
        "Which orders are currently being packed?",
        "How many orders were cancelled by payment mode?",
        "Which stores have the most open orders?",
        "Which orders contain more than four order lines?",
        "What is the average delivered order value per city?",
        "Which orders have not been assigned a rider?",
        "During a flash sale, how many orders arrive each minute?",
        "During a flash sale, how many placed orders are waiting at each store?",
    ],
    "riders": [
        "How many idle riders does each store have?",
        "Which riders have no assigned orders?",
        "How many delivered orders has each rider handled?",
        "Which riders have the most orders out for delivery?",
        "What is the average delivery duration by rider?",
        "Which stores have no idle riders?",
        "Which riders work at stores in Bengaluru?",
        "How many riders are assigned to each city?",
        "During a flash sale, which riders have multiple active deliveries?",
        "During a flash sale, which stores have more active orders than idle riders?",
    ],
    "payouts": [
        "What are delivered-order payouts by store over seven days?",
        "What is delivered revenue by payment mode?",
        "How much delivered revenue did each rider handle?",
        "What is the daily delivered revenue by store?",
        "Which stores have delivered revenue above the store average?",
        "What is the total cancelled order value per city?",
        "What share of delivered revenue uses cash on delivery?",
        "Which payment modes have the highest average order value?",
        "During a flash sale, what is delivered revenue by hour?",
        "During a flash sale, how much order value is still awaiting delivery?",
    ],
    "sales": [
        "What are hourly order counts and revenue per store?",
        "Which twenty products sell the most units at one store?",
        "Which categories have the highest line-item revenue?",
        "Which brands have the most units sold?",
        "Which active products have never appeared in an order?",
        "Which cities have the highest order revenue?",
        "How many distinct products sold at each store?",
        "What is the average quantity per order line by category?",
        "During a flash sale, which products have the most units sold in fifteen minutes?",
        "During a flash sale, which stores have the most revenue in the last hour?",
    ],
    "customers": [
        "Which customers in one city have an open order?",
        "Which customers have ordered more than ten times?",
        "Which customers have no orders?",
        "Who are the highest-spending customers by city?",
        "Which customers have placed orders at multiple stores?",
        "Which customers have cancelled an order?",
        "When did each customer last place an order?",
        "Which customers use more than one payment mode?",
        "During a flash sale, which customers placed multiple orders in one hour?",
        "During a flash sale, which cities have the most distinct ordering customers?",
    ],
}


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, suffix=".tmp", delete=False) as handle:
        temporary = handle.name
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")
    os.replace(temporary, path)


def main() -> None:
    """Generate up to 250 validated questions with a hard HTTP-request budget."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=1234)
    parser.add_argument("--count", type=int, default=250)
    parser.add_argument("--max-requests", type=int, default=15)
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).parents[1] / "data/questions.json"
    )
    args = parser.parse_args()
    if not 200 <= args.count <= 250 or not 1 <= args.max_requests <= 15:
        parser.error("Count must be 200..250; at most 15 HTTP requests for this setup")
    logging.basicConfig(level=logging.INFO)
    settings = replace(get_settings(), llm_provider="openrouter")
    provider = OpenRouterProvider(settings)
    provider.request_budget = args.max_requests
    rng = np.random.default_rng(args.seed)
    questions = []
    usages = []
    rejected = []
    seen = set()
    seeds = [
        {"category": category, "question": q, "sale_specific": i >= 8}
        for category, items in SEEDS.items()
        for i, q in enumerate(items)
    ]
    with connect(settings.live_db) as conn:
        catalog = get_catalog(conn)
        as_of = conn.execute("SELECT value FROM meta WHERE key='as_of'").fetchone()[0]
        schema = "\n".join(
            t["name"] + ": " + ", ".join(c["name"] + " " + c["type"] for c in t["columns"])
            for t in catalog.tables
        )
        round_number = 0
        while len(questions) < args.count and provider.requests_used < args.max_requests:
            selected = seeds[(round_number * 10) % 60 : ((round_number * 10) % 60) + 10]
            count = min(25, args.count - len(questions))
            prompt = (
                f"Generate {count} NEW, different quick-commerce operations questions "
                "and matching PostgreSQL 16 SELECT SQL. Paraphrase or extend these seed questions. "
                'Return {"questions":[{"question":"...","sql":"...","sale_specific":false}]} only. '
                "Mix at least three report structures: joins, grouping, exists/subqueries, ranges. "
                "Use only the schema below. No comments, DML, system schemas, or pg_ functions. "
                "Allowed functions: count,sum,avg,min,max,date_trunc,date,extract, "
                "coalesce,nullif,lower,upper. "
                "No percentile, windows or casts to custom types. Name output columns; "
                "alias computed expressions. No generate_series. "
                f"Use frozen timestamp {as_of} instead of now(). Ensure 20% sale-specific. "
                "Use varied stores, cities and time windows. Never invent columns. "
                f"Round {round_number}. Seed ideas: "
                + json.dumps(selected)
                + "\nSchema:\n"
                + schema
                + "\nAlready used (avoid repeating): "
                + json.dumps([q["question"] for q in questions[-30:]])
            )
            try:
                content, usage = provider.complete(
                    [
                        {"role": "system", "content": "Return JSON only. SQL is untrusted data."},
                        {"role": "user", "content": prompt},
                    ]
                )
                usages.append(usage)
                document = extract_json(content)
                entries = document.get("questions") if isinstance(document, dict) else document
                if not isinstance(entries, list):
                    raise LLMError("unparseable question response")
                for entry in entries:
                    if (
                        not isinstance(entry, dict)
                        or not isinstance(entry.get("question"), str)
                        or not isinstance(entry.get("sql"), str)
                        or not isinstance(entry.get("sale_specific"), bool)
                    ):
                        rejected.append("invalid entry shape")
                        continue
                    key = (entry["question"].strip().lower(), entry["sql"].strip())
                    if not key[0] or key in seen:
                        rejected.append("duplicate/empty")
                        continue
                    raw = RawCandidate("rewrite", entry["sql"].strip(), "generated question")
                    # Generated queries have no original rewrite target. Reuse column
                    # resolution with themselves as baseline; equivalence is not claimed.
                    valid, reason = check(raw, replace(catalog, original_sql=raw.sql))
                    if not valid:
                        rejected.append(reason)
                        continue
                    try:
                        with conn.transaction():
                            conn.execute("SET TRANSACTION READ ONLY")
                            conn.execute("EXPLAIN (FORMAT JSON) " + raw.sql)
                    except Exception as exc:
                        rejected.append(type(exc).__name__)
                        continue
                    seen.add(key)
                    questions.append(
                        {
                            "id": f"q{len(questions) + 1:04d}",
                            "question": entry["question"].strip(),
                            "sql": raw.sql,
                            "sale_specific": entry["sale_specific"],
                            "day": int(rng.integers(1, 15)),
                        }
                    )
                    if len(questions) >= args.count:
                        break
            except LLMError as exc:
                rejected.append(str(exc))
            round_number += 1
            _LOG.info(
                "Question generation: %d valid, %d requests", len(questions), provider.requests_used
            )
            _write(args.out, questions)
            _write(
                args.out.with_name("questions_meta.json"),
                {
                    "created_at": datetime.now(UTC).isoformat(),
                    "seed": args.seed,
                    "source": "OpenRouter model-generated; safety screened and EXPLAIN validated",
                    "model": settings.openrouter_model,
                    "temperature": settings.openrouter_temperature,
                    "seed_questions": seeds,
                    "requests": provider.requests_used,
                    "valid": len(questions),
                    "usage": usages,
                    "cost": sum(u["cost"] for u in usages)
                    if all(u["cost"] is not None for u in usages)
                    else None,
                    "rejections": rejected,
                },
            )
    if len(questions) < 200:
        raise SystemExit(f"Only {len(questions)} validated questions; acceptance requires >=200")
    print(
        json.dumps(
            {
                "valid": len(questions),
                "requests": provider.requests_used,
                "cost": sum(u["cost"] for u in usages)
                if all(u["cost"] is not None for u in usages)
                else None,
            }
        )
    )


if __name__ == "__main__":
    main()
