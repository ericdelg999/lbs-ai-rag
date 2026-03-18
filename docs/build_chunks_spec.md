# build_chunks.py -- PRD + Technical Requirements Document

## Context

This is script 5 of 9 in the LBS AI RAG pipeline. It sits between the completed data preparation stage (scripts 1-4) and the upcoming embedding/indexing stage (script 6).

**What exists upstream:**
- `data/prepared/bulbrite_products_parsed_v2.csv` -- 598 rows, 25 columns. One row per SKU with structured product data + parsed custom fields + 10 normalized technical attributes.
- `data/spec_text/*.txt` -- 598 text files extracted from spec sheet PDFs. Naming: `BULR-{sku}.txt`. All files are 691-1873 bytes (median 1105 bytes). Single page each.
- `data/spec_text/*.json` -- 598 metadata files with extraction details per SKU.

**What this script produces:**
- `data/chunks/chunks.jsonl` -- One JSON object per line. Each object is a chunk ready for embedding.

**What consumes the output:**
- `embed_and_index.py` (script 6) will read `chunks.jsonl`, call the OpenAI embedding API, and store results in ChromaDB + SQLite.

---

## Objective

Create a deterministic, re-runnable script that combines structured product records and spec sheet text into chunk objects with rich metadata. The chunks are the atomic units of retrieval -- everything the RAG system searches over.

---

## Chunk Types

### Type A: SKU Master Record (one per SKU)

The most important chunk type. Contains all structured product data for a single SKU, rendered as clean readable text. This chunk should be able to answer most common CS/sales questions on its own.

**Text format (render in this exact structure):**

```
SKU: {sku}
Brand: {brand}
Product: {h1}
Category: {category}
UPC: {upc}
PDP: {pdp_url}
Spec Sheet: {spec_sheet_url}
Min Purchase Qty: {minimum_purchase_qty}

Technical Specifications:
- Wattage: {wattage_actual}
- Lumens: {lumens_actual}
- Voltage: {voltage}
- Color Temperature: {color_temperature}
- Base Type: {base_type}
- Shape: {shape}
- Dimmable: {dimmable}
- Finish: {finish}
- Pack Qty: {pack_qty}
- Type: {bulb_or_fixture_type}

Custom Fields:
{flattened custom_fields_json as "Key: Value" lines}

Description:
{product_description_html stripped to plain text, truncated to 1500 chars}
```

**Rules:**
- Omit any technical spec line where the value is empty/null. Do not print "Wattage: " with no value.
- For `custom_fields_json`: parse the JSON string, render each key-value pair as `- {Key}: {Value}`. Skip fields already covered by the technical specs section (wattage, lumens, voltage, color_temperature, base_type, shape, dimmable, finish, pack_qty, bulb_or_fixture_type) to avoid duplication.
- For `product_description_html`: strip all HTML tags to produce plain text. Collapse whitespace. Truncate to 1500 characters if longer. This prevents bloating the chunk with marketing copy.
- If a SKU has no custom fields and no technical specs, still create the chunk with whatever data exists.

**Metadata:**
```json
{
  "chunk_id": "{sku}_sku_record",
  "sku": "{sku}",
  "internal_lbs_sku": "{internal_lbs_sku}",
  "brand": "{brand}",
  "doc_type": "sku_record",
  "chunk_label": "master",
  "source_url": "{pdp_url}",
  "spec_sheet_url": "{spec_sheet_url}",
  "source_priority": 1,
  "category": "{category}",
  "upc": "{upc}",
  "wattage": {numeric or null},
  "lumens": {numeric or null},
  "voltage": {numeric or null},
  "color_temperature": {numeric or null},
  "base_type": "{string or null}",
  "dimmable": "{Yes/No or null}",
  "price": null,
  "minimum_purchase_qty": {numeric or null}
}
```

### Type B: Spec Sheet Text (one per SKU, if spec text exists)

Contains the raw extracted text from the spec sheet PDF. Provides supplementary detail that may not be in the structured data (dimensions, compliance info, ordering codes, application suggestions).

