# LBS AI RAG Project Brain (Bulbrite POC)

Last updated: 2026-03-13
Project owner: Eric
Working scope: Internal CS/Sales product answer copilot for lightbulbsurplus.com

## Collaboration Handoff
What changed:
- 2026-03-13: Executed `docs/eval_spec.md`. Added `data/eval/bulbrite_test_questions.csv` with 20 catalog-grounded questions (10 varied exact-SKU lookups + 10 spec-suggestion queries) and built `src/eval_runner.py` with proxy-safe judge client settings, structured constraint scoring, and timestamped CSV output. Verified with a 2-question smoke test, then ran the full eval: `data/eval/eval_results_20260313_151016.csv` finished at 17/20 pass overall (`7/10` Type A, `10/10` Type B, `20/20` generated). Remaining failures are exact-SKU answer misses for finish/voltage on SKUs `776201`, `771102`, and `772253`.
- 2026-03-13: Strengthened the popup SKU card button CSS in `src/app_streamlit.py` after manual browser feedback showed underlines still rendering. Updated selectors to target Streamlit markdown anchors directly (`[data-testid="stMarkdownContainer"] a.btn-*`) and forced `text-decoration: none !important` plus `border-bottom: none !important` / `box-shadow: none !important`.
- 2026-03-13: Fixed the local Streamlit/OpenAI proxy bug by setting `trust_env=False` on custom `httpx` clients in `src/query_service.py`, `src/test_llm_connection.py`, and `src/embed_and_index.py` (keeping `proxy=None`). Verified with `src/test_llm_connection.py` (4/4 pass), CLI query (`llm_status: generated`), and Streamlit `AppTest` (`llm_status: generated`, `semantic_count: 15`).
- 2026-03-13: Updated popup SKU card link-button CSS in `src/app_streamlit.py` so `Product Page` and `Spec Sheet` button labels no longer underline in normal, visited, hover, focus, or active states.
- 2026-03-12: Verified LLM answer generation is working end-to-end. Previous `APIConnectionError` blocker has resolved. Ran `test_llm_connection.py` (4/4 tests pass) and CLI query test (`llm_status: generated`). Fixed two remaining issues: (1) raised `OPENAI_TIMEOUT_SECONDS` from 12s to 30s (query was taking 9.7s, dangerously close), (2) fixed Windows `UnicodeEncodeError` on console output — gpt-5-mini returns Unicode chars (e.g. non-breaking hyphen U+2011) that cp1252 can't encode; added UTF-8 stdout reconfiguration to both `query_service.py` and `test_llm_connection.py`. Removed redundant `timeout=` kwarg from `responses.create()` (already set at client init level).
- 2026-03-12: Hardened `src/query_service.py` against intermittent OpenAI outages after app fallback report: added bounded OpenAI client timeout/retry settings, improved embedding and answer retry/backoff logic, made answer extraction resilient (`output_text` with structured fallback parsing), added LLM status metadata (`llm_status`, `llm_second_pass`), and prevented expensive second-pass LLM retry when the primary failure is a connection error.
- 2026-03-12: Executed `docs/debug_llm_connection_spec.md` investigation. Added debug logging in `src/query_service.py::generate_answer()`, created `src/test_llm_connection.py`, ran CLI and isolated connectivity tests, and confirmed repeated `APIConnectionError: Connection error` for both embeddings and Responses API calls in local environment. Updated `generate_answer()` to recommended Responses pattern (`instructions=...`, `input=raw_query`, return `resp.output_text`) so code is aligned once connectivity is restored.
- 2026-03-12: Reverted free-text chat input focus styling from black back to prior navy styling in `src/app_streamlit.py` per latest UI preference; kept sidebar button selector scoping/collapse-arrow fix intact.
- 2026-03-12: Applied additional Streamlit CSS hotfixes from QA: scoped sidebar button styling to sidebar `stButton` only (restores native sidebar collapse arrow styling/behavior), and strengthened chat input focus selectors (`textarea:focus-visible` + BaseWeb textarea container focus states) to force black focus outline instead of red.
- 2026-03-12: Applied follow-up Streamlit UI fixes from manual QA: changed chat input focus outline styling to black, updated sidebar `Clear Conversation` button to navy fill + white text with `#0a5299` hover, and switched clear action to callback-based state reset (`on_click`) for more reliable chat-thread clearing.
- 2026-03-12: Applied `docs/app_streamlit_v2_spec.md` UI refresh in `src/app_streamlit.py` (single global CSS injection after `SUPPORTS_BORDER` check, custom navy/electric-blue HTML link buttons in SKU cards, sidebar cleanup/removal of horizontal rules, and updated app title/subtitle text). Preserved query/session/error logic and existing `SUPPORTS_BORDER` behavior.
- 2026-02-25: Standardized shared-agent docs (`AGENTS.md`, `PROJECT_BRAIN.md`, `CLAUDE.md`).
- 2026-02-25: Migrated prior `claude.md` content into `PROJECT_BRAIN.md`.
- 2026-02-25: Added `src/export_prep.py` and validated it against `Bulbrite - AI RAG Schema Test.csv` (598 rows written).
- 2026-02-25: Added `src/parse_custom_fields.py` and generated `data/prepared/bulbrite_products_parsed.csv` (598 rows, 0 parse warnings).
- 2026-02-25: Added optional config-driven alias mapping via `config/field_alias_map.yaml` and wired `src/parse_custom_fields.py` to load brand alias profiles (`--brand`, `--alias-config`).
- 2026-03-05: Added `src/download_spec_sheets.py` with retries, skip/force behavior, deterministic naming, and CSV logging.
- 2026-03-05: Ran full Bulbrite download from `data/prepared/bulbrite_products_prepped.csv`; results: 598 processed, 593 downloaded, 5 skipped-existing, 0 failed. PDFs saved to `data/spec_pdfs/`, log at `data/spec_pdfs/download_log.csv`.
- 2026-03-05: Added `src/extract_spec_text.py` for native text extraction with OCR fallback.
- 2026-03-05: Aligned OCR setup with existing local reference script pattern using explicit `tesseract.exe` and Poppler paths.
- 2026-03-05: Ran full extraction with `.venv` + `--force`; results: 598 processed, 598 OK, 0 failed, methods = 54 `native` + 544 `ocr_only`. Outputs in `data/spec_text/` with log `data/spec_text/extraction_log.csv`.
- 2026-03-10: Full project audit by Claude Code. Confirmed `bulbrite_products_parsed_v2.csv` as canonical parsed output. Reference script (`REFERENCE_AI_upload_script_v2.3.1_toggle.py`) confirmed as historical reference only.
- 2026-03-10: Stack decisions locked: ChromaDB (local), OpenAI `text-embedding-3-small`, SQLite FTS5, `gpt-5-mini` (answer model).
- 2026-03-10: Renamed `export_prep_bulbrite.py` -> `export_prep.py`. Removed hardcoded spec URL fallback. Made `internal_lbs_sku` optional. Script is now brand-generic.
- 2026-03-10: Added `.gitignore`, `.env.example`, `README.md` for git readiness. Added `chromadb` to `requirements.txt`.
- 2026-03-10: Established agent workflow: Claude Code for planning/architecture, Codex for execution/tests.
- 2026-03-10: Added `src/build_chunks.py` per `docs/build_chunks_spec.md` and generated `data/chunks/chunks.jsonl`.
- 2026-03-10: Chunk build validation passed: 1196 total chunks (`598 sku_record`, `598 spec_sheet`), all lines valid JSON with `text`+`metadata`, all `chunk_id` values unique, no HTML tags in chunk text, deterministic output confirmed by byte-identical reruns.
- 2026-03-11: Added `src/embed_and_index.py` per `docs/embed_and_index_spec.md` (ChromaDB + SQLite + FTS5, idempotent reindex, metadata sanitization, embedding batch retry/progress logic, `.env` key validation).
- 2026-03-11: Installed `chromadb` in `.venv` to satisfy runtime dependency.
- 2026-03-11: Execution smoke test run with `--limit 10` confirms expected fail-fast behavior when `OPENAI_API_KEY` is missing in repo `.env`.
- 2026-03-11: After `.env` key setup, ran `embed_and_index.py --limit 10` smoke test successfully (10 embeddings indexed, SQLite built with 598 rows).
- 2026-03-11: Ran full `embed_and_index.py` successfully and reran for idempotency; final counts stable at ChromaDB `1196` and SQLite `598`.
- 2026-03-11: Verified post-index checks: no `None` values in sampled Chroma metadata, FTS search works (`132507`, `NOS25T6`), structured SQL filter query returns expected results, semantic retrieval smoke query returns relevant recessed/dimmable Bulbrite chunks.
- 2026-03-11: Built `src/query_service.py` (Claude Code, architecture-level work). Hybrid retrieval: semantic (ChromaDB) + keyword (FTS5) + structured (SQL WHERE). Merge/re-rank with scoring system. Answer generation via `gpt-5-mini`. Brand is parameterized (`--brand`, default Bulbrite). Price filtering deferred (data absent, changes too frequently for embedding). API notes: gpt-5-mini does not support `temperature` or `max_tokens` params -- uses `max_completion_tokens` only.
- 2026-03-11: Tested query_service.py end-to-end: SKU lookup ("Is SKU 132507 dimmable?" -> correct), semantic search ("dimmable LED flood for recessed cans" -> relevant downlights), constraint search ("3000K dimmable E26 LED" -> 98 matches filtered, top 5 returned with links). All answers grounded with PDP/spec URLs.
- 2026-03-11: Added `src/app_streamlit.py` per `docs/app_streamlit_spec.md` (logo sidebar, brand selector, persistent chat thread, query_service wiring, SKU cards, evidence expander, clear conversation).
- 2026-03-11: Installed `streamlit` in `.venv`; `app_streamlit.py` compiles. Browser-launch verification remains manual due local machine permission prompt interruption.
- 2026-03-11: Updated `app_streamlit.py` to use new logo file `LBS Clear Logo.png`.
- 2026-03-11: Removed manual brand dropdown from app UI; query now runs with `brand=None` (search all indexed brands). Sidebar now displays detected indexed brands from SQLite as informational status.
- 2026-03-11: Patched `query_service.py` semantic channel to fail open on embedding API connection errors. If semantic embedding fails, query now continues with FTS + structured retrieval instead of raising and breaking the app flow.
- 2026-03-11: Patched `query_service.py` answer-generation path to fail open on OpenAI connection errors and return retrieval-backed fallback text instead of surfacing a hard failure in chat.
- 2026-03-11: Fixed syntax bug in `build_fallback_answer()` regex branch and verified runtime behavior via CLI for both `brand=Bulbrite` and `brand=None` (all-brands mode): query `"How many watts is the BULR-776218"` now resolves to SKU `776218` with `7.0W` fallback answer when model calls fail.

