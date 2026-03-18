#!/usr/bin/env python3
"""Streamlit chat UI for the LBS AI product assistant."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Ensure sibling modules in src/ are importable before importing query_service.
sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
import query_service
from query_service import QueryResult


st.set_page_config(
    page_title="Incon Lighting AI",
    page_icon="💡",
    layout="wide",
)

# st.container(border=True) requires Streamlit >= 1.28
try:
    _v = tuple(int(x) for x in st.__version__.split(".")[:2])
    SUPPORTS_BORDER = _v >= (1, 28)
except Exception:
    SUPPORTS_BORDER = False

st.markdown(
    """
<style>
/* --- Chat avatars --- */
/* Hide user message avatar entirely */
[data-testid="stChatMessageAvatarUser"] {
    display: none;
}
/* Assistant avatar: electric blue background */
[data-testid="stChatMessageAvatarAssistant"] {
    background-color: #0eb5fd !important;
}

/* --- Chat input --- */
/* Focus border: navy */
.stChatInput textarea:focus {
    border-color: #063c6e !important;
    box-shadow: 0 0 0 1px #063c6e !important;
}
/* Send button: navy background, white icon */
.stChatInput button {
    background-color: #063c6e !important;
    color: white !important;
}
.stChatInput button:hover {
    background-color: #0a5299 !important;
}
/* Also override the chat input container border on focus */
.stChatInput:focus-within {
    border-color: #063c6e !important;
}

/* --- Sidebar Clear Conversation button: navy filled --- */
[data-testid="stSidebar"] [data-testid="stButton"] button {
    border: 2px solid #063c6e !important;
    color: white !important;
    background-color: #063c6e !important;
}
[data-testid="stSidebar"] [data-testid="stButton"] button:hover {
    background-color: #0a5299 !important;
    color: white !important;
}

