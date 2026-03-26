#!/usr/bin/env python3
"""Embed chunks into ChromaDB and build SQLite product/FTS indexes."""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import httpx
from dotenv import load_dotenv
from openai import OpenAI


COLLECTION_NAME = "lbs_chunks"
EMBED_MODEL = "text-embedding-3-small"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Embed chunks and build index stores for retrieval.")
    parser.add_argument("--chunks", default="data/chunks/chunks.jsonl", help="Path to chunks JSONL.")
    parser.add_argument("--csv", default="data/prepared/bulbrite_products_parsed_v2.csv", help="Path to parsed CSV.")
    parser.add_argument("--chroma-dir", default="db/chroma", help="Path to Chroma persist directory.")
    parser.add_argument("--sqlite-path", default="db/products.sqlite", help="Path to SQLite DB file.")
    parser.add_argument("--batch-size", type=int, default=100, help="Embedding API batch size.")
    parser.add_argument("--limit", type=int, default=0, help="Only process first N chunks (0 = all).")
    parser.add_argument("--append", action="store_true",
                        help="Append to existing indexes instead of rebuilding from scratch.")
    return parser.parse_args()


def load_chunks(path: Path) -> List[Dict[str, object]]:
    chunks: List[Dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for i, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict) or "text" not in obj or "metadata" not in obj:
                raise ValueError(f"Invalid chunk format at line {i}")
            chunks.append(obj)
    return chunks


