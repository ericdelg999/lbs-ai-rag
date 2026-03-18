# Codex Handoff: Fix OpenAI API Connection in Streamlit

Read `AGENTS.md` and `PROJECT_BRAIN.md` (section 16 — Debug Log) first.

## Problem

OpenAI API calls (`embeddings.create()` and `responses.create()`) fail with `APIConnectionError` when running inside Streamlit, but work perfectly from CLI. The app shows fallback text instead of AI-generated answers.

**Error:** `httpcore.ConnectError: [WinError 10061] No connection could be made because the target machine actively refused it`

**Key stack trace line:** `httpcore/_sync/http_proxy.py:288` — httpcore is routing through a proxy that refuses connections.

## What We Know (Confirmed Facts)

- **CLI works RIGHT NOW** from the same terminal, same `.venv`, same API key: `llm_status: generated`, `semantic_count: 15`
- **Streamlit fails EVERY TIME**: `llm_status: fallback`, `semantic_count: 0`, all 3 retry attempts fail identically
- Both embeddings and Responses API fail — this is a blanket network issue, not an API-specific bug
- Windows WPAD auto-proxy detection is **ON** (registry `DefaultConnectionSettings` byte 5 bit 0x08 = True)
- No `HTTP_PROXY` / `HTTPS_PROXY` environment variables are set
- No manual proxy in Windows registry (`ProxyEnable = 0`)
- No proxy in `.env` file
- No `.streamlit/config.toml` exists
- httpx 0.28.1, httpcore 1.0.7, OpenAI SDK 2.9.0, Python 3.13, Windows 11 Pro
- `test_llm_connection.py` passes 4/4 tests from CLI
- File-based logging in `logs/query_service.log` captures full stack traces

## What We Already Tried (DO NOT REPEAT)

1. **Increased timeout** from 12s to 30s — not the issue (fails instantly with connection refused)
2. **Fixed API call pattern** — code already uses correct `instructions=` / `input=` / `output_text` pattern
3. **Passed `httpx.Client(proxy=None)` to OpenAI constructor** — transport logs as `HTTPTransport` but httpcore STILL routes through `http_proxy.py`. The proxy bypass is being ignored.
4. **Checked all proxy env vars** — HTTP_PROXY, HTTPS_PROXY, http_proxy, https_proxy, ALL_PROXY, NO_PROXY — all unset
5. **Checked Windows registry** ProxyEnable — disabled (0)
6. **Checked `.env`** — no proxy vars
7. **Checked Streamlit config** — no config file exists
8. **Added UTF-8 stdout fix** — unrelated encoding issue, already fixed
9. **Added diagnostic panel to Streamlit UI** — shows `llm_status`, `llm_error` in browser
10. **Added file-based logging** to `logs/query_service.log` — captures errors regardless of stdout

## Current Code State

### `src/query_service.py` — `_init_clients()` (line ~128):
```python
_http_client = httpx.Client(proxy=None, timeout=OPENAI_TIMEOUT_SECONDS)
openai_client = OpenAI(
    api_key=api_key,
    timeout=OPENAI_TIMEOUT_SECONDS,
    max_retries=OPENAI_CLIENT_MAX_RETRIES,  # 0
    http_client=_http_client,
)
```

### `src/app_streamlit.py` — query call (line ~241):
```python
result = query_service.query(raw_query=prompt, brand=None)
```

### Diagnostic tools already in place:
- `logs/query_service.log` — file logger with full stack traces (`_log.error(..., exc_info=True)`)
- Streamlit debug panel — shows `llm_status`, `llm_error`, `semantic_count` in browser
- `src/test_llm_connection.py` — isolated API tests

## Next Steps to Try (In Order)

### Step 1: Force `trust_env=False` on httpx

The `proxy=None` fix didn't work because httpcore may still auto-detect proxy via OS-level settings when `trust_env=True` (the default). Try disabling environment trust entirely.

In `src/query_service.py`, `_init_clients()`, change the httpx client creation to:

```python
_http_client = httpx.Client(
    proxy=None,
    trust_env=False,
    timeout=OPENAI_TIMEOUT_SECONDS,
)
```

