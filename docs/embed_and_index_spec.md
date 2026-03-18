# embed_and_index.py -- PRD + Technical Requirements Document

## Context

This is script 6 of 9 in the LBS AI RAG pipeline. It sits between the completed chunking stage (script 5) and the upcoming query/retrieval stage (script 7).

**What exists upstream:**
- `data/chunks/chunks.jsonl` -- 1196 JSON objects (598 Type A "sku_record" + 598 Type B "spec_sheet"). Each object has `{"text": "...", "metadata": {...}}`.
- `data/prepared/bulbrite_products_parsed_v2.csv` -- 598 rows, 25 columns. The canonical structured product data.

**What this script produces:**
- A ChromaDB persistent collection at `db/chroma/` containing all 1196 chunk embeddings with metadata.
- A SQLite database at `db/products.sqlite` containing a structured product table for filtering on numeric/categorical fields, plus an FTS5 virtual table for keyword search.

**What consumes the output:**
- `query_service.py` (script 7) will query ChromaDB for semantic search, SQLite for structured filtering and keyword search, then merge/re-rank results before passing to the answer model.

---

## Objective

Create a re-runnable script that:
1. Reads all chunks from `chunks.jsonl`
2. Generates embeddings via the OpenAI API
3. Stores chunks + embeddings + metadata in a ChromaDB persistent collection
4. Builds a structured SQLite product table from the parsed CSV (for filtering on specs like wattage, lumens, price)
5. Builds a SQLite FTS5 virtual table for keyword/exact-match search (SKU, model number, product name)

After this script runs, the data is fully searchable -- both semantically (vector) and structurally (SQL).

---

## Architecture Overview

```
chunks.jsonl ──► OpenAI Embedding API ──► ChromaDB (db/chroma/)
                                           • 1196 vectors (1536 dimensions each)
                                           • metadata per chunk
                                           • text per chunk
                                           • collection name: "lbs_chunks"

parsed CSV ────► SQLite (db/products.sqlite)
                   • Table: products (598 rows, filterable columns)
                   • FTS5 table: products_fts (keyword search)
```

The two stores serve different purposes at query time:
- **ChromaDB** answers: "what chunks are semantically similar to this question?"
- **SQLite products table** answers: "which SKUs have wattage < 10 AND lumens > 500?"
- **SQLite FTS5** answers: "which SKUs match the exact term 'NOS25T6' or 'E12'?"

---

## Part 1: ChromaDB Vector Store

### Collection Setup

- **Collection name:** `lbs_chunks`
- **Persist directory:** `db/chroma/`
- **Distance metric:** cosine (ChromaDB default)
- **Embedding model:** OpenAI `text-embedding-3-small` (1536 dimensions)

### What gets stored per chunk

ChromaDB stores three things per record:
- **id:** The `chunk_id` from metadata (e.g. `"108040_sku_record"`, `"108040_spec_sheet_0"`)
- **embedding:** The 1536-float vector from OpenAI
- **document:** The chunk `text` field
- **metadata:** A flat dict of metadata fields (see below)

### ChromaDB metadata fields

ChromaDB metadata values must be `str`, `int`, `float`, or `bool`. No `None` values allowed -- ChromaDB will reject them. Convert nulls as follows:

| Field | Type | Null handling |
|-------|------|---------------|
| `sku` | str | Required, never null |
| `internal_lbs_sku` | str | `""` if null |
| `brand` | str | `""` if null |
| `doc_type` | str | Required (`"sku_record"` or `"spec_sheet"`) |
| `chunk_label` | str | Required |
| `source_url` | str | `""` if null |
| `spec_sheet_url` | str | `""` if null |
| `source_priority` | int | Required (1 or 2) |
| `category` | str | `""` if null |
| `upc` | str | `""` if null |
| `wattage` | float | Omit from metadata if null (do NOT store) |
| `lumens` | float | Omit from metadata if null (do NOT store) |
| `voltage` | float | Omit from metadata if null (do NOT store) |
| `color_temperature` | float | Omit from metadata if null (do NOT store) |
| `base_type` | str | `""` if null |
| `dimmable` | str | `""` if null |
| `price` | float | Omit from metadata if null |
| `minimum_purchase_qty` | float | Omit from metadata if null |

**Why omit numeric nulls instead of using a sentinel?** ChromaDB `where` filters (`{"wattage": {"$lte": 10}}`) would incorrectly match a sentinel value like `-1` or `0`. Omitting the key entirely means those chunks are simply excluded from numeric filter queries, which is the correct behavior.

### Embedding API Calls