Current status:
- Sprint 1 complete: export prep + custom field parsing done.
- Sprint 2 complete: spec sheet download + text extraction done. All 598 SKUs have extracted text.
- Sprint 2 final step complete: chunk generation is done and validated.
- Sprint 3 core indexing complete: embeddings/vector store + SQLite/FTS index built and validated (historical). Proxy-safe OpenAI client settings (`proxy=None`, `trust_env=False`) are now applied in indexing and diagnostic scripts to avoid local Windows WPAD auto-proxy issues.
- Sprint 3 query service complete: hybrid retrieval + re-ranking is working; answer generation via gpt-5-mini Responses API is confirmed working end-to-end (tested 2026-03-12, `llm_status: generated`). Includes bounded retries/backoff + graceful failover to retrieval-backed fallback during outages.
- Sprint 3 app layer implemented: Streamlit UI code is complete with all-brands search behavior; v2 UI/UX styling refresh plus follow-up QA fixes are applied (navy chat-input focus styling restored, navy-filled clear button, callback-based clear behavior, scoped sidebar button styling that no longer affects collapse arrow, and popup Product Page/Spec Sheet buttons now use stronger anchor-specific no-underline CSS). Local Streamlit/OpenAI proxy issue is fixed in code and verified via Streamlit `AppTest`; manual browser verification still pending.
- Sprint 4 eval foundation complete: `src/eval_runner.py` and `data/eval/bulbrite_test_questions.csv` are in place. Full run `data/eval/eval_results_20260313_151016.csv` scored `17/20` pass overall, with spec-suggestion performance at `10/10` pass and three remaining exact-SKU misses tied to finish/voltage answers.
- Agent workflow established: Claude Code (planning/structure) + Codex (execution/tests).