**Text format:**

Use the spec text file content as-is. Strip the `[PAGE N]` markers. Trim leading/trailing whitespace.

Since all Bulbrite spec text files are under 2KB (~200-400 tokens), there is **no need to split them into multiple chunks**. One chunk per spec text file is correct for this dataset. If a future brand produces spec text files larger than 1500 tokens, the script should split them with 200-token overlap, but for now this is not needed.

**Metadata:**
```json
{
  "chunk_id": "{sku}_spec_sheet_0",
  "sku": "{sku}",
  "internal_lbs_sku": "{internal_lbs_sku}",
  "brand": "{brand}",
  "doc_type": "spec_sheet",
  "chunk_label": "spec_full",
  "source_url": "{spec_sheet_url}",
  "spec_sheet_url": "{spec_sheet_url}",
  "source_priority": 2,
  "category": "{category}",
  "upc": null,
  "wattage": null,
  "lumens": null,
  "voltage": null,
  "color_temperature": null,
  "base_type": null,
  "dimmable": null,
  "price": null,
  "minimum_purchase_qty": null
}
```

**Rules:**
- Only create a Type B chunk if the corresponding `.txt` file exists in `data/spec_text/` AND has more than 50 characters of content after stripping page markers.
- The `chunk_id` suffix `_0` is for future-proofing (if splitting becomes needed, chunks would be `_0`, `_1`, `_2`, etc.).

---

## Output Format

File: `data/chunks/chunks.jsonl`

Each line is a JSON object with exactly two top-level keys:

```json
{"text": "the chunk text content", "metadata": { ... metadata object ... }}
```

**Ordering:** All Type A chunks first (sorted by SKU ascending), then all Type B chunks (sorted by SKU ascending). This is not functionally required but makes the file easy to inspect.

---

## Input Files

| File | Format | Key Columns/Fields |
|------|--------|-------------------|
| `data/prepared/bulbrite_products_parsed_v2.csv` | CSV, UTF-8, 25 columns | sku, internal_lbs_sku, brand, h1, product_description_html, category, upc, pdp_url, spec_sheet_url, custom_fields_raw, custom_fields_json, wattage_actual, lumens_actual, voltage, color_temperature, base_type, shape, dimmable, finish, pack_qty, bulb_or_fixture_type, minimum_purchase_qty |
| `data/spec_text/{internal_lbs_sku}.txt` | Plain text, UTF-8 | Full extracted spec text with `[PAGE N]` markers |
| `data/spec_text/{internal_lbs_sku}.json` | JSON | Extraction metadata (method_used, total_char_count, etc.) |

---

## CLI Interface

```
python src/build_chunks.py [OPTIONS]

Options:
  --input         Path to parsed CSV. Default: data/prepared/bulbrite_products_parsed_v2.csv
  --spec-text-dir Path to spec text directory. Default: data/spec_text
  --output        Path to output JSONL. Default: data/chunks/chunks.jsonl
  --brand         Optional brand filter (only process rows matching this brand). Default: process all rows.
  --max-desc-chars  Max characters for description text. Default: 1500
  --limit         Process only first N rows (for testing). Default: 0 (all)
```

---

## Script Structure

```
src/build_chunks.py
  parse_args()
  strip_html(html_text) -> str           # Remove HTML tags, collapse whitespace
  parse_numeric(value) -> float | None   # "60W" -> 60.0, "" -> None
  load_spec_text(spec_dir, internal_lbs_sku) -> str | None
  build_sku_record_chunk(row) -> dict    # Type A
  build_spec_text_chunk(row, spec_text) -> dict | None  # Type B
  main()
```

**Dependencies:** Standard library only (`csv`, `json`, `re`, `argparse`, `pathlib`, `html`). No external packages needed.

---

## Success Criteria

### Functional

