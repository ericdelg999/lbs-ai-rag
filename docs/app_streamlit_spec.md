# app_streamlit.py -- PRD + Technical Requirements Document

## Context

This is script 8 of 9 in the LBS AI RAG pipeline. It is the user-facing Streamlit web app that wraps `query_service.py` in a chat interface for CS/sales reps.

**What exists upstream:**
- `src/query_service.py` -- Complete. Exposes a `query()` function that returns a `QueryResult` dataclass with `answer_text`, `top_skus`, `evidence_chunks`, `parsed_query`, and `retrieval_meta`.
- `db/chroma/` -- ChromaDB vector index (1196 chunks)
- `db/products.sqlite` -- SQLite product table + FTS5 (598 rows)
- `.env` -- Contains `OPENAI_API_KEY` (loaded by query_service)
- `Incon Lighting AI Logo.png` -- Project root, 25KB PNG, navy blue brand logo

**What this script produces:**
- A Streamlit web app at `http://localhost:8501`
- Persistent chat thread (full conversation visible, like ChatGPT)
- No feedback buttons in this version (future feature)

---

## Objective

Build a single-file Streamlit app (~200-250 lines) that:
1. Shows the Incon Lighting logo in the sidebar
2. Lets reps type product questions in a chat input bar
3. Displays answers in a persistent conversation thread
4. Renders SKU result cards with spec summary + clickable PDP/spec links
5. Shows a collapsible "Source Evidence" section per answer
6. Has a brand selector in the sidebar (Bulbrite only for now)
7. Has a clear conversation button

---

## Layout

```
┌─ SIDEBAR ──────────────────┐  ┌─ MAIN ──────────────────────────────────┐
│ [Incon Lighting Logo]      │  │  💡 Incon Lighting Product Assistant    │
│                            │  │  Ask a product question...              │
│ Brand                      │  │                                         │
│ [Bulbrite        ▼]        │  │  👤 You:                                │
│                            │  │  Is SKU 132507 dimmable?                │
│ [🗑️ Clear Conversation]    │  │                                         │
│                            │  │  🤖 Assistant:                          │
│ ────────────────────────── │  │  Yes, SKU 132507 is dimmable...         │
│ ℹ️ Showing Bulbrite data.  │  │                                         │
│ Prices not available.      │  │  ┌─────────────────────────────────┐    │
└────────────────────────────┘  │  │ Bulbrite 132507 - 25W T6 ...    │    │
                                │  │ 25W · 90 lm · 2700K · E12       │    │
                                │  │ Dimmable: Yes                   │    │
                                │  │ [🔗 Product Page] [📄 Spec]     │    │
                                │  └─────────────────────────────────┘    │
                                │                                         │
                                │  ▶ View Source Evidence                 │
                                │                                         │
                                │  ┌─ Ask a product question ──────────┐  │
                                │  └───────────────────────────────────┘  │
                                └─────────────────────────────────────────┘
```

---

## File to Create

`src/app_streamlit.py`

**Run command (from project root):**
```bash
streamlit run src/app_streamlit.py
```

**Prerequisite:** Streamlit must be installed first:
```bash
.venv/Scripts/pip install streamlit
```

---

## Dependencies

All already in `.venv` except Streamlit:
- `streamlit` (install first)
- `query_service` (local import from `src/`)
- `pathlib`, `sys` (stdlib)

---

## Full Script Structure

### 1. Imports and path setup

```python
import sys
from pathlib import Path

# Ensure src/ is on the path so query_service can be imported
sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
import query_service
from query_service import QueryResult
```

**Important:** `sys.path.insert` must happen before the `import query_service` line.

### 2. Page config (MUST be the very first Streamlit call)

```python
st.set_page_config(
    page_title="Incon Lighting AI",
    page_icon="💡",
    layout="wide",
)
```

### 3. Helper: render SKU cards

