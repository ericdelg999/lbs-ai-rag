#!/usr/bin/env python3
"""Parse custom field strings into JSON plus normalized technical fields."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple


NORMALIZED_FIELDS = [
    "wattage_actual",
    "lumens_actual",
    "voltage",
    "color_temperature",
    "base_type",
    "shape",
    "dimmable",
    "finish",
    "pack_qty",
    "bulb_or_fixture_type",
]

DEFAULT_CANONICAL_KEY_ALIASES = {
    "wattage": {"wattage", "watts", "watt"},
    "lumens": {"lumens", "lumen"},
    "voltage": {"voltage", "volt", "volts"},
    "color_temperature": {"color temperature", "colour temperature", "cct"},
    "color_temperature_range": {"cct range"},
    "base": {"base", "base type", "socket", "base size"},
    "shape": {"shape", "bulb shape"},
    "size": {"size"},
    "dimmable": {"dimmable"},
    "finish": {"finish", "fixture finish"},
    "color": {"color"},
    "pack": {"pack", "pack qty", "pack quantity", "case qty", "case quantity"},
    "fixture_type": {"lighting technology", "product type", "bulb type", "technology"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parse custom field strings for prepared products.")
    parser.add_argument(
        "--input",
        default="data/prepared/bulbrite_products_prepped.csv",
        help="Input prepared CSV path.",
    )
    parser.add_argument(
        "--output",
        default="data/prepared/bulbrite_products_parsed.csv",
        help="Output parsed CSV path.",
    )
    parser.add_argument(
        "--brand",
        default="bulbrite",
        help="Brand profile name used for alias map overlays.",
    )
    parser.add_argument(
        "--alias-config",
        default="config/field_alias_map.yaml",
        help="Path to alias map file (YAML extension, JSON-compatible content).",
    )
    return parser.parse_args()


def split_cf_parts(raw: str) -> List[str]:
    if not raw:
        return []

    parts: List[str] = []
    current: List[str] = []
    in_quotes = False

    for ch in raw:
        if ch == '"':
            in_quotes = not in_quotes
            current.append(ch)
            continue

        if ch == ";" and not in_quotes:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
        else:
            current.append(ch)

    tail = "".join(current).strip()
    if tail:
        parts.append(tail)

    return parts


def strip_wrapping_quotes(text: str) -> str:
    value = text.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]
    return value.replace('""', '"').strip()


def parse_custom_fields(raw: str) -> Tuple[Dict[str, str], List[str]]:
    parsed: Dict[str, str] = {}
    errors: List[str] = []

    for part in split_cf_parts(raw):
        cleaned = strip_wrapping_quotes(part)
        if not cleaned:
            continue

        if "=" not in cleaned:
            errors.append(f"missing_equals:{cleaned[:80]}")
            continue

        key, value = cleaned.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            errors.append(f"empty_key:{cleaned[:80]}")
            continue

        parsed[key] = value

    return parsed, errors


def key_norm(key: str) -> str:
    text = key.strip().lower()
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_alias_map(aliases: Dict[str, Iterable[str]]) -> Dict[str, Set[str]]:
    normalized: Dict[str, Set[str]] = {}
    for canonical_key, alias_values in aliases.items():
        normalized[canonical_key] = {key_norm(str(a)) for a in alias_values}
    return normalized


def merge_aliases(base: Dict[str, Set[str]], overlay: Dict[str, Set[str]]) -> Dict[str, Set[str]]:
    merged: Dict[str, Set[str]] = {k: set(v) for k, v in base.items()}
    for canonical_key, alias_values in overlay.items():
        merged.setdefault(canonical_key, set()).update(alias_values)
    return merged


def load_alias_map(config_path: Path, brand: str) -> Dict[str, Set[str]]:
    base = normalize_alias_map(DEFAULT_CANONICAL_KEY_ALIASES)
    if not config_path.exists():
        return base

    raw = config_path.read_text(encoding="utf-8").strip()
    if not raw:
        return base

    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Alias config must be JSON-compatible YAML. Failed to parse {config_path}: {exc}"
        ) from exc

    default_overlay = normalize_alias_map(loaded.get("default", {}))
    merged = merge_aliases(base, default_overlay)

    brand_overlays = loaded.get("brands", {})
    profile = brand_overlays.get(brand.lower(), {})
    profile_overlay = normalize_alias_map(profile)
    merged = merge_aliases(merged, profile_overlay)

    return merged


def alias_match(key: str, alias_set: Iterable[str]) -> bool:
    return key_norm(key) in alias_set


def first_value(cf: Dict[str, str], alias_set: Iterable[str], exclude_private: bool = True) -> str:
    alias_lookup = set(alias_set)
    for key, value in cf.items():
        if exclude_private and key.strip().startswith("__"):
            continue
        if alias_match(key, alias_lookup):
            return value.strip()
    return ""


def extract_number(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"\d+(?:\.\d+)?", text)
    return m.group(0) if m else ""


def normalize_voltage(text: str) -> str:
    n = extract_number(text)
    if not n:
        return ""
    return f"{n}V"


def normalize_cct(text: str) -> str:
    value = text.strip()
    if not value:
        return ""
    temps = re.findall(r"\d{3,4}\s*[kK]", value)
    if len(temps) == 1:
        return temps[0].upper().replace(" ", "")
    if len(temps) > 1:
        compact = [t.upper().replace(" ", "") for t in temps]
        return "/".join(compact)
    return value


def normalize_base(text: str) -> str:
    value = text.strip()
    if not value:
        return ""
    m = re.search(r"\bE\d{2}\b", value.upper())
    if m:
        return m.group(0)
    return value


def normalize_dimmable(text: str) -> str:
    value = text.strip().lower()
    if not value:
        return ""
    if value in {"yes", "y", "true", "1", "dimmable"}:
        return "Yes"
    if value in {"no", "n", "false", "0", "non-dimmable", "not dimmable"}:
        return "No"
    return text.strip()


def normalize_pack_qty(text: str) -> str:
    n = extract_number(text)
    if not n:
        return ""
    try:
        return str(int(float(n)))
    except ValueError:
        return ""


def get_alias(aliases: Dict[str, Set[str]], key: str) -> Set[str]:
    return aliases.get(key, set())


def normalize_fields(cf: Dict[str, str], aliases: Dict[str, Set[str]]) -> Dict[str, str]:
    wattage = first_value(cf, get_alias(aliases, "wattage"))
    lumens = first_value(cf, get_alias(aliases, "lumens"))
    voltage = first_value(cf, get_alias(aliases, "voltage"))

    cct = first_value(cf, get_alias(aliases, "color_temperature"))
    cct_range = first_value(cf, get_alias(aliases, "color_temperature_range"))

    base = first_value(cf, get_alias(aliases, "base"))
    shape = first_value(cf, get_alias(aliases, "shape")) or first_value(cf, get_alias(aliases, "size"))
    dimmable = first_value(cf, get_alias(aliases, "dimmable"))
    finish = first_value(cf, get_alias(aliases, "finish")) or first_value(cf, get_alias(aliases, "color"))
    pack = first_value(cf, get_alias(aliases, "pack"))
    fixture_type = first_value(cf, get_alias(aliases, "fixture_type"))

    color_temperature = cct_range if cct_range else cct

    return {
        "wattage_actual": extract_number(wattage),
        "lumens_actual": extract_number(lumens),
        "voltage": normalize_voltage(voltage),
        "color_temperature": normalize_cct(color_temperature),
        "base_type": normalize_base(base),
        "shape": shape.strip(),
        "dimmable": normalize_dimmable(dimmable),
        "finish": finish.strip(),
        "pack_qty": normalize_pack_qty(pack),
        "bulb_or_fixture_type": fixture_type.strip(),
    }


def main() -> int:
    args = parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    config_path = Path(args.alias_config)

    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")

    aliases = load_alias_map(config_path, args.brand.lower())
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with input_path.open("r", encoding="utf-8-sig", newline="") as src_file:
        reader = csv.DictReader(src_file)
        if not reader.fieldnames:
            raise ValueError("Input CSV has no header row.")

        in_fields = list(reader.fieldnames)
        out_fields = in_fields + ["custom_fields_json", "custom_fields_parse_errors"] + NORMALIZED_FIELDS

        rows_written = 0
        rows_with_errors = 0

        with output_path.open("w", encoding="utf-8", newline="") as dst_file:
            writer = csv.DictWriter(dst_file, fieldnames=out_fields)
            writer.writeheader()

            for row in reader:
                raw = row.get("custom_fields_raw", "")
                parsed, errors = parse_custom_fields(raw)
                normalized = normalize_fields(parsed, aliases)

                row["custom_fields_json"] = json.dumps(parsed, ensure_ascii=True, sort_keys=True)
                row["custom_fields_parse_errors"] = " | ".join(errors)

                for field in NORMALIZED_FIELDS:
                    row[field] = normalized.get(field, "")

                if errors:
                    rows_with_errors += 1

                writer.writerow(row)
                rows_written += 1

    print(f"Input file: {input_path}")
    print(f"Output file: {output_path}")
    print(f"Alias config: {config_path} (brand profile: {args.brand.lower()})")
    print(f"Rows written: {rows_written}")
    print(f"Rows with parse warnings: {rows_with_errors}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
