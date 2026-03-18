# Debug LLM Connection — Investigation Spec

## Problem

The Streamlit app retrieves products correctly (embeddings + SKU cards work), but the answer generation via `gpt-5-mini` always fails. Users see fallback text like "Based on retrieved product data, SKU 776218 is 7.0W" instead of a full AI-generated paragraph answer.

The `generate_answer()` function in `src/query_service.py` uses the OpenAI Responses API (`client.responses.create()`), matching the pattern in the reference script (`REFERENCE_AI_upload_script_v2.3.1_toggle.py`). Despite this, the call fails after 3 retries and falls back to `build_fallback_answer()`.

**Embeddings work** — `client.embeddings.create()` succeeds, so the API key is valid and network connectivity is fine. The failure is specific to the Responses API answer generation call.

**We want to use the Responses API** (not Chat Completions) because OpenAI confirms Responses API has better performance for gpt-5-mini and is the recommended path forward.

### Verified Patterns from OpenAI Docs (March 2026)

**Simplest call pattern** (from OpenAI quickstart):
```python
response = client.responses.create(
    model="gpt-5-mini",
    input="Say hello in one sentence.",
)
print(response.output_text)  # Direct property — no iteration needed
```

**With system prompt** (from migration guide — use `instructions` parameter, NOT role: "system" in input):
```python
response = client.responses.create(
    model="gpt-5-mini",
    instructions="You are a helpful assistant.",
    input="Say hello in one sentence.",
)
print(response.output_text)
```

**Key differences from Chat Completions:**
- System prompt goes in `instructions` parameter (top-level), not in the input array
- `response.output_text` gives the text directly — no `resp.choices[0].message.content`
- `input` can be a simple string for single-turn, or a list of message dicts for multi-turn

---

## Context: How the Reference Script Connects

The working reference script (`REFERENCE_AI_upload_script_v2.3.1_toggle.py`) uses the same API key and the same model (`gpt-5-mini`). Its connection pattern:

```python
# Client setup (lines 287-292)
load_dotenv()
_api_key = os.getenv("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=_api_key)

# API call (lines 329-343)
@retry(
    reraise=True,
    wait=wait_exponential(min=1, max=60),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type((RateLimitError, APIError, APIConnectionError, Timeout)),
)
def create_response(**kwargs):
    return client.responses.create(**kwargs)

# Input format (lines 294-310)
input = [
    {
        "role": "system",
        "content": [{"type": "input_text", "text": "system prompt here"}],
    },
    {
        "role": "user",
        "content": [{"type": "input_text", "text": "user message here"}],
    },
]

# Response extraction (lines 312-327)
output = getattr(resp, "output", None)
for item in output:
    contents = getattr(item, "content", None)
    for part in contents:
        text_val = getattr(part, "text", None)
```

The reference script is confirmed working with this same API key and model.

---

## Current Code Under Investigation

**File:** `src/query_service.py`
**Function:** `generate_answer()` (line ~609)

```python
def generate_answer(raw_query, context, brand, openai_client):
    brand_label = brand if brand else "All Brands"
    system = SYSTEM_PROMPT.format(brand=brand_label, context=context)

    last_err = None
    for attempt in range(1, 4):
        try:
            resp = openai_client.responses.create(
                model=ANSWER_MODEL,  # "gpt-5-mini"
                input=[
                    {
                        "role": "system",
                        "content": [{"type": "input_text", "text": system}],
                    },
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": raw_query}],
                    },
                ],
            )
            output = getattr(resp, "output", None)
            if not output:
                return ""
            chunks = []
            for item in output:
                contents = getattr(item, "content", None)
                if not contents:
                    continue
                for part in contents:
                    text_val = getattr(part, "text", None) or getattr(part, "output_text", None)
                    if text_val:
                        chunks.append(str(text_val))
            return "\n".join(chunks).strip()
        except Exception as exc:
            last_err = exc
            if attempt < 3:
                time.sleep(2)

    print(f"[WARN] Answer model call failed, using retrieval fallback: {last_err}")
    return ""
```

---

## Investigation Tasks

### Task 1: Capture the exact error

Add temporary verbose logging to `generate_answer()` so we can see what's actually failing. Before the retry loop, add:

```python
print(f"[DEBUG] Attempting Responses API call with model={ANSWER_MODEL}")
print(f"[DEBUG] System prompt length: {len(system)} chars")
print(f"[DEBUG] Raw query: {raw_query}")
```

Inside the except block, change the print to include the exception type:

```python
print(f"[DEBUG] Attempt {attempt} failed: {type(exc).__name__}: {exc}")
```

Run the CLI test and capture the full terminal output:
```bash
.venv/Scripts/python src/query_service.py --query "Is SKU 132507 dimmable?" --brand Bulbrite --verbose
```

**Report what the `[DEBUG]` lines say.** The error type and message will determine the fix.

### Task 2: Isolate the Responses API call

Create a minimal test script at `src/test_llm_connection.py`:

