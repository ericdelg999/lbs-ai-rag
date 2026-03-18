# app_streamlit.py v2 — UI/UX Refresh Spec

## Context

`src/app_streamlit.py` already exists and is functional. This spec describes visual/UX changes only — no logic or retrieval changes. The existing file should be edited in place.

**Brand colors:**
- Navy: `#063c6e`
- Electric Blue: `#0eb5fd`

**Important:** Read the existing `src/app_streamlit.py` before making any changes. Preserve all existing functionality — this is a styling pass, not a rewrite.

---

## Change 1: Inject custom CSS block

Add a single `st.markdown("<style>...</style>", unsafe_allow_html=True)` call immediately after `st.set_page_config(...)` and the `SUPPORTS_BORDER` check. This block contains ALL custom CSS for the app.

```python
st.markdown("""
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

/* --- Sidebar Clear Conversation button: navy outline --- */
[data-testid="stSidebar"] button {
    border: 2px solid #063c6e !important;
    color: #063c6e !important;
    background-color: white !important;
}
[data-testid="stSidebar"] button:hover {
    background-color: #063c6e !important;
    color: white !important;
}

/* --- Custom link buttons (Product Page / Spec Sheet) --- */
.btn-navy {
    display: inline-block;
    padding: 0.4rem 1rem;
    margin: 0.2rem 0;
    border-radius: 0.5rem;
    background-color: #063c6e;
    color: white !important;
    text-decoration: none;
    font-size: 0.85rem;
    font-weight: 500;
    text-align: center;
    width: 100%;
    box-sizing: border-box;
}
.btn-navy:hover {
    background-color: #0a5299;
    color: white !important;
    text-decoration: none;
}
.btn-blue {
    display: inline-block;
    padding: 0.4rem 1rem;
    margin: 0.2rem 0;
    border-radius: 0.5rem;
    background-color: #0eb5fd;
    color: white !important;
    text-decoration: none;
    font-size: 0.85rem;
    font-weight: 500;
    text-align: center;
    width: 100%;
    box-sizing: border-box;
}
.btn-blue:hover {
    background-color: #3dc7fd;
    color: white !important;
    text-decoration: none;
}
</style>
""", unsafe_allow_html=True)
```

**Placement:** This must go AFTER `st.set_page_config(...)` and the `SUPPORTS_BORDER` version check, but BEFORE any sidebar or main content rendering.

---

## Change 2: Replace `st.link_button` with styled HTML buttons

In the `_render_sku_cards` function, replace the `st.link_button` calls in `col2` with `st.markdown` using the custom CSS classes.

**Current code (in col2):**
```python
with col2:
    if sku_info.get("pdp_url"):
        st.link_button("Product Page", sku_info["pdp_url"])
    if sku_info.get("spec_sheet_url"):
        st.link_button("Spec Sheet", sku_info["spec_sheet_url"])
```

**Replace with:**
```python
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
```

---

## Change 3: Sidebar cleanup

**Remove** both `st.sidebar.markdown("---")` calls (the horizontal rules above and below the Clear Conversation button).

**Replace** the sidebar caption block. Current:
```python
st.sidebar.markdown("---")
if indexed_brands:
    st.sidebar.caption(
        "Searching across all indexed brands.\n\n"
        f"Indexed brands: **{', '.join(indexed_brands)}**\n\n"
        "Prices are not available in this dataset."
    )
else:
    st.sidebar.caption(
        "Searching across all indexed brands.\n\n"
        "Indexed brands: not detected\n\n"
        "Prices are not available in this dataset."
    )
```

**Replace with:**
```python
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
```

Also remove the `st.sidebar.markdown("---")` line that appears between the logo and the Clear Conversation button.

---

## Change 4: Title and subtitle

**Current:**
```python
st.title("Incon Lighting Product Assistant")
st.caption("Ask a product question - SKU lookups, spec comparisons, recommendations.")
```

**Replace with:**
```python
st.title("Lighting Product Assistant")
st.caption("Search across indexed product brands \u2014 SKU lookups, spec comparisons, recommendations.")
```

---

## Summary of all changes to `src/app_streamlit.py`

| Section | What to change |
|---------|---------------|
| After `SUPPORTS_BORDER` check | Add the full CSS `<style>` block via `st.markdown` |
| `_render_sku_cards` col2 | Replace `st.link_button` calls with `st.markdown` HTML buttons using `btn-navy` and `btn-blue` classes |
| Sidebar | Remove both `st.sidebar.markdown("---")` calls. Remove "Searching across all indexed brands" text. Simplify caption to `Indexed Brands: **{brands}**` |
| Title/subtitle | Change title to "Lighting Product Assistant", update subtitle |

**Do NOT change:**
- `st.set_page_config` (keep page_title as "Incon Lighting AI")
- The `_get_indexed_brands` function
- The `_render_evidence` function
- The session state logic
- The query flow / error handling
- The `SUPPORTS_BORDER` version check logic
- Any imports

---

## Change to `src/query_service.py`

**Already done — no action needed.** `MAX_SKUS_IN_ANSWER` has been changed from `5` to `3`.

---

## Verification

```bash
# 1. Launch the app
streamlit run src/app_streamlit.py

# 2. Visual checks:

# Check 1: Chat avatars
# - Type a question. The user message should show text only (no avatar icon).
# - The assistant response should show a robot icon with electric blue (#0eb5fd) background.

# Check 2: Product cards
# - At most 3 SKU cards should appear per answer (not 5).
# - "Product Page" button should be navy (#063c6e) with white text.
# - "Spec Sheet" button should be electric blue (#0eb5fd) with white text.
# - Both buttons should open links in a new tab when clicked.

# Check 3: Chat input
# - Click into the text input area. The focus border should be navy, not red.
# - The send arrow button should be navy with a white arrow, not red.

# Check 4: Sidebar
# - No horizontal rule lines above or below "Clear Conversation".
# - No "Searching across all indexed brands" text.
# - Should show: "Indexed Brands: Bulbrite" and "Prices are not available in this dataset."
# - "Clear Conversation" button should have a navy border with navy text on white.
# - Hover over the button — it should fill navy with white text.

# Check 5: Title
# - Main heading reads "Lighting Product Assistant" (no "Incon").
# - Subtitle reads "Search across indexed product brands — SKU lookups, spec comparisons, recommendations."

# Check 6: Functional regression
# - Ask "Is SKU 132507 dimmable?" → answer + SKU card with working PDP/spec links.
# - Click "Clear Conversation" → thread clears, page refreshes cleanly.
# - Ask a second question → both Q&A pairs visible in thread.
```

---

## Important Notes for the Implementing Agent

1. **Read `src/app_streamlit.py` before making changes.** This is an edit to an existing file, not a new file.
2. **Do NOT rewrite the file from scratch.** Make targeted edits to the sections described above.
3. **The CSS `<style>` block must be a single injection** — do not scatter multiple `st.markdown` style blocks throughout the file.
4. **Preserve the `SUPPORTS_BORDER` logic** — it is still used for the SKU card containers.
5. **The `unsafe_allow_html=True` parameter is required** for both the CSS block and the HTML button links.
6. **Test that PDP and spec sheet URLs still open correctly** after replacing `st.link_button` with `st.markdown` HTML links.
7. **Do NOT change `query_service.py`** — the `MAX_SKUS_IN_ANSWER` constant has already been updated.
8. **After completing changes, update `PROJECT_BRAIN.md`** with a dated entry in the Collaboration Handoff section noting the UI/UX refresh.