```python
def _render_sku_cards(top_skus: list) -> None:
    """Render a bordered card for each top SKU result."""
    if not top_skus:
        return

    for sku_info in top_skus:
        with st.container(border=True):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(f"**{sku_info.get('h1', sku_info.get('sku', ''))}**")
                specs = []
                if sku_info.get("wattage") is not None:
                    specs.append(f"{sku_info['wattage']}W")
                if sku_info.get("lumens") is not None:
                    specs.append(f"{int(sku_info['lumens'])} lm")
                if sku_info.get("color_temperature") is not None:
                    specs.append(f"{int(sku_info['color_temperature'])}K")
                if sku_info.get("base_type"):
                    specs.append(sku_info["base_type"])
                if sku_info.get("dimmable"):
                    specs.append(f"Dimmable: {sku_info['dimmable']}")
                st.caption(" · ".join(specs) if specs else "Specs not available")
            with col2:
                if sku_info.get("pdp_url"):
                    st.link_button("🔗 Product Page", sku_info["pdp_url"])
                if sku_info.get("spec_sheet_url"):
                    st.link_button("📄 Spec Sheet", sku_info["spec_sheet_url"])
```

**Note on `st.container(border=True)`:** This requires Streamlit ≥ 1.28. If the installed version is older, fall back to `st.container()` without the `border` kwarg and use `st.markdown("---")` to visually separate cards.

### 4. Helper: render evidence expander

```python
def _render_evidence(evidence_chunks: list) -> None:
    """Render a collapsed expander with source evidence snippets."""
    if not evidence_chunks:
        return
    with st.expander("View Source Evidence", expanded=False):
        for chunk in evidence_chunks[:6]:  # cap to avoid wall of text
            label = (
                "📋 Product Record"
                if chunk.get("doc_type") == "sku_record"
                else "📄 Spec Sheet"
            )
            st.markdown(f"**{label} — SKU {chunk.get('sku', '')}**")
            st.text(chunk.get("text_snippet", ""))
            st.markdown("---")
```

### 5. Sidebar

```python
# Logo -- path is relative to project root (where streamlit is launched from)
logo_path = Path(__file__).parent.parent / "Incon Lighting AI Logo.png"
if logo_path.exists():
    st.sidebar.image(str(logo_path), use_container_width=True)

st.sidebar.markdown("---")

brand = st.sidebar.selectbox(
    "Brand",
    options=["Bulbrite"],   # Append new brands here once indexed
    index=0,
)

if st.sidebar.button("🗑️ Clear Conversation"):
    st.session_state.messages = []
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.caption(
    f"Showing **{brand}** product data only.\n\nPrices are not available in this dataset."
)
```

**Logo path note:** Use `Path(__file__).parent.parent / "Incon Lighting AI Logo.png"` so the path resolves correctly regardless of where the script is launched from.

### 6. Session state initialization

```python
if "messages" not in st.session_state:
    st.session_state.messages = []
# Each message dict: {"role": "user"|"assistant", "content": str, "result": QueryResult|None}
```

### 7. Page header

```python
st.title("💡 Incon Lighting Product Assistant")
st.caption("Ask a product question — SKU lookups, spec comparisons, recommendations.")
```

### 8. Render conversation history

```python
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and msg.get("result") is not None:
            _render_sku_cards(msg["result"].top_skus)
            _render_evidence(msg["result"].evidence_chunks)
```

### 9. Chat input and query flow

```python
if prompt := st.chat_input("Ask a product question..."):
    # Append and display user message
    st.session_state.messages.append({"role": "user", "content": prompt, "result": None})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Run retrieval + answer generation
    with st.chat_message("assistant"):
        with st.spinner("Searching product database..."):
            result = query_service.query(raw_query=prompt, brand=brand)

        st.markdown(result.answer_text)
        _render_sku_cards(result.top_skus)
        _render_evidence(result.evidence_chunks)

    # Persist assistant message
    st.session_state.messages.append(
        {"role": "assistant", "content": result.answer_text, "result": result}
    )
```

---

## Input / Output Contract

**Input:** User types a natural language question in `st.chat_input`.

**What gets called:**
```python
result = query_service.query(raw_query=prompt, brand=brand)
```

**What gets displayed per assistant message:**
- `result.answer_text` → `st.markdown()` (main answer)
- `result.top_skus` → `_render_sku_cards()` (one bordered card per SKU)
- `result.evidence_chunks` → `_render_evidence()` (collapsed expander)

**QueryResult fields used:**

| Field | Used for |
|-------|---------|
| `answer_text` | Main answer text (markdown) |
| `top_skus[].h1` | Card title |
| `top_skus[].wattage` | Spec line |
| `top_skus[].lumens` | Spec line |
| `top_skus[].color_temperature` | Spec line |
| `top_skus[].base_type` | Spec line |
| `top_skus[].dimmable` | Spec line |
| `top_skus[].pdp_url` | "Product Page" link button |
| `top_skus[].spec_sheet_url` | "Spec Sheet" link button |
| `evidence_chunks[].doc_type` | Label (Product Record vs Spec Sheet) |
| `evidence_chunks[].sku` | Evidence header |
| `evidence_chunks[].text_snippet` | Evidence body text |

