"""Apply-URL liveness classification (career-ops liveness-core patterns, plain HTTP v1).

Status: live | expired | uncertain
BOT_CHALLENGE / access-blocked → UNCERTAIN, never expired (keep reasoning as comments).

Run on queue entry; re-run on approval.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests

from .providers.base import USER_AGENT, DEFAULT_TIMEOUT_SECONDS
from .store import get_conn, DB_PATH

# Hard-expired banners (multi-language) — from career-ops liveness-core.mjs
HARD_EXPIRED_PATTERNS = [
    re.compile(p, re.I)
    for p in [
        r"job (is )?no longer available",
        r"job.*no longer open",
        r"position has been filled",
        r"this job has expired",
        r"job posting has expired",
        r"no longer accepting applications",
        r"this (position|role|job) (is )?no longer",
        r"this job (listing )?is closed",
        r"job (listing )?not found",
        r"the page you are looking for doesn.t exist",
        r"applications?\s+(?:(?:have|are|is)\s+)?closed",
        r"diese stelle (ist )?(nicht mehr|bereits) besetzt",
        r"offre (expirée|n'est plus disponible)",
    ]
]

LISTING_PAGE_PATTERNS = [
    re.compile(p, re.I)
    for p in [
        r"\d+\s+jobs?\s+found",
        r"search for jobs page is loaded",
    ]
]

# Anti-bot interstitials — MUST NOT be read as expired.
# Short challenge bodies would otherwise fall through to insufficient_content → expired.
BOT_CHALLENGE_PATTERNS = [
    re.compile(p, re.I)
    for p in [
        r"just a moment",
        r"performing security verification",
        r"checking your browser before",
        r"verify you are (a |not a )?human",
        r"enable javascript and cookies to continue",
        r"attention required.*cloudflare",
        r"\bray id\b",
        r"\bcf-ray\b",
        r"please complete the security check",
    ]
]

EXPIRED_URL_PATTERNS = [re.compile(r"[?&]error=true", re.I)]

APPLY_PATTERNS = [
    re.compile(p, re.I)
    for p in [
        r"\bapply\b",
        r"\bsolicitar\b",
        r"\bbewerben\b",
        r"\bpostuler\b",
        r"submit application",
        r"easy apply",
        r"start application",
        r"\baplikuj\b",
    ]
]

MIN_CONTENT_CHARS = 300
JOB_ID_TOKEN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d{5,}",
    re.I,
)


def _first_match(patterns: list[re.Pattern[str]], text: str) -> re.Pattern[str] | None:
    for p in patterns:
        if p.search(text or ""):
            return p
    return None


def _job_id_token(url: str = "") -> str | None:
    matches = JOB_ID_TOKEN.findall(url or "")
    return matches[-1].lower() if matches else None


def classify_liveness(
    *,
    status: int = 0,
    requested_url: str = "",
    final_url: str = "",
    body_text: str = "",
) -> dict[str, str]:
    """Return {result, code, reason} where result is live|expired|uncertain."""
    if status in (404, 410):
        return {"result": "expired", "code": "http_gone", "reason": f"HTTP {status}"}

    # Bot/anti-scraping walls — never expired. Check BEFORE content-length /
    # listing-page heuristics, which would misread a short challenge as dead.
    bot = _first_match(BOT_CHALLENGE_PATTERNS, body_text)
    if bot:
        return {
            "result": "uncertain",
            "code": "bot_challenge",
            "reason": f"anti-bot challenge: {bot.pattern}",
        }
    if status in (403, 503):
        return {
            "result": "uncertain",
            "code": "access_blocked",
            "reason": f"HTTP {status} (access blocked, likely anti-bot)",
        }

    expired_url = _first_match(EXPIRED_URL_PATTERNS, final_url)
    if expired_url:
        return {"result": "expired", "code": "expired_url", "reason": f"redirect to {final_url}"}

    expired_body = _first_match(HARD_EXPIRED_PATTERNS, body_text)
    if expired_body:
        return {
            "result": "expired",
            "code": "expired_body",
            "reason": f"pattern matched: {expired_body.pattern}",
        }

    # Permalink that lost its job id after redirect → uncertain (portal migration
    # can 301 live postings too; false "expired" permanently filters a real job).
    job_id = _job_id_token(requested_url)
    if job_id and final_url and job_id not in final_url.lower():
        return {
            "result": "uncertain",
            "code": "redirected_off_posting",
            "reason": f'redirected to {final_url} — job id "{job_id}" missing',
        }

    if _first_match(APPLY_PATTERNS, body_text):
        return {"result": "live", "code": "apply_control_visible", "reason": "apply language detected"}

    listing = _first_match(LISTING_PAGE_PATTERNS, body_text)
    if listing:
        return {
            "result": "expired",
            "code": "listing_page",
            "reason": f"pattern matched: {listing.pattern}",
        }

    if len((body_text or "").strip()) < MIN_CONTENT_CHARS:
        return {
            "result": "expired",
            "code": "insufficient_content",
            "reason": "insufficient content — likely nav/footer only",
        }

    return {
        "result": "uncertain",
        "code": "no_apply_control",
        "reason": "content present but no visible apply control found",
    }


def check_url(url: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    if not url or not str(url).startswith("http"):
        return {
            "result": "uncertain",
            "code": "missing_url",
            "reason": "no apply URL",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            allow_redirects=True,
        )
        # Strip tags lightly for pattern matching
        body = re.sub(r"<script[\s\S]*?</script>", " ", resp.text or "", flags=re.I)
        body = re.sub(r"<style[\s\S]*?</style>", " ", body, flags=re.I)
        body = re.sub(r"<[^>]+>", " ", body)
        result = classify_liveness(
            status=resp.status_code,
            requested_url=url,
            final_url=str(resp.url),
            body_text=body,
        )
    except Exception as exc:  # noqa: BLE001
        result = {
            "result": "uncertain",
            "code": "fetch_error",
            "reason": f"{type(exc).__name__}: {exc}",
        }
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    result["url"] = url
    return result


def persist_job_liveness(
    source_ats: str,
    external_id: str,
    result: dict[str, Any],
    *,
    db_path=None,
) -> None:
    status = result.get("result") or "uncertain"
    checked_at = result.get("checked_at") or datetime.now(timezone.utc).isoformat()
    with get_conn(db_path or DB_PATH) as conn:
        conn.execute(
            """
            UPDATE jobs
            SET liveness = ?, liveness_checked_at = ?
            WHERE source_ats = ? AND external_id = ?
            """,
            (status, checked_at, source_ats, external_id),
        )


def check_and_store_job(job: dict[str, Any], *, db_path=None) -> dict[str, Any]:
    result = check_url(job.get("apply_url") or "")
    if job.get("source_ats") and job.get("external_id"):
        persist_job_liveness(
            job["source_ats"], job["external_id"], result, db_path=db_path
        )
    return result