- **Model:** `text-embedding-3-small`
- **Batch size:** 100 chunks per API call (the API accepts up to 2048, but 100 keeps request size manageable and provides good progress visibility)
- **Rate limiting:** Add a 0.5-second sleep between batches to stay well within OpenAI rate limits
- **Error handling:** If a batch fails, retry up to 3 times with 2-second backoff. If still failing, log the error and abort (do not silently skip chunks)

### Idempotent Behavior

The script must support clean re-runs:
- **Delete and recreate** the collection on each run. This is simpler and safer than upsert logic for a dataset this small (1196 chunks, ~$0.01 per run).
- Print a clear warning when deleting an existing collection: `"Deleting existing collection 'lbs_chunks' (N items). Re-indexing from scratch."`

---

## Part 2: SQLite Structured Product Table

### Database

- **Path:** `db/products.sqlite`
- **Behavior on re-run:** Drop and recreate all tables (same idempotent approach as ChromaDB).

### Table: `products`

One row per SKU (598 rows), sourced from `bulbrite_products_parsed_v2.csv`.

```sql
CREATE TABLE products (
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
    product_description   TEXT
);
```

**Column notes:**
- `wattage`, `lumens`, `voltage`, `color_temperature`, `pack_qty`, `minimum_purchase_qty` are REAL to support numeric comparison queries (`WHERE wattage <= 10`).
- Parse these from the CSV the same way `build_chunks.py` does: extract first numeric value from the string, or NULL if empty.
- `product_description` should be the HTML-stripped plain text from `product_description_html`, truncated to 2000 chars.
- `custom_fields_json` is stored as-is (raw JSON string) for display purposes.

### Table: `products_fts` (FTS5 Virtual Table)

For keyword/exact-match search on text fields:

```sql
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
);
```

**`custom_fields_text`:** Flatten the `custom_fields_json` into a single searchable string. Format: `"Key1: Value1 | Key2: Value2 | ..."`. This makes model numbers, ordering codes, and other custom field values searchable via FTS.

After creating the FTS table, populate it:
```sql
INSERT INTO products_fts(rowid, sku, internal_lbs_sku, h1, brand, category, base_type, shape, bulb_or_fixture_type, custom_fields_text)
SELECT rowid, sku, internal_lbs_sku, h1, brand, category, base_type, shape, bulb_or_fixture_type, custom_fields_text FROM products;
```

**Note:** This means `products` table needs a `custom_fields_text` column too, or you generate it inline. Simplest approach: add a `custom_fields_text TEXT` column to the `products` table and populate it during insert.

Update the CREATE TABLE to include:
```sql
    custom_fields_text    TEXT,
```

---

## Input Files

| File | Format | Used For |
|------|--------|----------|
| `data/chunks/chunks.jsonl` | JSONL, UTF-8 | ChromaDB: text + metadata + embeddings |
| `data/prepared/bulbrite_products_parsed_v2.csv` | CSV, UTF-8, 25 columns | SQLite: structured product table |

---

## CLI Interface

```
python src/embed_and_index.py [OPTIONS]

Options:
  --chunks        Path to chunks JSONL. Default: data/chunks/chunks.jsonl
  --csv           Path to parsed product CSV. Default: data/prepared/bulbrite_products_parsed_v2.csv
  --chroma-dir    Path to ChromaDB persist directory. Default: db/chroma
  --sqlite-path   Path to SQLite database file. Default: db/products.sqlite
  --batch-size    Embedding API batch size. Default: 100
  --limit         Process only first N chunks (for testing). Default: 0 (all)
```

---

## Script Structure

```
src/embed_and_index.py
  parse_args()
  load_chunks(path) -> List[dict]                    # Read chunks.jsonl
  sanitize_metadata(metadata) -> dict                # Convert nulls for ChromaDB
  embed_batch(texts, client) -> List[List[float]]    # Call OpenAI embedding API
  build_chroma_index(chunks, chroma_dir, batch_size)  # Main ChromaDB logic
  parse_numeric(value) -> float | None               # Same as build_chunks.py
  strip_html(html_text) -> str                       # Same as build_chunks.py
  flatten_custom_fields(json_str) -> str             # JSON -> "Key: Val | Key: Val"
  build_sqlite_db(csv_path, sqlite_path)             # Main SQLite logic
  main()
```

### Dependencies

```
openai          # OpenAI API client
chromadb        # Vector database
python-dotenv   # Load .env for API key
```

Plus standard library: `csv`, `json`, `argparse`, `pathlib`, `sqlite3`, `re`, `html`, `os`, `time`.

### Environment Setup

This is the first script in the pipeline that requires an API key. Load it from `.env`:

```python
from dotenv import load_dotenv
load_dotenv()

# In main():
api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    raise RuntimeError("OPENAI_API_KEY not set. Copy .env.example to .env and add your key.")
```

---

## Detailed Behavior

### ChromaDB Flow

```python
# 1. Load chunks
chunks = load_chunks(args.chunks)  # List of {"text": ..., "metadata": ...}

# 2. Initialize ChromaDB client
client = chromadb.PersistentClient(path=args.chroma_dir)

# 3. Delete existing collection if present
existing = client.list_collections()
if "lbs_chunks" in [c.name for c in existing]:
    count = client.get_collection("lbs_chunks").count()
    print(f"Deleting existing collection 'lbs_chunks' ({count} items). Re-indexing from scratch.")
    client.delete_collection("lbs_chunks")

# 4. Create fresh collection
collection = client.create_collection(
    name="lbs_chunks",
    metadata={"hnsw:space": "cosine"}
)

# 5. Embed and add in batches
for batch in batched(chunks, batch_size):
    texts = [c["text"] for c in batch]
    ids = [c["metadata"]["chunk_id"] for c in batch]
    metadatas = [sanitize_metadata(c["metadata"]) for c in batch]
    embeddings = embed_batch(texts, openai_client)
    collection.add(
        ids=ids,
        documents=texts,
        embeddings=embeddings,
        metadatas=metadatas
    )
```

### SQLite Flow

```python
# 1. Create/overwrite database
conn = sqlite3.connect(args.sqlite_path)
conn.execute("DROP TABLE IF EXISTS products_fts")
conn.execute("DROP TABLE IF EXISTS products")

# 2. Create products table
conn.execute("""CREATE TABLE products (...)""")

# 3. Read CSV rows, parse, insert
with open(csv_path) as f:
    reader = csv.DictReader(f)
    for row in reader:
        # parse_numeric for wattage, lumens, etc.
        # strip_html for description
        # flatten_custom_fields for custom_fields_text
        conn.execute("INSERT INTO products VALUES (...)", values)

# 4. Create FTS5 table and populate
conn.execute("""CREATE VIRTUAL TABLE products_fts USING fts5(...)""")
conn.execute("""INSERT INTO products_fts(...) SELECT ... FROM products""")

conn.commit()
conn.close()
```

---

## Success Criteria

### Functional

1. Running `python src/embed_and_index.py` completes without errors.
2. ChromaDB collection `lbs_chunks` exists at `db/chroma/` with exactly 1196 items.
3. Every ChromaDB record has a non-empty document, a 1536-dimension embedding, and metadata.
4. SQLite `products` table at `db/products.sqlite` has exactly 598 rows.
5. SQLite `products_fts` table exists and is queryable.
6. Running the script twice produces the same results (idempotent -- deletes and recreates).
7. The script loads `OPENAI_API_KEY` from `.env` and fails with a clear error if not set.
8. Progress is printed during embedding (e.g. `"Embedding batch 3/12 (300/1196 chunks)"`)

### Data Quality

9. ChromaDB metadata contains no `None` values (ChromaDB would reject them).
10. Numeric metadata fields in ChromaDB (`wattage`, `lumens`, etc.) are floats, not strings.
11. ChromaDB metadata numeric fields are omitted (not present as keys) when the source value is null.
12. SQLite numeric columns (`wattage`, `lumens`, `voltage`, `color_temperature`) contain REAL values or NULL -- never strings like `"120V"`.
13. SQLite `product_description` column contains no HTML tags.
14. SQLite FTS keyword search for `"132507"` returns the matching SKU row.
15. SQLite FTS keyword search for `"NOS25T6"` (a model number in custom fields) returns the matching SKU row.

### Integration

16. ChromaDB semantic search works: querying with `"dimmable LED flood light"` returns relevant chunks.
17. SQLite structured query works: `SELECT sku, wattage, lumens FROM products WHERE wattage <= 10 AND lumens >= 500` returns expected results.
18. No chunks are silently dropped -- the total count in ChromaDB must match the input JSONL line count.

---

## Verification Steps

After implementation, run these checks:

