#!/usr/bin/env python3
"""Minimal test of OpenAI Responses API connection."""

import os
import sys

import httpx

# Fix Windows console encoding — gpt-5-mini often returns Unicode chars
# (e.g. non-breaking hyphen U+2011) that cp1252 cannot encode.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import openai as openai_pkg
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
api_key = os.getenv("OPENAI_API_KEY", "").strip()
print(f"API key loaded: {'yes' if api_key else 'NO'}")
print(f"API key prefix: {api_key[:8]}..." if api_key else "N/A")
print(f"OpenAI SDK version: {openai_pkg.__version__}")

http_client = httpx.Client(
    proxy=None,
    trust_env=False,
    timeout=30.0,
)
client = OpenAI(api_key=api_key, timeout=30.0, max_retries=0, http_client=http_client)

# Test 1: Embeddings (known working)
print("\n--- Test 1: Embeddings ---")
try:
    resp = client.embeddings.create(model="text-embedding-3-small", input=["test"])
    print(f"OK: embedding dimension = {len(resp.data[0].embedding)}")
except Exception as e:  # noqa: BLE001
    print(f"FAIL: {type(e).__name__}: {e}")

# Test 2a: Responses API - simplest form (string input, no system prompt)
print("\n--- Test 2a: Responses API (simple string input) ---")
try:
    resp = client.responses.create(
        model="gpt-5-mini",
        input="Say hello in one sentence.",
    )
    print(f"OK: output_text = {resp.output_text}")
except Exception as e:  # noqa: BLE001
    print(f"FAIL: {type(e).__name__}: {e}")

# Test 2b: Responses API - with instructions parameter
print("\n--- Test 2b: Responses API (instructions + string input) ---")
try:
    resp = client.responses.create(
        model="gpt-5-mini",
        instructions="You are a helpful lighting product assistant.",
        input="What is a common wattage for LED bulbs?",
    )
    print(f"OK: output_text = {resp.output_text}")
except Exception as e:  # noqa: BLE001
    print(f"FAIL: {type(e).__name__}: {e}")

# Test 2c: Responses API - role-based input array
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
except Exception as e:  # noqa: BLE001
    print(f"FAIL: {type(e).__name__}: {e}")

# Test 3: Check if client.responses attribute exists
print("\n--- Test 3: SDK attribute check ---")
print(f"Has client.responses: {hasattr(client, 'responses')}")
print(f"Has client.chat: {hasattr(client, 'chat')}")
if hasattr(client, "responses"):
    print(f"client.responses type: {type(client.responses)}")

print("\n--- Done ---")
