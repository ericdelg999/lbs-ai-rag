#!/usr/bin/env python3
"""Download product spec sheet PDFs from prepared product data."""

from __future__ import annotations

import argparse
import csv
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import requests


@dataclass
class DownloadResult:
    sku: str
    internal_lbs_sku: str
    spec_sheet_url: str
    local_path: str
    status: str
    http_status: str
    attempts: int
    bytes_written: int
    error: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download spec sheet PDFs from prepared Bulbrite CSV.")
    parser.add_argument(
        "--input",
        default="data/prepared/bulbrite_products_prepped.csv",
        help="Input CSV with spec_sheet_url column.",
    )
    parser.add_argument(
        "--output-dir",
        default="data/spec_pdfs",
        help="Folder where PDFs will be saved.",
    )
    parser.add_argument(
        "--log-path",
        default="data/spec_pdfs/download_log.csv",
        help="CSV log output path.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=25.0,
        help="HTTP request timeout in seconds.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Retries after first attempt (total attempts = 1 + max-retries).",
    )
    parser.add_argument(
        "--retry-wait",
        type=float,
        default=1.5,
        help="Seconds to wait between retries.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max rows to process (0 means all).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Redownload even if target file already exists.",
    )
    return parser.parse_args()


def safe_file_key(internal_lbs_sku: str, sku: str) -> str:
    base = (internal_lbs_sku or "").strip() or (sku or "").strip()
    base = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in base)
    return base or "unknown_sku"


def fetch_pdf(
    session: requests.Session,
    url: str,
    destination: Path,
    timeout: float,
    max_retries: int,
    retry_wait: float,
) -> DownloadResult:
    attempts = 0
    last_error = ""
    http_status = ""

    for attempt in range(1, max_retries + 2):
        attempts = attempt
        try:
            with session.get(url, timeout=timeout, stream=True, allow_redirects=True) as response:
                http_status = str(response.status_code)

                if response.status_code != 200:
                    last_error = f"http_{response.status_code}"
                else:
                    content_type = (response.headers.get("Content-Type") or "").lower()
                    if "pdf" not in content_type and "application/octet-stream" not in content_type:
                        # Not always reliable, but useful signal for debugging.
                        last_error = f"unexpected_content_type:{content_type or 'missing'}"

                    tmp_path = destination.with_suffix(".tmp")
                    bytes_written = 0
                    with tmp_path.open("wb") as handle:
                        for chunk in response.iter_content(chunk_size=64 * 1024):
                            if chunk:
                                handle.write(chunk)
                                bytes_written += len(chunk)

                    if bytes_written == 0:
                        last_error = "empty_response"
                        if tmp_path.exists():
                            tmp_path.unlink(missing_ok=True)
                    else:
                        tmp_path.replace(destination)
                        return DownloadResult(
                            sku="",
                            internal_lbs_sku="",
                            spec_sheet_url=url,
                            local_path=str(destination),
                            status="downloaded",
                            http_status=http_status,
                            attempts=attempts,
                            bytes_written=bytes_written,
                            error="" if last_error.startswith("unexpected_content_type") else "",
                        )

        except requests.RequestException as exc:
            last_error = f"request_error:{exc.__class__.__name__}"

        if attempt <= max_retries:
            time.sleep(retry_wait)

    return DownloadResult(
        sku="",
        internal_lbs_sku="",
        spec_sheet_url=url,
        local_path=str(destination),
        status="failed",
        http_status=http_status,
        attempts=attempts,
        bytes_written=0,
        error=last_error or "unknown_error",
    )


def write_log(log_path: Path, rows: List[DownloadResult]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "sku",
        "internal_lbs_sku",
        "spec_sheet_url",
        "local_path",
        "status",
        "http_status",
        "attempts",
        "bytes_written",
        "error",
    ]

    with log_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "sku": row.sku,
                    "internal_lbs_sku": row.internal_lbs_sku,
                    "spec_sheet_url": row.spec_sheet_url,
                    "local_path": row.local_path,
                    "status": row.status,
                    "http_status": row.http_status,
                    "attempts": row.attempts,
                    "bytes_written": row.bytes_written,
                    "error": row.error,
                }
            )


def main() -> int:
    args = parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    log_path = Path(args.log_path)

    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("Input CSV has no header row.")

        required = {"sku", "internal_lbs_sku", "spec_sheet_url"}
        missing = required.difference(reader.fieldnames)
        if missing:
            raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")

        rows: List[Dict[str, str]] = list(reader)

    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    session = requests.Session()
    session.headers.update({
        "User-Agent": "LBS-RAG-SpecDownloader/1.0",
        "Accept": "application/pdf,application/octet-stream,*/*",
    })

    results: List[DownloadResult] = []
    downloaded = 0
    skipped_existing = 0
    skipped_missing_url = 0
    failed = 0

    total = len(rows)

    for idx, row in enumerate(rows, start=1):
        sku = (row.get("sku") or "").strip()
        internal_lbs_sku = (row.get("internal_lbs_sku") or "").strip()
        spec_url = (row.get("spec_sheet_url") or "").strip()

        file_key = safe_file_key(internal_lbs_sku, sku)
        target = output_dir / f"{file_key}.pdf"

        if not spec_url:
            skipped_missing_url += 1
            results.append(
                DownloadResult(
                    sku=sku,
                    internal_lbs_sku=internal_lbs_sku,
                    spec_sheet_url=spec_url,
                    local_path=str(target),
                    status="skipped_missing_url",
                    http_status="",
                    attempts=0,
                    bytes_written=0,
                    error="missing_spec_sheet_url",
                )
            )
            print(f"[{idx}/{total}] SKIP missing url | {file_key}")
            continue

        if target.exists() and not args.force:
            skipped_existing += 1
            results.append(
                DownloadResult(
                    sku=sku,
                    internal_lbs_sku=internal_lbs_sku,
                    spec_sheet_url=spec_url,
                    local_path=str(target),
                    status="skipped_existing",
                    http_status="",
                    attempts=0,
                    bytes_written=target.stat().st_size,
                    error="",
                )
            )
            print(f"[{idx}/{total}] SKIP exists      | {file_key}")
            continue

        result = fetch_pdf(
            session=session,
            url=spec_url,
            destination=target,
            timeout=args.timeout,
            max_retries=args.max_retries,
            retry_wait=args.retry_wait,
        )

        result.sku = sku
        result.internal_lbs_sku = internal_lbs_sku

        if result.status == "downloaded":
            downloaded += 1
            print(f"[{idx}/{total}] OK   downloaded  | {file_key} ({result.bytes_written} bytes)")
        else:
            failed += 1
            print(f"[{idx}/{total}] FAIL {result.error} | {file_key}")

        results.append(result)

    write_log(log_path, results)

    print("\nDownload summary")
    print(f"Input rows processed: {total}")
    print(f"Downloaded: {downloaded}")
    print(f"Skipped (existing): {skipped_existing}")
    print(f"Skipped (missing url): {skipped_missing_url}")
    print(f"Failed: {failed}")
    print(f"Log written: {log_path}")
    print(f"PDF folder: {output_dir}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