Next steps:
- Initialize git repo and push to remote.
- Restart Streamlit app and rerun manual browser verification checklist, including follow-up checks (chat input focus uses restored navy styling, sidebar collapse arrow appears/behaves normally, clear button uses navy fill + matching hover, clear conversation reliably resets thread, Product Page/Spec Sheet popup buttons show no underline, and AI answers render with `llm_status: generated` in the debug panel).
- Re-test Streamlit app flow with a few known questions (e.g., SKU 776218 wattage, SKU 132507 dimmable) and confirm the live browser session matches the passing Streamlit `AppTest`.
- Verify OpenAI dashboard/org settings (active key, model access limits for `gpt-5-mini`, billing/usage) to rule out account-level access constraints.
- Add feedback logging controls to app (Helpful/Wrong/Missing) in follow-up.
- Review the three exact-SKU eval failures (`776201` finish, `771102` voltage, `772253` finish) and trace why answer generation is dropping facts present in the canonical parsed data.
- Expand the eval set from the current catalog-grounded starter set to real CS/sales questions once owner examples are available.
- Spike: investigate automatic brand-intent routing/filtering in `query_service.py` (detect brand mentions in user query and apply explicit brand filter when confidence is high; compare against current all-brands default behavior).
- Phase 2 idea: URL-based similarity search — detect product page URL in query, fetch + parse specs, reformulate as structured similarity search against index. Currently NOT supported; app only searches local ChromaDB/SQLite.

