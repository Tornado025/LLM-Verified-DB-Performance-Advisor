# Step 09 — LLM layer, safety allowlist, plan-saturation proposer

**Goal (end state):** `app/llm/` (base, openrouter, ollama, mock, prompts), `app/safety.py`, `app/propose.py`
work. With `LLM_PROVIDER=mock` the proposer returns the fixtures for every shape deterministically; with
`openrouter` it returns validated candidates; plan-saturation stopping is demonstrably triggered; every
LLM string passes the allowlist **before** any DB call.

**Prerequisites:** steps 03, 04, 08.

## 9.1 `app/llm/base.py`

```python
@dataclass
class SchemaContext: tables: list[dict]; indexes: list[dict]; row_counts: dict; plan_text: str; sample_idx: int
@dataclass
class SuggestResult: candidates: list[RawCandidate]; usage: dict          # usage: prompt_tokens, completion_tokens, cost (optional), model

class LLMProvider(Protocol):
    name: str
    def suggest(self, shape, ctx: SchemaContext, sample_idx: int, rendered_sql: str) -> SuggestResult: ...

def get_provider(settings) -> LLMProvider
def parse_candidates(text: str) -> list[RawCandidate]     # robust JSON extraction
```
`parse_candidates`:
1. Strip markdown code fences (```json … ```), leading/trailing prose; find the first `{` … last `}`.
2. `json.loads`; expect `{"candidates": [{"kind","sql","rationale"}]}`; accept a bare list too.
3. Drop entries with missing/invalid `kind` or empty `sql`; cap at 6 candidates per call.
4. On total failure raise `LLMError("unparseable")` (caller retries once).

## 9.2 `app/llm/prompts.py`

`SYSTEM` (fixed text):
* You are a PostgreSQL 16 performance advisor for a quick-commerce order database.
* Return **only** JSON: `{"candidates":[{"kind":"index|stats|rewrite","sql":"...","rationale":"..."}]}`.
* Allowed: B-tree indexes (plain/partial/expression/covering via `INCLUDE`) written as `CREATE INDEX ...` (no `UNIQUE`, no `CONCURRENTLY`);
  `ANALYZE <table>`; `CREATE STATISTICS ...`; rewrites of exactly three classes (index-friendly predicates; correlated subquery → join; removing redundant DISTINCT/ORDER BY/joins).
* A rewrite must return exactly the same rows and column names for **every** database state and every session `TimeZone`.
* Writes matter: orders/stock tables receive constant inserts and updates; every index slows them; indexing a frequently updated column removes HOT updates.
* Maximum 4 candidates; shortest effective change first.

`render(shape, ctx, sample_idx, rendered_sql)` builds the user message:
1. Question (plain English) and the **concrete SQL**.
2. Schema summary: tables with columns+types, existing indexes, row counts (compact table format).
3. `EXPLAIN` text (not JSON) of the baseline, truncated to 4000 chars.
4. Frequently updated columns hint (static list from the schema: `orders.status`, `orders.delivered_at`, `store_stock.qty`, `store_stock.updated_at`).
5. **Order shuffling:** for `sample_idx > 0` shuffle the order of tables/indexes sections with `Random(sample_idx)` (position-bias mitigation), and vary the instruction wording from a list of 3 equivalent phrasings.

## 9.3 `app/llm/openrouter.py`

* `POST {OPENROUTER_BASE_URL}/chat/completions`, headers `Authorization: Bearer <key>`, `HTTP-Referer`, `X-Title: DB Performance Advisor`.
* Body: `model`, `temperature` (= `OPENROUTER_TEMPERATURE`), `messages` (system + user), `response_format: {"type":"json_object"}` **only if** the env flag `OPENROUTER_JSON_MODE=1` (default on; auto-disable on HTTP 400 mentioning response_format and retry once without it).
* Timeouts `LLM_TIMEOUT_S`; retry with exponential backoff (1 s, 2 s, 4 s) on HTTP 429/5xx and `httpx.TransportError`; max 3 attempts; raise `LLMError` after.
* Parse `choices[0].message.content` via `parse_candidates`; on parse failure retry **once** with an appended reminder "Return only JSON".
* Usage: read `usage.prompt_tokens`, `usage.completion_tokens`; if the response includes a cost field use it, else leave `cost=None` (compute later from a price table if you maintain one). Always include `model` as returned.
* Never log the API key or full prompts at INFO (DEBUG only, with truncation).
* Pin and record: model slug, temperature, and (if present in the response) the serving provider name in `usage["provider"]`.

