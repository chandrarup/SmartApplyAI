"""Manual scraper run entrypoint — provider registry based."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from pathlib import Path
import time
from typing import Any

import requests
import yaml

from .normalize import normalize_job
from .providers.base import (
    MAX_CONCURRENCY,
    SLEEP_BETWEEN_CALLS_SECONDS,
    CompanyEntry,
)
from .providers.registry import load_providers, resolve_provider
from .store import get_conn, upsert_company_jobs

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_COMPANIES_PATH = BASE_DIR / "companies.yaml"

# Backward-compat alias for older tests/imports
CompanySpec = CompanyEntry


def load_companies(path: Path = DEFAULT_COMPANIES_PATH) -> list[CompanyEntry]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = payload.get("companies", [])
    specs: list[CompanyEntry] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        ats = str(entry.get("ats") or entry.get("provider") or "").strip().lower()
        token = str(entry.get("token") or "").strip()
        careers_url = str(entry.get("careers_url") or "").strip()
        api = str(entry.get("api") or "").strip()
        name = str(entry.get("name") or entry.get("company") or token or "").strip()
        if not ats and not careers_url and not api:
            continue
        if ats and ats != "tracker" and not token and not careers_url and not api:
            continue
        specs.append(
            CompanyEntry(
                name=name,
                ats=ats,
                token=token,
                careers_url=careers_url,
                api=api,
                cluster=str(entry.get("cluster") or "").strip(),
                source=str(entry.get("source") or "").strip(),
                max_pages=int(entry.get("max_pages") or 0),
            )
        )
    return specs


def _fetch_one(entry: CompanyEntry, providers: dict) -> tuple[CompanyEntry, str, Any]:
    provider = resolve_provider(entry, providers)
    if not provider:
        return entry, "", ValueError(f"no provider for {entry.label}")
    try:
        raw = provider.fetch(entry)
        return entry, provider.id, raw
    except Exception as exc:  # per-provider / per-company isolation
        return entry, provider.id, exc


def execute_run(companies_path: Path = DEFAULT_COMPANIES_PATH) -> dict[str, Any]:
    specs = load_companies(companies_path)
    # Always include the tracker provider once (crowd-sourced internships).
    if not any(s.ats == "tracker" for s in specs):
        specs.append(CompanyEntry(name="internship-trackers", ats="tracker", token="trackers"))

    if not specs:
        print("No companies configured. Add entries to backend/scraper/companies.yaml")
        return {"fetched": 0, "new": 0, "updated": 0, "expired": 0, "by_provider": {}}

    providers = load_providers()
    results: list[tuple[CompanyEntry, str, Any]] = []
    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as executor:
        futures = []
        for spec in specs:
            futures.append(executor.submit(_fetch_one, spec, providers))
            time.sleep(SLEEP_BETWEEN_CALLS_SECONDS)
        for fut in as_completed(futures):
            results.append(fut.result())

    totals = {"fetched": 0, "new": 0, "updated": 0, "expired": 0}
    flag_totals = {"is_internship": 0, "location_match": 0, "sponsorship_knockout": 0}
    by_provider: dict[str, dict[str, int]] = defaultdict(
        lambda: {"companies": 0, "fetched": 0, "new": 0, "updated": 0, "expired": 0, "errors": 0}
    )
    dropped_details: list[str] = []

    with get_conn() as conn:
        for entry, provider_id, raw_or_error in results:
            pid = provider_id or entry.ats or "unknown"
            by_provider[pid]["companies"] += 1

            if isinstance(raw_or_error, Exception):
                by_provider[pid]["errors"] += 1
                if isinstance(raw_or_error, requests.HTTPError) and raw_or_error.response is not None:
                    if raw_or_error.response.status_code == 404:
                        dropped_details.append(f"{entry.label} (404)")
                        print(f"{entry.label} DROPPED reason=404 provider={pid}")
                        continue
                print(
                    f"{entry.label} fetched=0 new=0 updated=0 expired=0 "
                    f"provider={pid} error={type(raw_or_error).__name__}"
                )
                continue

            raw_jobs: list[dict[str, Any]] = raw_or_error if isinstance(raw_or_error, list) else []
            if not raw_jobs:
                dropped_details.append(f"{entry.label} (zero_jobs)")
                print(f"{entry.label} DROPPED reason=zero_jobs provider={pid}")
                continue

            token = entry.token or entry.name or pid
            try:
                normalized_jobs = [normalize_job(pid, token, job) for job in raw_jobs]
            except Exception as exc:
                by_provider[pid]["errors"] += 1
                print(f"{entry.label} normalize_error={type(exc).__name__}: {exc}")
                continue

            company_scope = normalized_jobs[0]["company"] if normalized_jobs else token
            stats = upsert_company_jobs(conn, pid, company_scope, normalized_jobs)

            fetched_count = len(raw_jobs)
            totals["fetched"] += fetched_count
            totals["new"] += stats["new"]
            totals["updated"] += stats["updated"]
            totals["expired"] += stats["expired"]
            by_provider[pid]["fetched"] += fetched_count
            by_provider[pid]["new"] += stats["new"]
            by_provider[pid]["updated"] += stats["updated"]
            by_provider[pid]["expired"] += stats["expired"]
            flag_totals["is_internship"] += sum(1 for job in normalized_jobs if job["is_internship"])
            flag_totals["location_match"] += sum(1 for job in normalized_jobs if job["location_match"])
            flag_totals["sponsorship_knockout"] += sum(
                1 for job in normalized_jobs if job["sponsorship_knockout"]
            )
            print(
                f"{entry.label} provider={pid} fetched={fetched_count} "
                f"new={stats['new']} updated={stats['updated']} expired={stats['expired']}"
            )

    for pid, stats in sorted(by_provider.items()):
        print(
            f"PROVIDER {pid}: companies={stats['companies']} fetched={stats['fetched']} "
            f"new={stats['new']} updated={stats['updated']} expired={stats['expired']} "
            f"err={stats['errors']}"
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
    print("DROPPED " + (", ".join(dropped_details) if dropped_details else "none"))
    totals["by_provider"] = dict(by_provider)
    return totals


def main() -> None:
    execute_run()


if __name__ == "__main__":
    main()
