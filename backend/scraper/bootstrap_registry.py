"""One-time bootstrap tool — harvest ATS tokens from internship tracker repos.

Run manually (NOT part of nightly):
  python -m backend.scraper.bootstrap_registry
  # or: cd backend && python -m scraper.bootstrap_registry

Prints: harvested, verified, added, failed.
Appends verified new entries to companies.yaml with `# source: bootstrap`.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import yaml

from .providers.base import USER_AGENT, DEFAULT_TIMEOUT_SECONDS

BASE_DIR = Path(__file__).resolve().parent
COMPANIES_PATH = BASE_DIR / "companies.yaml"

SOURCES = (
    ("readme", "https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/README.md"),
    ("readme", "https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/INTERN_INTL.md"),
    ("readme", "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/README.md"),
    (
        "json",
        "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json",
    ),
)

# Explicit bio-AI cluster seed (careers URLs / known boards when resolvable).
BIO_AI_SEED: list[dict[str, str]] = [
    {"name": "Genentech", "careers_url": "https://careers.gene.com/", "cluster": "bio-ai"},
    {"name": "Xaira", "careers_url": "https://jobs.ashbyhq.com/xaira", "cluster": "bio-ai"},
    {"name": "Lila", "careers_url": "https://jobs.ashbyhq.com/lila", "cluster": "bio-ai"},
    {"name": "PathAI", "careers_url": "https://www.pathai.com/careers", "cluster": "bio-ai"},
    {"name": "10x Genomics", "careers_url": "https://boards.greenhouse.io/10xgenomics", "cluster": "bio-ai"},
    {"name": "Recursion", "careers_url": "https://boards.greenhouse.io/recursionpharmaceuticals", "cluster": "bio-ai"},
    {"name": "Tempus", "careers_url": "https://www.tempus.com/careers/", "cluster": "bio-ai"},
    {"name": "Insitro", "careers_url": "https://boards.greenhouse.io/insitro", "cluster": "bio-ai"},
    {"name": "CZI", "careers_url": "https://boards.greenhouse.io/chanzuckerberginitiative", "cluster": "bio-ai"},
    {"name": "Anthropic", "ats": "greenhouse", "token": "anthropic", "cluster": "bio-ai"},
    {"name": "OpenAI", "careers_url": "https://openai.com/careers", "cluster": "bio-ai"},
]

_URL_RE = re.compile(r"https?://[^\s<>)\]\"]+")
_GH = re.compile(r"boards\.greenhouse\.io/([\w-]+)", re.I)
_GH_API = re.compile(r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)", re.I)
_LEVER = re.compile(r"jobs\.(?:eu\.)?lever\.co/([\w-]+)", re.I)
_ASHBY = re.compile(r"jobs\.ashbyhq\.com/([\w-]+)", re.I)
_WD = re.compile(
    r"(https://[\w-]+\.wd[\w-]*\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?[^/?#\s]+)",
    re.I,
)
_ICIMS = re.compile(r"(careers-[\w-]+\.icims\.com)", re.I)


def _get(url: str) -> str:
    resp = requests.get(url, timeout=DEFAULT_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.text


def extract_from_text(text: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for url in _URL_RE.findall(text or ""):
        hit = classify_url(url)
        if hit:
            found.append(hit)
    return found


def classify_url(url: str) -> dict[str, str] | None:
    m = _GH.search(url) or _GH_API.search(url)
    if m:
        return {"ats": "greenhouse", "token": m.group(1), "careers_url": url}
    m = _LEVER.search(url)
    if m:
        return {"ats": "lever", "token": m.group(1), "careers_url": url}
    m = _ASHBY.search(url)
    if m:
        return {"ats": "ashby", "token": m.group(1), "careers_url": url}
    m = _WD.search(url)
    if m:
        return {"ats": "workday", "token": "", "careers_url": m.group(1)}
    m = _ICIMS.search(url)
    if m:
        # iCIMS often login-walled — keep careers_url for future; skip token probe
        return {"ats": "icims", "token": m.group(1), "careers_url": f"https://{m.group(1)}/"}
    return None


def harvest() -> list[dict[str, str]]:
    harvested: list[dict[str, str]] = []
    for kind, url in SOURCES:
        try:
            body = _get(url)
        except Exception as exc:
            print(f"⚠️ bootstrap source failed {url}: {exc}")
            continue
        if kind == "json":
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                print(f"⚠️ bootstrap: bad JSON at {url}")
                continue
            items = data if isinstance(data, list) else data.get("listings") or []
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                for key in ("url", "application_url", "apply_url", "link"):
                    u = item.get(key)
                    if isinstance(u, str) and u.startswith("http"):
                        hit = classify_url(u)
                        if hit:
                            if item.get("company_name"):
                                hit["name"] = str(item["company_name"])
                            harvested.append(hit)
        else:
            harvested.extend(extract_from_text(body))

    for seed in BIO_AI_SEED:
        hit = dict(seed)
        if hit.get("careers_url") and not hit.get("ats"):
            classified = classify_url(hit["careers_url"])
            if classified:
                hit.update({k: v for k, v in classified.items() if v})
        harvested.append(hit)
    return harvested


def _existing_keys(path: Path = COMPANIES_PATH) -> set[str]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    keys: set[str] = set()
    for e in payload.get("companies") or []:
        if not isinstance(e, dict):
            continue
        ats = str(e.get("ats") or "").lower()
        token = str(e.get("token") or "").lower()
        url = str(e.get("careers_url") or "").lower()
        if ats and token:
            keys.add(f"{ats}:{token}")
        if url:
            keys.add(f"url:{url}")
    return keys


def _dedupe_key(entry: dict[str, str]) -> str:
    ats = (entry.get("ats") or "").lower()
    token = (entry.get("token") or "").lower()
    url = (entry.get("careers_url") or "").lower()
    if ats and token:
        return f"{ats}:{token}"
    if url:
        return f"url:{url}"
    return f"name:{(entry.get('name') or '').lower()}"


def probe(entry: dict[str, str]) -> bool:
    """Live probe — HTTP 200 on the board API keeps the entry."""
    ats = (entry.get("ats") or "").lower()
    token = entry.get("token") or ""
    careers = entry.get("careers_url") or ""
    try:
        if ats == "greenhouse" and token:
            url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
        elif ats == "lever" and token:
            url = f"https://api.lever.co/v0/postings/{token}?mode=json"
        elif ats == "ashby" and token:
            url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
        elif ats == "workday" and careers:
            from .providers.workday import detect
            from .providers.base import CompanyEntry

            api = detect(CompanyEntry(ats="workday", careers_url=careers))
            if not api:
                return False
            resp = requests.post(
                api,
                json={"limit": 1, "offset": 0, "searchText": "", "appliedFacets": {}},
                timeout=DEFAULT_TIMEOUT_SECONDS,
                headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
            )
            return resp.status_code == 200
        elif careers:
            resp = requests.get(
                careers, timeout=DEFAULT_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT},
                allow_redirects=True,
            )
            return resp.status_code == 200
        else:
            return False
        resp = requests.get(url, timeout=DEFAULT_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
        return resp.status_code == 200
    except Exception:
        return False


def append_companies(entries: list[dict[str, str]], path: Path = COMPANIES_PATH) -> int:
    if not entries:
        return 0
    block = ["", "# --- source: bootstrap ---"]
    added = 0
    for e in entries:
        if e.get("ats") == "icims":
            continue
        item: dict[str, Any] = {"source": "bootstrap"}
        for k in ("name", "ats", "token", "careers_url", "cluster"):
            if e.get(k):
                item[k] = e[k]
        if not item.get("ats") and not item.get("careers_url"):
            continue
        if item.get("ats") == "workday":
            item.pop("token", None)
        dumped = yaml.safe_dump([item], default_flow_style=True).strip()
        if dumped.startswith("- "):
            block.append("  " + dumped)
            added += 1
    if added == 0:
        return 0
    text = path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text + "\n".join(block) + "\n", encoding="utf-8")
    return added


def run(dry_run: bool = False) -> dict[str, int]:
    raw = harvest()
    existing = _existing_keys()
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for e in raw:
        key = _dedupe_key(e)
        if key in seen or key in existing:
            continue
        # also skip if ats:token already known under different url key
        ats_tok = f"{(e.get('ats') or '').lower()}:{(e.get('token') or '').lower()}"
        if e.get("token") and ats_tok in existing:
            continue
        seen.add(key)
        unique.append(e)

    verified: list[dict[str, str]] = []
    failed: list[str] = []
    for e in unique:
        label = e.get("name") or e.get("token") or e.get("careers_url") or "?"
        if probe(e):
            verified.append(e)
            print(f"  verified {label} ({e.get('ats')})")
        else:
            failed.append(label)
            print(f"  failed   {label} ({e.get('ats')})")

    added = 0
    if not dry_run:
        added = append_companies(verified)
    else:
        added = len(verified)
        print("(dry-run — companies.yaml not written)")

    summary = {
        "harvested": len(raw),
        "unique_new": len(unique),
        "verified": len(verified),
        "added": added,
        "failed": len(failed),
    }
    print(
        f"BOOTSTRAP harvested={summary['harvested']} unique_new={summary['unique_new']} "
        f"verified={summary['verified']} added={summary['added']} failed={summary['failed']}"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap companies.yaml from tracker repos")
    parser.add_argument("--dry-run", action="store_true", help="Probe but do not write YAML")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
