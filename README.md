# LBS AI Product Answer Copilot (Bulbrite POC)

Internal AI-powered product Q&A tool for lightbulbsurplus.com. Answers CS/sales product questions using structured BigCommerce export data + spec sheet PDFs, returning grounded answers with SKU matches and evidence.

**Current scope:** Bulbrite brand only (598 SKUs). Architecture is designed for multi-brand expansion.

## Prerequisites

**To run the app (Streamlit):**
- **Python 3.11+**
- **OpenAI API key** — For embeddings (`text-embedding-3-small`) and answer generation (`gpt-5-mini`)

**To run the full data pipeline (steps 1–6) locally:**
- **Tesseract OCR** — [Install guide](https://github.com/tesseract-ocr/tesseract)
- **Poppler** — Required for `pdf2image`. [Windows binaries](https://github.com/oschwartz10612/poppler-windows/releases)

## Setup

```bash
# 1. Clone the repo
git clone <repo-url>
cd "LBS AI RAG Project"

# 2. Create virtual environment
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # macOS/Linux

# 3. Install app dependencies
pip install -r requirements.txt

# For running the full data pipeline (steps 1-6), also install:
# pip install -r requirements_pipeline.txt

# 4. Configure environment
copy .env.example .env
# Edit .env with your OpenAI API key and local tool paths
```

## Running the App

```bash
# Local
.venv/Scripts/streamlit run src/app_streamlit.py
```

**Streamlit Cloud:** Connect this repo, set `OPENAI_API_KEY` as a secret, and set the main file path to `src/app_streamlit.py`. The `db/` index is committed to the repo so no local pipeline run is needed.

---

## Pipeline (run in order)

The system is built as a sequential pipeline. Each script reads the output of the previous one.

| Step | Script | What it does |
|------|--------|-------------|
| 1 | `src/export_prep.py` | Filters raw BigCommerce CSV by brand, normalizes headers, constructs URLs |
| 2 | `src/parse_custom_fields.py` | Parses custom field strings into JSON, normalizes 10 technical attributes |
| 3 | `src/download_spec_sheets.py` | Downloads spec sheet PDFs with retries and logging |
| 4 | `src/extract_spec_text.py` | Extracts text from PDFs (native + OCR fallback) |
| 5 | `src/build_chunks.py` | Combines SKU records + spec text into chunk objects |
| 6 | `src/embed_and_index.py` | Embeds chunks into ChromaDB + builds SQLite product table |
| 7 | `src/query_service.py` | Hybrid retrieval + re-ranking + LLM answer generation |
| 8 | `src/app_streamlit.py` | Streamlit UI with answer, SKU cards, and evidence |
| 9 | `src/eval_runner.py` | Repeatable evaluation harness (17/20 pass on initial run) |

### Running a script

All scripts use CLI args with sensible defaults:

```bash
python src/export_prep.py --input "Bulbrite - AI RAG Schema Test.csv" --brand Bulbrite
python src/parse_custom_fields.py
python src/download_spec_sheets.py
python src/extract_spec_text.py
```

Use `--help` on any script for full argument details.

## Folder Structure

```
.
├── src/                    # Pipeline scripts (1-9)
├── config/                 # Brand-specific field alias maps
│   └── field_alias_map.yaml
├── data/
│   ├── prepared/           # Normalized CSVs (tracked in git)
│   ├── spec_pdfs/          # Downloaded PDFs (not tracked -- regenerate via step 3)
│   ├── spec_text/          # Extracted text + JSON metadata (not tracked -- regenerate via step 4)
│   ├── chunks/             # Chunk JSONL (tracked)
│   └── eval/               # Test questions + eval results (tracked)
├── db/                     # ChromaDB + SQLite (not tracked -- regenerate via step 6)
├── prompts/                # LLM prompt templates (when created)
├── AGENTS.md               # Multi-agent collaboration protocol
├── PROJECT_BRAIN.md        # Living project status and decisions
├── requirements.txt        # Python dependencies
└── .env.example            # Environment variable template
```

## Tech Stack (POC)

| Component | Choice |
|-----------|--------|
| Language | Python 3.11+ |
| Vector DB | ChromaDB (local, persistent) |
| Embeddings | OpenAI `text-embedding-3-small` |
| Keyword search | SQLite FTS5 |
| Answer model | OpenAI `gpt-5-mini` (upgradeable) |
| UI | Streamlit |
| PDF extraction | PyMuPDF + Tesseract OCR fallback |

## Adding a New Brand

The pipeline is designed for multi-brand expansion:

1. **Export prep:** Run `src/export_prep.py --input your_export.csv --brand "BrandName"` (same script for all brands)
2. **Alias config:** Add a brand profile to `config/field_alias_map.yaml` (maps field name variations to canonical names)
3. **Parse + download + extract:** Run pipeline steps 2-4 with the brand's prepared CSV
4. **Chunk + embed:** Run steps 5-6 to add the brand's data to the shared ChromaDB + SQLite index
5. **Query service** automatically picks up new brand data via metadata filters

## Source Priority Rules

When facts conflict across sources, trust in this order:
1. Parsed structured custom fields / product row data
2. Spec sheet text
3. Product description
4. H1/title

## Project Docs

- [PRD + Tech Spec](AI%20Product%20Query%20POC%20Concept%20and%20PRD.txt) -- Full requirements and architecture
- [PROJECT_BRAIN.md](PROJECT_BRAIN.md) -- Living project status, decisions, and backlog
- [AGENTS.md](AGENTS.md) -- Multi-agent workflow protocol
