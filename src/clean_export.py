#!/usr/bin/env python3
"""Clean raw BigCommerce export CSVs before export_prep.py."""

from __future__ import annotations

import argparse
import csv
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable

from shared_constants import BRAND_SKU_PREFIXES


BASE_STORE_URL = "https://lightbulbsurplus.com"

OUTPUT_FIELDS = [
    "SKU",
    "Internal LBS SKU",
    "H1",
    "Brand Name",
    "Product Description",
    "Category",
    "Product UPC",
    "Product URL",
    "Spec Sheet URL",
    "Custom Fields",
    "Minimum Purchase Quantity",
    "Big Commerce Product ID",
]

COLUMN_ALIASES = {
    "sku": ["SKU"],
    "internal_lbs_sku": ["Internal LBS SKU"],
    "h1": ["H1", "Product Name", "Name"],
    "brand": ["Brand Name", "Brand"],
    "product_description_html": ["Product Description", "Description"],
    "category": ["Category", "Categories"],
    "upc": ["Product UPC", "UPC"],
    "pdp_url": ["Product URL", "PDP URL", "Product Link"],
    "spec_sheet_url": ["Spec Sheet URL", "Spec URL"],
    "custom_fields_raw": ["Custom Fields", "Custom Field", "CustomFields"],
    "minimum_purchase_qty": ["Minimum Purchase Quantity", "Min Purchase Qty"],
    "bigcommerce_product_id": ["Big Commerce Product ID", "BigCommerce Product ID", "Product ID"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean a raw BigCommerce export before export_prep.py.")
    parser.add_argument("--input", required=True, help="Path to the source export CSV.")
    parser.add_argument("--output", required=True, help="Path to write the cleaned CSV.")
    parser.add_argument(
        "--brand",
        default=None,
        help="Brand name used when stripping known internal SKU prefixes (for example: satco).",
    )
    return parser.parse_args()


def detect_encoding(file_path: Path) -> str:
    with open(file_path, "rb") as f:
        raw = f.read(4)
    if raw[:2] == b"\xff\xfe":
        return "utf-16-le"
    if raw[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    return "utf-8-sig"


def normalize_header(value: str) -> str:
    return (value or "").replace("\ufeff", "").strip().lower()


def build_header_index(fieldnames: Iterable[str]) -> Dict[str, str]:
    header_map = {normalize_header(name): name for name in fieldnames if name}
    resolved: Dict[str, str] = {}

    for canonical, aliases in COLUMN_ALIASES.items():
        matched = ""
        for alias in aliases:
            key = normalize_header(alias)
            if key in header_map:
                matched = header_map[key]
                break
        resolved[canonical] = matched

    if not resolved.get("internal_lbs_sku") and not resolved.get("bigcommerce_product_id"):
        raise ValueError("Missing required column(s): Internal LBS SKU or Big Commerce Product ID")

    return resolved


def clean_value(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip()


def get_value(row: Dict[str, str], header_index: Dict[str, str], canonical_key: str) -> str:
    source_col = header_index.get(canonical_key, "")
    if not source_col:
        return ""
    return clean_value(row.get(source_col, ""))


def normalize_spaces(value: str) -> str:
    return " ".join(clean_value(value).split())


def normalize_store_url(url: str) -> str:
    value = clean_value(url)
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("//"):
        return f"https:{value}"
    if value.lower().startswith("lightbulbsurplus.com"):
        return f"https://{value}"
    if value.startswith("/"):
        return f"{BASE_STORE_URL}{value}"
    return f"{BASE_STORE_URL}/{value.lstrip('/')}"


def is_option_set_row(h1: str, description_html: str, category: str) -> bool:
    normalized_h1 = normalize_spaces(h1)
    if normalized_h1.upper().startswith("[S]"):
        return True
    return not normalized_h1 and not clean_value(description_html) and not clean_value(category)


def fill_missing_sku(sku: str, internal_lbs_sku: str, brand_name: str) -> tuple[str, bool]:
    cleaned_sku = clean_value(sku)
    cleaned_internal = clean_value(internal_lbs_sku)
    if cleaned_sku or not cleaned_internal:
        return cleaned_sku, False

    prefix = BRAND_SKU_PREFIXES.get(clean_value(brand_name).lower(), "")
    if prefix and cleaned_internal.upper().startswith(prefix.upper()):
        return cleaned_internal[len(prefix) :], True
    return cleaned_internal, True


class SpecSheetHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.current_anchor: Dict[str, str] | None = None
        self.candidates: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {str(key).lower(): clean_value(value) for key, value in attrs}
        lowered_tag = tag.lower()

        if lowered_tag == "a":
            self.current_anchor = attr_map
            self._maybe_add_anchor_candidate(attr_map)
            return

        if lowered_tag == "img" and self.current_anchor is not None:
            self._maybe_add_image_candidate(self.current_anchor, attr_map)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a":
            self.current_anchor = None

    def _maybe_add_anchor_candidate(self, anchor_attrs: Dict[str, str]) -> None:
        href = normalize_store_url(anchor_attrs.get("href", ""))
        if not href:
            return

        context = self._context_text(anchor_attrs, {})
        if self._is_excluded(context):
            return

        if "spec" in context:
            self._add_candidate(href)

    def _maybe_add_image_candidate(self, anchor_attrs: Dict[str, str], image_attrs: Dict[str, str]) -> None:
        href = normalize_store_url(anchor_attrs.get("href", ""))
        if not href:
            return

        anchor_context = self._context_text(anchor_attrs, {})
        image_context = self._context_text({}, image_attrs)
        combined_context = f"{anchor_context} {image_context}".strip()

        if self._is_excluded(combined_context):
            return

        if "spec" in image_context or "spec" in anchor_context:
            self._add_candidate(href)
            return

        if "pdf.png" in image_context and "spec" in anchor_context:
            self._add_candidate(href)

    def _add_candidate(self, href: str) -> None:
        if href and href not in self.candidates:
            self.candidates.append(href)

    @staticmethod
    def _context_text(anchor_attrs: Dict[str, str], image_attrs: Dict[str, str]) -> str:
        parts = [
            anchor_attrs.get("href", ""),
            anchor_attrs.get("title", ""),
            anchor_attrs.get("class", ""),
            image_attrs.get("title", ""),
            image_attrs.get("alt", ""),
            image_attrs.get("src", ""),
            image_attrs.get("class", ""),
        ]
        return " ".join(part for part in parts if part).lower()

    @staticmethod
    def _is_excluded(text: str) -> bool:
        return "install" in text or "globe" in text


def extract_spec_sheet_url(description_html: str) -> str:
    html = clean_value(description_html)
    if not html:
        return ""

    parser = SpecSheetHTMLParser()
    parser.feed(html)
    parser.close()
    return parser.candidates[0] if parser.candidates else ""


def get_spec_sheet_url(
    row: Dict[str, str],
    header_index: Dict[str, str],
    has_spec_column: bool,
) -> tuple[str, bool]:
    if has_spec_column:
        existing_spec_url = normalize_store_url(get_value(row, header_index, "spec_sheet_url"))
        if existing_spec_url:
            return existing_spec_url, False

    extracted_spec_url = extract_spec_sheet_url(get_value(row, header_index, "product_description_html"))
    return extracted_spec_url, bool(extracted_spec_url)


def main() -> int:
    args = parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    encoding = detect_encoding(input_path)
    total_rows = 0
    option_set_rows_removed = 0
    sku_filled_count = 0
    product_urls_normalized = 0
    spec_sheet_urls_extracted = 0
    rows_written = 0

    with input_path.open("r", encoding=encoding, newline="") as src_file, output_path.open(
        "w", encoding="utf-8", newline=""
    ) as dst_file:
        reader = csv.DictReader(src_file)
        if not reader.fieldnames:
            raise ValueError("Input CSV has no header row.")

        header_index = build_header_index(reader.fieldnames)
        has_spec_column = bool(header_index.get("spec_sheet_url"))
        writer = csv.DictWriter(dst_file, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()

        for row in reader:
            total_rows += 1

            h1 = get_value(row, header_index, "h1")
            description_html = get_value(row, header_index, "product_description_html")
            category = get_value(row, header_index, "category")
            if is_option_set_row(h1, description_html, category):
                option_set_rows_removed += 1
                continue

            internal_lbs_sku = get_value(row, header_index, "internal_lbs_sku")
            row_brand = get_value(row, header_index, "brand")
            effective_brand = args.brand or row_brand
            sku, sku_was_filled = fill_missing_sku(get_value(row, header_index, "sku"), internal_lbs_sku, effective_brand)
            if sku_was_filled:
                sku_filled_count += 1

            original_product_url = get_value(row, header_index, "pdp_url")
            normalized_product_url = normalize_store_url(original_product_url)
            if normalized_product_url and normalized_product_url != clean_value(original_product_url):
                product_urls_normalized += 1

            spec_sheet_url, spec_was_extracted = get_spec_sheet_url(row, header_index, has_spec_column)
            if spec_was_extracted:
                spec_sheet_urls_extracted += 1

            writer.writerow(
                {
                    "SKU": sku,
                    "Internal LBS SKU": internal_lbs_sku,
                    "H1": normalize_spaces(h1),
                    "Brand Name": row_brand,
                    "Product Description": description_html,
                    "Category": category,
                    "Product UPC": get_value(row, header_index, "upc"),
                    "Product URL": normalized_product_url,
                    "Spec Sheet URL": spec_sheet_url,
                    "Custom Fields": get_value(row, header_index, "custom_fields_raw"),
                    "Minimum Purchase Quantity": get_value(row, header_index, "minimum_purchase_qty"),
                    "Big Commerce Product ID": get_value(row, header_index, "bigcommerce_product_id"),
                }
            )
            rows_written += 1

    print(f"Input file: {input_path}")
    print(f"Output file: {output_path}")
    print(f"Total rows scanned: {total_rows}")
    print(f"Option set rows removed: {option_set_rows_removed}")
    print(f"SKUs filled from Internal LBS SKU: {sku_filled_count}")
    print(f"Product URLs normalized: {product_urls_normalized}")
    print(f"Spec sheet URLs extracted: {spec_sheet_urls_extracted}")
    print(f"Rows written: {rows_written}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
