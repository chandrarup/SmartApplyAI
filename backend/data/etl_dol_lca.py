"""Offline ETL helpers for US DOL OFLC LCA disclosure files."""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

_PUNCT = re.compile(r"[^a-z0-9]+")
_SPACE = re.compile(r"\s+")


def _norm(value: Any) -> str:
    value = _PUNCT.sub(" ", str(value or "").lower())
    return _SPACE.sub(" ", value).strip()


def normalize_employer(value: Any) -> str:
    """Return a stable employer key, dropping common corporate suffixes."""
    result = _norm(value)
    result = re.sub(r"\b(incorporated|corporation|company|limited|llc|inc|corp|co)\b", "", result)
    return _SPACE.sub(" ", result).strip()


def normalize_domain(value: Any) -> str:
    return _norm(value)


def normalize_soc(value: Any) -> str:
    match = re.search(r"\d{2}-\d{4}", str(value or ""))
    return match.group(0) if match else _norm(value)


def _value(row: dict[str, Any], *names: str) -> str:
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for name in names:
        if name.lower() in lowered:
            return str(lowered[name.lower()] or "").strip()
    return ""


def load_lca_csv(path: str | Path) -> list[dict[str, Any]]:
    """Read a DOL CSV without network access and normalize its lookup fields."""
    records: list[dict[str, Any]] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            employer = normalize_employer(_value(row, "EMPLOYER_NAME", "employer_name"))
            status = _norm(_value(row, "CASE_STATUS", "case_status"))
            records.append(
                {
                    "employer": employer,
                    "status": status,
                    "soc": normalize_soc(_value(row, "SOC_CODE", "soc_code")),
                    "soc_title": _value(row, "SOC_TITLE", "soc_title"),
                    "domain": normalize_domain(_value(row, "NAICS_CODE", "NAICS_TITLE", "domain")),
                    "received_date": _value(row, "RECEIVED_DATE", "received_date"),
                    "decision_date": _value(row, "DECISION_DATE", "decision_date"),
                }
            )
    return records


def aggregate_lca_records(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate case volume, outcomes, SOCs, and domains per normalized employer."""
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        employer = normalize_employer(record.get("employer"))
        if not employer:
            continue
        item = result.setdefault(
            employer,
            {
                "total_cases": 0,
                "certified_cases": 0,
                "denied_cases": 0,
                "recent_cases": 0,
                "soc_counts": defaultdict(int),
                "domain_counts": defaultdict(int),
            },
        )
        item["total_cases"] += 1
        status = _norm(record.get("status"))
        if status in {"certified", "certified expired"}:
            item["certified_cases"] += 1
        if "denied" in status:
            item["denied_cases"] += 1
        soc = normalize_soc(record.get("soc"))
        if soc:
            item["soc_counts"][soc] += 1
        domain = normalize_domain(record.get("domain"))
        if domain:
            item["domain_counts"][domain] += 1
    for item in result.values():
        item["soc_counts"] = dict(item["soc_counts"])
        item["domain_counts"] = dict(item["domain_counts"])
    return result