```bash
# 1. Run the script
python src/embed_and_index.py

# 2. Verify ChromaDB collection count
python -c "
import chromadb
client = chromadb.PersistentClient(path='db/chroma')
coll = client.get_collection('lbs_chunks')
print(f'ChromaDB collection count: {coll.count()}')
# Expected: 1196
"

# 3. Verify ChromaDB metadata has no None values
python -c "
import chromadb
client = chromadb.PersistentClient(path='db/chroma')
coll = client.get_collection('lbs_chunks')
sample = coll.get(limit=10, include=['metadatas'])
for i, m in enumerate(sample['metadatas']):
    for k, v in m.items():
        assert v is not None, f'Record {i}: {k} is None'
print('No None values in sampled metadata.')
"

# 4. Verify ChromaDB semantic search works
python -c "
import chromadb
from openai import OpenAI
import os
from dotenv import load_dotenv
load_dotenv()
oc = OpenAI()
client = chromadb.PersistentClient(path='db/chroma')
coll = client.get_collection('lbs_chunks')
q = 'dimmable LED flood light for recessed cans'
emb = oc.embeddings.create(input=[q], model='text-embedding-3-small').data[0].embedding
results = coll.query(query_embeddings=[emb], n_results=5, include=['documents','metadatas'])
for i, (doc, meta) in enumerate(zip(results['documents'][0], results['metadatas'][0])):
    print(f'{i+1}. [{meta[\"sku\"]}] {meta[\"doc_type\"]} -- {doc[:80]}...')
"

# 5. Verify SQLite row count
python -c "
import sqlite3
conn = sqlite3.connect('db/products.sqlite')
count = conn.execute('SELECT COUNT(*) FROM products').fetchone()[0]
print(f'SQLite products count: {count}')
# Expected: 598
"

# 6. Verify SQLite FTS search
python -c "
import sqlite3
conn = sqlite3.connect('db/products.sqlite')
# Search by SKU
rows = conn.execute(\"SELECT sku, h1 FROM products_fts WHERE products_fts MATCH '132507'\").fetchall()
print(f'FTS search for 132507: {rows}')
# Search by model number from custom fields
rows = conn.execute(\"SELECT sku, h1 FROM products_fts WHERE products_fts MATCH 'NOS25T6'\").fetchall()
print(f'FTS search for NOS25T6: {rows}')
"

# 7. Verify SQLite structured query
python -c "
import sqlite3
conn = sqlite3.connect('db/products.sqlite')
rows = conn.execute('SELECT sku, wattage, lumens FROM products WHERE wattage <= 10 AND lumens >= 500 ORDER BY lumens DESC').fetchall()
print(f'SKUs with wattage<=10 AND lumens>=500: {len(rows)} results')
for r in rows[:5]:
    print(f'  SKU {r[0]}: {r[1]}W, {r[2]} lumens')
"

# 8. Verify idempotency (run again, check same counts)
python src/embed_and_index.py
python -c "
import chromadb, sqlite3
client = chromadb.PersistentClient(path='db/chroma')
print(f'ChromaDB: {client.get_collection(\"lbs_chunks\").count()}')
conn = sqlite3.connect('db/products.sqlite')
print(f'SQLite: {conn.execute(\"SELECT COUNT(*) FROM products\").fetchone()[0]}')
"
```

---

## Cost Estimate

- **1196 chunks, ~529K tokens total**
- **text-embedding-3-small: $0.02 per 1M tokens**
- **Estimated cost per full run: ~$0.01**
- Each re-run costs the same (the embedding API is called every time since we delete and recreate)

---

## Important Notes for the Implementing Agent

1. **Read `PROJECT_BRAIN.md` before starting.** It has the full project context.
2. **Load the API key from `.env` using `python-dotenv`.** This is the first script in the pipeline that calls an external API. The `.env` file must have `OPENAI_API_KEY=sk-...`.
3. **ChromaDB does not accept `None` in metadata.** This is the most common bug. You must either convert nulls to empty strings (for string fields) or omit the key entirely (for numeric fields). Test this explicitly.
4. **Reuse `parse_numeric` and `strip_html` logic** from `build_chunks.py`. You can copy the functions or import them -- copying is fine for now.
5. **Do not use pandas.** Use `csv.DictReader` for CSV reading, consistent with all other scripts.
6. **The CSV is UTF-8** (with potential BOM). Open with `encoding="utf-8-sig"`.
7. **Create `db/` directory** if it doesn't exist (use `pathlib.Path.mkdir(parents=True, exist_ok=True)`).
8. **Print clear progress** during embedding batches. The script takes about 30-60 seconds to run. Users need to know it's working.
9. **After completing the script, update `PROJECT_BRAIN.md`** with: what changed, current status, and next steps.
10. **Test with `--limit 10` first** to verify the full flow works before running on all 1196 chunks. This avoids burning API calls on a broken script.

---

## Existing Code Patterns to Follow

See these files for established conventions:
- `src/build_chunks.py` -- `parse_numeric()`, `strip_html()`, CLI arg pattern, `main()` structure
- `src/export_prep.py` -- CSV reading with `utf-8-sig`, pathlib usage
- `.env.example` -- Shows the expected environment variable names