def strip_html(html_text: str) -> str:
    text = html.unescape(html_text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_numeric(value: str) -> Optional[float]:
    text = (value or "").strip()
    if not text:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def flatten_custom_fields(json_str: str) -> str:
    text = (json_str or "").strip()
    if not text:
        return ""
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return ""

    if not isinstance(loaded, dict):
        return ""

    items: List[str] = []
    for key in sorted(loaded.keys(), key=lambda x: str(x).lower()):
        value = loaded[key]
        k = str(key).strip()
        v = "" if value is None else str(value).strip()
        if k and v:
            items.append(f"{k}: {v}")
    return " | ".join(items)


def sanitize_metadata(metadata: Dict[str, object]) -> Dict[str, object]:
    out: Dict[str, object] = {}

    # Required/flat string fields
    out["sku"] = str(metadata.get("sku") or "")
    out["internal_lbs_sku"] = str(metadata.get("internal_lbs_sku") or "")
    out["brand"] = str(metadata.get("brand") or "")
    out["doc_type"] = str(metadata.get("doc_type") or "")
    out["chunk_label"] = str(metadata.get("chunk_label") or "")
    out["source_url"] = str(metadata.get("source_url") or "")
    out["spec_sheet_url"] = str(metadata.get("spec_sheet_url") or "")
    out["category"] = str(metadata.get("category") or "")
    out["upc"] = str(metadata.get("upc") or "")
    out["base_type"] = str(metadata.get("base_type") or "")
    out["dimmable"] = str(metadata.get("dimmable") or "")

    # Required int
    source_priority = metadata.get("source_priority")
    out["source_priority"] = int(source_priority) if source_priority is not None else 0

    # Numeric optional fields: omit if null.
    for key in ["wattage", "lumens", "voltage", "color_temperature", "price", "minimum_purchase_qty"]:
        value = metadata.get(key)
        if value is None:
            continue
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            continue

    return out


def embed_batch(texts: List[str], client: OpenAI, retries: int = 3, backoff_sec: float = 2.0) -> List[List[float]]:
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            response = client.embeddings.create(model=EMBED_MODEL, input=texts)
            return [item.embedding for item in response.data]
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == retries:
                break
            time.sleep(backoff_sec)

    raise RuntimeError(f"Embedding batch failed after {retries} attempts: {last_error}")


def batched(items: List[Dict[str, object]], size: int) -> Iterable[List[Dict[str, object]]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def build_chroma_index(
    chunks: List[Dict[str, object]],
    chroma_dir: Path,
    batch_size: int,
    openai_client: OpenAI,
    append_mode: bool = False,
) -> int:
    import chromadb

    chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_dir))

    if append_mode:
        collection = client.get_or_create_collection(
            name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )
        before_count = collection.count()
        print(f"Append mode: existing collection has {before_count} items.")
    else:
        existing_names = [c.name for c in client.list_collections()]
        if COLLECTION_NAME in existing_names:
            existing = client.get_collection(COLLECTION_NAME)
            count = existing.count()
            print(f"Deleting existing collection '{COLLECTION_NAME}' ({count} items). Re-indexing from scratch.")
            client.delete_collection(COLLECTION_NAME)
        collection = client.create_collection(name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"})

    add_fn = collection.upsert if append_mode else collection.add

    total = len(chunks)
    batches = (total + batch_size - 1) // batch_size

    indexed = 0
    for bi, batch in enumerate(batched(chunks, batch_size), start=1):
        texts: List[str] = [str(c["text"]) for c in batch]
        metadatas: List[Dict[str, object]] = [sanitize_metadata(dict(c["metadata"])) for c in batch]
        ids: List[str] = [str(c["metadata"]["chunk_id"]) for c in batch]

        print(f"Embedding batch {bi}/{batches} ({indexed + len(batch)}/{total} chunks)")
        embeddings = embed_batch(texts=texts, client=openai_client)

        add_fn(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)
        indexed += len(batch)
        time.sleep(0.5)

    return collection.count()


def build_sqlite_db(csv_path: Path, sqlite_path: Path, append_mode: bool = False) -> int:
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(sqlite_path))
    try:
        if not append_mode:
            conn.execute("DROP TABLE IF EXISTS products_fts")
            conn.execute("DROP TABLE IF EXISTS products")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                sku                   TEXT PRIMARY KEY,
                internal_lbs_sku      TEXT,
                brand                 TEXT,
                h1                    TEXT,
                category              TEXT,
                upc                   TEXT,
                pdp_url               TEXT,
                spec_sheet_url        TEXT,
                wattage               REAL,
                lumens                REAL,
                voltage               REAL,
                color_temperature     REAL,
                base_type             TEXT,
                shape                 TEXT,
                dimmable              TEXT,
                finish                TEXT,
                pack_qty              REAL,
                bulb_or_fixture_type  TEXT,
                minimum_purchase_qty  REAL,
                custom_fields_json    TEXT,
                custom_fields_text    TEXT,
                product_description   TEXT
            )
            """
        )

        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)

        inserted = 0
        for row in rows:
            sku = (row.get("sku") or "").strip()
            if not sku:
                continue

            custom_fields_json = row.get("custom_fields_json") or ""
            custom_fields_text = flatten_custom_fields(custom_fields_json)

            product_description = strip_html(row.get("product_description_html") or "")[:2000]

            values = (
                sku,
                (row.get("internal_lbs_sku") or "").strip(),
                (row.get("brand") or "").strip(),
                (row.get("h1") or "").strip(),
                (row.get("category") or "").strip(),
                (row.get("upc") or "").strip(),
                (row.get("pdp_url") or "").strip(),
                (row.get("spec_sheet_url") or "").strip(),
                parse_numeric(row.get("wattage_actual") or ""),
                parse_numeric(row.get("lumens_actual") or ""),
                parse_numeric(row.get("voltage") or ""),
                parse_numeric(row.get("color_temperature") or ""),
                (row.get("base_type") or "").strip(),
                (row.get("shape") or "").strip(),
                (row.get("dimmable") or "").strip(),
                (row.get("finish") or "").strip(),
                parse_numeric(row.get("pack_qty") or ""),
                (row.get("bulb_or_fixture_type") or "").strip(),
                parse_numeric(row.get("minimum_purchase_qty") or ""),
                custom_fields_json,
                custom_fields_text,
                product_description,
            )

            conn.execute(
                """
                INSERT OR REPLACE INTO products (
                    sku, internal_lbs_sku, brand, h1, category, upc, pdp_url, spec_sheet_url,
                    wattage, lumens, voltage, color_temperature, base_type, shape, dimmable, finish,
                    pack_qty, bulb_or_fixture_type, minimum_purchase_qty,
                    custom_fields_json, custom_fields_text, product_description
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            inserted += 1

        conn.execute("DROP TABLE IF EXISTS products_fts")
        conn.execute(
            """
            CREATE VIRTUAL TABLE products_fts USING fts5(
                sku,
                internal_lbs_sku,
                h1,
                brand,
                category,
                base_type,
                shape,
                bulb_or_fixture_type,
                custom_fields_text,
                content='products',
                content_rowid='rowid'
            )
            """
        )

        conn.execute(
            """
            INSERT INTO products_fts(
                rowid, sku, internal_lbs_sku, h1, brand, category, base_type, shape, bulb_or_fixture_type, custom_fields_text
            )
            SELECT
                rowid, sku, internal_lbs_sku, h1, brand, category, base_type, shape, bulb_or_fixture_type, custom_fields_text
            FROM products
            """
        )

        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        return int(count)
    finally:
        conn.close()


def main() -> int:
    args = parse_args()

    load_dotenv()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set. Copy .env.example to .env and add your key.")

    chunks_path = Path(args.chunks)
    csv_path = Path(args.csv)
    chroma_dir = Path(args.chroma_dir)
    sqlite_path = Path(args.sqlite_path)

    if not chunks_path.exists():
        raise FileNotFoundError(f"Chunks file not found: {chunks_path}")
    if not csv_path.exists():
        raise FileNotFoundError(f"Parsed CSV not found: {csv_path}")

    chunks = load_chunks(chunks_path)
    if args.limit and args.limit > 0:
        chunks = chunks[: args.limit]

    print(f"Loaded chunks: {len(chunks)}")

    http_client = httpx.Client(
        proxy=None,
        trust_env=False,
        timeout=60.0,
    )
    openai_client = OpenAI(
        api_key=api_key,
        timeout=60.0,
        max_retries=0,
        http_client=http_client,
    )

    chroma_count = build_chroma_index(
        chunks=chunks,
        chroma_dir=chroma_dir,
        batch_size=args.batch_size,
        openai_client=openai_client,
        append_mode=args.append,
    )
    print(f"ChromaDB indexed items: {chroma_count}")

    sqlite_count = build_sqlite_db(csv_path=csv_path, sqlite_path=sqlite_path, append_mode=args.append)
    print(f"SQLite products rows: {sqlite_count}")

    print("Embedding + indexing complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
