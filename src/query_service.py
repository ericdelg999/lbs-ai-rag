#!/usr/bin/env python3
"""Hybrid retrieval + answer generation for the LBS AI product Q&A copilot.

This module is both a CLI tool and an importable library.  The public API is
the ``query()`` function which downstream consumers (``app_streamlit.py``,
``eval_runner.py``) call directly.

Retrieval strategy (hybrid):
  1. Semantic search  – embed the query, find similar chunks in ChromaDB
  2. Keyword search   – FTS5 full-text match in SQLite
  3. Structured filter – SQL WHERE on numeric/categorical product columns
Results are merged per-SKU, scored, and the top candidates are packaged as
context for the answer model (gpt-5-mini).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time

# Fix Windows console encoding — gpt-5-mini often returns Unicode chars
# (e.g. non-breaking hyphen U+2011) that cp1252 cannot encode.
# Wrapped in try/except because Streamlit may replace sys.stdout with a
# wrapper that lacks reconfigure().
try:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv
from openai import OpenAI
from shared_constants import BRAND_SKU_PREFIXES

# File-based logger — captures errors regardless of stdout/Streamlit context.
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)
_log = logging.getLogger("query_service")
_log.setLevel(logging.DEBUG)
if not _log.handlers:
    _fh = logging.FileHandler(_LOG_DIR / "query_service.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _log.addHandler(_fh)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMBED_MODEL = "text-embedding-3-small"
ANSWER_MODEL = "gpt-5-mini"
COLLECTION_NAME = "lbs_chunks"

TOP_K_SEMANTIC = 15
TOP_K_FTS = 10
MAX_SKUS_IN_ANSWER = 3
OPENAI_TIMEOUT_SECONDS = 30.0
OPENAI_CLIENT_MAX_RETRIES = 0
EMBED_ATTEMPTS = 3
ANSWER_ATTEMPTS = 3
RETRY_BASE_SECONDS = 1.0
RETRY_MAX_SECONDS = 4.0

SYSTEM_PROMPT = """\
You are a lighting product expert assistant for LightBulbSurplus.com.
You help CS and sales reps answer product questions quickly and accurately.
You are currently showing results for: {brand}.

RULES:
1. Answer ONLY from the product data provided below. Do not invent specs.
2. If the data doesn't contain enough info, say so clearly.
3. Always cite specific SKUs when recommending products.
4. Include PDP and spec sheet links for each recommended SKU.
5. When comparing products, organize by the specs the user asked about.
6. If specs conflict between the product record and spec sheet, prefer the product record.
7. Be concise but thorough. Reps need fast, actionable answers.
8. Only recommend products that meet stated numeric constraints.
9. If the user mentions a price constraint, note that pricing data is not currently available in the product database and cannot be verified.
10. Format your response with clear headings and bullet points for easy scanning.

