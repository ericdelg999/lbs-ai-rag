#!/usr/bin/env python3
"""Filter a cleaned BigCommerce CSV by product category (Pipeline Step 0.5).

Keeps products matching KEEP_PATTERNS unless overridden by EXCLUDE_PATTERNS.
Designed for trimming large brand datasets to fit hosting constraints.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


KEEP_PATTERNS = [
    "light bulbs/led",
    "industrial",
    "warehouse",
    "emergency",
    "exit",
]

EXCLUDE_PATTERNS = [
    "parts & components",
    "lighting fixture parts",
]

EXCLUDE_EXCEPTIONS = [
    "ballasts",
    "drivers",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Filter products by category keep/exclude rules.")
    parser.add_argument("--input", required=True, help="Path to cleaned CSV.")
    parser.add_argument("--output", required=True, help="Path for filtered output CSV.")
    return parser.parse_args()


def should_keep(category_str: str) -> tuple[bool, str]:
    """Determine if a product should be kept based on its category string.

    Returns (keep: bool, reason: str).
    """
    cats = [c.strip() for c in category_str.split(";") if c.strip()]

    # Pass 1: check for exclude override
    for c in cats:
        cl = c.lower()
        for ep in EXCLUDE_PATTERNS:
            if ep in cl:
                is_exception = any(ex in cl for ex in EXCLUDE_EXCEPTIONS)
                if not is_exception:
                    return False, "parts/accessories"

    # Pass 2: check for keep match
    for c in cats:
        cl = c.lower()
        if "light bulbs/led" in cl:
            return True, "LED Bulb"
        if "industrial" in cl or "warehouse" in cl:
            return True, "Industrial"
        if "emergency" in cl or "exit" in cl:
            return True, "Emergency/Exit"

    return False, "no matching category"


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    kept = 0
    keep_reasons: dict[str, int] = {}
    cut_reasons: dict[str, int] = {}

    with input_path.open("r", encoding="utf-8-sig", newline="") as src, \
         output_path.open("w", encoding="utf-8", newline="") as dst:
        reader = csv.DictReader(src)
        if not reader.fieldnames:
            raise ValueError("Input CSV has no header row.")
        writer = csv.DictWriter(dst, fieldnames=reader.fieldnames)
        writer.writeheader()

        for row in reader:
            total += 1
            category = row.get("Category", "")
            keep, reason = should_keep(category)

            if keep:
                writer.writerow(row)
                kept += 1
                keep_reasons[reason] = keep_reasons.get(reason, 0) + 1
            else:
                cut_reasons[reason] = cut_reasons.get(reason, 0) + 1

    cut = total - kept

    print(f"Input:  {input_path} ({total} rows)")
    print(f"Output: {output_path}")
    print(f"\nKept: {kept}")
    for reason, count in sorted(keep_reasons.items(), key=lambda x: -x[1]):
        print(f"  {reason}: {count}")
    print(f"\nCut: {cut}")
    for reason, count in sorted(cut_reasons.items(), key=lambda x: -x[1]):
        print(f"  {reason}: {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
