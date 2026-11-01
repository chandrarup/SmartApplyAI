"""Compatibility adapter from scraper rows to matcher-ready job records."""

from __future__ import annotations

from typing import Any, Callable, Mapping


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def ingest_jobs(
    raw_jobs: list[Mapping[str, Any]],
    *,
    normalizer: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Normalize scraper records while preserving the matcher input contract.

    The scraper already emits normalized rows. This adapter makes that seam
    explicit and accepts a future canonical normalizer without coupling the
    matcher to a particular scraper provider.
    """
    result: list[dict[str, Any]] = []
    for raw in raw_jobs:
        if not isinstance(raw, Mapping):
            continue
        try:
            item = dict(normalizer(raw) if normalizer else raw)
        except Exception as exc:
            print(f"[matcher-ingest] skip job normalization={type(exc).__name__}: {exc}")
            continue
        item["source_ats"] = _text(item.get("source_ats") or item.get("source"))
        item["external_id"] = _text(
            item.get("external_id") or item.get("id") or item.get("job_id")
        )
        item["company"] = _text(item.get("company") or item.get("company_name"))
        item["title"] = _text(item.get("title") or item.get("name") or item.get("text"))
        item["description_text"] = _text(
            item.get("description_text")
            or item.get("description")
            or item.get("descriptionPlain")
            or item.get("descriptionHtml")
        )
        item["location"] = _text(item.get("location") or item.get("locationsText"))
        item["apply_url"] = _text(
            item.get("apply_url") or item.get("applyUrl") or item.get("hostedUrl")
        )
        item["matched_searches"] = item.get("matched_searches") or []
        result.append(item)
    return result