CONTEXT DATA:
{context}"""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class QueryResult:
    answer_text: str
    top_skus: List[Dict[str, Any]]
    evidence_chunks: List[Dict[str, Any]]
    parsed_query: Dict[str, Any]
    retrieval_meta: Dict[str, Any]


# ---------------------------------------------------------------------------
# Init helpers
# ---------------------------------------------------------------------------

def _init_clients(
    chroma_dir: str,
    sqlite_path: str,
) -> Tuple[Any, sqlite3.Connection, OpenAI]:
    """Load env, validate API key, open ChromaDB + SQLite, return triple."""
    load_dotenv()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY not set. Copy .env.example to .env and add your key."
        )

    import chromadb  # deferred so module imports don't require chromadb

    chroma_client = chromadb.PersistentClient(path=chroma_dir)
    collection = chroma_client.get_collection(COLLECTION_NAME)

    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row

    # Bypass WPAD/auto-proxy detection. Windows WPAD can route httpx through
    # a stale/down corporate proxy, causing APIConnectionError in Streamlit
    # while CLI works fine. trust_env=False prevents httpx/httpcore from
    # consulting proxy-related environment and OS-discovered settings.
    _http_client = httpx.Client(
        proxy=None,
        trust_env=False,
        timeout=OPENAI_TIMEOUT_SECONDS,
    )
    openai_client = OpenAI(
        api_key=api_key,
        timeout=OPENAI_TIMEOUT_SECONDS,
        max_retries=OPENAI_CLIENT_MAX_RETRIES,
        http_client=_http_client,
    )
    _log.info("OpenAI client created, transport=%s", type(_http_client._transport).__name__)
    return collection, conn, openai_client


# ---------------------------------------------------------------------------
# Query parser
# ---------------------------------------------------------------------------

# Operator types returned by the parser
_OP_EQ = "eq"
_OP_LTE = "lte"
_OP_GTE = "gte"

# Known base-type patterns (checked before shape to resolve ambiguity)
_BASE_TYPE_RE = re.compile(r"\b(E\d{1,2}|GU\d{1,2}|G\d{1,2}|Bi-?Pin)\b", re.I)

# Known shape patterns
_SHAPE_RE = re.compile(
    r"\b(A\d{1,2}|R\d{1,2}|BR\d{1,2}|PAR\d{1,2}|T\d{1,2}|MR\d{1,2}|ST\d{1,2}|B\d{1,2}|C\d{1,2}|S\d{1,2})\b",
    re.I,
)

_KNOWN_SKU_PREFIXES = tuple(
    sorted({prefix.upper() for prefix in BRAND_SKU_PREFIXES.values()}, key=len, reverse=True)
)
_EXPLICIT_SKU_RE = re.compile(
    r"\bsku\b(?:\s*(?:#|:|-)\s*|\s+is\s+|\s+)([A-Z0-9-]*\d[A-Z0-9-]*)\b",
    re.I,
)
if _KNOWN_SKU_PREFIXES:
    _PREFIXED_SKU_RE = re.compile(
        r"\b(?:%s)[A-Z0-9-]+\b" % "|".join(re.escape(prefix) for prefix in _KNOWN_SKU_PREFIXES),
        re.I,
    )
else:
    _PREFIXED_SKU_RE = re.compile(r"$^")

# Numeric constraint patterns  (order matters – longer phrases first)
_NUMERIC_PATTERNS: List[Tuple[str, str, re.Pattern]] = [
    # --- price (extracted but NOT applied as a filter) ---
    (
        "price",
        _OP_LTE,
        re.compile(
            r"(?:under|less\s+than|below|max|up\s+to)\s*\$\s*(\d+(?:\.\d+)?)",
            re.I,
        ),
    ),
    (
        "price",
        _OP_GTE,
        re.compile(
            r"(?:at\s+least|minimum|min|over|more\s+than|above)\s*\$\s*(\d+(?:\.\d+)?)",
            re.I,
        ),
    ),
    # --- lumens ---
    (
        "lumens",
        _OP_GTE,
        re.compile(
            r"(?:at\s+least|minimum|min|over|more\s+than|above)\s+(\d+(?:\.\d+)?)\s*(?:lumens?|lm)\b",
            re.I,
        ),
    ),
    (
        "lumens",
        _OP_GTE,
        re.compile(r"(\d+(?:\.\d+)?)\+\s*(?:lumens?|lm)\b", re.I),
    ),
    (
        "lumens",
        _OP_LTE,
        re.compile(
            r"(?:under|less\s+than|below|max|up\s+to)\s+(\d+(?:\.\d+)?)\s*(?:lumens?|lm)\b",
            re.I,
        ),
    ),
    (
        "lumens",
        _OP_EQ,
        re.compile(r"(\d+(?:\.\d+)?)\s*(?:lumens?|lm)\b", re.I),
    ),
    # --- wattage ---
    (
        "wattage",
        _OP_GTE,
        re.compile(
            r"(?:at\s+least|minimum|min|over|more\s+than|above)\s+(\d+(?:\.\d+)?)\s*(?:watts?|w)\b",
            re.I,
        ),
    ),
    (
        "wattage",
        _OP_LTE,
        re.compile(
            r"(?:under|less\s+than|below|max|up\s+to)\s+(\d+(?:\.\d+)?)\s*(?:watts?|w)\b",
            re.I,
        ),
    ),
    (
        "wattage",
        _OP_EQ,
        re.compile(r"(\d+(?:\.\d+)?)\s*(?:watts?|w)\b", re.I),
    ),
    # --- color temperature ---
    (
        "color_temperature",
        _OP_EQ,
        re.compile(r"(\d{3,5})\s*(?:k|kelvin)\b", re.I),
    ),
    # --- voltage ---
    (
        "voltage",
        _OP_EQ,
        re.compile(r"(\d{2,3})\s*(?:v|volts?)\b", re.I),
    ),
]


def parse_query(raw_query: str) -> Dict[str, Any]:
    """Extract structured constraints from a natural-language query.

    Returns a dict with keys:
      clean_text        – query with extracted tokens removed (for semantic search)
      sku_mentions      – list of 6-digit SKU strings found
      <field>           – {"op": "eq"|"lte"|"gte", "val": float} or None
      dimmable          – True | None
      base_type         – str | None
      shape             – str | None
    """
    text = raw_query.strip()
    result: Dict[str, Any] = {
        "clean_text": text,
        "sku_mentions": [],
        "wattage": None,
        "lumens": None,
        "color_temperature": None,
        "voltage": None,
        "price": None,
        "dimmable": None,
        "base_type": None,
        "shape": None,
    }

    # --- SKU mentions (explicit "SKU X", known internal prefixes, 6-digit tokens) ---
    sku_matches: List[Tuple[int, int, str]] = []

    def _normalize_sku_token(token: str) -> str:
        cleaned = token.strip().strip(".,:;!?()[]{}").upper()
        for prefix in _KNOWN_SKU_PREFIXES:
            if cleaned.startswith(prefix):
                return cleaned[len(prefix) :]
        return cleaned

    def _add_sku_matches(matches: List[re.Match[str]], extractor: Any) -> None:
        for match in matches:
            start, end = match.span()
            if any(not (end <= existing_start or start >= existing_end) for existing_start, existing_end, _ in sku_matches):
                continue
            sku_value = extractor(match)
            if sku_value:
                sku_matches.append((start, end, sku_value))

    _add_sku_matches(list(_EXPLICIT_SKU_RE.finditer(text)), lambda m: _normalize_sku_token(m.group(1)))
    _add_sku_matches(list(_PREFIXED_SKU_RE.finditer(text)), lambda m: _normalize_sku_token(m.group(0)))
    _add_sku_matches(list(re.finditer(r"\b(\d{6})\b", text)), lambda m: m.group(1))

    if sku_matches:
        seen_skus = set()
        ordered_skus: List[str] = []
        for _, _, sku_value in sku_matches:
            if sku_value not in seen_skus:
                seen_skus.add(sku_value)
                ordered_skus.append(sku_value)
        result["sku_mentions"] = ordered_skus
        for start, end, _ in reversed(sku_matches):
            text = text[:start] + text[end:]

    # --- dimmable ---
    dim_match = re.search(r"\bdimmable\b", text, re.I)
    if dim_match:
        result["dimmable"] = True
        text = text[: dim_match.start()] + text[dim_match.end() :]

    # --- base type ---
    bt_match = _BASE_TYPE_RE.search(text)
    if bt_match:
        result["base_type"] = bt_match.group(1).upper()
        text = text[: bt_match.start()] + text[bt_match.end() :]

    # --- shape ---
    sh_match = _SHAPE_RE.search(text)
    if sh_match:
        result["shape"] = sh_match.group(1).upper()
        text = text[: sh_match.start()] + text[sh_match.end() :]

    # --- numeric constraints ---
    for field_name, op, pattern in _NUMERIC_PATTERNS:
        if result.get(field_name) is not None:
            continue  # already matched by an earlier (more specific) pattern
        m = pattern.search(text)
        if m:
            result[field_name] = {"op": op, "val": float(m.group(1))}
            text = text[: m.start()] + text[m.end() :]

    # --- clean up leftover text ---
    text = re.sub(r"\s+", " ", text).strip()
    # Remove dangling prepositions / noise words left after extraction
    text = re.sub(
        r"\b(sku|that\s+is|and|with|for|the|an?|of)\b",
        " ",
        text,
        flags=re.I,
    )
    text = re.sub(r"\s+", " ", text).strip()
    result["clean_text"] = text

    return result


# ---------------------------------------------------------------------------
# Retrieval channels
# ---------------------------------------------------------------------------

def _embed_query(text: str, client: OpenAI) -> List[float]:
    """Embed a single query string with retries/backoff."""
    def _sleep_seconds(attempt: int) -> float:
        return min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2 ** (attempt - 1)))

    last_err: Optional[Exception] = None
    for attempt in range(1, EMBED_ATTEMPTS + 1):
        try:
            resp = client.embeddings.create(
                model=EMBED_MODEL,
                input=[text],
            )
            return resp.data[0].embedding
        except Exception as exc:
            last_err = exc
            if attempt < EMBED_ATTEMPTS:
                time.sleep(_sleep_seconds(attempt))
    raise RuntimeError(
        f"Embedding failed after {EMBED_ATTEMPTS} attempts: "
        f"{type(last_err).__name__}: {last_err}"
    )


def search_semantic(
    query_text: str,
    collection: Any,
    openai_client: OpenAI,
    brand: Optional[str] = None,
    n_results: int = TOP_K_SEMANTIC,
) -> List[Dict[str, Any]]:
    """Run semantic search against ChromaDB. Returns list of hit dicts."""
    try:
        embedding = _embed_query(query_text, openai_client)
    except Exception as exc:
        # Fail open: keep query flow alive via FTS + structured retrieval.
        _log.warning("Semantic embedding failed, continuing without semantic hits: %s", exc)
        return []

    where_filter = None
    if brand:
        where_filter = {"brand": brand}

    try:
        results = collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        print(f"[WARN] ChromaDB query failed: {exc}")
        return []

    hits: List[Dict[str, Any]] = []
    if not results or not results["ids"] or not results["ids"][0]:
        return hits

    for i, chunk_id in enumerate(results["ids"][0]):
        meta = results["metadatas"][0][i] if results["metadatas"] else {}
        # ChromaDB returns cosine *distance*; similarity = 1 - distance
        distance = results["distances"][0][i] if results["distances"] else 1.0
        similarity = 1.0 - distance
        hits.append(
            {
                "chunk_id": chunk_id,
                "sku": meta.get("sku", ""),
                "doc_type": meta.get("doc_type", ""),
                "source_priority": meta.get("source_priority", 9),
                "text": (results["documents"][0][i] if results["documents"] else ""),
                "similarity": similarity,
            }
        )
    return hits


def search_fts(
    query_text: str,
    sku_mentions: List[str],
    conn: sqlite3.Connection,
    brand: Optional[str] = None,
    limit: int = TOP_K_FTS,
) -> List[str]:
    """Run FTS5 keyword search. Returns list of matching SKUs."""
    skus: List[str] = []

    # Direct SKU lookup (exact match, highest priority)
    for sku in sku_mentions:
        try:
            row = conn.execute(
                """
                SELECT sku
                FROM products
                WHERE UPPER(sku) = UPPER(?)
                   OR UPPER(internal_lbs_sku) = UPPER(?)
                """,
                (sku, sku),
            ).fetchone()
            if row:
                skus.append(row["sku"])
        except sqlite3.OperationalError:
            pass

    # FTS full-text search on the cleaned query
    if query_text.strip():
        # Sanitize for FTS5: keep alphanumeric + spaces, quote each term
        words = re.findall(r"[A-Za-z0-9]+", query_text)
        words = [w for w in words if len(w) >= 2]
        if words:
            fts_expr = " OR ".join(f'"{w}"' for w in words)
            sql = """
                SELECT p.sku
                FROM products_fts f
                JOIN products p ON p.rowid = f.rowid
                WHERE products_fts MATCH ?
            """
            params: List[Any] = [fts_expr]
            if brand:
                sql += " AND p.brand = ?"
                params.append(brand)
            sql += f" LIMIT {limit}"
            try:
                rows = conn.execute(sql, params).fetchall()
                for r in rows:
                    if r["sku"] not in skus:
                        skus.append(r["sku"])
            except sqlite3.OperationalError as exc:
                print(f"[WARN] FTS query failed: {exc}")

    return skus[:limit]


def search_structured(
    parsed: Dict[str, Any],
    conn: sqlite3.Connection,
    brand: Optional[str] = None,
) -> List[str]:
    """Run structured SQL filter based on parsed numeric/categorical constraints.

    Price constraints are intentionally skipped (column is NULL).
    Returns list of matching SKUs.
    """
    conditions: List[str] = []
    params: List[Any] = []

    if brand:
        conditions.append("brand = ?")
        params.append(brand)

    # Numeric fields (skip price)
    for col in ("wattage", "lumens", "color_temperature", "voltage"):
        constraint = parsed.get(col)
        if constraint is None:
            continue
        op = constraint["op"]
        val = constraint["val"]
        if op == _OP_EQ:
            conditions.append(f"{col} = ?")
        elif op == _OP_LTE:
            conditions.append(f"{col} <= ?")
        elif op == _OP_GTE:
            conditions.append(f"{col} >= ?")
        params.append(val)

    # Categorical
    if parsed.get("dimmable"):
        conditions.append("LOWER(dimmable) = 'yes'")
    if parsed.get("base_type"):
        conditions.append("UPPER(base_type) = ?")
        params.append(parsed["base_type"].upper())
    if parsed.get("shape"):
        conditions.append("UPPER(shape) = ?")
        params.append(parsed["shape"].upper())

    if not conditions:
        return []  # no constraints -> don't return all 598 rows

    where = " AND ".join(conditions)
    sql = f"SELECT sku FROM products WHERE {where}"
    try:
        rows = conn.execute(sql, params).fetchall()
        return [r["sku"] for r in rows]
    except sqlite3.OperationalError as exc:
        print(f"[WARN] Structured query failed: {exc}")
        return []


# ---------------------------------------------------------------------------
# Merge & rank
# ---------------------------------------------------------------------------

def _has_hard_constraints(parsed: Dict[str, Any]) -> bool:
    """Return True if the parsed query has any non-price numeric or categorical constraint."""
    for col in ("wattage", "lumens", "color_temperature", "voltage"):
        if parsed.get(col) is not None:
            return True
    if parsed.get("dimmable") or parsed.get("base_type") or parsed.get("shape"):
        return True
    return False


def merge_and_rank(
    semantic_hits: List[Dict[str, Any]],
    fts_skus: List[str],
    structured_skus: List[str],
    parsed: Dict[str, Any],
    conn: sqlite3.Connection,
    top_k: int = MAX_SKUS_IN_ANSWER,
) -> List[Dict[str, Any]]:
    """Merge results from all channels, score per-SKU, return ranked list."""
    sku_scores: Dict[str, float] = {}
    sku_chunks: Dict[str, List[Dict[str, Any]]] = {}

    has_constraints = _has_hard_constraints(parsed)
    structured_set = set(structured_skus)
    fts_set = set(fts_skus)
    mentioned_set = set(parsed.get("sku_mentions", []))

    # Track best semantic similarity per SKU (take max across chunks)
    sku_best_sim: Dict[str, float] = {}

    # Seed from semantic hits
    for hit in semantic_hits:
        sku = hit["sku"]
        sim = hit.get("similarity", 0.0)
        sku_best_sim[sku] = max(sku_best_sim.get(sku, 0.0), sim)
        sku_chunks.setdefault(sku, []).append(hit)

    # Apply semantic scores (scale similarity to 0-3 range so it competes
    # with FTS bonus; raw cosine sim for product queries is typically 0.4-0.7)
    for sku, sim in sku_best_sim.items():
        scaled_sim = sim * 4.0  # 0.6 sim -> 2.4 points
        sku_scores[sku] = scaled_sim
        # Bonus for having a sku_record chunk in the results
        if any(c.get("doc_type") == "sku_record" for c in sku_chunks[sku]):
            sku_scores[sku] += 0.5

    # Add FTS bonus
    for sku in fts_skus:
        sku_scores.setdefault(sku, 0.0)
        sku_scores[sku] += 1.5

    # Add SKU-mention bonus
    for sku in mentioned_set:
        sku_scores.setdefault(sku, 0.0)
        sku_scores[sku] += 5.0

    # Seed structured-matched SKUs into the pool so they can surface even when
    # semantic/FTS didn't find them (e.g. semantic embedding failed, or FTS
    # matched a different subset).  Give them a baseline score so they compete.
    for sku in structured_skus:
        if sku not in sku_scores:
            sku_scores[sku] = 1.0  # constraint match baseline

    # Gate: if hard constraints exist, exclude SKUs that fail them
    if has_constraints and structured_skus:
        for sku in list(sku_scores.keys()):
            if sku not in structured_set and sku not in mentioned_set:
                sku_scores[sku] = -1.0  # will be filtered out
    # If constraints exist but structured search returned nothing, keep semantic results
    # (the constraints might be too strict or the column might be NULL)

    # Sort
    ranked = sorted(sku_scores.items(), key=lambda x: x[1], reverse=True)
    ranked = [(sku, score) for sku, score in ranked if score > 0]

    # Fetch product rows for top candidates
    results: List[Dict[str, Any]] = []
    for sku, score in ranked[:top_k]:
        row = conn.execute(
            "SELECT * FROM products WHERE sku = ?", (sku,)
        ).fetchone()
        if not row:
            continue
        product = dict(row)
        product["relevance_score"] = round(score, 3)
        product["chunks"] = sku_chunks.get(sku, [])
        results.append(product)

    return results


# ---------------------------------------------------------------------------
# Context packaging
# ---------------------------------------------------------------------------

def build_context_package(
    ranked_skus: List[Dict[str, Any]],
    max_skus: int = MAX_SKUS_IN_ANSWER,
) -> str:
    """Format top SKUs into a text block for the LLM prompt."""
    if not ranked_skus:
        return "(No matching products found in the database.)"

    def _strip_helper_custom_fields(text: str) -> str:
        if not text:
            return ""
        parts = [part.strip() for part in text.split("|")]
        kept = [part for part in parts if part and not part.startswith("__")]
        return " | ".join(kept)

    def _trim_sku_record_text(text: str) -> str:
        if not text:
            return ""
        trimmed = text.split("\nDescription:\n", 1)[0]
        lines = trimmed.splitlines()
        kept_lines = [line for line in lines if not re.match(r"\s*-\s*__", line)]
        return "\n".join(kept_lines).strip()

    blocks: List[str] = []
    for product in ranked_skus[:max_skus]:
        sku = product.get("sku", "?")
        h1 = product.get("h1", "")
        pdp = product.get("pdp_url", "")
        spec_url = product.get("spec_sheet_url", "")

        header = f"=== SKU: {sku} ==="
        finish = product.get("finish") or ""
        voltage = product.get("voltage")
        voltage_str = f"{voltage}V" if voltage is not None else "N/A"
        specs = (
            f"Product: {h1}\n"
            f"Wattage: {product.get('wattage', 'N/A')} | "
            f"Lumens: {product.get('lumens', 'N/A')} | "
            f"CCT: {product.get('color_temperature', 'N/A')}K | "
            f"Base: {product.get('base_type', 'N/A')} | "
            f"Shape: {product.get('shape', 'N/A')} | "
            f"Dimmable: {product.get('dimmable', 'N/A')} | "
            f"Voltage: {voltage_str}"
            + (f" | Finish: {finish}" if finish else "")
            + "\n"
            f"Category: {product.get('category', 'N/A')}\n"
            f"PDP: {pdp}\n"
            f"Spec Sheet: {spec_url}"
        )

        # Collect chunk texts
        chunks = product.get("chunks", [])
        sku_record_text = ""
        spec_text = ""
        for c in chunks:
            if c.get("doc_type") == "sku_record":
                sku_record_text = _trim_sku_record_text(c.get("text", ""))[:1500]
            elif c.get("doc_type") == "spec_sheet":
                spec_text = c.get("text", "")[:400]

        # Always include custom fields from SQLite (covers all non-normalized attributes;
        # essential for product types like drivers/fixtures where normalized columns are N/A)
        custom_fields = _strip_helper_custom_fields(product.get("custom_fields_text") or "")

        body_parts = [header, specs]
        if custom_fields:
            body_parts.append(f"\n[Custom Fields]\n{custom_fields}")
        if sku_record_text:
            body_parts.append(f"\n[Product Data]\n{sku_record_text}")
        if spec_text:
            body_parts.append(f"\n[Spec Sheet Extract]\n{spec_text}")

        blocks.append("\n".join(body_parts))

    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Answer generation
# ---------------------------------------------------------------------------

def generate_answer(
    raw_query: str,
    context: str,
    brand: str,
    openai_client: OpenAI,
) -> Tuple[str, Optional[Exception]]:
    """Call gpt-5-mini to generate a grounded answer.

    Returns tuple: (answer_text, last_exception).
    """
    def _sleep_seconds(attempt: int) -> float:
        return min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * (2 ** (attempt - 1)))

    def _extract_output_text(resp: Any) -> str:
        # Preferred path for Responses API.
        output_text = getattr(resp, "output_text", None)
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        # Defensive fallback path for SDK response-shape variance.
        output = getattr(resp, "output", None)
        if not output:
            return ""
        chunks: List[str] = []
        for item in output:
            contents = getattr(item, "content", None)
            if not contents:
                continue
            for part in contents:
                text_val = getattr(part, "text", None) or getattr(part, "output_text", None)
                if text_val:
                    chunks.append(str(text_val))
        return "\n".join(chunks).strip()

    brand_label = brand if brand else "All Brands"
    system = SYSTEM_PROMPT.format(brand=brand_label, context=context)
    _log.info("generate_answer called: model=%s, query=%r, system_len=%d", ANSWER_MODEL, raw_query, len(system))

    last_err: Optional[Exception] = None
    for attempt in range(1, ANSWER_ATTEMPTS + 1):
        try:
            resp = openai_client.responses.create(
                model=ANSWER_MODEL,
                instructions=system,
                input=raw_query,
            )
            text = _extract_output_text(resp)
            if text:
                _log.info("generate_answer succeeded on attempt %d, answer_len=%d", attempt, len(text))
                return text, None
            last_err = RuntimeError("Responses API returned empty output.")
            _log.warning("Attempt %d: API returned empty output", attempt)
        except Exception as exc:
            last_err = exc
            _log.error("Attempt %d failed: %s: %s", attempt, type(exc).__name__, exc, exc_info=True)
            if attempt < ANSWER_ATTEMPTS:
                time.sleep(_sleep_seconds(attempt))

    _log.warning("Answer model call failed after %d attempts, using fallback: %s", ANSWER_ATTEMPTS, last_err)
    return "", last_err


def build_fallback_answer(raw_query: str, ranked: List[Dict[str, Any]], brand: Optional[str]) -> str:
    """Generate a retrieval-only fallback answer when the answer model is unavailable."""
    if not ranked:
        scope = brand if brand else "all indexed brands"
        return (
            "I could not reach the answer model right now, and no retrieval matches were found.\n\n"
            f"Try rephrasing your question or including a SKU. Current search scope: {scope}."
        )

    top = ranked[0]
    sku = top.get("sku", "")
    name = top.get("h1", sku)
    q = (raw_query or "").lower()

    # Lightweight intent mapping for common support questions.
    if "watt" in q and top.get("wattage") is not None:
        main_line = f"Based on retrieved product data, **SKU {sku}** is **{top.get('wattage')}W**."
    elif "lumen" in q and top.get("lumens") is not None:
        main_line = f"Based on retrieved product data, **SKU {sku}** is **{top.get('lumens')} lumens**."
    elif ("dimmable" in q) and top.get("dimmable"):
        main_line = f"Based on retrieved product data, **SKU {sku}** dimmable status is **{top.get('dimmable')}**."
    elif (
        "color temperature" in q
        or "cct" in q
        or "kelvin" in q
        or re.search(r"\b\d{3,5}k\b", q)
    ) and top.get("color_temperature") is not None:
        main_line = (
            f"Based on retrieved product data, **SKU {sku}** color temperature is "
            f"**{int(top.get('color_temperature'))}K**."
        )
    elif "base" in q and top.get("base_type"):
        main_line = f"Based on retrieved product data, **SKU {sku}** base type is **{top.get('base_type')}**."
    else:
        main_line = f"I could not reach the answer model right now, but retrieval found **{name}** as the top match."

    return (
        f"{main_line}\n\n"
        "Showing retrieval-backed product matches below."
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def query(
    raw_query: str,
    brand: Optional[str] = "Bulbrite",
    chroma_dir: str = "db/chroma",
    sqlite_path: str = "db/products.sqlite",
    top_k: int = MAX_SKUS_IN_ANSWER,
    skip_llm: bool = False,
) -> QueryResult:
    """Run hybrid retrieval + answer generation.

    Parameters
    ----------
    raw_query : str
        Natural-language question from the user.
    brand : str | None
        Brand to filter by. None or "" for all brands.
    chroma_dir : str
        Path to ChromaDB persist directory.
    sqlite_path : str
        Path to SQLite database.
    top_k : int
        Max SKUs to return.
    skip_llm : bool
        If True, skip the answer model call (useful for retrieval debugging).

    Returns
    -------
    QueryResult
    """
    if not raw_query or not raw_query.strip():
        return QueryResult(
            answer_text="Please enter a product question.",
            top_skus=[],
            evidence_chunks=[],
            parsed_query={},
            retrieval_meta={},
        )

    brand_filter = brand if brand else None
    t0 = time.time()

    # Init
    collection, conn, openai_client = _init_clients(chroma_dir, sqlite_path)

    # Step 1: Parse
    parsed = parse_query(raw_query)

    # Step 2: Retrieve
    semantic_hits = search_semantic(
        parsed["clean_text"] or raw_query,
        collection,
        openai_client,
        brand=brand_filter,
        n_results=TOP_K_SEMANTIC,
    )

    fts_skus = search_fts(
        parsed["clean_text"] or raw_query,
        parsed["sku_mentions"],
        conn,
        brand=brand_filter,
        limit=TOP_K_FTS,
    )

    structured_skus = search_structured(parsed, conn, brand=brand_filter)

    # Step 3: Merge & rank
    ranked = merge_and_rank(
        semantic_hits, fts_skus, structured_skus, parsed, conn, top_k=top_k
    )

    # Step 4: Build context
    context = build_context_package(ranked, max_skus=top_k)

    # Step 5: Generate answer
    llm_second_pass = False
    llm_status = "skipped"
    llm_error_str = None
    if skip_llm:
        answer_text = "(LLM skipped -- retrieval results only)"
    else:
        llm_answer, llm_err = generate_answer(raw_query, context, brand or "All Brands", openai_client)
        err_text = str(llm_err).lower() if llm_err else ""
        is_connection_error = "connection error" in err_text
        if (not llm_answer) and (len(ranked) > 1) and (not is_connection_error):
            # Retry with compact context in case large prompt payload contributed.
            llm_second_pass = True
            compact_context = build_context_package(ranked[:1], max_skus=1)
            llm_answer, llm_err = generate_answer(
                raw_query, compact_context, brand or "All Brands", openai_client
            )
        answer_text = llm_answer if llm_answer else build_fallback_answer(raw_query, ranked, brand)
        llm_status = "generated" if llm_answer else "fallback"
        llm_error_str = f"{type(llm_err).__name__}: {llm_err}" if llm_err else None

    elapsed = time.time() - t0

    # Package evidence
    evidence: List[Dict[str, Any]] = []
    for product in ranked:
        for chunk in product.get("chunks", []):
            evidence.append(
                {
                    "chunk_id": chunk.get("chunk_id", ""),
                    "doc_type": chunk.get("doc_type", ""),
                    "sku": chunk.get("sku", ""),
                    "text_snippet": chunk.get("text", "")[:200],
                }
            )

    # Package top SKUs (strip chunks and large text for the return value)
    top_skus_clean: List[Dict[str, Any]] = []
    for product in ranked:
        top_skus_clean.append(
            {
                "sku": product.get("sku"),
                "h1": product.get("h1"),
                "pdp_url": product.get("pdp_url"),
                "spec_sheet_url": product.get("spec_sheet_url"),
                "relevance_score": product.get("relevance_score"),
                "wattage": product.get("wattage"),
                "lumens": product.get("lumens"),
                "color_temperature": product.get("color_temperature"),
                "base_type": product.get("base_type"),
                "dimmable": product.get("dimmable"),
                "shape": product.get("shape"),
            }
        )

    conn.close()

    return QueryResult(
        answer_text=answer_text,
        top_skus=top_skus_clean,
        evidence_chunks=evidence,
        parsed_query=parsed,
        retrieval_meta={
            "semantic_count": len(semantic_hits),
            "fts_count": len(fts_skus),
            "structured_count": len(structured_skus),
            "merged_candidate_count": len(ranked),
            "elapsed_seconds": round(elapsed, 2),
            "llm_status": llm_status,
            "llm_second_pass": llm_second_pass,
            "llm_error": llm_error_str,
        },
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LBS AI Product Q&A – hybrid retrieval + answer generation."
    )
    parser.add_argument("--query", required=True, help="The question to ask.")
    parser.add_argument(
        "--brand",
        default="Bulbrite",
        help='Brand filter. Default: Bulbrite. Pass "" for all brands.',
    )
    parser.add_argument("--chroma-dir", default="db/chroma", help="ChromaDB directory.")
    parser.add_argument("--sqlite-path", default="db/products.sqlite", help="SQLite DB.")
    parser.add_argument("--top-k", type=int, default=5, help="Max SKUs to return.")
    parser.add_argument(
        "--verbose", action="store_true", help="Print retrieval debug info."
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM answer generation (debug retrieval only).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    brand = args.brand if args.brand else None

    print(f"\n{'='*60}")
    print(f"Query:  {args.query}")
    print(f"Brand:  {brand or 'All'}")
    print(f"{'='*60}\n")

    result = query(
        raw_query=args.query,
        brand=brand,
        chroma_dir=args.chroma_dir,
        sqlite_path=args.sqlite_path,
        top_k=args.top_k,
        skip_llm=args.no_llm,
    )

    if args.verbose:
        print("--- Parsed Query ---")
        for k, v in result.parsed_query.items():
            if v is not None and v != [] and v != "":
                print(f"  {k}: {v}")
        print()
        print("--- Retrieval Meta ---")
        for k, v in result.retrieval_meta.items():
            print(f"  {k}: {v}")
        print()

    print("--- Answer ---")
    print(result.answer_text)
    print()

    if result.top_skus:
        print("--- Top SKUs ---")
        for i, sku_info in enumerate(result.top_skus, 1):
            print(
                f"  {i}. [{sku_info['sku']}] {sku_info.get('h1', '')}"
                f"  (score: {sku_info.get('relevance_score', '?')})"
            )
            print(
                f"     Wattage: {sku_info.get('wattage', 'N/A')} | "
                f"Lumens: {sku_info.get('lumens', 'N/A')} | "
                f"CCT: {sku_info.get('color_temperature', 'N/A')}K | "
                f"Base: {sku_info.get('base_type', 'N/A')} | "
                f"Dimmable: {sku_info.get('dimmable', 'N/A')}"
            )
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
