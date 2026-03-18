#!/usr/bin/env python3
"""Extract text from spec sheet PDFs with native extraction and OCR fallback."""

from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple


DEFAULT_POPPLER_PATH = r"C:\Users\edelgado\Downloads\Release-24.08.0-0\poppler-24.08.0\Library\bin"
DEFAULT_TESSERACT_CMD = r"C:\Users\edelgado\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"


@dataclass
class ExtractionResult:
    internal_lbs_sku: str
    sku: str
    pdf_path: str
    txt_path: str
    json_path: str
    status: str
    method_used: str
    ocr_fallback_used: bool
    n_pages: int
    pages_with_native_text: int
    pages_ocr: int
    native_char_count: int
    ocr_char_count: int
    total_char_count: int
    error: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract text from spec PDFs with OCR fallback.")
    parser.add_argument("--pdf-dir", default="data/spec_pdfs", help="Directory containing spec PDFs.")
    parser.add_argument("--output-dir", default="data/spec_text", help="Directory to write extracted text/json files.")
    parser.add_argument(
        "--log-path",
        default="data/spec_text/extraction_log.csv",
        help="CSV log path for extraction metadata.",
    )
    parser.add_argument(
        "--min-native-chars",
        type=int,
        default=300,
        help="If native extracted chars are below this, OCR fallback is attempted.",
    )
    parser.add_argument("--ocr-dpi", type=int, default=300, help="DPI used during OCR rendering.")
    parser.add_argument(
        "--tess-config",
        default="--oem 1 --psm 6",
        help="Tesseract config flags.",
    )
    parser.add_argument(
        "--tesseract-cmd",
        default=os.getenv("TESSERACT_CMD", DEFAULT_TESSERACT_CMD),
        help="Path to tesseract executable.",
    )
    parser.add_argument(
        "--poppler-path",
        default=os.getenv("POPPLER_PATH", DEFAULT_POPPLER_PATH),
        help="Path to Poppler bin directory for pdf2image.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of PDFs to process (0 = all).")
    parser.add_argument("--force", action="store_true", help="Re-extract even if output txt/json already exist.")
    return parser.parse_args()


def parse_ids_from_filename(pdf_path: Path) -> Tuple[str, str]:
    stem = pdf_path.stem.strip()
    internal_lbs_sku = stem

    sku = ""
    if "-" in stem:
        sku = stem.split("-", 1)[1]
    elif stem.isdigit():
        sku = stem

    return internal_lbs_sku, sku


def extract_native_text(pdf_path: Path) -> Tuple[List[str], int, int, int]:
    page_texts: List[str] = []
    pages_with_text = 0
    char_count = 0

    try:
        import fitz  # PyMuPDF

        with fitz.open(str(pdf_path)) as doc:
            n_pages = doc.page_count
            for i in range(n_pages):
                page = doc.load_page(i)
                text = (page.get_text() or "").strip()
                page_texts.append(text)
                if text:
                    pages_with_text += 1
                    char_count += len(text)
    except Exception:
        # Fallback path when PyMuPDF is unavailable.
        import pdfplumber

        with pdfplumber.open(str(pdf_path)) as doc:
            n_pages = len(doc.pages)
            for page in doc.pages:
                text = (page.extract_text() or "").strip()
                page_texts.append(text)
                if text:
                    pages_with_text += 1
                    char_count += len(text)

    return page_texts, n_pages, pages_with_text, char_count


def detect_ocr_availability(tesseract_cmd: str, poppler_path: str) -> Tuple[bool, str]:
    try:
        import pytesseract
        from pdf2image import convert_from_path  # noqa: F401

        if not Path(tesseract_cmd).exists():
            return False, "tesseract_path_missing"

        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        _ = pytesseract.get_tesseract_version()

        if poppler_path and (not Path(poppler_path).exists()):
            return False, "poppler_path_missing"

        return True, ""
    except Exception as exc:
        return False, exc.__class__.__name__


def ocr_pdf(pdf_path: Path, dpi: int, poppler_path: str, tesseract_cmd: str, tess_config: str) -> Tuple[List[str], int, int]:
    import pytesseract
    from pdf2image import convert_from_path

    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    poppler_arg = poppler_path if poppler_path and Path(poppler_path).exists() else None
    images = convert_from_path(str(pdf_path), dpi=dpi, poppler_path=poppler_arg)

    ocr_texts: List[str] = []
    ocr_char_count = 0
    pages_ocr = 0

    for image in images:
        text = (pytesseract.image_to_string(image, config=tess_config) or "").strip()
        ocr_texts.append(text)
        pages_ocr += 1
        if text:
            ocr_char_count += len(text)

    return ocr_texts, pages_ocr, ocr_char_count