## 9.4 `app/llm/ollama.py`
`POST {OLLAMA_URL}/api/chat` with `{"model","messages","stream":false,"format":"json","options":{"temperature":...}}`; parse `message.content`; usage from `prompt_eval_count`/`eval_count`; same retry rules.

## 9.5 `app/llm/mock.py`

Deterministic provider using `shape.mock_candidates` (step 04):
* `suggest(shape, ctx, sample_idx, rendered_sql)` builds an RNG from `(job_seed, shape.id, sample_idx)`.
* Returns 1–3 fixtures sampled **without replacement**, with probabilities that make early samples cover most fixtures and later samples repeat (so plan saturation is reachable):
  sample 0: all "good" fixtures; sample 1: two random; sample ≥ 2: one random. Include a `noise` option to occasionally emit an invalid candidate (e.g. `DROP INDEX x`, multi-statement) with probability 0.1 to exercise the safety filter.
* `rewrite` fixtures that contain `%(as_of)s` etc. are rendered with the same params as the job's concrete SQL (the fixture builder receives `params`).
* `usage = {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0, "model": "mock"}`.

## 9.6 `app/safety.py` — the allowlist

```python
def check(raw: RawCandidate, catalog: Catalog) -> tuple[bool, str]
```
Reject (return `(False, reason)`) unless **all** of the following hold:

Common:
* `kind in {"index","stats","rewrite"}`; `sql` is ≤ 4000 chars; parses with sqlglot (`dialect="postgres"`) into **exactly one** statement; no `;` followed by more text; no SQL comments (`--` or `/* */`) anywhere in the candidate (reject rather than strip); **reject any function call whose name starts with `pg_`** or `current_setting`/`set_config`/`dblink`/`lo_`.

`index`:
* Statement type `CREATE INDEX`; **not** `UNIQUE`; method is btree or omitted; no `CONCURRENTLY` (system adds it at apply time) — if present, strip and continue.
* Table exists in catalog; every referenced column exists on that table; `INCLUDE` columns exist; predicate (partial index) uses only columns of that table, no subqueries, no volatile functions (`now()`, `random()`), no `CURRENT_*`.
* Max 4 key columns; at most 1 candidate per (table, column list, predicate) in a job.

`stats`:
* `ANALYZE <table>` (optionally with column list) or `CREATE STATISTICS [name] [(kinds)] ON col, col FROM table`; table/columns exist. No `VACUUM`, no `ANALYZE VERBOSE` options other than column lists.

`rewrite`:
* Single `SELECT` (no `WITH ... INSERT`, no DML, no `FOR UPDATE`), no `INTO`, no `COPY`.
* No function calls outside an allowlist: aggregate functions, `date`, `date_trunc`, `extract`, `coalesce`, `nullif`, `lower`, `upper`, `now`, `current_date`, `current_timestamp`, `interval` literals, casts, `count`, `sum`, `avg`, `min`, `max`.
* All tables/columns exist; output column **names and count** equal the original's (checked later in equivalence tier 0 too).
* Does not reference system schemas (`pg_catalog`, `information_schema`).

Always: `check` is pure and fast; it never connects to a database (the catalog object is passed in). Unit-test exhaustively.

## 9.7 `app/propose.py` — plan-saturation sampling

```python
def propose(shape, rendered_sql, provider, ctx_builder, signature_fn, safety_fn, *, k_max, patience, job_id, store=None) -> ProposeResult
```
Algorithm:
1. `seen_signatures = {baseline_signature}` is **not** pre-seeded for no-plan checking; instead keep `seen = set()` of candidate signatures **and** a `texts` set of normalised SQL to skip exact repeats.
2. For `i in range(k_max)`: `result = provider.suggest(...)`. For each raw candidate:
   * `ok, reason = safety_fn(raw)`; if not ok, record in `rejected` (status `rejected_safety`) and continue.
   * Normalise SQL (sqlglot `.sql(dialect="postgres", normalize=True)`); skip if text already seen.
   * `sig = signature_fn(candidate)` (may raise `InvalidDDL` ⇒ record `rejected_invalid_ddl`).
   * If `sig` is new ⇒ keep, `new_this_round += 1`. If not new ⇒ still record the candidate as a duplicate for the evidence card but do **not** count it as new.
