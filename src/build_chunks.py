#!/usr/bin/env python3
"""Build chunk objects from parsed product CSV + extracted spec text."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


TECH_FIELD_SPECS: List[Tuple[str, str]] = [
    ("wattage_actual", "Wattage"),
    ("lumens_actual", "Lumens"),
    ("voltage", "Voltage"),
    ("color_temperature", "Color Temperature"),
    ("base_type", "Base Type"),
    ("shape", "Shape"),
    ("dimmable", "Dimmable"),
    ("finish", "Finish"),
    ("pack_qty", "Pack Qty"),
    ("bulb_or_fixture_type", "Type"),
]

# Normalized key names to skip from custom fields to avoid duplicate technical specs.
CUSTOM_FIELD_SKIP_KEYS = {
    "wattage",
    "watts",
    "watt",
    "lumens",
    "lumen",
    "voltage",
    "volt",
    "volts",
    "color temperature",
    "colour temperature",
    "cct",
    "cct range",
    "base",
    "base type",
    "socket",
    "base size",
    "shape",
    "bulb shape",
    "size",
    "dimmable",
    "finish",
    "color",
    "pack",
    "pack qty",
    "pack quantity",
    "case qty",
    "case quantity",
    "lighting technology",
    "product type",
    "bulb type",
    "technology",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build RAG chunks from parsed product + spec text inputs.")
    parser.add_argument(
        "--input",
        default="data/prepared/bulbrite_products_parsed_v2.csv",
        help="Path to parsed product CSV.",
    )
    parser.add_argument(
        "--spec-text-dir",
        default="data/spec_text",
        help="Path to spec text directory containing .txt files.",
    )
    parser.add_argument(
        "--output",
        default="data/chunks/chunks.jsonl",
        help="Path to output JSONL.",
    )
    parser.add_argument(
        "--brand",
        default="",
        help="Optional brand filter (case-insensitive exact match).",
    )
    parser.add_argument(
        "--max-desc-chars",
        type=int,
        default=1500,
        help="Max characters for plain-text product description.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process only first N rows after filtering/sorting (0 = all).",
    )
    return parser.parse_args()


def value_or_none(value: str) -> Optional[str]:
    text = (value or "").strip()
    return text if text else None


def strip_html(html_text: str) -> str:
    text = html.unescape(html_text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_numeric(value: str) -> Optional[float]:
    text = (value or "").strip()
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def normalize_key(key: str) -> str:
    lowered = (key or "").strip().lower().replace("_", " ")
    return re.sub(r"\s+", " ", lowered)


def strip_page_markers(spec_text: str) -> str:
    # Removes lines like [PAGE 1] while preserving content flow.
    text = re.sub(r"\[PAGE\s+\d+\]\s*", "", spec_text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def load_spec_text(spec_dir: Path, internal_lbs_sku: str) -> Optional[str]:
    key = (internal_lbs_sku or "").strip()
    if not key:
        return None

    path = spec_dir / f"{key}.txt"
    if not path.exists():
        return None

    text = path.read_text(encoding="utf-8", errors="ignore")
    cleaned = strip_page_markers(text)
    return cleaned if len(cleaned) > 50 else None


def parse_custom_fields_json(raw_json: str) -> Dict[str, str]:
    text = (raw_json or "").strip()
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return {}

    if not isinstance(loaded, dict):
        return {}

    out: Dict[str, str] = {}
    for k, v in loaded.items():
        key = str(k).strip()
        val = str(v).strip() if v is not None else ""
        if key and val:
            out[key] = val
    return out


def build_sku_record_text(row: Dict[str, str], max_desc_chars: int) -> str:
    lines: List[str] = []

    sku = (row.get("sku") or "").strip()
    brand = (row.get("brand") or "").strip()
    h1 = (row.get("h1") or "").strip()
    category = (row.get("category") or "").strip()
    upc = (row.get("upc") or "").strip()
    pdp_url = (row.get("pdp_url") or "").strip()
    spec_sheet_url = (row.get("spec_sheet_url") or "").strip()
    min_purchase_qty = (row.get("minimum_purchase_qty") or "").strip()

    lines.append(f"SKU: {sku}")
    lines.append(f"Brand: {brand}")
    lines.append(f"Product: {h1}")
    lines.append(f"Category: {category}")
    lines.append(f"UPC: {upc}")
    lines.append(f"PDP: {pdp_url}")
    lines.append(f"Spec Sheet: {spec_sheet_url}")
    lines.append(f"Min Purchase Qty: {min_purchase_qty}")

    tech_lines: List[str] = []
    for field_key, label in TECH_FIELD_SPECS:
        value = (row.get(field_key) or "").strip()
        if value:
            tech_lines.append(f"- {label}: {value}")

    if tech_lines:
        lines.append("")
        lines.append("Technical Specifications:")
        lines.extend(tech_lines)

    custom_fields = parse_custom_fields_json(row.get("custom_fields_json") or "")
    custom_lines: List[str] = []
    for key in sorted(custom_fields.keys(), key=lambda x: x.lower()):
        if normalize_key(key) in CUSTOM_FIELD_SKIP_KEYS:
            continue
        custom_lines.append(f"- {key}: {custom_fields[key]}")

    if custom_lines:
        lines.append("")
        lines.append("Custom Fields:")
        lines.extend(custom_lines)

    desc_plain = strip_html(row.get("product_description_html") or "")
    if desc_plain and max_desc_chars > 0:
        desc_plain = desc_plain[:max_desc_chars]

    lines.append("")
    lines.append("Description:")
    lines.append(desc_plain)

    text = "\n".join(lines).strip()
    # Guarantee no HTML tags remain.
    return re.sub(r"<[^>]+>", "", text)


def build_sku_record_chunk(row: Dict[str, str], max_desc_chars: int) -> Dict[str, object]:
    sku = (row.get("sku") or "").strip()

    metadata = {
        "chunk_id": f"{sku}_sku_record",
        "sku": sku,
        "internal_lbs_sku": value_or_none(row.get("internal_lbs_sku") or ""),
        "brand": value_or_none(row.get("brand") or ""),
        "doc_type": "sku_record",
        "chunk_label": "master",
        "source_url": value_or_none(row.get("pdp_url") or ""),
        "spec_sheet_url": value_or_none(row.get("spec_sheet_url") or ""),
        "source_priority": 1,
        "category": value_or_none(row.get("category") or ""),
        "upc": value_or_none(row.get("upc") or ""),
        "wattage": parse_numeric(row.get("wattage_actual") or ""),
        "lumens": parse_numeric(row.get("lumens_actual") or ""),
        "voltage": parse_numeric(row.get("voltage") or ""),
        "color_temperature": parse_numeric(row.get("color_temperature") or ""),
        "base_type": value_or_none(row.get("base_type") or ""),
        "dimmable": value_or_none(row.get("dimmable") or ""),
        "price": None,
        "minimum_purchase_qty": parse_numeric(row.get("minimum_purchase_qty") or ""),
    }

    return {
        "text": build_sku_record_text(row=row, max_desc_chars=max_desc_chars),
        "metadata": metadata,
    }


def build_spec_text_chunk(row: Dict[str, str], spec_text: str) -> Optional[Dict[str, object]]:
    sku = (row.get("sku") or "").strip()
    internal_lbs_sku = (row.get("internal_lbs_sku") or "").strip()
    cleaned = strip_page_markers(spec_text)
    if len(cleaned) <= 50:
        return None

    metadata = {
        "chunk_id": f"{sku}_spec_sheet_0",
        "sku": sku,
        "internal_lbs_sku": value_or_none(internal_lbs_sku),
        "brand": value_or_none(row.get("brand") or ""),
        "doc_type": "spec_sheet",
        "chunk_label": "spec_full",
        "source_url": value_or_none(row.get("spec_sheet_url") or ""),
        "spec_sheet_url": value_or_none(row.get("spec_sheet_url") or ""),
        "source_priority": 2,
        "category": value_or_none(row.get("category") or ""),
        "upc": None,
        "wattage": None,
        "lumens": None,
        "voltage": None,
        "color_temperature": None,
        "base_type": None,
        "dimmable": None,
        "price": None,
        "minimum_purchase_qty": None,
    }

    return {
        "text": cleaned,
        "metadata": metadata,
    }


def load_rows(csv_path: Path) -> List[Dict[str, str]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("Input CSV has no header row.")
        return list(reader)


def apply_filters(rows: List[Dict[str, str]], brand_filter: str, limit: int) -> List[Dict[str, str]]:
    filtered = rows
    if brand_filter:
        wanted = brand_filter.strip().lower()
        filtered = [r for r in filtered if (r.get("brand") or "").strip().lower() == wanted]

    filtered.sort(key=lambda r: ((r.get("sku") or ""), (r.get("internal_lbs_sku") or "")))

    if limit and limit > 0:
        filtered = filtered[:limit]
    return filtered


def main() -> int:
    args = parse_args()

    input_path = Path(args.input)
    spec_dir = Path(args.spec_text_dir)
    output_path = Path(args.output)

    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")
    if not spec_dir.exists():
        raise FileNotFoundError(f"Spec text directory not found: {spec_dir}")

    rows = load_rows(input_path)
    rows = apply_filters(rows, brand_filter=args.brand, limit=args.limit)

    sku_chunks: List[Dict[str, object]] = []
    spec_chunks: List[Dict[str, object]] = []
    chunk_ids = set()

    for row in rows:
        sku_chunk = build_sku_record_chunk(row=row, max_desc_chars=args.max_desc_chars)
        cid = sku_chunk["metadata"]["chunk_id"]
        if cid in chunk_ids:
            raise ValueError(f"Duplicate chunk_id detected: {cid}")
        chunk_ids.add(cid)
        sku_chunks.append(sku_chunk)

    for row in rows:
        spec_text = load_spec_text(spec_dir=spec_dir, internal_lbs_sku=(row.get("internal_lbs_sku") or "").strip())
        if not spec_text:
            continue

        spec_chunk = build_spec_text_chunk(row=row, spec_text=spec_text)
        if not spec_chunk:
            continue

        cid = spec_chunk["metadata"]["chunk_id"]
        if cid in chunk_ids:
            raise ValueError(f"Duplicate chunk_id detected: {cid}")
        chunk_ids.add(cid)
        spec_chunks.append(spec_chunk)

    all_chunks = sku_chunks + spec_chunks

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for chunk in all_chunks:
            handle.write(json.dumps(chunk, ensure_ascii=True, separators=(",", ":")))
            handle.write("\n")

    print(f"Input rows processed: {len(rows)}")
    print(f"SKU record chunks: {len(sku_chunks)}")
    print(f"Spec sheet chunks: {len(spec_chunks)}")
    print(f"Total chunks written: {len(all_chunks)}")
    print(f"Output file: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
