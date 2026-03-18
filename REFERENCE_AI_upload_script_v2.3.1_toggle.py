#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Universal AI Upload Script (v2.3.1)
- Features:
    * INPUT_MODE toggle: "PDF", "ROW", or "BOTH".
    * Master Prompt Toggle: Choose which prompts to run [1, 2, 3].
    * Responses API with GPT-5-mini.
    * Auto-fallback for filename derivation (CSV Column -> URL -> SKU).
    * EXCLUSION LOGIC: Ignores columns with "image", "video", "lighting facts" in headers.
- Created 12/18/25
"""

import csv
import os
import re
import sys
import json
import logging
import requests
import fitz  # PyMuPDF
import typing as t
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
from dotenv import load_dotenv
from openai import OpenAI, RateLimitError, APIError, APIConnectionError, Timeout

# Tesseract / OCR
import pytesseract
from pdf2image import convert_from_path

# =============================
# ========== CONFIG ===========
# =============================

# Files
INPUT_CSV = "Input.csv"
OUTPUT_CSV_TEMPLATE = "brand_formatted_{YYYY-MM-DD}.csv"
BRAND = "Insert BRAND here"

# Model & Threads
MODEL_NAME = "gpt-5-mini"
MAX_WORKERS = 20  # Aggressive setting (12-25)

# --- INPUT MODE TOGGLE ---
# "PDF"  -> Downloads PDF, extracts text. Ignores row text. (Legacy behavior)
# "ROW"  -> Skips PDF download. Serializes row columns into text. (Fastest)
# "BOTH" -> Downloads PDF AND serializes row columns. (Maximum Context)
INPUT_MODE = "BOTH"

# --- MASTER PROMPT TOGGLE ---
# [1] -> Main Data, [2] -> Custom Fields, [3] -> Category
ACTIVE_PROMPTS = [1, 2, 3]

# OCR / PDF Configuration
OCR_DPI = 300
TESS_CFG = "--oem 1 --psm 6"

# *** IMPORTANT: UPDATE THESE PATHS FOR YOUR LOCAL MACHINE ***
POPPLER_PATH = r"C:\Users\edelgado\Downloads\Release-24.08.0-0\poppler-24.08.0\Library\bin"
TESSERACT_CMD = r"C:\Users\edelgado\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"

pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

# Prompts
PROMPT_001 = r"""
PROMPT 001 HERE
"""

PROMPT_002 = r"""
PROMPT 002 HERE
"""

PROMPT_003 = r"""
PROMPT 003 HERE
"""

# =============================
# ===== Helper Functions ======
# =============================

def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def sanitize_filename(filename):
    return re.sub(r'[<>:"/\\|?*]', '_', filename)

def clean_text(text):
    if not text: return ""
    return " ".join(text.split())

# -------------------- PDF & OCR LOGIC --------------------

def download_pdf(spec_sheet_url, sku):
    if not spec_sheet_url:
        return None
    try:
        response = requests.get(spec_sheet_url, timeout=30)
        response.raise_for_status()
        # Skip if the response came back empty
        if not response.content:
            log(f"Error downloading PDF for SKU {sku}: empty response")
            return None
        clean_sku = sanitize_filename(sku)
        pdf_filename = f"TEMP_{clean_sku}.pdf"
        with open(pdf_filename, "wb") as f:
            f.write(response.content)
        return pdf_filename
    except Exception as e:
        log(f"Error downloading PDF for SKU {sku}: {e}")
        return None

def extract_text_from_pdf_with_ocr(pdf_path):
    try:
        if not os.path.exists(POPPLER_PATH):
            log(f"WARNING: Poppler path not found at {POPPLER_PATH}. OCR may fail.")
        images = convert_from_path(pdf_path, dpi=OCR_DPI, poppler_path=POPPLER_PATH)
        ocr_text = ""
        for image in images:
            ocr_text += pytesseract.image_to_string(image, config=TESS_CFG) + "\n"
        return ocr_text
    except Exception as e:
        log(f"OCR extraction failed for {pdf_path}: {e}")
        return ""

def extract_text_from_pdf(pdf_path):
    try:
        text = ""
        with fitz.open(pdf_path) as doc:
            for page in doc:
                text += page.get_text() + "\n"
        if len(text.strip()) < 50:
            return extract_text_from_pdf_with_ocr(pdf_path)
        return text
    except Exception as e:
        log(f"PyMuPDF error: {e}. Attempting OCR fallback.")
        return extract_text_from_pdf_with_ocr(pdf_path)

# -------------------- CSV & POST-PROCESSING --------------------
def get_spec_link(row: t.Dict[str, str]) -> str:
    candidates = {
        "spec sheet url",
        "spec_sheet_url",
        "spec sheet link",
        "spec link",
        "spec url",
    }
    for k, v in row.items():
        if not k:
            continue
        if k.strip().lower() in candidates:
            return str(v).strip()
    return ""

def get_sku_value(row: t.Dict[str, str]) -> str:
    """
    Case-insensitive SKU lookup to handle headers like 'sku' or 'SKU'.
    """
    for k, v in row.items():
        if k and k.strip().lower() == "sku":
            return str(v).strip()
    return ""

def read_csv_all_text(path: str) -> t.Tuple[t.List[t.Dict[str, str]], t.List[str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            clean = {str(k): str(v) for k, v in row.items()}
            rows.append(clean)
        return rows, (reader.fieldnames or [])

def write_csv_quote_all(path: str, rows: t.List[t.Dict[str, str]], fieldnames: t.List[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in fieldnames})

def derive_spec_filename(row: t.Dict[str, str]) -> str:
    """
    Determine the spec sheet filename with priority:
    1. 'Spec Sheet Filename' column (Manual Override)
    2. 'Spec Sheet URL' (Parse from link)
    3. Fallback: BRAND-{SKU}.pdf
    """
    sku = get_sku_value(row)
    
    # Priority 1: Explicit Column
    # Check for likely column names
    explicit_name = row.get("Spec Sheet Filename") or row.get("spec_sheet_filename")
    if explicit_name and explicit_name.strip():
        return explicit_name.strip()

    # Priority 2: Extract from URL
    spec_link = get_spec_link(row)
    if spec_link:
        part = spec_link.rstrip("/").split("/")[-1]
        if "?" in part:
            part = part.split("?")[0]
        if part.lower().endswith(".pdf"):
            return part

    # Priority 3: Fallback
    prefix = BRAND or "Spec"
    return f"{prefix}-{sku}.pdf" if sku else "Spec-Sheet.pdf"


def replace_spec_filename_and_title(html_desc: str, brand: str, row: t.Dict[str, str]) -> str:
    if not html_desc: return html_desc

    brand = (brand or BRAND or "").strip()
    sku = get_sku_value(row)
    desired_title = f"{brand} {sku} Spec Sheet".strip()
    
    # Use the new smart derivation logic
    filename = derive_spec_filename(row)

    out = html_desc.replace("spec_sheet_filename.pdf", filename)
    pattern_anchor = re.compile(
        rf'(<a\b[^>]*href="[^"]*{re.escape(filename)}"[^>]*)(>)',
        flags=re.IGNORECASE
    )
    if pattern_anchor.search(out) and 'title="' not in pattern_anchor.search(out).group(1):
         out = pattern_anchor.sub(rf'\1 title="{desired_title}"\2', out, count=1)
    return out

def dedupe_custom_fields(cf_line: str) -> str:
    s = (cf_line or "").strip()
    if not s: return s
    parts = [p.strip() for p in s.split(";") if p.strip()]
    seen = set()
    ordered = []
    for p in parts:
        core = p[1:-1] if (len(p) >= 2 and p[0] == '"' and p[-1] == '"') else p
        if core not in seen:
            seen.add(core)
            ordered.append(core)
    return ";".join(f"\"{x}\"" for x in ordered)

def parse_json_strict(s: str) -> t.Optional[t.Dict[str, t.Any]]:
    s = (s or "").strip()
    if not s: return None
    try:
        return json.loads(s)
    except Exception:
        pass
    start = s.find("{"); end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(s[start:end+1])
        except Exception:
            return None
    return None

def serialize_row_data(row: t.Dict[str, str]) -> str:
    """
    Format row as 'Header: Value | Header: Value' for AI context.
    Excludes columns with headers containing: image, video, lighting facts.
    """
    # Columns to ignore if header contains these substrings (case-insensitive)
    exclude_keywords = ["image", "video", "lighting facts"]

    items = []
    for k, v in row.items():
        # Skip empty keys/values
        if not k or not v or not str(v).strip():
            continue
        
        # Skip if header contains an excluded keyword
        k_lower = k.lower()
        if any(ex in k_lower for ex in exclude_keywords):
            continue

        # Format: "Header: Value"
        items.append(f"{k}: {v}")

    return " | ".join(items)

# =============================
# ===== OpenAI Connection =====
# =============================

load_dotenv()
_api_key = os.getenv("OPENAI_API_KEY", "").strip()
if not _api_key:
    log("WARNING: OPENAI_API_KEY not found in environment.")

client = OpenAI(api_key=_api_key)

def build_response_input(prompt_text: str, runtime_json: t.Dict[str, t.Any]) -> t.List[t.Dict[str, t.Any]]:
    system_msg = {
        "role": "system",
        "content": [{"type": "input_text", "text": "You are a precise assistant that follows instructions exactly."}],
    }
    user_content: t.List[t.Dict[str, t.Any]] = []
    
    prompt_block = (prompt_text or "").rstrip()
    if prompt_block:
        user_content.append({"type": "input_text", "text": prompt_block})
    
    # Pass runtime as formatted JSON block
    runtime_block = "\n\nRUNTIME INPUTS (JSON):\n" + json.dumps(runtime_json, ensure_ascii=False)
    user_content.append({"type": "input_text", "text": runtime_block})

    user_msg = {"role": "user", "content": user_content}
    return [system_msg, user_msg]

def extract_response_text(resp) -> str:
    output = getattr(resp, "output", None)
    if not output: return ""
    chunks = []
    for item in output:
        if getattr(item, "type", "") == "refusal":
            log(f"WARNING: Model refused request: {getattr(item, 'refusal', 'Unknown')}")
            continue
        contents = getattr(item, "content", None)
        if contents is None and hasattr(item, "message"):
            contents = getattr(item.message, "content", None)
        if not contents: continue
        for part in contents:
            text_val = getattr(part, "text", None) or getattr(part, "output_text", None)
            if text_val: chunks.append(str(text_val))
    return "\n".join(chunks).strip()

@retry(
    reraise=True,
    wait=wait_exponential(min=1, max=60),
    stop=stop_after_attempt(5),
    retry=retry_if_exception_type((RateLimitError, APIError, APIConnectionError, Timeout)),
)
def create_response(**kwargs):
    return client.responses.create(**kwargs)

def call_openai_response(prompt_text: str, runtime_json: t.Dict[str, t.Any]) -> str:
    resp = create_response(
        model=MODEL_NAME,
        input=build_response_input(prompt_text, runtime_json),
    )
    return extract_response_text(resp)

# =============================
# ===== Row-level Worker ======
# =============================

def process_row(row: t.Dict[str, str]) -> t.Dict[str, str]:
    sku = get_sku_value(row)
    spec_link = get_spec_link(row)
    
    result = {
        "H1": "", "H2": "", "Product Description": "", "Meta Title": "", "Meta Description": "",
        "AI UPC": "", "AI Minimum Purchase Quantity": "", "Custom Fields": "", "Category": "",
        "Extracted Text Snippet": "" 
    }

    if not ACTIVE_PROMPTS:
        return result

    # --- INPUT MODE LOGIC ---
    spec_text = ""
    row_text_str = ""

    # 1. PDF Download Mode (Enabled if NOT in "ROW" mode)
    if INPUT_MODE in ["PDF", "BOTH"]:
        pdf_path = download_pdf(spec_link, sku)
        if pdf_path:
            spec_text = extract_text_from_pdf(pdf_path)
            try:
                os.remove(pdf_path)
            except Exception:
                pass
        else:
            if INPUT_MODE == "PDF": # Only log error if PDF was the MAIN source
                log(f"SKU {sku}: No PDF downloaded.")

    # 2. Row Data Serialization (Enabled if NOT in "PDF" mode)
    if INPUT_MODE in ["ROW", "BOTH"]:
        row_text_str = serialize_row_data(row)

    clean_spec_text = clean_text(spec_text)
    result["Extracted Text Snippet"] = clean_spec_text[:500] 

    # 3. Build Runtime Input
    # Build runtime payload based on INPUT_MODE
    runtime = {
        "sku": sku,
        "brand": BRAND,
        "spec_sheet_url": spec_link,
        "extracted_text": clean_spec_text[:40000],  # PDF text (or empty if PDF failed)
    }

    if INPUT_MODE in ["ROW", "BOTH"]:
        # When row context is desired, include the serialized text and full dict
        runtime["row_data_text"] = row_text_str
        runtime["row_data"] = row
    else:  # PDF-only mode: limit row payload to essentials (or strip entirely if preferred)
        runtime["row_data_text"] = ""
        runtime["row_data"] = {"sku": sku, "spec_sheet_url": spec_link}

    # 4. Prompt 001 (Main Data)
    if 1 in ACTIVE_PROMPTS:
        try:
            out_001 = call_openai_response(PROMPT_001, runtime)
            data_001 = parse_json_strict(out_001)
            if data_001:
                desc = str(data_001.get("Product Description", "") or "")
                # Updated replacement logic passes 'row' to find filename
                desc = replace_spec_filename_and_title(desc, BRAND, row)
                
                result.update({
                    "H1": str(data_001.get("H1", "") or ""),
                    "H2": str(data_001.get("H2", "") or ""),
                    "Product Description": desc,
                    "Meta Title": str(data_001.get("Meta Title", "") or ""),
                    "Meta Description": str(data_001.get("Meta Description", "") or ""),
                    "AI UPC": str(data_001.get("UPC", "") or ""),
                    "AI Minimum Purchase Quantity": str(data_001.get("Minimum Purchase Quantity", "") or ""),
                })
                runtime["prev_output"] = data_001
        except Exception as e:
            log(f"SKU {sku}: Prompt 001 error: {e}")

    # 5. Prompt 002 (Custom Fields)
    if 2 in ACTIVE_PROMPTS:
        try:
            out_002 = call_openai_response(PROMPT_002, runtime)
            cf_line = (out_002 or "").strip()
            if "\n" in cf_line:
                cf_line = next((ln.strip() for ln in cf_line.splitlines() if ln.strip()), "")
            result["Custom Fields"] = dedupe_custom_fields(cf_line)
        except Exception as e:
            log(f"SKU {sku}: Prompt 002 error: {e}")

    # 6. Prompt 003 (Category)
    if 3 in ACTIVE_PROMPTS:
        try:
            out_003 = call_openai_response(PROMPT_003, runtime)
            result["Category"] = (out_003 or "").strip()
        except Exception as e:
            log(f"SKU {sku}: Prompt 003 error: {e}")

    return result

# =============================
# =========== Main ============
# =============================

def main():
    logging.basicConfig(level=logging.INFO)

    if not os.getenv("OPENAI_API_KEY"):
        log("ERROR: OPENAI_API_KEY is missing.")
        sys.exit(1)

    try:
        rows, original_cols = read_csv_all_text(INPUT_CSV)
    except FileNotFoundError:
        log(f"ERROR: Input CSV not found: {INPUT_CSV}")
        sys.exit(1)

    if not rows:
        log("ERROR: Input CSV has no data rows.")
        sys.exit(1)

    new_cols = [
        "H1", "H2", "Product Description", "Meta Title", "Meta Description",
        "AI UPC", "AI Minimum Purchase Quantity", "Custom Fields", "Category", 
        "Extracted Text Snippet"
    ]
    
    final_fieldnames = list(original_cols)
    for nc in new_cols:
        if nc not in final_fieldnames:
            final_fieldnames.append(nc)

    log(f"Loaded {len(rows)} rows.")
    log(f"Config: Mode={INPUT_MODE}, Workers={MAX_WORKERS}, Prompts={ACTIVE_PROMPTS}")

    processed_rows = [dict(r) for r in rows]

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {}
        for idx, row in enumerate(rows):
            futures[ex.submit(process_row, row)] = idx

        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                ai_data = fut.result()
            except Exception as e:
                log(f"Row {idx+1}: Unhandled exception: {e}")
                ai_data = {k: "" for k in new_cols}

            for k, v in ai_data.items():
                processed_rows[idx][k] = v

            if (idx + 1) % 5 == 0 or (idx + 1) == len(rows):
                log(f"Progress: {idx + 1}/{len(rows)} rows complete.")

    today = datetime.now().strftime("%Y-%m-%d")
    output_csv = OUTPUT_CSV_TEMPLATE.replace("{YYYY-MM-DD}", today)

    write_csv_quote_all(output_csv, processed_rows, final_fieldnames)
    log(f"Done. Wrote: {output_csv}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Interrupted by user.")
        sys.exit(130)
