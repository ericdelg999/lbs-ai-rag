#!/usr/bin/env python3
"""Prepare a BigCommerce product export CSV into a clean, deterministic intermediate dataset.

Works with any brand on the LBS BigCommerce store. Use --brand to filter
to a specific brand (default: all rows kept). Column names are matched
flexibly via COLUMN_ALIASES so different export templates work without
code changes.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Dict, Iterable


OUTPUT_FIELDS = [
    "sku",
    "internal_lbs_sku",
    "brand",
    "h1",
    "product_description_html",
    "category",
    "upc",
    "pdp_url",
    "spec_sheet_url",
    "custom_fields_raw",
    "minimum_purchase_qty",
    "bigcommerce_product_id",
    "source_row_number",
]

COLUMN_ALIASES = {
    "sku": ["SKU"],
    "internal_lbs_sku": ["Internal LBS SKU"],
    "brand": ["Brand Name", "Brand"],
    "h1": ["H1", "Product Name", "Name"],
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
    parser = argparse.ArgumentParser(description="Prepare a BigCommerce product export for the RAG pipeline.")
    parser.add_argument(
        "--input",
        required=True,
        help="Path to source export CSV (e.g. 'Bulbrite - AI RAG Schema Test.csv').",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path for prepared output CSV. Defaults to data/prepared/<brand>_products_prepped.csv.",
    )
    parser.add_argument(
        "--brand",
        default=None,
        help="Brand filter value (case-insensitive contains match). If omitted, all rows are kept.",
    )
    parser.add_argument(
        "--disable-brand-filter",
        action="store_true",
        help="Keep all rows regardless of brand.",
    )
    return parser.parse_args()


def build_header_index(fieldnames: Iterable[str]) -> Dict[str, str]:
    header_map = {name.strip().lower(): name for name in fieldnames if name}
    resolved: Dict[str, str] = {}

    for canonical, aliases in COLUMN_ALIASES.items():
        matched = ""
        for alias in aliases:
            key = alias.strip().lower()
            if key in header_map:
                matched = header_map[key]
                break
        resolved[canonical] = matched

    missing_required = [k for k in ("sku", "brand") if not resolved.get(k)]
    if missing_required:
        joined = ", ".join(missing_required)
        raise ValueError(f"Missing required column(s): {joined}")

    return resolved


def clean_value(value: str) -> str:
    if value is None:
        return ""
    return value.strip()


def normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_url(url: str) -> str:
    value = clean_value(url)
    if not value:
        return ""
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return f"https://{value.lstrip('/')}"


def normalize_spec_url(spec_url: str) -> str:
    """Normalize the spec sheet URL from the CSV. Returns empty string if not provided."""
    return normalize_url(spec_url)


# Maps brand names to known internal_lbs_sku prefixes for fallback matching.
BRAND_SKU_PREFIXES = {
    "bulbrite": "BULR-",
}


def row_is_target_brand(brand_value: str, internal_lbs_sku: str, target_brand: str) -> bool:
    brand_text = clean_value(brand_value).lower()
    target = clean_value(target_brand).lower()

    if not target:
        return True

    if target in brand_text:
        return True

    # Fallback: check if internal_lbs_sku has a known prefix for this brand.
    prefix = BRAND_SKU_PREFIXES.get(target, "")
    if prefix and clean_value(internal_lbs_sku).upper().startswith(prefix):
        return True

    return False


def get_value(row: Dict[str, str], header_index: Dict[str, str], canonical_key: str) -> str:
    source_col = header_index.get(canonical_key, "")
    if not source_col:
        return ""
    return clean_value(row.get(source_col, ""))


def main() -> int:
    args = parse_args()

    # If no brand filter provided, keep all rows.
    if args.brand is None:
        args.disable_brand_filter = True

    input_path = Path(args.input)

    # Default output path based on brand name.
    if args.output is None:
        brand_slug = (args.brand or "all").lower().replace(" ", "_")
        args.output = f"data/prepared/{brand_slug}_products_prepped.csv"

    output_path = Path(args.output)

    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    total_rows = 0
    kept_rows = 0
    dropped_non_brand = 0
    dropped_missing_sku = 0

    with input_path.open("r", encoding="utf-8-sig", newline="") as src_file, output_path.open(
        "w", encoding="utf-8", newline=""
    ) as dst_file:
        reader = csv.DictReader(src_file)
        if not reader.fieldnames:
            raise ValueError("Input CSV has no header row.")

        header_index = build_header_index(reader.fieldnames)
        writer = csv.DictWriter(dst_file, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()

        for row_number, row in enumerate(reader, start=2):
            total_rows += 1

            sku = get_value(row, header_index, "sku")
            internal_lbs_sku = get_value(row, header_index, "internal_lbs_sku") or sku
            brand = get_value(row, header_index, "brand")

            if not args.disable_brand_filter and not row_is_target_brand(brand, internal_lbs_sku, args.brand):
                dropped_non_brand += 1
                continue

            if not sku:
                dropped_missing_sku += 1
                continue

            output_row = {
                "sku": sku,
                "internal_lbs_sku": internal_lbs_sku,
                "brand": brand,
                "h1": normalize_spaces(get_value(row, header_index, "h1")),
                "product_description_html": get_value(row, header_index, "product_description_html"),
                "category": get_value(row, header_index, "category"),
                "upc": get_value(row, header_index, "upc"),
                "pdp_url": normalize_url(get_value(row, header_index, "pdp_url")),
                "spec_sheet_url": normalize_spec_url(
                    get_value(row, header_index, "spec_sheet_url"),
                ),
                "custom_fields_raw": get_value(row, header_index, "custom_fields_raw"),
                "minimum_purchase_qty": get_value(row, header_index, "minimum_purchase_qty"),
                "bigcommerce_product_id": get_value(row, header_index, "bigcommerce_product_id"),
                "source_row_number": str(row_number),
            }

            writer.writerow(output_row)
            kept_rows += 1

    print(f"Input file: {input_path}")
    print(f"Output file: {output_path}")
    print(f"Total rows scanned: {total_rows}")
    print(f"Rows written: {kept_rows}")
    print(f"Dropped (brand filter): {dropped_non_brand}")
    print(f"Dropped (missing SKU): {dropped_missing_sku}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