Then restart Streamlit (`Ctrl+C`, re-run `.venv/Scripts/streamlit run src/app_streamlit.py`) and test.

Check `logs/query_service.log` — if `http_proxy.py` is STILL in the stack trace, the proxy is being injected below httpx.

### Step 2: If Step 1 fails — set NO_PROXY before imports

At the very top of `src/app_streamlit.py` (before ANY imports), add:

```python
import os
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
```

This must be BEFORE `import query_service` (line 14) so the env var is set before httpx reads it.

### Step 3: If Step 2 fails — test whether Streamlit process can reach OpenAI at all

Add a subprocess-based connectivity test inside `app_streamlit.py` to check if it's a process-level network issue:

```python
import subprocess
result = subprocess.run(
    [".venv/Scripts/python", "-c",
     "from openai import OpenAI; import os; from dotenv import load_dotenv; "
     "load_dotenv(); c = OpenAI(api_key=os.environ['OPENAI_API_KEY']); "
     "r = c.responses.create(model='gpt-5-mini', input='Say hi'); "
     "print('OK:', r.output_text)"],
    capture_output=True, text=True, timeout=30
)
print("SUBPROCESS stdout:", result.stdout)
print("SUBPROCESS stderr:", result.stderr)
```

If the subprocess works but in-process fails, the issue is in Streamlit's Python process environment specifically. If both fail, it's a machine-level network issue.

### Step 4: If Step 3 subprocess works — use subprocess for API calls

As a workaround, create a thin wrapper that makes OpenAI calls in a subprocess:

```python
# src/openai_subprocess_wrapper.py
import subprocess, json, sys

def call_openai_responses(model, instructions, input_text):
    """Make OpenAI API call in a clean subprocess to bypass proxy issues."""
    script = f"""
import os, json
from dotenv import load_dotenv
from openai import OpenAI
load_dotenv()
c = OpenAI(api_key=os.environ['OPENAI_API_KEY'])
r = c.responses.create(model={json.dumps(model)}, instructions={json.dumps(instructions)}, input={json.dumps(input_text)})
print(json.dumps({{"output_text": r.output_text}}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(f"Subprocess failed: {result.stderr}")
    return json.loads(result.stdout)["output_text"]
```

This is a workaround, not ideal — but it isolates the problem.

### Step 5: If nothing works — check for network interception software

Run from within the Streamlit process (add temporarily to `app_streamlit.py`):

```python
import socket
try:
    sock = socket.create_connection(("api.openai.com", 443), timeout=5)
    print("Direct socket connection to api.openai.com:443 SUCCEEDED")
    sock.close()
except Exception as e:
    print(f"Direct socket connection FAILED: {e}")
```

If raw socket works but httpx fails, the issue is in httpx/httpcore proxy detection. If raw socket also fails, there's a firewall or network interception tool blocking the Streamlit process.

## Verification

After applying a fix, restart Streamlit and ask: "Is SKU 132507 dimmable?"

**Success criteria:**
- Streamlit diagnostic panel shows `llm_status: generated`
- Streamlit diagnostic panel shows `semantic_count: >0`
- `logs/query_service.log` shows `generate_answer succeeded on attempt 1`
- No `http_proxy.py` in the log stack traces

**CLI regression check:**
```bash
.venv/Scripts/python src/query_service.py --query "Is SKU 132507 dimmable?" --brand Bulbrite --verbose
```
Should still show `llm_status: generated`.

## Files to Modify

| File | What to change |
|------|---------------|
| `src/query_service.py` | `_init_clients()` — httpx client proxy/trust_env settings |
| `src/app_streamlit.py` | Possibly add `NO_PROXY=*` env var at top before imports |
| `PROJECT_BRAIN.md` | Update Debug Log (section 16) with outcome |

## After Fixing

1. Update `PROJECT_BRAIN.md` section 16 Debug Log with the confirmed root cause
2. Remove the `http_proxy.py` workaround comments once stable
3. Keep the diagnostic panel and file logging — they're useful for future debugging
