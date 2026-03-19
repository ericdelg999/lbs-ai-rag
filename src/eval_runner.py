#!/usr/bin/env python3
"""Run the Bulbrite eval set against query_service and score the results."""

from __future__ import annotations

import argparse
import ast
import csv
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import httpx
from dotenv import load_dotenv
from openai import OpenAI

# Keep sibling imports working when invoked as `python src/eval_runner.py`.
sys.path.insert(0, str(Path(__file__).parent))

import query_service


DEFAULT_QUESTIONS = Path("data/eval/bulbrite_test_questions.csv")
DEFAULT_SQLITE = Path("db/products.sqlite")
DEFAULT_RESULTS_DIR = Path("data/eval")
JUDGE_MODEL = "gpt-5-mini"
JUDGE_TIMEOUT_SECONDS = 30.0

JUDGE_INSTRUCTIONS = (
    "You are grading answers from a lighting-product retrieval system. "
    "Return exactly one line in the format PASS|reason or FAIL|reason. "
    "Keep the reason to one short sentence."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Bulbrite eval harness.")
    parser.add_argument(
        "--questions",
        default=str(DEFAULT_QUESTIONS),
        help="Path to the eval question CSV.",
    )
    parser.add_argument(
        "--sqlite-path",
        default=str(DEFAULT_SQLITE),
        help="Path to the SQLite products database.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only run the first N questions (0 = all).",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip the first N questions before applying --limit.",
    )
    return parser.parse_args()


def parse_jsonish(raw: str, default: Any) -> Any:
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return ast.literal_eval(raw)


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def try_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    filtered = "".join(ch for ch in text if ch.isdigit() or ch in ".-")
    if not filtered or filtered in {"-", ".", "-."}:
        return None
    try:
        return float(filtered)
    except ValueError:
        return None


def values_equal(actual: Any, expected: Any) -> bool:
    actual_num = try_float(actual)
    expected_num = try_float(expected)
    if actual_num is not None and expected_num is not None:
        return abs(actual_num - expected_num) < 1e-6
    return normalize_text(actual) == normalize_text(expected)


def load_questions(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def load_products_by_sku(sqlite_path: Path) -> Dict[str, Dict[str, Any]]:
    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM products").fetchall()
    finally:
        conn.close()
    return {str(row["sku"]): dict(row) for row in rows}


def init_judge_client() -> OpenAI:
    load_dotenv()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set. Add it to the repo .env file.")

    http_client = httpx.Client(
        proxy=None,
        trust_env=False,
        timeout=JUDGE_TIMEOUT_SECONDS,
    )
    return OpenAI(
        api_key=api_key,
        timeout=JUDGE_TIMEOUT_SECONDS,
        max_retries=0,
        http_client=http_client,
    )


def parse_judge_output(text: str) -> Tuple[str, str]:
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        return "ERROR", "Judge returned empty output."

    if "|" in cleaned:
        verdict_raw, reason = cleaned.split("|", 1)
        verdict = verdict_raw.strip().upper()
        if verdict in {"PASS", "FAIL"}:
            return verdict, reason.strip()

    upper = cleaned.upper()
    if upper.startswith("PASS"):
        return "PASS", cleaned[4:].lstrip(" :|-") or "Judge marked the answer correct."
    if upper.startswith("FAIL"):
        return "FAIL", cleaned[4:].lstrip(" :|-") or "Judge marked the answer incorrect."
    return "ERROR", cleaned


def call_judge(client: OpenAI, prompt: str) -> Tuple[str, str]:
    try:
        resp = client.responses.create(
            model=JUDGE_MODEL,
            instructions=JUDGE_INSTRUCTIONS,
            input=prompt,
        )
        return parse_judge_output(getattr(resp, "output_text", "") or "")
    except Exception as exc:  # noqa: BLE001
        return "ERROR", f"{type(exc).__name__}: {exc}"


def product_matches_constraints(product: Dict[str, Any], constraints: Dict[str, Any]) -> bool:
    for key, expected in constraints.items():
        if key.endswith("_min"):
            field = key[:-4]
            actual_num = try_float(product.get(field))
            expected_num = try_float(expected)
            if actual_num is None or expected_num is None or actual_num < expected_num:
                return False
            continue

        if key.endswith("_max"):
            field = key[:-4]
            actual_num = try_float(product.get(field))
            expected_num = try_float(expected)
            if actual_num is None or expected_num is None or actual_num > expected_num:
                return False
            continue

        if key.endswith("_in"):
            field = key[:-3]
            options = expected if isinstance(expected, list) else [expected]
            actual_norm = normalize_text(product.get(field))
            if actual_norm not in {normalize_text(option) for option in options}:
                return False
            continue

        if key.endswith("_contains"):
            field = key[:-9]
            expected_norm = normalize_text(expected)
            actual_norm = normalize_text(product.get(field))
            if expected_norm not in actual_norm:
                return False
            continue

        if not values_equal(product.get(key), expected):
            return False

    return True


def top_sku_columns(result: query_service.QueryResult) -> Tuple[str, str, str]:
    skus = [str(item.get("sku", "")) for item in result.top_skus[:3]]
    while len(skus) < 3:
        skus.append("")
    return skus[0], skus[1], skus[2]


def answer_preview(text: str, limit: int = 240) -> str:
    flat = " ".join((text or "").split())
    return flat[:limit]


def fact_present_for_row(row: Dict[str, str], answer_text: str) -> bool:
    question_id = (row.get("question_id") or "").strip().upper()
    answer_norm = normalize_text(answer_text)

    if question_id == "Q031":
        return any(token in answer_norm for token in ("spring", "accessory", "kit"))

    if question_id == "Q032":
        return any(token in answer_norm for token in ("25-pack", "25 pack")) or bool(
            re.search(r"\b25\b", answer_text or "")
        )

    if question_id == "Q034":
        has_dollar_amount = bool(re.search(r"\$\s*\d", answer_text or ""))
        has_graceful_decline = any(
            token in answer_norm for token in ("not available", "check", "website", "pdp")
        )
        return (not has_dollar_amount) and has_graceful_decline

    expected_fact = (row.get("expected_fact") or "").strip()
    return normalize_text(expected_fact) in answer_norm


def judge_prompt_for_lookup(row: Dict[str, str], result: query_service.QueryResult) -> str:
    question_id = (row.get("question_id") or "").strip().upper()
    question = row.get("question", "")
    target_sku = (row.get("target_sku") or "").strip()
    expected_fact = (row.get("expected_fact") or "").strip()
    expected_field = (row.get("expected_fact_field") or "").strip()

    if question_id == "Q031":
        return (
            "The user asked what SKU 773299 is. The answer should identify it as a kit "
            "accessory (mounting springs / retrofit springs) and should NOT invent "
            "wattage, lumens, or other specs that do not apply.\n\n"
            f"Answer text:\n{result.answer_text}"
        )

    return (
        f"Question: {question}\n"
        f"Target SKU: {target_sku}\n"
        f"Expected field: {expected_field}\n"
        f"Expected fact: {expected_fact}\n"
        f"LLM status: {result.retrieval_meta.get('llm_status', 'unknown')}\n"
        f"Returned top SKUs: {[item.get('sku', '') for item in result.top_skus]}\n\n"
        f"Answer text:\n{result.answer_text}"
    )


def judge_prompt_for_edge_case(row: Dict[str, str], result: query_service.QueryResult) -> str:
    question_id = (row.get("question_id") or "").strip().upper()
    if question_id == "Q034":
        return (
            "The user asked for the price of SKU 774257. The system does not have price data. "
            "Does the answer correctly state that price is not available without hallucinating a price?\n\n"
            f"Answer text:\n{result.answer_text}"
        )

    return (
        f"Question: {row.get('question', '')}\n"
        f"Expected behavior: handle the edge case gracefully.\n\n"
        f"Answer text:\n{result.answer_text}"
    )


def format_product_summary(product: Dict[str, Any]) -> str:
    if not product:
        return "(missing product row)"
    return (
        f"SKU {product.get('sku')}: {product.get('h1', '')} | "
        f"wattage={product.get('wattage')} | lumens={product.get('lumens')} | "
        f"voltage={product.get('voltage')} | cct={product.get('color_temperature')} | "
        f"base={product.get('base_type')} | shape={product.get('shape')} | "
        f"dimmable={product.get('dimmable')} | type={product.get('bulb_or_fixture_type')} | "
        f"finish={product.get('finish')}"
    )


def score_sku_lookup(
    row: Dict[str, str],
    result: query_service.QueryResult,
    judge_client: OpenAI,
) -> Tuple[bool, bool, str, str]:
    target_sku = (row.get("target_sku") or "").strip()
    retrieval_hit = any(str(item.get("sku", "")) == target_sku for item in result.top_skus)
    fact_present = fact_present_for_row(row, result.answer_text)

    prompt = judge_prompt_for_lookup(row, result)
    verdict, reason = call_judge(judge_client, prompt)
    return retrieval_hit, fact_present, verdict, reason


def score_edge_case(
    row: Dict[str, str],
    result: query_service.QueryResult,
    judge_client: OpenAI,
) -> Tuple[str, bool, str, str]:
    fact_present = fact_present_for_row(row, result.answer_text)
    prompt = judge_prompt_for_edge_case(row, result)
    verdict, reason = call_judge(judge_client, prompt)
    return "", fact_present, verdict, reason


def score_spec_suggestion(
    row: Dict[str, str],
    result: query_service.QueryResult,
    judge_client: OpenAI,
    products_by_sku: Dict[str, Dict[str, Any]],
) -> Tuple[float, str, str]:
    constraints = parse_jsonish(row.get("required_constraints", ""), {})

    top_products: List[Dict[str, Any]] = []
    for item in result.top_skus[:3]:
        sku = str(item.get("sku", "")).strip()
        if sku:
            top_products.append(products_by_sku.get(sku, {}))

    matching_skus: List[str] = []
    if top_products:
        for product in top_products:
            if product_matches_constraints(product, constraints):
                matching_skus.append(str(product.get("sku", "")))
        matches = len(matching_skus)
        constraint_match_rate = matches / len(top_products)
    else:
        constraint_match_rate = 0.0

    summaries = "\n".join(f"- {format_product_summary(product)}" for product in top_products) or "- (no SKUs returned)"
    prompt = (
        f"User question: {row.get('question', '')}\n"
        f"Required constraints: {json.dumps(constraints, ensure_ascii=True)}\n"
        f"Constraint match rate across top products: {constraint_match_rate:.2f}\n\n"
        f"Top returned SKUs that satisfy all constraints: {matching_skus or ['none']}\n\n"
        f"Returned top products:\n{summaries}\n\n"
        f"Answer text:\n{result.answer_text}\n\n"
        "Grade PASS if the returned recommendations satisfy the required constraints and the answer does not materially contradict them. "
        "Grade FAIL only if the recommended products or answer conflict with the required constraints."
    )
    verdict, reason = call_judge(judge_client, prompt)
    return constraint_match_rate, verdict, reason


def run_eval(
    questions: List[Dict[str, str]],
    sqlite_path: Path,
    judge_client: OpenAI,
) -> List[Dict[str, Any]]:
    products_by_sku = load_products_by_sku(sqlite_path)
    results: List[Dict[str, Any]] = []

    for idx, row in enumerate(questions, start=1):
        question_id = row.get("question_id", f"Q{idx:03d}")
        query_type = row.get("query_type", "")
        question = row.get("question", "")
        print(f"[{idx}/{len(questions)}] {question_id} ({query_type})")

        try:
            result = query_service.query(raw_query=question, brand=None)
            llm_status = str(result.retrieval_meta.get("llm_status", "unknown"))
            elapsed = result.retrieval_meta.get("elapsed_seconds", "")
            retrieval_hit: str | bool = ""
            fact_present: str | bool = ""
            constraint_match_rate = ""

            if query_type in {"sku_lookup", "custom_field_lookup"}:
                retrieval_hit, fact_present, verdict, reason = score_sku_lookup(
                    row=row,
                    result=result,
                    judge_client=judge_client,
                )
            elif query_type == "spec_suggestion":
                constraint_match_rate_value, verdict, reason = score_spec_suggestion(
                    row=row,
                    result=result,
                    judge_client=judge_client,
                    products_by_sku=products_by_sku,
                )
                constraint_match_rate = f"{constraint_match_rate_value:.2f}"
            elif query_type == "edge_case":
                retrieval_hit, fact_present, verdict, reason = score_edge_case(
                    row=row,
                    result=result,
                    judge_client=judge_client,
                )
            else:
                verdict, reason = "ERROR", f"Unknown query_type: {query_type}"

            top_1, top_2, top_3 = top_sku_columns(result)
            results.append(
                {
                    "question_id": question_id,
                    "query_type": query_type,
                    "question": question,
                    "llm_status": llm_status,
                    "retrieval_hit": retrieval_hit,
                    "fact_present": fact_present,
                    "llm_judge_verdict": verdict,
                    "llm_judge_reason": reason,
                    "elapsed_seconds": elapsed,
                    "top_sku_1": top_1,
                    "top_sku_2": top_2,
                    "top_sku_3": top_3,
                    "answer_preview": answer_preview(result.answer_text),
                    "constraint_match_rate": constraint_match_rate,
                }
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                {
                    "question_id": question_id,
                    "query_type": query_type,
                    "question": question,
                    "llm_status": "error",
                    "retrieval_hit": "",
                    "fact_present": "",
                    "llm_judge_verdict": "ERROR",
                    "llm_judge_reason": f"{type(exc).__name__}: {exc}",
                    "elapsed_seconds": "",
                    "top_sku_1": "",
                    "top_sku_2": "",
                    "top_sku_3": "",
                    "answer_preview": "",
                    "constraint_match_rate": "",
                }
            )

    return results


def write_results(results: List[Dict[str, Any]], results_dir: Path) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = results_dir / f"eval_results_{timestamp}.csv"
    fieldnames = [
        "question_id",
        "query_type",
        "question",
        "llm_status",
        "retrieval_hit",
        "fact_present",
        "llm_judge_verdict",
        "llm_judge_reason",
        "elapsed_seconds",
        "top_sku_1",
        "top_sku_2",
        "top_sku_3",
        "answer_preview",
        "constraint_match_rate",
    ]
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    return output_path


def print_summary(results: List[Dict[str, Any]], output_path: Path) -> None:
    total = len(results)
    pass_count = sum(1 for row in results if row["llm_judge_verdict"] == "PASS")
    fail_count = total - pass_count
    llm_generated = sum(1 for row in results if row["llm_status"] == "generated")

    elapsed_values = [try_float(row["elapsed_seconds"]) for row in results]
    elapsed_values = [value for value in elapsed_values if value is not None]
    avg_elapsed = sum(elapsed_values) / len(elapsed_values) if elapsed_values else 0.0

    print("\n=== EVAL SUMMARY ===")
    print(f"Total: {total} | Pass: {pass_count} | Fail: {fail_count} | LLM generated: {llm_generated}/{total}")
    label_map = {
        "sku_lookup": "SKU lookup",
        "custom_field_lookup": "Custom field lookup",
        "spec_suggestion": "Spec suggestion",
        "edge_case": "Edge case",
    }
    for query_type in sorted({row["query_type"] for row in results}):
        subset = [row for row in results if row["query_type"] == query_type]
        subset_pass = sum(1 for row in subset if row["llm_judge_verdict"] == "PASS")
        print(f"{label_map.get(query_type, query_type)}: {subset_pass}/{len(subset)} pass")
    print(f"Avg elapsed: {avg_elapsed:.1f}s")
    print(f"Results written to: {output_path}")


def main() -> int:
    args = parse_args()
    questions_path = Path(args.questions)
    sqlite_path = Path(args.sqlite_path)

    if not questions_path.exists():
        raise FileNotFoundError(f"Question CSV not found: {questions_path}")
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite DB not found: {sqlite_path}")

    questions = load_questions(questions_path)
    if args.offset and args.offset > 0:
        questions = questions[args.offset :]
    if args.limit and args.limit > 0:
        questions = questions[: args.limit]

    if not questions:
        raise RuntimeError("No questions loaded from the eval CSV.")

    judge_client = init_judge_client()
    results = run_eval(questions=questions, sqlite_path=sqlite_path, judge_client=judge_client)
    output_path = write_results(results, DEFAULT_RESULTS_DIR)
    print_summary(results, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