```python
#!/usr/bin/env python3
"""Minimal test of OpenAI Responses API connection."""

import os
import openai as openai_pkg
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
api_key = os.getenv("OPENAI_API_KEY", "").strip()
print(f"API key loaded: {'yes' if api_key else 'NO'}")
print(f"API key prefix: {api_key[:8]}..." if api_key else "N/A")
print(f"OpenAI SDK version: {openai_pkg.__version__}")

client = OpenAI(api_key=api_key)

# Test 1: Embeddings (known working)
print("\n--- Test 1: Embeddings ---")
try:
    resp = client.embeddings.create(model="text-embedding-3-small", input=["test"])
    print(f"OK: embedding dimension = {len(resp.data[0].embedding)}")
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")

# Test 2a: Responses API — simplest form (string input, no system prompt)
print("\n--- Test 2a: Responses API (simple string input) ---")
try:
    resp = client.responses.create(
        model="gpt-5-mini",
        input="Say hello in one sentence.",
    )
    print(f"OK: output_text = {resp.output_text}")
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")

# Test 2b: Responses API — with instructions parameter (recommended for system prompts)
print("\n--- Test 2b: Responses API (instructions + string input) ---")
try:
    resp = client.responses.create(
        model="gpt-5-mini",
        instructions="You are a helpful lighting product assistant.",
        input="What is a common wattage for LED bulbs?",
    )
    print(f"OK: output_text = {resp.output_text}")
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")

# Test 2c: Responses API — with role-based input array (reference script pattern)
print("\n--- Test 2c: Responses API (role-based input array) ---")
try:
    resp = client.responses.create(
        model="gpt-5-mini",
        input=[
            {
                "role": "system",
                "content": [{"type": "input_text", "text": "You are a helpful assistant."}],
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "Say hello in one sentence."}],
            },
        ],
    )
    print(f"OK: output_text = {resp.output_text}")
except Exception as e:
    print(f"FAIL: {type(e).__name__}: {e}")

# Test 3: Check if client.responses attribute even exists
print("\n--- Test 3: SDK attribute check ---")
print(f"Has client.responses: {hasattr(client, 'responses')}")
print(f"Has client.chat: {hasattr(client, 'chat')}")
if hasattr(client, 'responses'):
    print(f"client.responses type: {type(client.responses)}")

print("\n--- Done ---")
```

Run it:
```bash
.venv/Scripts/python src/test_llm_connection.py
```

**This test reveals:**
- Whether the API key works at all (Test 1 — should pass since embeddings work)
- Whether `client.responses.create()` works with `gpt-5-mini` (Test 2 — the real test)
- Whether `client.chat.completions.create()` works instead (Test 3 — in case the API needs Chat Completions after all)

### Task 3: Check OpenAI SDK version

The Responses API requires a recent version of the `openai` Python package. Run:

```bash
.venv/Scripts/pip show openai
```

If the version is older than `1.60.0`, the `client.responses` attribute may not exist. Upgrade if needed:

```bash
.venv/Scripts/pip install --upgrade openai
```

### Task 4: Check for API key permissions or model access

Possible issues with a new API key:
- The API key might not have access to `gpt-5-mini` — some models require specific tier access
- The organization might need billing set up before API calls work (embeddings might be on a free tier)
- There might be a rate limit at Tier 1

Check the OpenAI dashboard:
1. Go to platform.openai.com → API Keys → verify the key is active
2. Go to Settings → Limits → check if `gpt-5-mini` is listed under available models
3. Go to Usage → check if there are any failed requests logged

---

## Expected Outcomes

| Test | If it passes | If it fails |
|------|-------------|-------------|
| Test 1 (Embeddings) | API key works, network OK | Key or network issue (unlikely — already working) |
| Test 2a (Simple string) | Responses API works with gpt-5-mini | Error message tells us exactly what's wrong |
| Test 2b (instructions param) | System prompt pattern works — apply recommended fix | Try Test 2c as fallback |
| Test 2c (role-based input) | Reference script pattern works | Neither pattern works — permissions/tier issue |
| Test 3 (SDK check) | SDK supports Responses API | Upgrade openai package first |

---

## After Investigation: What to Fix

Based on findings:

1. **If Test 2a/2b pass:** The Responses API works. Apply the recommended fix below to `generate_answer()` using the `instructions` parameter and `output_text`.

2. **If Test 2a/2b fail with permission/model error:** The API key needs model access enabled in the OpenAI dashboard, or `gpt-5-mini` isn't available on this account tier. Check platform.openai.com → Settings → Limits.

3. **If SDK is too old or `client.responses` doesn't exist (Test 3):** Upgrade: `.venv/Scripts/pip install --upgrade openai`

4. **If tests pass but production code still fails:** The issue is likely system prompt size (includes all product context). Test with `--top-k 1` to reduce context.

### Recommended Fix for `generate_answer()` (apply after tests pass)

Replace the current `generate_answer()` body (inside the try block) with the cleaner pattern using `instructions` + `output_text`:

```python
def generate_answer(raw_query, context, brand, openai_client):
    brand_label = brand if brand else "All Brands"
    system = SYSTEM_PROMPT.format(brand=brand_label, context=context)

    last_err = None
    for attempt in range(1, 4):
        try:
            resp = openai_client.responses.create(
                model=ANSWER_MODEL,
                instructions=system,
                input=raw_query,
            )
            return resp.output_text or ""
        except Exception as exc:
            last_err = exc
            print(f"[DEBUG] Attempt {attempt} failed: {type(exc).__name__}: {exc}")
            if attempt < 3:
                time.sleep(2)

    print(f"[WARN] Answer model call failed, using retrieval fallback: {last_err}")
    return ""
```

**What changed vs current code:**
- System prompt moved from input array `role: "system"` → `instructions` parameter (OpenAI recommended pattern)
- User message passed as simple string `input=raw_query` instead of nested content blocks
- Response extraction: `resp.output_text` instead of iterating `output[].content[].text`
- Added debug logging with exception type on each failed attempt

---

## Files to Create/Modify

| File | Action |
|------|--------|
| `src/test_llm_connection.py` | **Create** — minimal diagnostic script |
| `src/query_service.py` | **Add debug logging** to `generate_answer()` temporarily |

## Verification

After running `test_llm_connection.py`, paste the full terminal output. The fix will be determined by which tests pass and fail.