1. Running `python src/build_chunks.py` produces `data/chunks/chunks.jsonl` with no errors.
2. Output contains exactly 598 Type A chunks (one per SKU in the CSV).
3. Output contains one Type B chunk for each SKU that has a matching `.txt` file in `data/spec_text/` with >50 chars of content.
4. Every line in the JSONL is valid JSON with exactly `{"text": "...", "metadata": {...}}`.
5. Every `metadata` object contains all fields listed in the metadata spec above (null values are acceptable for optional fields).
6. Every `chunk_id` is unique across the entire file.
7. No HTML tags appear in any `text` field.
8. The `source_priority` field is `1` for all Type A chunks and `2` for all Type B chunks.
9. Running the script twice with the same inputs produces byte-identical output (deterministic).

### Data Quality

10. Type A chunk text includes all non-empty technical spec fields for a SKU.
11. Type A chunk text does NOT duplicate fields already in the Technical Specifications section within the Custom Fields section.
12. Type B chunk text does not contain `[PAGE N]` markers.
13. Metadata numeric fields (`wattage`, `lumens`, `voltage`, `color_temperature`, `minimum_purchase_qty`) are actual numbers (float/int) or null -- never strings.

### Edge Cases

14. SKU 773299 (accessory with no technical specs): Type A chunk should still be created with product info + custom fields. No technical specs section should appear (or appear empty).
15. If a spec text file is missing for a SKU, only a Type A chunk is created. No error is raised.
16. If `custom_fields_json` is empty or invalid JSON, the Custom Fields section is omitted. No error is raised.

---

## Verification Steps

After implementation, run these checks:

```bash
# 1. Run the script
python src/build_chunks.py

# 2. Check output exists and has content
wc -l data/chunks/chunks.jsonl
# Expected: ~1196 lines (598 Type A + up to 598 Type B)

# 3. Validate every line is valid JSON
python -c "
import json
with open('data/chunks/chunks.jsonl') as f:
    for i, line in enumerate(f, 1):
        obj = json.loads(line)
        assert 'text' in obj and 'metadata' in obj, f'Line {i}: missing text or metadata'
        assert obj['metadata'].get('chunk_id'), f'Line {i}: missing chunk_id'
print(f'All {i} lines are valid JSON with required fields.')
"

# 4. Check chunk type counts
python -c "
import json
from collections import Counter
counts = Counter()
with open('data/chunks/chunks.jsonl') as f:
    for line in f:
        obj = json.loads(line)
        counts[obj['metadata']['doc_type']] += 1
print(dict(counts))
# Expected: {'sku_record': 598, 'spec_sheet': ~598}
"

# 5. Check uniqueness of chunk_ids
python -c "
import json
ids = []
with open('data/chunks/chunks.jsonl') as f:
    for line in f:
        ids.append(json.loads(line)['metadata']['chunk_id'])
assert len(ids) == len(set(ids)), f'Duplicate chunk_ids found! {len(ids)} total, {len(set(ids))} unique'
print(f'All {len(ids)} chunk_ids are unique.')
"

# 6. Check no HTML in text fields
python -c "
import json, re
with open('data/chunks/chunks.jsonl') as f:
    for i, line in enumerate(f, 1):
        text = json.loads(line)['text']
        if re.search(r'<[a-zA-Z/][^>]*>', text):
            print(f'WARNING: HTML found in line {i}')
            break
    else:
        print('No HTML tags found in any chunk text.')
"

# 7. Check determinism (run twice, compare)
python src/build_chunks.py --output /tmp/chunks_run1.jsonl
python src/build_chunks.py --output /tmp/chunks_run2.jsonl
diff /tmp/chunks_run1.jsonl /tmp/chunks_run2.jsonl && echo "Deterministic: outputs match"

# 8. Spot-check a specific SKU (132507 - has good spec data)
python -c "
import json
with open('data/chunks/chunks.jsonl') as f:
    for line in f:
        obj = json.loads(line)
        if obj['metadata']['sku'] == '132507':
            print(f\"--- {obj['metadata']['chunk_id']} ---\")
            print(obj['text'][:500])
            print()
"
```

---

## Sample Expected Output

