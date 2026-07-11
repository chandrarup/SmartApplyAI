"""Posting legitimacy assessment (career-ops Block G, adapted).

Independent of match_pct — observations, not accusations. Suspicious items are
badged and shown; never auto-dropped from the queue.

Signals (v1, free/observable only):
  - description quality (tech specificity, realism, boilerplate ratio, contradictions)
  - reposting (first_seen age / reappear after expire when fields present)
  - role–market plausibility (title vs requirements level mismatch)
  - US contractor-language note (1099 / independent contractor / W-2 not provided)
  - web research: ≤2 queries, Strong-band only ("{company} layoffs", "{company} hiring freeze")

Tiers: high_confidence | caution | suspicious
Insufficient data → caution, never suspicious without evidence.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import quote_plus

import requests

TIERS = ("high_confidence", "caution", "suspicious")

_TECH_TERMS = re.compile(
    r"\b(python|pytorch|tensorflow|java|c\+\+|sql|aws|azure|gcp|kubernetes|docker|"
    r"llm|rag|nlp|cuda|spark|hadoop|react|typescript|golang|rust|kafka|redis|"
    r"postgres|mongodb|fastapi|django|flask|pytorch|huggingface|langchain)\b",
    re.I,
)
_BOILERPLATE = re.compile(
    r"\b(equal opportunity|eeo|affirmative action|reasonable accommodation|"
    r"diversity and inclusion|we are an equal|all qualified applicants|"
    r"without regard to race|click here to apply|join our team|"
    r"fast[- ]paced environment|wear many hats|rockstar|ninja|guru)\b",
    re.I,
)
_ROLE_SPECIFIC = re.compile(
    r"\b(you will|responsibilit|requirement|qualification|what you.?ll do|"
    r"day to day|tech stack|our team|this role|about the role)\b",
    re.I,
)
_CONTRADICTION = re.compile(
    r"(entry[- ]level.{0,40}(5\+|five|senior|staff))|"
    r"(intern.{0,40}(5\+|seven|10\+) years)|"
    r"(junior.{0,40}(staff|principal|director))",
    re.I | re.S,
)
_CONTRACTOR = re.compile(
    r"\b(1099|independent contractor|w-?2 not provided|corp[- ]to[- ]corp|"
    r"no benefits provided)\b",
    re.I,
)
_SENIOR_TITLE = re.compile(r"\b(senior|staff|principal|director|manager|lead)\b", re.I)
_JUNIOR_TITLE = re.compile(r"\b(intern|internship|co-?op|junior|entry[- ]level|new grad)\b", re.I)
_YEARS_REQ = re.compile(r"(\d+)\+?\s*years?\s+(of\s+)?experience", re.I)

WebSearchFn = Callable[[str], list[dict[str, str]]]


def _count(pattern: re.Pattern[str], text: str) -> int:
    return len(pattern.findall(text or ""))


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def assess_description_quality(jd_text: str) -> list[dict[str, str]]:
    """Pure-text JD quality signals."""
    jd = str(jd_text or "")
    signals: list[dict[str, str]] = []
    if not jd.strip():
        return [{"code": "empty_jd", "detail": "No job description text available", "severity": "concern"}]

    tech = _count(_TECH_TERMS, jd)
    boiler = _count(_BOILERPLATE, jd)
    roleish = _count(_ROLE_SPECIFIC, jd)
    words = max(1, len(jd.split()))

    if tech >= 3:
        signals.append({
            "code": "tech_specificity",
            "detail": f"Names {tech} concrete technologies",
            "severity": "info",
        })
    elif tech == 0 and words > 80:
        signals.append({
            "code": "generic_tech",
            "detail": "Long JD with no named technologies — common in both ghost posts and poorly written real ones",
            "severity": "concern",
        })

    if boiler > roleish and boiler >= 3:
        signals.append({
            "code": "boilerplate_heavy",
            "detail": f"Boilerplate phrases ({boiler}) outnumber role-specific cues ({roleish})",
            "severity": "concern",
        })
    elif roleish >= 3:
        signals.append({
            "code": "role_specific",
            "detail": "JD has role-specific responsibility/requirement language",
            "severity": "info",
        })

    if _CONTRADICTION.search(jd):
        signals.append({
            "code": "internal_contradiction",
            "detail": "Possible level contradiction (e.g. intern/junior title with senior years)",
            "severity": "concern",
        })

    # Unrealistic: entry title + many years
    title_blob = jd[:400]
    years = _YEARS_REQ.search(jd)
    if years and _JUNIOR_TITLE.search(title_blob) and int(years.group(1)) >= 5:
        signals.append({
            "code": "unrealistic_years",
            "detail": f"Junior/intern framing with {years.group(1)}+ years required",
            "severity": "concern",
        })

    return signals


def assess_reposting(job: dict[str, Any], *, now: datetime | None = None) -> list[dict[str, str]]:
    """Repost / staleness from first_seen / last_seen when present on the job row."""
    now = now or datetime.now(timezone.utc)
    signals: list[dict[str, str]] = []
    first = _parse_iso(job.get("first_seen"))
    last = _parse_iso(job.get("last_seen"))
    status = str(job.get("status") or "")

    if first:
        age_days = (now - first.astimezone(timezone.utc)).days
        if age_days >= 60:
            signals.append({
                "code": "posting_age",
                "detail": f"First seen ~{age_days} days ago (60d+ can be normal for niche roles)",
                "severity": "concern",
            })
        elif age_days <= 30:
            signals.append({
                "code": "posting_fresh",
                "detail": f"First seen ~{age_days} days ago",
                "severity": "info",
            })

    # Reappear after expire: status flipped back to active with old first_seen
    if status == "active" and first and last and (last - first).days >= 14:
        # Heuristic only — store may update last_seen every scrape
        pass

    repost_count = int(job.get("repost_count") or 0)
    if repost_count >= 2:
        signals.append({
            "code": "reposting",
            "detail": f"Same role fingerprint seen {repost_count} times in our store",
            "severity": "concern",
        })
    return signals


def assess_role_plausibility(title: str, jd_text: str) -> list[dict[str, str]]:
    signals: list[dict[str, str]] = []
    title = str(title or "")
    jd = str(jd_text or "")
    years = _YEARS_REQ.search(jd)
    if _SENIOR_TITLE.search(title) and years and int(years.group(1)) <= 1:
        signals.append({
            "code": "title_years_mismatch",
            "detail": "Senior-style title with ≤1 year experience required — unusual, verify",
            "severity": "concern",
        })
    if _JUNIOR_TITLE.search(title) and years and int(years.group(1)) >= 7:
        signals.append({
            "code": "title_years_mismatch",
            "detail": "Junior/intern title with 7+ years required — unusual, verify",
            "severity": "concern",
        })
    return signals


def assess_contractor_language(jd_text: str) -> list[dict[str, str]]:
    if _CONTRACTOR.search(jd_text or ""):
        return [{
            "code": "contractor_language",
            "detail": "JD mentions 1099 / independent contractor / W-2 not provided — note only, not a scam flag",
            "severity": "info",
        }]
    return []


def default_web_search(query: str, timeout: float = 8.0) -> list[dict[str, str]]:
    """Plain DuckDuckGo HTML scrape — best-effort, fail soft. Cap callers at 2 queries."""
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "SmartApplyAI-legitimacy/1.0 (personal job search)"},
        )
        if resp.status_code != 200:
            return []
        text = resp.text.lower()
        hits: list[dict[str, str]] = []
        for needle, label in (
            ("layoff", "layoff mention"),
            ("hiring freeze", "hiring freeze mention"),
            ("job cut", "job cut mention"),
        ):
            if needle in text and any(w in query.lower() for w in needle.split()):
                hits.append({"title": label, "snippet": f"Page mentions '{needle}' for query: {query}"})
        # Also flag if company+layoff both appear densely
        if "layoff" in text or "laid off" in text:
            hits.append({"title": "layoff-related result", "snippet": query})
        if "hiring freeze" in text:
            hits.append({"title": "hiring-freeze-related result", "snippet": query})
        return hits[:3]
    except Exception:  # noqa: BLE001
        return []


def assess_web_signals(
    company: str,
    *,
    web_search: WebSearchFn | None = None,
    year: int | None = None,
) -> list[dict[str, str]]:
    """At most 2 queries. Observations only."""
    company = (company or "").strip()
    if not company or len(company) < 2:
        return []
    year = year or datetime.now(timezone.utc).year
    search = web_search or default_web_search
    signals: list[dict[str, str]] = []
    queries = [f'"{company}" layoffs {year}', f'"{company}" hiring freeze {year}']
    for q in queries[:2]:
        hits = search(q) or []
        if hits:
            signals.append({
                "code": "web_hiring_signal",
                "detail": f"Web results for {q} mention layoffs/freeze — may be unrelated dept/timing; verify before deciding",
                "severity": "concern",
            })
    return signals


def tier_from_signals(signals: list[dict[str, str]]) -> str:
    """Conservative: suspicious only with multiple concerns; empty → caution."""
    if not signals:
        return "caution"
    concerns = [s for s in signals if s.get("severity") == "concern"]
    infos = [s for s in signals if s.get("severity") == "info"]
    # Hard quality failures
    hard = {s.get("code") for s in concerns}
    if "internal_contradiction" in hard and "unrealistic_years" in hard:
        return "suspicious"
    if len(concerns) >= 3:
        return "suspicious"
    if len(concerns) >= 1:
        return "caution"
    if infos:
        return "high_confidence"
    return "caution"


def assess_legitimacy(
    job: dict[str, Any],
    *,
    match_pct: int = 0,
    strong_threshold: int = 85,
    enable_web: bool = True,
    web_search: WebSearchFn | None = None,
) -> dict[str, Any]:
    """Return legitimacy object. Does NOT modify match_pct. Never drops the job."""
    jd = job.get("description_text") or ""
    title = job.get("title") or ""
    company = job.get("company") or ""

    signals: list[dict[str, str]] = []
    signals.extend(assess_description_quality(jd))
    signals.extend(assess_reposting(job))
    signals.extend(assess_role_plausibility(title, jd))
    signals.extend(assess_contractor_language(jd))

    # Web research capped at 2 queries and ONLY for Strong-band items.
    if enable_web and int(match_pct) >= int(strong_threshold):
        signals.extend(assess_web_signals(company, web_search=web_search))

    tier = tier_from_signals(signals)
    return {
        "tier": tier,
        "signals": signals,
        "note": (
            "Observations to help prioritize time — not accusations. "
            "Every signal has legitimate explanations; you decide."
        ),
    }