Open decisions:
- 2026-03-12 resilience pass: decide whether to keep diagnostic `retrieval_meta` fields (`llm_status`, `llm_second_pass`) permanently for app telemetry.
- Confirm BigCommerce export column contract and naming edge cases.
- Confirm if first pass should cover all Bulbrite SKUs or a constrained category subset.
- Answer model upgrade path (start `gpt-5-mini`, evaluate for `gpt-5.4` if needed).
- Brand routing strategy: keep always-all-brands retrieval vs add automatic brand filtering when users mention a brand.

## Pricing Architecture Decision (2026-03-11)
Pricing will NOT be embedded in chunks. Prices change frequently and embedding them would require constant re-indexing. Instead, pricing will be pulled separately via the BigCommerce API and stored in a dedicated SQLite table (`prices`) that the query service can JOIN against at runtime -- fully decoupled from the chunk/embedding pipeline.

Design intent:
- `src/sync_prices.py` calls the BigCommerce Products API and upserts current prices into `db/products.sqlite`.
- `query_service.py` JOINs against this table when answering price-related queries.
- Chunks and embeddings never need to be regenerated when prices change.
- This is a Phase 2 / post-POC feature -- not blocking the current build.

## UI Theming Note (Post-POC)
After POC quality bar is met, apply LBS brand theming in Streamlit UI:
- Electric Blue: `#0eb5fd`
- Navy: `#063c6e`
Scope:
- Sidebar/header accents, button/link styling, card accents, and consistent contrast-safe text colors.

Implementation specifics (Phase 2):
- BC API endpoint: `GET /v3/catalog/products?limit=250&page=N&include_fields=sku,price,sale_price`
- Scale: ~30K SKUs, ~120 paginated API calls, < 2 min runtime.
- Price logic: use `sale_price` when non-zero, else fall back to `price`.
- Target table: `prices (sku TEXT PRIMARY KEY, price REAL, sale_price REAL, updated_at TEXT)` in `db/products.sqlite`.
- Required `.env` credentials: `BC_STORE_HASH`, `BC_CLIENT_ID`, `BC_ACCESS_TOKEN`.
- Schedule: nightly cron or on-demand manual run -- does not block POC or any pipeline script.

Blockers:
- ~~2026-03-12: Local OpenAI connectivity blocker~~ — RESOLVED 2026-03-12. Both embeddings and Responses API calls now succeed. `test_llm_connection.py` passes 4/4 tests. Timeout raised from 12s to 30s for safety margin.
- ~~2026-03-13: Local Streamlit/OpenAI proxy blocker~~ - code fix applied and validated via CLI, `test_llm_connection.py`, and Streamlit `AppTest`; live browser verification still pending after restart.