### Type A chunk for SKU 132507:
```json
{
  "text": "SKU: 132507\nBrand: Bulbrite\nProduct: Bulbrite 132507 - 25W T6 Clear Thread E12 120V\nCategory: ...\nUPC: 739698132754\nPDP: https://lightbulbsurplus.com/...\nSpec Sheet: https://lightbulbsurplus.com/content/BULR-132507.pdf\nMin Purchase Qty: ...\n\nTechnical Specifications:\n- Wattage: 25\n- Lumens: 90\n- Voltage: 120\n- Color Temperature: 2700K\n- Base Type: E12\n- Shape: T6\n- Dimmable: Yes\n- Finish: Clear\n- Pack Qty: 1\n- Type: ...\n\nCustom Fields:\n- Model Number: 132507\n- Product Name: ...\n- Ordering Code: ...\n\nDescription:\n...",
  "metadata": {
    "chunk_id": "132507_sku_record",
    "sku": "132507",
    "internal_lbs_sku": "BULR-132507",
    "brand": "Bulbrite",
    "doc_type": "sku_record",
    "chunk_label": "master",
    "source_url": "https://lightbulbsurplus.com/...",
    "spec_sheet_url": "https://lightbulbsurplus.com/content/BULR-132507.pdf",
    "source_priority": 1,
    "category": "...",
    "upc": "739698132754",
    "wattage": 25.0,
    "lumens": 90.0,
    "voltage": 120.0,
    "color_temperature": 2700.0,
    "base_type": "E12",
    "dimmable": "Yes",
    "price": null,
    "minimum_purchase_qty": 0
  }
}
```

### Type B chunk for SKU 132507:
```json
{
  "text": "TECHNICAL SPECIFICATION SHEET\nNostalgic Collection\n25W T6 CLEAR THREAD E12 120V\nItem# 132507\nOrdering Code 25T6/SQ/E12\nUPC 739698132754\n...",
  "metadata": {
    "chunk_id": "132507_spec_sheet_0",
    "sku": "132507",
    "internal_lbs_sku": "BULR-132507",
    "brand": "Bulbrite",
    "doc_type": "spec_sheet",
    "chunk_label": "spec_full",
    "source_url": "https://lightbulbsurplus.com/content/BULR-132507.pdf",
    "spec_sheet_url": "https://lightbulbsurplus.com/content/BULR-132507.pdf",
    "source_priority": 2,
    "category": "...",
    "upc": null,
    "wattage": null,
    "lumens": null,
    "voltage": null,
    "color_temperature": null,
    "base_type": null,
    "dimmable": null,
    "price": null,
    "minimum_purchase_qty": null
  }
}
```

---

## Important Notes for the Implementing Agent

1. **Read `PROJECT_BRAIN.md` before starting.** It has the full project context.
2. **Do not use pandas.** Use `csv.DictReader` for consistency with existing scripts in `src/`.
3. **Do not use external NLP libraries** for HTML stripping. Use `re.sub` or `html` standard library.
4. **Match the coding style** of the existing scripts (`src/export_prep.py`, `src/parse_custom_fields.py`): argparse CLI, pathlib for paths, dataclasses where helpful, print-based logging.
5. **The `custom_fields_json` column** contains a JSON string (already parsed by script 2). Parse it with `json.loads()`. It may be empty string or malformed -- handle gracefully.
6. **Numeric parsing for metadata:** `wattage_actual` contains values like `"25"`, `"10.3"`, or `""`. Parse to float where possible, null otherwise. Same for lumens, voltage, color_temperature, minimum_purchase_qty.
7. **The script must create `data/chunks/` directory** if it doesn't exist.
8. **After completing the script, update `PROJECT_BRAIN.md`** with: what changed, current status, and next steps.
9. **Test with `--limit 5` first** to verify output format before running on all 598 rows.

---

## Existing Code Patterns to Follow

See these files for established conventions:
- `src/export_prep.py` -- CLI arg pattern, CSV reading, pathlib usage
- `src/parse_custom_fields.py` -- JSON handling, field normalization, parse_args style
- `src/extract_spec_text.py` -- File I/O patterns, dataclass usage, logging