def join_pages(page_texts: List[str]) -> str:
    parts: List[str] = []
    for idx, txt in enumerate(page_texts, start=1):
        parts.append(f"[PAGE {idx}]\n{txt}".strip())
    return "\n\n".join(parts).strip()


def write_outputs(
    txt_path: Path,
    json_path: Path,
    internal_lbs_sku: str,
    sku: str,
    pdf_path: Path,
    method_used: str,
    ocr_fallback_used: bool,
    n_pages: int,
    pages_with_native_text: int,
    pages_ocr: int,
    native_char_count: int,
    ocr_char_count: int,
    total_char_count: int,
    full_text: str,
) -> None:
    txt_path.parent.mkdir(parents=True, exist_ok=True)

    txt_path.write_text(full_text, encoding="utf-8")

    payload = {
        "internal_lbs_sku": internal_lbs_sku,
        "sku": sku,
        "source_pdf": str(pdf_path),
        "method_used": method_used,
        "ocr_fallback_used": ocr_fallback_used,
        "n_pages": n_pages,
        "pages_with_native_text": pages_with_native_text,
        "pages_ocr": pages_ocr,
        "native_char_count": native_char_count,
        "ocr_char_count": ocr_char_count,
        "total_char_count": total_char_count,
        "text_path": str(txt_path),
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def write_log(log_path: Path, rows: List[ExtractionResult]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "internal_lbs_sku",
        "sku",
        "pdf_path",
        "txt_path",
        "json_path",
        "status",
        "method_used",
        "ocr_fallback_used",
        "n_pages",
        "pages_with_native_text",
        "pages_ocr",
        "native_char_count",
        "ocr_char_count",
        "total_char_count",
        "error",
    ]

    with log_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "internal_lbs_sku": r.internal_lbs_sku,
                    "sku": r.sku,
                    "pdf_path": r.pdf_path,
                    "txt_path": r.txt_path,
                    "json_path": r.json_path,
                    "status": r.status,
                    "method_used": r.method_used,
                    "ocr_fallback_used": str(r.ocr_fallback_used),
                    "n_pages": r.n_pages,
                    "pages_with_native_text": r.pages_with_native_text,
                    "pages_ocr": r.pages_ocr,
                    "native_char_count": r.native_char_count,
                    "ocr_char_count": r.ocr_char_count,
                    "total_char_count": r.total_char_count,
                    "error": r.error,
                }
            )