## 1) North Star
Build a reliable internal AI assistant that answers product questions using real LBS product data, returning grounded answers with SKU candidates and evidence links/snippets.

POC target:
- Brand: Bulbrite only
- Data sources: structured product export + custom fields + spec sheet PDFs
- UX: fast rep workflow (question -> answer + SKU matches + proof)

## 2) Current State Snapshot
What exists now:
- PRD/tech reference: `AI Product Query POC Concept and PRD.txt`
- Structured dataset: `Bulbrite - AI RAG Schema Test.csv` (598 rows)
- Normalized prepared CSV: `data/prepared/bulbrite_products_prepped.csv` (598 rows)
- Canonical parsed CSV: `data/prepared/bulbrite_products_parsed_v2.csv` (598 rows, 25 columns including 10 normalized technical fields)
- Downloaded spec PDFs: `data/spec_pdfs/` (598 PDFs total; deterministic `BULR-{sku}.pdf` naming)
- Extracted spec text: `data/spec_text/` (598 .txt + 598 .json metadata files; 54 native, 544 OCR)
- Chroma vector index: `db/chroma/` collection `lbs_chunks` (1196 vectors)
- SQLite structured store: `db/products.sqlite` (`products` = 598 rows, `products_fts` ready)
- Config: `config/field_alias_map.yaml` (brand-specific field alias profiles)
- Git readiness: `.gitignore`, `.env.example`, `README.md`
- Pipeline scripts 1-4 complete in `src/`
- Reference script (historical only): `REFERENCE_AI_upload_script_v2.3.1_toggle.py`

What is not yet implemented:
- Streamlit feedback logging (core app UI exists; feedback controls/logging pending)
- Broader real-world eval set sourced from actual CS/sales questions (current starter eval set is catalog-grounded)
- Prompt templates (`prompts/` directory)

## 3) POC Scope (Hard Boundaries)
In scope:
- Bulbrite-only retrieval and answering
- Hybrid retrieval (keyword + vector)
- Source-grounded answers with evidence
- Basic internal Streamlit UI
- Feedback capture (Helpful/Wrong/Missing)

Out of scope for v1:
- Live inventory integration
- Multi-brand runtime routing
- Teams/Gorgias integration
- Quote/cart automation
- Production auth/SSO hardening

## 4) Success Criteria (POC Exit)
Functional:
- Handles 20-30 real CS/sales questions with useful accuracy
- Returns SKU(s) plus evidence snippets for supported queries
- Faster than manual lookup for common tasks

Technical:
- End-to-end pipeline runs reproducibly
- Retrieval uses both SKU records and spec-sheet chunks
- Architecture is reusable for adding another brand with mapping work

Usability:
- Reps can use with minimal training
- Feedback loop is captured and reviewable

## 5) Source-of-Truth Priority Rules
When facts conflict:
1. Parsed structured custom fields / product row data
2. Spec sheet text
3. Product description
4. H1/title

Rule intent:
- Prevent ambiguous PDF matrix text from overriding cleaner product-level facts
- Preserve trust in technical answers

## 6) Canonical Product Model (Minimum)
Required per SKU object:
- sku
- brand
- product_name_h1
- description_html_or_text
- custom_fields_raw
- custom_fields_json
- pdp_url
- spec_sheet_url
- price (if available)
- active_status (if available)
- category/product_type (if available)

Normalized technical subset (when extractable):
- wattage_actual
- lumens_actual
- voltage
- cct/color_temperature
- base_type
- shape
- dimmable
- finish
- pack_qty
- bulb_or_fixture_type

## 7) Retrieval Architecture (POC)
Chunk types:
- Type A: one SKU master chunk per SKU (highest utility)
- Type B: 1..N spec-sheet chunks per SKU (section-based preferred)

Each chunk metadata should include:
- chunk_id
- sku
- brand
- doc_type (`sku_record` | `spec_sheet`)
- source_url
- product_type (if available)
- source_priority
- chunk_label
- optional: price, active_status

Runtime retrieval behavior:
- Enforce `brand=Bulbrite` filter in POC
- Combine exact/keyword matching (SKUs/model terms) + vector search
- Re-rank merged candidates
- Generate answer only from retrieved context

