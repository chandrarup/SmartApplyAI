import os
import re


ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
SOURCE_DIR = os.path.join(ROOT_DIR, "Original _current _resume")
BASE_RESUME_PATH = os.path.join(SOURCE_DIR, "AI_ML_resume.tex")
CV_PATH = os.path.join(SOURCE_DIR, "CV.tex")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _section(tex: str, title_patterns: list[str]) -> str:
    if not tex:
        return ""
    pats = "|".join(re.escape(p) for p in title_patterns)
    m = re.search(rf"\\section\*?\{{(?:{pats})\}}", tex, re.IGNORECASE)
    if not m:
        return ""
    rest = tex[m.end():]
    n = re.search(r"\\section\*?\{", rest)
    return rest[:n.start()] if n else rest


def latex_to_plain(tex: str) -> str:
    if not tex:
        return ""
    text = tex
    text = re.sub(r"%.*", "", text)
    text = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\textbf\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\textit\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\underline\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:section|subsection)\*?\{([^}]*)\}", r"\1\n", text)
    text = re.sub(r"\\resumeItem\{([^}]*)\}", r"- \1\n", text)
    text = re.sub(r"\\CVItem\{([^}]*)\}", r"- \1\n", text)
    text = re.sub(r"\\resumeSubheading\{([^}]*)\}\{([^}]*)\}\{([^}]*)\}\{([^}]*)\}", r"\1 | \2 | \3 | \4\n", text)
    text = re.sub(r"\\CVSubheading\s*\{([^}]*)\}\s*\{([^}]*)\}\s*\{([^}]*)\}\s*\{([^}]*)\}", r"\1 | \2 | \3 | \4\n", text)
    text = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?", " ", text)
    text = text.replace("{", " ").replace("}", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_resume_projects(tex: str) -> list[dict]:
    section = _section(tex, ["Selected Projects", "SELECTED TECHNICAL PROJECTS"])
    if not section:
        return []
    entries = []
    pattern = re.compile(
        r"\\resumeProjectHeading\s*\{\s*(.*?)\s*\}\s*\{\s*(.*?)\s*\}(.*?)(?=(\\resumeProjectHeading|\\resumeSubHeadingListEnd|\\section))",
        re.S,
    )
    for head, date, body, _ in pattern.findall(section):
        title_match = re.search(r"\\textbf\{([^}]*)\}", head)
        title = title_match.group(1).strip() if title_match else re.sub(r"\\.*", "", head).strip()
        url_match = re.search(r"\\href\{([^}]*)\}", head)
        tech = []
        if "$|$" in head:
            tech = [t.strip() for t in latex_to_plain(head.split("$|$", 1)[1]).split(",") if t.strip()]
        bullets = re.findall(r"\\resumeItem\{([^}]*)\}", body, re.S)
        desc = " ".join(latex_to_plain(b) for b in bullets[:2]).strip()
        entries.append({
            "title": latex_to_plain(title),
            "url": url_match.group(1).strip() if url_match else "",
            "date": latex_to_plain(date),
            "description": desc,
            "tech_stack": tech,
        })
    if entries:
        return entries

    # CV fallback
    cv_entries = []
    chunks = re.split(r"\\CVSubheading", section)
    for chunk in chunks:
        title_match = re.search(r"\{([^}]*)\}\s*\{([^}]*)\}\s*\{([^}]*)\}", chunk)
        if not title_match:
            continue
        title = latex_to_plain(title_match.group(1))
        date = latex_to_plain(title_match.group(2))
        bullets = re.findall(r"\\CVItem\{([^}]*)\}", chunk, re.S)
        cv_entries.append({
            "title": title,
            "url": "",
            "date": date,
            "description": " ".join(latex_to_plain(b) for b in bullets[:2]).strip(),
            "tech_stack": [],
        })
    return cv_entries


def _parse_summary(tex: str) -> str:
    section = _section(tex, ["Summary"])
    if not section:
        return ""
    m = re.search(r"\\small\{(.*?)\}", section, re.S)
    return latex_to_plain(m.group(1)) if m else latex_to_plain(section[:500])


def build_resume_source_bundle() -> dict:
    base_tex = _read(BASE_RESUME_PATH)
    cv_tex = _read(CV_PATH)
    base_projects = _parse_resume_projects(base_tex)
    cv_projects = _parse_resume_projects(cv_tex)

    return {
        "base_resume_path": BASE_RESUME_PATH,
        "cv_path": CV_PATH,
        "base_resume_tex": base_tex,
        "cv_tex": cv_tex,
        "base_resume_plain": latex_to_plain(base_tex),
        "cv_plain": latex_to_plain(cv_tex),
        "base_summary": _parse_summary(base_tex),
        "base_projects": base_projects,
        "cv_projects": cv_projects,
        "editable_regions": [
            "summary",
            "skills",
            "projects",
            "experience.0.bullets",
        ],
    }


def merge_project_libraries(primary: list[dict], extras: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for item in (primary or []) + (extras or []):
        title = (item.get("title") or "").strip()
        if not title:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
