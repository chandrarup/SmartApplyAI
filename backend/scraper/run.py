"""Manual scraper run entrypoint."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

import requests
import yaml

from .clients import MAX_CONCURRENCY, SLEEP_BETWEEN_CALLS_SECONDS, fetch_board
from .normalize import normalize_job
from .store import get_conn, upsert_company_jobs

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_COMPANIES_PATH = BASE_DIR / "companies.yaml"


@dataclass(frozen=True)
class CompanySpec:
    ats: str
    token: str


def load_companies(path: Path = DEFAULT_COMPANIES_PATH) -> list[CompanySpec]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = payload.get("companies", [])
    specs: list[CompanySpec] = []
    for entry in entries:
        ats = str(entry.get("ats", "")).strip().lower()
        token = str(entry.get("token", "")).strip()
        if not ats or not token:
            continue
        specs.append(CompanySpec(ats=ats, token=token))
    return specs


def _fetch_all(specs: list[CompanySpec]) -> dict[CompanySpec, Any]:
    results: dict[CompanySpec, Any] = {}
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as executor:
        future_map = {}
        for spec in specs:
            future_map[executor.submit(fetch_board, spec.ats, spec.token)] = spec
            time.sleep(SLEEP_BETWEEN_CALLS_SECONDS)
        for future in as_completed(future_map):
            spec = future_map[future]
            try:
                results[spec] = future.result()
            except Exception as exc:  # keep run resilient to per-company failures
                results[spec] = exc
    return results


def execute_run(companies_path: Path = DEFAULT_COMPANIES_PATH) -> dict[str, int]:
    specs = load_companies(companies_path)
    if not specs:
        print("No companies configured. Add entries to backend/scraper/companies.yaml")
        return {"fetched": 0, "new": 0, "updated": 0, "expired": 0}

    fetch_results = _fetch_all(specs)
    totals = {"fetched": 0, "new": 0, "updated": 0, "expired": 0}
    flag_totals = {"is_internship": 0, "location_match": 0, "sponsorship_knockout": 0}
    dropped_tokens: list[str] = []
    dropped_details: list[str] = []

    with get_conn() as conn:
        for spec in specs:
            raw_or_error = fetch_results.get(spec, [])
            if isinstance(raw_or_error, Exception):
                if isinstance(raw_or_error, requests.HTTPError) and raw_or_error.response is not None:
                    if raw_or_error.response.status_code == 404:
                        dropped_tokens.append(f"{spec.ats}:{spec.token}")
                        dropped_details.append(f"{spec.ats}:{spec.token} (404)")
                        print(f"{spec.ats}:{spec.token} DROPPED reason=404")
                        continue
                print(
                    f"{spec.ats}:{spec.token} fetched=0 new=0 updated=0 expired=0 "
                    f"error={type(raw_or_error).__name__}"
                )
                continue

            raw_jobs: list[dict[str, Any]] = raw_or_error
            if not raw_jobs:
                dropped_tokens.append(f"{spec.ats}:{spec.token}")
                dropped_details.append(f"{spec.ats}:{spec.token} (zero_jobs)")
                print(f"{spec.ats}:{spec.token} DROPPED reason=zero_jobs")
                continue
            normalized_jobs = [normalize_job(spec.ats, spec.token, job) for job in raw_jobs]
            company_scope = normalized_jobs[0]["company"] if normalized_jobs else spec.token
            stats = upsert_company_jobs(conn, spec.ats, company_scope, normalized_jobs)

            fetched_count = len(raw_jobs)
            totals["fetched"] += fetched_count
            totals["new"] += stats["new"]
            totals["updated"] += stats["updated"]
            totals["expired"] += stats["expired"]
            flag_totals["is_internship"] += sum(1 for job in normalized_jobs if job["is_internship"])
            flag_totals["location_match"] += sum(1 for job in normalized_jobs if job["location_match"])
            flag_totals["sponsorship_knockout"] += sum(
                1 for job in normalized_jobs if job["sponsorship_knockout"]
            )
            print(
                f"{spec.ats}:{spec.token} fetched={fetched_count} "
                f"new={stats['new']} updated={stats['updated']} expired={stats['expired']}"
            )

    print(
        "TOTAL "
        f"fetched={totals['fetched']} new={totals['new']} "
        f"updated={totals['updated']} expired={totals['expired']}"
    )
    print(
        "FLAGS "
        f"is_internship={flag_totals['is_internship']} "
        f"location_match={flag_totals['location_match']} "
        f"sponsorship_knockout={flag_totals['sponsorship_knockout']}"
    )
    if dropped_details:
        print("DROPPED " + ", ".join(dropped_details))
    else:
        print("DROPPED none")
    return totals


def main() -> None:
    execute_run()


if __name__ == "__main__":
    main()

