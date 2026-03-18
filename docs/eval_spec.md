# Eval System Spec — LBS AI RAG POC

## Purpose

Build an end-to-end evaluation harness that:
1. Generates a realistic CS/sales question set covering two query types
2. Runs each question through `query_service.query()`
3. Scores each answer automatically using gpt-5-mini as judge
4. Outputs a CSV report with pass/fail per question plus aggregate metrics

This is Sprint 4 work. Do NOT change `query_service.py` or `app_streamlit.py` — evaluation is read-only against the existing pipeline.

---

## Step 1 — Generate the Question Set

Create `data/eval/bulbrite_test_questions.csv` with realistic questions across two types.

### Type A: Exact SKU lookup (10 questions)

User knows the SKU and asks a specific factual question. The correct answer is verifiable from the product record in `data/prepared/bulbrite_products_parsed_v2.csv`.

Required columns: `question_id`, `query_type`, `question`, `target_sku`, `expected_fact`, `expected_fact_field`

Example questions to generate (pick SKUs from the canonical CSV):
- "Is SKU [X] dimmable?"
- "How many watts is SKU [X]?"
- "What color temperature is SKU [X]?"
- "What base type does SKU [X] use?"
- "How many lumens does SKU [X] output?"
- "What shape is SKU [X]?"
- "What is the voltage for SKU [X]?"
- "Is SKU [X] LED?"
- "What is the pack quantity for SKU [X]?"
- "What finish does SKU [X] have?"

**How to generate:** Sample 10 diverse SKUs from the CSV, pick one question per SKU, set `expected_fact` from the matching column (`dimmable`, `wattage_actual`, `cct`, `base_type`, `lumens_actual`, `shape`, `voltage`, etc.).

### Type B: Spec-based suggestion (10 questions)

User describes what they need and asks for recommendations. Correct behavior = top returned SKUs should match stated constraints.

Required columns: `question_id`, `query_type`, `question`, `required_constraints`, `forbidden_values`

Example questions to generate:
- "I need a dimmable LED A19 bulb with an E26 base around 800 lumens"
- "What's a good 3000K LED recessed downlight that's dimmable?"
- "Find me a candelabra base bulb under 5 watts"
- "I need a PAR30 flood bulb, dimmable, warm white"
- "Do you have any GU10 LED bulbs?"
- "Looking for a T4 halogen bulb with a bi-pin base"
- "What LED bulbs do you carry that are 5000K daylight?"
- "I need a dimmable MR16 spotlight"
- "Find me a decorative LED bulb with an E12 base"
- "What's a good outdoor-rated PAR38 flood?"

**How to generate:** Write `required_constraints` as a JSON dict (e.g. `{"dimmable": "Yes", "base_type": "E26"}`). Leave `target_sku` blank. Set `forbidden_values` to anything the answer should NOT contain (e.g. a wrong base type).

### CSV schema

```
question_id,query_type,question,target_sku,expected_fact,expected_fact_field,required_constraints,forbidden_values,notes
Q001,sku_lookup,"Is SKU 132507 dimmable?",132507,Yes,dimmable,,,
Q011,spec_suggestion,"I need a dimmable E26 LED A19 around 800 lumens",,,,"{'dimmable':'Yes','base_type':'E26'}","non-dimmable",
```

---

## Step 2 — Build `src/eval_runner.py`

### Inputs
- `data/eval/bulbrite_test_questions.csv`
- `query_service.query()` (import directly — no subprocess)
- OpenAI gpt-5-mini (same model, same client pattern as query_service.py: `proxy=None, trust_env=False`)

### Process per question

```python
result = query_service.query(raw_query=row["question"], brand=None)
```

Then score with a judge call:

**For Type A (sku_lookup):**
1. Check if `target_sku` appears in `result.top_skus[*].sku` → `retrieval_hit: bool`
2. Check if `expected_fact` appears in `result.answer_text` (case-insensitive) → `fact_present: bool`
3. Check `result.retrieval_meta["llm_status"] == "generated"` → `llm_generated: bool`
4. LLM judge call (gpt-5-mini):
   ```
   Does this answer correctly state that [expected_fact_field] for SKU [target_sku] is [expected_fact]?
   Answer: PASS or FAIL, then one sentence why.
   Answer text: [result.answer_text]
   ```

**For Type B (spec_suggestion):**
1. Parse `required_constraints` JSON
2. For each top SKU returned, check if it satisfies required constraints from the SQLite products table
3. `constraint_match_rate`: fraction of top 3 SKUs that satisfy all constraints
4. LLM judge call (gpt-5-mini):
   ```
   The user asked: [question]
   Required constraints: [required_constraints]
   The system returned this answer: [result.answer_text]
   Did the answer recommend products that satisfy the stated constraints?
   Answer: PASS or FAIL, then one sentence why.
   ```

### Output

Write `data/eval/eval_results_YYYYMMDD_HHMMSS.csv` with columns:
```
question_id, query_type, question, llm_status, retrieval_hit, fact_present, llm_judge_verdict,
llm_judge_reason, elapsed_seconds, top_sku_1, top_sku_2, top_sku_3, answer_preview
```

Print summary to console:
```
=== EVAL SUMMARY ===
Total: 20 | Pass: 16 | Fail: 4 | LLM generated: 20/20
Type A (SKU lookup): 9/10 pass
Type B (Spec suggestion): 7/10 pass
Avg elapsed: 12.3s
Results written to: data/eval/eval_results_20260313_103045.csv
```

### CLI usage
```bash
.venv/Scripts/python src/eval_runner.py
.venv/Scripts/python src/eval_runner.py --questions data/eval/bulbrite_test_questions.csv
.venv/Scripts/python src/eval_runner.py --limit 5   # test first 5 questions only
```

---

## Step 3 — OpenAI Client Setup in eval_runner.py

Use the same proxy-safe pattern as query_service.py:

```python
import httpx
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()

_http_client = httpx.Client(proxy=None, trust_env=False, timeout=30.0)
judge_client = OpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    timeout=30.0,
    max_retries=0,
    http_client=_http_client,
)
```

Note: `query_service.query()` creates its own internal OpenAI client — don't share the judge client with it.

---

## Files to Create

| File | Description |
|------|-------------|
| `data/eval/bulbrite_test_questions.csv` | 20-question eval set (10 Type A + 10 Type B) |
| `src/eval_runner.py` | Runner: loads questions, runs queries, scores, writes results CSV |

Do NOT modify any existing files.

---

## Verification

```bash
# Test with just 2 questions first
.venv/Scripts/python src/eval_runner.py --limit 2

# Full run
.venv/Scripts/python src/eval_runner.py
```

Expected: console summary prints, `data/eval/eval_results_*.csv` is written, no unhandled exceptions.

---

## Open Question for Owner (Eric)

When generating Type A questions — do you want Codex to pick SKUs automatically from `data/prepared/bulbrite_products_parsed_v2.csv`, or do you want to provide a shortlist of 10 SKUs that are known-good products you can verify against manually? Manual shortlist = more trustworthy ground truth.