## 8) Implementation Plan (POC)
Pipeline scripts:
1. `src/export_prep.py`
2. `src/parse_custom_fields.py`
3. `src/download_spec_sheets.py`
4. `src/extract_spec_text.py`
5. `src/build_chunks.py`
6. `src/embed_and_index.py`
7. `src/query_service.py`
8. `src/app_streamlit.py`
9. `src/eval_runner.py`

Recommended order:
- Sprint 1: scripts 1-2 + eval question set seed
- Sprint 2: scripts 3-5
- Sprint 3: scripts 6-8
- Sprint 4: script 9 + pilot + fixes

## 9) Proposed Repository Layout
```text
./
  README.md
  docs/
  data/raw/
  data/prepared/
  data/spec_pdfs/
  data/spec_text/
  data/chunks/
  data/eval/
  data/app/
  db/
  src/
  src/utils/
  prompts/
  config/
```

## 10) Decision Log (Track as You Build)
Use this section for concrete, dated decisions.

- [x] 2026-03-10: Vector DB choice: **ChromaDB** (local, persistent, zero-infra)
- [x] 2026-03-10: Embedding model: **OpenAI `text-embedding-3-small`**
- [x] 2026-03-10: Keyword search method: **SQLite FTS5**
- [x] 2026-03-10: Answer model: **OpenAI `gpt-5-mini`** (upgrade to `gpt-5.4` after eval if needed)
- [x] 2026-03-10: Canonical parsed CSV: **`bulbrite_products_parsed_v2.csv`** (non-v2 is stale)
- [x] 2026-03-10: Reference script role: historical reference only, not part of RAG pipeline
- [ ] Confirm exact spec sheet URL naming edge cases
- [ ] Confirm final BigCommerce export columns template
- [ ] Decide initial category coverage (all Bulbrite vs subset)

## 11) Working Backlog (Execution Checklist)
Data prep:
- [x] Create raw data ingestion contract and column map
- [x] Build Bulbrite-only filter and clean prep output (`export_prep.py`)
- [x] Build robust custom field parser with malformed-string handling (`parse_custom_fields.py`)
- [x] Add alias map for field normalization (`config/field_alias_map.yaml`)

Docs and text:
- [x] Download all reachable spec PDFs (`download_spec_sheets.py` -- 598/598 available locally; 593 newly downloaded + 5 pre-existing)
- [x] Add retry + failure log for missing PDFs (`download_log.csv`)
- [x] Implement PDF extraction with OCR fallback path (`extract_spec_text.py` -- 598/598)
- [ ] Validate extraction quality on representative sample (91% OCR -- needs audit)

Retrieval/index:
- [x] Build deterministic chunk generator with metadata (`build_chunks.py` -> `data/chunks/chunks.jsonl`, 1196 chunks)
- [x] Embed chunks and persist vector index (`embed_and_index.py` -> Chroma `lbs_chunks`, 1196 vectors)
- [x] Build structured SKU table (SQLite) + FTS5 (`db/products.sqlite`, 598 rows)
- [x] Implement hybrid retrieval + re-ranking (`query_service.py` -- semantic + FTS + structured, brand-parameterized)

App and eval:
- [x] Build Streamlit UI with answer + SKU cards + evidence (`app_streamlit.py`; manual browser verification pending)
- [ ] Add feedback logging pipeline
- [x] Build eval runner + starter catalog-grounded eval set (`src/eval_runner.py`, `data/eval/bulbrite_test_questions.csv`, full run = `17/20` pass on 2026-03-13)
- [ ] Run failure analysis and patch retrieval/prompting gaps (current exact-SKU misses: `776201` finish, `771102` voltage, `772253` finish)

## 12) Definition of Done (POC)
POC is done when:
- End-to-end pipeline is runnable from raw CSV to queryable app
- App returns grounded answers with SKU suggestions and evidence links
- Evaluation set is executed and reviewed
- Known limitations are documented
- Next-step plan for Phase 2 is documented

## 13) Risks and Mitigations
Risk: inconsistent custom field formatting
- Mitigation: tolerant parser + alias map + raw preservation

Risk: noisy OCR text from PDFs
- Mitigation: prioritize structured fields, label low-confidence extract outputs