3. `stale = 0 if new_this_round else stale + 1`; stop when `stale >= patience` ⇒ `saturated=True`.
4. Return `kept`, `samples = i + 1`, `saturated`, summed `usage`, `rejected`.
5. Emit store events `propose` per sample: counts of raw/ok/new; emit a final `result` event.
6. Budget: stop early if the job's remaining wall-clock budget is below one provider latency (pass a `should_stop()` callback).

Record the cumulative **distinct-plan curve** `[n_new_after_sample_i]` in the result for the RQ4 figure.

## 9.8 `scripts/generate_questions.py` (offline, once)

Uses the configured provider (OpenRouter) to turn 250 plain-English ops questions into SQL for the schema:
1. Seed list: 60 hand-written questions (categories: stock, orders, riders, payouts, sales, customers, with 12 **sale-specific** ones).
2. Ask the LLM to paraphrase to 250 total and write PostgreSQL SQL. Validate each SQL by `EXPLAIN` on `qc_live`; drop failures.
3. Save `backend/data/questions.json`: `[{"id","question","sql","sale_specific": bool,"day": 1..14}]`; assign `day` pseudo-randomly (seeded) to simulate daily workloads for RQ4.
4. Log tokens/cost.

## 9.9 Tests (`tests/test_safety.py`, `test_llm.py`, `test_propose.py`)

Safety (no DB): accepts each valid fixture; rejects `DROP INDEX`, `CREATE UNIQUE INDEX`, two statements, `CREATE INDEX ... USING hash`, unknown table/column,
predicate with `now()`, rewrite with `DELETE`, rewrite calling `pg_sleep`, 5 key columns, empty SQL, SQL > 4000 chars, comment injection.
LLM: `parse_candidates` handles fenced JSON, prose before/after, bare list, invalid JSON ⇒ `LLMError`. OpenRouter provider with `httpx.MockTransport`:
200 OK, 429 then 200, 500×3 ⇒ `LLMError`, 400 on `response_format` ⇒ retry without it.
Propose (`db`): mock provider on `recent_orders` ⇒ `saturated=True` within `k_max`, kept ≥ 2, distinct signatures; an invalid-DDL fixture ends as `rejected_invalid_ddl`.
Real API test `@pytest.mark.llm`: one call, response parses, all candidates pass `safety.check` or are cleanly rejected.

## 9.10 Done checklist
- [x] Mock proposer reproducible (same seed ⇒ same kept list)
- [x] OpenRouter provider tested with mocked HTTP; one real call logged in `docs/PROGRESS.md` (model, tokens)
- [x] `questions.json` generated (≥ 200 valid items)
- [x] Commit `step 09: llm, safety, proposer`


## Implementation clarifications

- Catalog.original_sql must be set to the concrete baseline for rewrite safety;
  missing baseline fails closed. sqlglot qualification resolves aliases/columns
  and expands stars before output name/count checks. This is not equivalence.
- get_provider remains mock by default. Local ignored .env selects GPT-4.1 Nano
  for explicitly requested real tests; the key is never committed or logged.
- First mock sample caps all-good fixtures at three, resolving the text's conflict
  between all-good and the 1–3 candidate cap. Trusted fixture tags are removed
  before constructing mock output; untrusted LLM comments are rejected outright.
- MockProvider receives job_seed/params; literals are adapted without a DB call.
- Index duplication is enforced in propose per table/keys/predicate, ignoring
  explicit index names/INCLUDE differences as required by section 9.6.
- ProposeResult has a default-empty distinct_plan_curve field for RQ4. Updated
  shared provider/propose signatures and Catalog context in the contracts.
- With at most three HTTP attempts, exponential backoff sleeps 1s then 2s;
  there is no fourth attempt after a 4s sleep. Format fallback/parse retry are
  separately bounded. An optional hard HTTP-request cap is used in setup scripts.
- Paid requests cap output at 8192 tokens and prompts at 50000 encoded bytes.
- Generated 228 validated questions before user requested the cheaper model; this
  exceeds the >=200 acceptance threshold. Preserved original Mini provenance;
  configured Nano for future tests rather than regenerating and spending again.
- Questions use seeded day assignments, not deterministic model generations.
  They are safety/EXPLAIN validated workload material, not semantic ground truth.
