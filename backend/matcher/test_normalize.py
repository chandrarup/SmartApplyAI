import pytest

from backend.matcher.normalize import CanonicalJob, normalize_job


@pytest.mark.parametrize(
    ("title", "normalized", "family", "level"),
    [
        ("Sr. ML Eng. (GenAI)", "senior machine learning engineer generative ai", "machine_learning", "senior"),
        ("SWE II - Platform", "software engineer ii platform", "software", "mid"),
        ("Data Scientist, Bioinformatics", "data scientist bioinformatics", "data", "unknown"),
        ("AI/ML Co-op", "artificial intelligence machine learning co op", "machine_learning", "intern"),
        ("Founding Full-Stack Product Engineer", "founding full stack product engineer", "software", "unknown"),
    ],
)
def test_non_standard_titles(title: str, normalized: str, family: str, level: str) -> None:
    job = normalize_job({"title": title, "company": "Acme"})
    assert isinstance(job, CanonicalJob)
    assert job.title_normalized == normalized
    assert job.family == family
    assert job.level == level
    assert job.taxonomy_version == "v3"


def test_metadata_is_preserved() -> None:
    job = normalize_job({"title": "ML Engineer", "company": "Acme", "location": "Remote"})
    assert job.metadata == {"company": "Acme", "location": "Remote"}


def test_unknown_title_uses_safe_fallback_seam() -> None:
    job = normalize_job(
        {"title": "Chief Wizard of Whatever"},
        fallback=lambda _: {"family": "software", "level": "executive"},
    )
    assert job.family == "software"
    assert job.level == "executive"
    assert job.fallback_used is True