---

## Success Criteria

### Functional
1. `streamlit run src/app_streamlit.py` starts without errors after `pip install streamlit`.
2. The Incon Lighting logo appears in the sidebar.
3. The brand dropdown shows "Bulbrite" and defaults to it.
4. Typing a question and pressing Enter displays the question in the chat thread and triggers a spinner.
5. After the spinner, the answer appears as an assistant message.
6. SKU cards appear below the answer with product name, spec summary, and working links.
7. "View Source Evidence" expander is collapsed by default; clicking it shows text snippets.
8. The conversation thread persists -- previous Q&As remain visible as new questions are asked.
9. The "Clear Conversation" button wipes the thread and the page refreshes cleanly.
10. Logo path resolves correctly regardless of whether `streamlit run` is invoked from the project root or from within `src/`.

### UX
11. At least 3 test queries work end-to-end (see Verification section).
12. No visible Python errors or tracebacks on the page.

---

## Verification Steps

```bash
# 1. Install streamlit
.venv/Scripts/pip install streamlit

# 2. Launch the app from the project root
streamlit run src/app_streamlit.py

# 3. In the browser (http://localhost:8501), test:

# Test 1: SKU lookup
# Query: "Is SKU 132507 dimmable?"
# Expected: Answer says "Yes", one SKU card for 132507, PDP + spec links present

# Test 2: Multi-constraint recommendation
# Query: "Need a 3000K dimmable E26 LED"
# Expected: Answer lists matching SKUs, up to 5 cards, each with links

# Test 3: Natural language semantic query
# Query: "dimmable LED flood light for recessed cans"
# Expected: Returns recessed downlights, answer mentions relevant SKUs

# Test 4: Multi-turn conversation
# Ask query 1, then ask query 2 -- both Q&A pairs should be visible in the thread

# Test 5: Clear conversation
# Click "Clear Conversation" -- thread should empty, fresh state

# 4. Confirm sidebar:
#    - Incon Lighting logo visible
#    - Brand dropdown shows "Bulbrite"
#    - Disclaimer text visible

# 5. Confirm evidence expander:
#    - Collapsed by default
#    - Opens to show text snippets with doc_type labels
```

---

## Important Notes for the Implementing Agent

1. **Read `PROJECT_BRAIN.md` before starting.** It has full project context.
2. **`st.set_page_config()` must be the very first Streamlit call** in the script -- before any `st.sidebar`, `st.title`, etc. Python will raise a `StreamlitAPIException` if this order is violated.
3. **Logo path:** Use `Path(__file__).parent.parent / "Incon Lighting AI Logo.png"` (not a relative string like `"../Incon Lighting AI Logo.png"`). Check existence before calling `st.sidebar.image()` so the app doesn't crash if the file is missing.
4. **`query_service` import:** The `sys.path.insert(0, str(Path(__file__).parent))` line is needed because `src/app_streamlit.py` imports `src/query_service.py`. Without this, Python won't find the sibling module when launched with `streamlit run`.
5. **`st.container(border=True)`** requires Streamlit ≥ 1.28. Check the version at runtime:
   ```python
   import streamlit as st
   # Use border=True only if supported
   try:
       with st.container(border=True):
           pass
       SUPPORTS_BORDER = True
   except TypeError:
       SUPPORTS_BORDER = False
   ```
   Or simply try it -- if it fails, remove the `border=True` kwarg.
6. **Do not use pandas or any heavy imports.** The app only needs `streamlit`, `sys`, `pathlib`, and `query_service`.
7. **After completing the script, update `PROJECT_BRAIN.md`** with: what changed, current status, and next steps.
8. **The `.env` file is loaded by `query_service`** -- no need to load it in `app_streamlit.py`.
9. **Do not hardcode the brand.** The brand value comes from the sidebar `selectbox`. Pass it to `query_service.query(brand=brand)`.

---

## Existing Code Patterns to Follow

| File | Pattern to follow |
|------|------------------|
| `src/query_service.py` | `query()` function signature and `QueryResult` fields |
| `src/embed_and_index.py` | `.env` loading, pathlib usage, `if __name__ == "__main__"` structure |
| `docs/embed_and_index_spec.md` | Format reference for this spec doc |