Risk: users expect inventory/ETA certainty in v1
- Mitigation: explicit UI scope note and uncertainty language

Risk: overbuilding for multi-brand before shipping POC
- Mitigation: keep runtime Bulbrite-only, design adapter seams now

## 14) POC -> Full Project Path
Phase 2 evolution path:
- Add brand adapters to canonical schema
- Reuse retrieval and answer orchestration layer
- Add brand routing/preference logic
- Introduce inventory snapshots/live feeds as filters/annotations
- Add Teams/Gorgias integration once core accuracy is stable

## 15) Operating Notes for Jumping In/Out
When returning to project:
1. Read this file first (`PROJECT_BRAIN.md`)
2. Check Decision Log and Working Backlog statuses
3. Run/validate pipeline from the earliest incomplete stage
4. Use eval set before changing model choices
5. Avoid expanding scope until POC quality bar is met

Execution principles:
- Structured data first, PDFs second
- Retrieval quality before model upgrades
- Evidence always visible to user
- Multi-brand-ready design, single-brand deployment

## 16) Debugging Protocol

**Purpose:** Prevent agents (and humans) from going in circles on the same bug. Every non-trivial debug investigation must leave a trace here.

### Rules
1. Before starting a fix, check the Debug Log below. If the same symptom was already investigated, read what was tried and what was learned — do NOT re-try the same approach.
2. After investigating, record what you tried and the outcome IMMEDIATELY, even if you didn't fix it.
3. Distinguish between **confirmed root cause** and **hypothesis**. Mark each clearly.
4. Always capture the **actual error type and message** before proposing a fix. Never guess.
5. When CLI works but Streamlit doesn't (or vice versa), the difference is in the execution context, not the code logic. Check: module caching, CWD/paths, stdout encoding, .env loading, import order.

### Diagnostic Tools Available
- `src/test_llm_connection.py` — isolated OpenAI API test (embeddings + Responses API)
- `logs/query_service.log` — file-based error log (written by `query_service.py`, survives stdout issues)
- Streamlit debug panel — in-app `llm_status` + `llm_error` display (expander under each answer)
- CLI test: `.venv/Scripts/python src/query_service.py --query "..." --brand Bulbrite --verbose`

### Debug Log

| Date | Symptom | What was tried | Outcome | Root cause |
|------|---------|---------------|---------|------------|
| 2026-03-12 | generate_answer() always returns empty, fallback text shown | Added debug print in except block, created test_llm_connection.py, ran CLI test | CLI test showed `APIConnectionError: Connection error` on all API calls (embeddings + responses) | **Confirmed:** Local network/API connectivity issue — not a code bug. Resolved on its own by 2026-03-12 evening. |
| 2026-03-12 | test_llm_connection.py Test 2b fails with `UnicodeEncodeError` | Added `sys.stdout.reconfigure(encoding="utf-8")` | Fixed — all 4 tests pass | **Confirmed:** Windows cp1252 console can't encode U+2011 (non-breaking hyphen) returned by gpt-5-mini |
| 2026-03-13 | CLI works (llm_status=generated) but Streamlit still shows fallback | Added in-app diagnostic panel + file logging. Diagnostic panel showed `APIConnectionError`, `semantic_count: 0`. Log stack trace revealed `httpcore/_sync/http_proxy.py` + `WinError 10061: target machine actively refused`. Checked env vars, `.env`, and registry proxy settings; none explained it. Confirmed `proxy=None` alone was insufficient. Then set `trust_env=False` on custom `httpx.Client(...)` instances used by `query_service.py`, `test_llm_connection.py`, and `embed_and_index.py`, and re-ran CLI, the isolated OpenAI test, and Streamlit `AppTest`. | Fixed: `src/test_llm_connection.py` now passes 4/4, CLI query returns `llm_status: generated`, and Streamlit `AppTest` returns `llm_status: generated` with `semantic_count: 15`. Recent log entries show successful `generate_answer` calls after the client change. | **Confirmed:** The default OpenAI/httpx client path was still trusting Windows WPAD/auto-proxy settings; `proxy=None` by itself did not fully disable that behavior. The effective fix is `proxy=None` plus `trust_env=False`. |