def main() -> int:
    args = parse_args()

    pdf_dir = Path(args.pdf_dir)
    output_dir = Path(args.output_dir)
    log_path = Path(args.log_path)

    if not pdf_dir.exists():
        raise FileNotFoundError(f"PDF directory not found: {pdf_dir}")

    pdf_paths = sorted(pdf_dir.glob("*.pdf"))
    if args.limit and args.limit > 0:
        pdf_paths = pdf_paths[: args.limit]

    if not pdf_paths:
        print("No PDF files found to process.")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    ocr_available, ocr_error_type = detect_ocr_availability(args.tesseract_cmd, args.poppler_path)

    results: List[ExtractionResult] = []
    ok_count = 0
    skipped_existing = 0
    failed_count = 0
    ocr_used_count = 0

    total = len(pdf_paths)

    for idx, pdf_path in enumerate(pdf_paths, start=1):
        internal_lbs_sku, sku = parse_ids_from_filename(pdf_path)
        txt_path = output_dir / f"{internal_lbs_sku}.txt"
        json_path = output_dir / f"{internal_lbs_sku}.json"

        if txt_path.exists() and json_path.exists() and not args.force:
            skipped_existing += 1
            results.append(
                ExtractionResult(
                    internal_lbs_sku=internal_lbs_sku,
                    sku=sku,
                    pdf_path=str(pdf_path),
                    txt_path=str(txt_path),
                    json_path=str(json_path),
                    status="skipped_existing",
                    method_used="",
                    ocr_fallback_used=False,
                    n_pages=0,
                    pages_with_native_text=0,
                    pages_ocr=0,
                    native_char_count=0,
                    ocr_char_count=0,
                    total_char_count=0,
                    error="",
                )
            )
            print(f"[{idx}/{total}] SKIP exists      | {internal_lbs_sku}")
            continue

        try:
            native_pages, n_pages, pages_with_native_text, native_char_count = extract_native_text(pdf_path)

            use_ocr = native_char_count < args.min_native_chars
            method_used = "native"
            ocr_fallback_used = False
            pages_ocr = 0
            ocr_char_count = 0
            final_pages = native_pages
            error_msg = ""

            if use_ocr:
                if not ocr_available:
                    ocr_fallback_used = True
                    ocr_used_count += 1
                    method_used = "native_ocr_unavailable"
                    error_msg = f"ocr_unavailable:{ocr_error_type or 'not_available'}"
                else:
                    try:
                        ocr_pages, pages_ocr, ocr_char_count = ocr_pdf(
                            pdf_path=pdf_path,
                            dpi=args.ocr_dpi,
                            poppler_path=args.poppler_path,
                            tesseract_cmd=args.tesseract_cmd,
                            tess_config=args.tess_config,
                        )
                        ocr_fallback_used = True
                        ocr_used_count += 1

                        if ocr_char_count > native_char_count:
                            final_pages = ocr_pages
                            method_used = "ocr_only"
                        else:
                            method_used = "native_plus_ocr"
                    except Exception as ocr_exc:
                        ocr_fallback_used = True
                        ocr_used_count += 1
                        method_used = "native_ocr_unavailable"
                        error_msg = f"ocr_unavailable:{ocr_exc.__class__.__name__}"

            full_text = join_pages(final_pages)
            total_chars = len(full_text)

            write_outputs(
                txt_path=txt_path,
                json_path=json_path,
                internal_lbs_sku=internal_lbs_sku,
                sku=sku,
                pdf_path=pdf_path,
                method_used=method_used,
                ocr_fallback_used=ocr_fallback_used,
                n_pages=n_pages,
                pages_with_native_text=pages_with_native_text,
                pages_ocr=pages_ocr,
                native_char_count=native_char_count,
                ocr_char_count=ocr_char_count,
                total_char_count=total_chars,
                full_text=full_text,
            )

            ok_count += 1
            print(f"[{idx}/{total}] OK   {method_used:<20} | {internal_lbs_sku} ({total_chars} chars)")
            results.append(
                ExtractionResult(
                    internal_lbs_sku=internal_lbs_sku,
                    sku=sku,
                    pdf_path=str(pdf_path),
                    txt_path=str(txt_path),
                    json_path=str(json_path),
                    status="ok",
                    method_used=method_used,
                    ocr_fallback_used=ocr_fallback_used,
                    n_pages=n_pages,
                    pages_with_native_text=pages_with_native_text,
                    pages_ocr=pages_ocr,
                    native_char_count=native_char_count,
                    ocr_char_count=ocr_char_count,
                    total_char_count=total_chars,
                    error=error_msg,
                )
            )

        except Exception as exc:
            failed_count += 1
            print(f"[{idx}/{total}] FAIL {internal_lbs_sku} | {exc.__class__.__name__}")
            results.append(
                ExtractionResult(
                    internal_lbs_sku=internal_lbs_sku,
                    sku=sku,
                    pdf_path=str(pdf_path),
                    txt_path=str(txt_path),
                    json_path=str(json_path),
                    status="failed",
                    method_used="",
                    ocr_fallback_used=False,
                    n_pages=0,
                    pages_with_native_text=0,
                    pages_ocr=0,
                    native_char_count=0,
                    ocr_char_count=0,
                    total_char_count=0,
                    error=f"{exc.__class__.__name__}:{exc}",
                )
            )

    write_log(log_path, results)

    print("\nExtraction summary")
    print(f"PDFs considered: {total}")
    print(f"Extracted OK: {ok_count}")
    print(f"Skipped existing: {skipped_existing}")
    print(f"Failed: {failed_count}")
    print(f"OCR fallback attempted: {ocr_used_count}")
    print(f"Tesseract cmd: {args.tesseract_cmd}")
    print(f"Poppler path: {args.poppler_path}")
    if not ocr_available:
        print(f"OCR environment status: unavailable ({ocr_error_type})")
    print(f"Log written: {log_path}")
    print(f"Text output folder: {output_dir}")

    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