/* --- Custom link buttons (Product Page / Spec Sheet) --- */
[data-testid="stMarkdownContainer"] a.btn-navy,
[data-testid="stMarkdownContainer"] a.btn-navy:link,
[data-testid="stMarkdownContainer"] a.btn-navy:visited,
[data-testid="stMarkdownContainer"] a.btn-navy:hover,
[data-testid="stMarkdownContainer"] a.btn-navy:focus,
[data-testid="stMarkdownContainer"] a.btn-navy:active {
    display: inline-block;
    padding: 0.4rem 1rem;
    margin: 0.2rem 0;
    border-radius: 0.5rem;
    background-color: #063c6e;
    color: white !important;
    text-decoration: none !important;
    border-bottom: none !important;
    box-shadow: none !important;
    font-size: 0.85rem;
    font-weight: 500;
    text-align: center;
    width: 100%;
    box-sizing: border-box;
}
[data-testid="stMarkdownContainer"] a.btn-navy:hover {
    background-color: #0a5299;
}
[data-testid="stMarkdownContainer"] a.btn-blue,
[data-testid="stMarkdownContainer"] a.btn-blue:link,
[data-testid="stMarkdownContainer"] a.btn-blue:visited,
[data-testid="stMarkdownContainer"] a.btn-blue:hover,
[data-testid="stMarkdownContainer"] a.btn-blue:focus,
[data-testid="stMarkdownContainer"] a.btn-blue:active {
    display: inline-block;
    padding: 0.4rem 1rem;
    margin: 0.2rem 0;
    border-radius: 0.5rem;
    background-color: #0eb5fd;
    color: white !important;
    text-decoration: none !important;
    border-bottom: none !important;
    box-shadow: none !important;
    font-size: 0.85rem;
    font-weight: 500;
    text-align: center;
    width: 100%;
    box-sizing: border-box;
}
[data-testid="stMarkdownContainer"] a.btn-blue:hover {
    background-color: #3dc7fd;
}
</style>
""",
    unsafe_allow_html=True,
)


def _render_sku_cards(top_skus: list) -> None:
    """Render a bordered card for each top SKU result."""
    if not top_skus:
        return

    for sku_info in top_skus:
        if SUPPORTS_BORDER:
            container = st.container(border=True)
        else:
            container = st.container()

        with container:
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
                st.caption(" | ".join(specs) if specs else "Specs not available")

            with col2:
                if sku_info.get("pdp_url"):
                    st.markdown(
                        f'<a href="{sku_info["pdp_url"]}" target="_blank" class="btn-navy">Product Page</a>',
                        unsafe_allow_html=True,
                    )
                if sku_info.get("spec_sheet_url"):
                    st.markdown(
                        f'<a href="{sku_info["spec_sheet_url"]}" target="_blank" class="btn-blue">Spec Sheet</a>',
                        unsafe_allow_html=True,
                    )

        if not SUPPORTS_BORDER:
            st.markdown("---")


def _render_evidence(evidence_chunks: list) -> None:
    """Render a collapsed expander with source evidence snippets."""
    if not evidence_chunks:
        return

    with st.expander("View Source Evidence", expanded=False):
        for chunk in evidence_chunks[:6]:
            label = "Product Record" if chunk.get("doc_type") == "sku_record" else "Spec Sheet"
            st.markdown(f"**{label} - SKU {chunk.get('sku', '')}**")
            st.text(chunk.get("text_snippet", ""))
            st.markdown("---")


def _get_indexed_brands(sqlite_path: Path) -> list[str]:
    if not sqlite_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(sqlite_path))
        rows = conn.execute(
            "SELECT DISTINCT brand FROM products WHERE brand IS NOT NULL AND TRIM(brand) != '' ORDER BY brand"
        ).fetchall()
        conn.close()
        return [r[0] for r in rows if r and r[0]]
    except Exception:
        return []


def _clear_conversation() -> None:
    """Reset chat history in session state."""
    st.session_state["messages"] = []


if "messages" not in st.session_state:
    st.session_state.messages = []
# Each message dict: {"role": "user"|"assistant", "content": str, "result": QueryResult|None}


logo_path = Path(__file__).parent.parent / "LBS Clear Logo.png"
if logo_path.exists():
    st.sidebar.image(str(logo_path), use_container_width=True)

indexed_brands = _get_indexed_brands(Path(__file__).parent.parent / "db" / "products.sqlite")

st.sidebar.button("Clear Conversation", on_click=_clear_conversation)

if indexed_brands:
    st.sidebar.caption(
        f"Indexed Brands: **{', '.join(indexed_brands)}**\n\n"
        "Prices are not available in this dataset."
    )
else:
    st.sidebar.caption(
        "Indexed Brands: not detected\n\n"
        "Prices are not available in this dataset."
    )

st.title("Lighting Product Assistant")
st.caption("Search across indexed product brands — SKU lookups, spec comparisons, recommendations.")


for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and msg.get("result") is not None:
            result: QueryResult = msg["result"]
            _render_sku_cards(result.top_skus)
            _render_evidence(result.evidence_chunks)


if prompt := st.chat_input("Ask a product question..."):
    st.session_state.messages.append({"role": "user", "content": prompt, "result": None})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Searching product database..."):
            try:
                # brand=None means no brand filter (search all indexed data).
                result = query_service.query(raw_query=prompt, brand=None)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Query failed: {exc}")
                result = QueryResult(
                    answer_text="I hit an internal error while running retrieval. Please retry.",
                    top_skus=[],
                    evidence_chunks=[],
                    parsed_query={},
                    retrieval_meta={},
                )

        st.markdown(result.answer_text)
        _render_sku_cards(result.top_skus)
        _render_evidence(result.evidence_chunks)

        # --- Temporary diagnostic panel (remove after LLM bug is resolved) ---
        meta = result.retrieval_meta
        if meta:
            llm_status = meta.get("llm_status", "unknown")
            diag_parts = [
                f"**llm_status:** `{llm_status}`",
                f"**llm_second_pass:** `{meta.get('llm_second_pass', '?')}`",
                f"**elapsed:** `{meta.get('elapsed_seconds', '?')}s`",
                f"**semantic_count:** `{meta.get('semantic_count', '?')}`",
                f"**fts_count:** `{meta.get('fts_count', '?')}`",
            ]
            if meta.get("llm_error"):
                diag_parts.append(f"**llm_error:** `{meta.get('llm_error')}`")
            with st.expander("🔍 Debug: Retrieval Diagnostics", expanded=(llm_status != "generated")):
                st.markdown(" | ".join(diag_parts))
        # --- End diagnostic panel ---

    st.session_state.messages.append(
        {"role": "assistant", "content": result.answer_text, "result": result}
    )
