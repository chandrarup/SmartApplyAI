"""
LaTeX AST infrastructure — parse, edit regions safely, regenerate, validate round-trip.

Built on pylatexenc.latexwalker. Provides:
  - parse(tex_str) -> LatexDocument
  - find_editable_regions(doc) -> dict[region_id, RegionRef]
  - apply_edits(doc, edits) -> new_doc  (with validation)
  - regenerate(doc) -> tex_str
  - validate_balanced(tex_str) -> bool

Editable regions are demarcated by special comment markers in the template:
    % LH_REGION_BEGIN: id=summary, type=text, max_words=80
    ...content...
    % LH_REGION_END: summary

Anything OUTSIDE marked regions is immutable. Anything INSIDE a marked region
can be replaced with text that passes type+constraints.
"""
import re
import json
from typing import Any
from pylatexenc.latexwalker import LatexWalker, LatexCommentNode, LatexNode
from pylatexenc.latex2text import LatexNodes2Text

# Region marker format:
#   % LH_REGION_BEGIN: id=<name>, type=<text|bullets|skills>, [max_words=N], [max_chars=N], [protected=true]
#   % LH_REGION_END: <name>
REGION_BEGIN_RE = re.compile(
    r"%\s*LH_REGION_BEGIN:\s*id=([\w.\-]+)(?:\s*,\s*(.+?))?\s*$",
    re.MULTILINE,
)
REGION_END_RE = re.compile(
    r"%\s*LH_REGION_END:\s*([\w.\-]+)\s*$",
    re.MULTILINE,
)


class RegionSpec:
    """Constraints and metadata for one editable region."""
    def __init__(self, region_id: str, attrs: dict[str, str]):
        self.id = region_id
        self.type = attrs.get("type", "text")  # text | bullets | skills | summary
        self.max_words = int(attrs["max_words"]) if "max_words" in attrs else None
        self.max_chars = int(attrs["max_chars"]) if "max_chars" in attrs else None
        self.max_items = int(attrs["max_items"]) if "max_items" in attrs else None
        self.max_words_per_item = int(attrs["max_words_per_item"]) if "max_words_per_item" in attrs else None
        self.protected = attrs.get("protected", "").lower() == "true"

    def __repr__(self):
        return f"RegionSpec(id={self.id}, type={self.type}, max_words={self.max_words})"


class Region:
    """A located editable region inside a LaTeX document."""
    def __init__(self, spec: RegionSpec, start: int, end: int, original_content: str):
        self.spec = spec
        self.start = start  # char offset of FIRST char AFTER the begin marker line
        self.end = end      # char offset of FIRST char of the end marker line
        self.original_content = original_content
        self.current_content = original_content
        self.modified = False
        self.constraint_violations: list[str] = []

    @property
    def is_safe_to_modify(self) -> bool:
        return not self.spec.protected

    def __repr__(self):
        return f"Region({self.spec.id}, {self.end - self.start} chars)"


def _parse_attrs(attrs_str: str) -> dict[str, str]:
    """Parse 'type=text, max_words=80, protected=true' → dict."""
    if not attrs_str:
        return {}
    out = {}
    for part in attrs_str.split(","):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def find_editable_regions(tex: str) -> list[Region]:
    """Scan a LaTeX source for LH_REGION markers and return ordered Region list.

    Raises ValueError if markers are malformed (mismatched begin/end).
    """
    begins = [(m.group(1), _parse_attrs(m.group(2) or ""), m.start(), m.end()) for m in REGION_BEGIN_RE.finditer(tex)]
    ends = {m.group(1): (m.start(), m.end()) for m in REGION_END_RE.finditer(tex)}

    regions = []
    for region_id, attrs, b_start, b_end in begins:
        if region_id not in ends:
            raise ValueError(f"LaTeX region '{region_id}' has no LH_REGION_END marker")
        e_start, e_end = ends[region_id]
        if e_start < b_end:
            raise ValueError(f"LaTeX region '{region_id}' end marker appears before begin")
        # Content is everything between the lines of begin and end markers.
        # Find newline after begin marker:
        content_start = tex.find("\n", b_end)
        if content_start == -1:
            content_start = b_end
        else:
            content_start += 1
        # Find start of end marker line (back up to its preceding newline):
        content_end = tex.rfind("\n", 0, e_start)
        if content_end == -1:
            content_end = e_start
        original = tex[content_start:content_end]
        regions.append(Region(RegionSpec(region_id, attrs), content_start, content_end, original))
    return regions


def validate_balanced(tex: str) -> tuple[bool, list[str]]:
    """Quick LaTeX sanity: balanced braces, balanced \\begin/\\end, no obvious typos.
    Returns (ok, list_of_problems).
    """
    problems = []
    # Braces — ignore those inside comments and after backslash
    stripped = re.sub(r"%[^\n]*", "", tex)  # strip comments
    stripped = re.sub(r"\\.", "", stripped)  # strip escaped chars like \{
    open_count = stripped.count("{")
    close_count = stripped.count("}")
    if open_count != close_count:
        problems.append(f"Brace imbalance: {{={open_count} vs }}={close_count}")

    # Environments
    begin_envs = re.findall(r"\\begin\{(\w+)\}", tex)
    end_envs = re.findall(r"\\end\{(\w+)\}", tex)
    from collections import Counter
    bc = Counter(begin_envs)
    ec = Counter(end_envs)
    for env, n in bc.items():
        if ec.get(env, 0) != n:
            problems.append(f"Environment '{env}': \\begin x{n} vs \\end x{ec.get(env, 0)}")

    # Check for forbidden patterns that often slip through LLM output
    if re.search(r"(?<!\\)%[^\n]*\\", tex):
        # % followed by command — usually means an unescaped % in a URL or text
        # Only flag if it's not LH_REGION
        bad_lines = []
        for line in tex.split("\n"):
            if re.search(r"(?<!\\)%[^\n]*\\", line) and "LH_REGION" not in line:
                bad_lines.append(line.strip()[:80])
        if bad_lines:
            problems.append(f"Possible unescaped %: {bad_lines[:2]}")

    return (len(problems) == 0, problems)


def latex_escape(text: str) -> str:
    """Escape LaTeX special characters. Use ONLY on plain-text content."""
    if not isinstance(text, str):
        return str(text) if text is not None else ""
    replacements = [
        ("\\", r"\textbackslash{}"),  # MUST be first
        ("&", r"\&"),
        ("%", r"\%"),
        ("$", r"\$"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
    ]
    # Handle the special case where input may already have escaped chars
    # If it looks pre-escaped (has \&, \%, etc), don't double-escape.
    if re.search(r"\\[&%$#_{}]", text):
        return text  # already escaped
    for src, dst in replacements:
        text = text.replace(src, dst)
    return text


def count_words(text: str) -> int:
    """Word count for constraint checking."""
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def check_region_constraints(region: Region, new_content: str) -> list[str]:
    """Return list of constraint violations (empty = OK)."""
    problems = []
    spec = region.spec

    if spec.protected:
        if new_content != region.original_content:
            problems.append(f"Region '{spec.id}' is protected and cannot be modified")
        return problems

    if spec.max_chars and len(new_content) > spec.max_chars:
        problems.append(f"Region '{spec.id}': {len(new_content)} chars exceeds max_chars={spec.max_chars}")

    if spec.max_words:
        n = count_words(new_content)
        if n > spec.max_words:
            problems.append(f"Region '{spec.id}': {n} words exceeds max_words={spec.max_words}")

    if spec.type == "bullets" and spec.max_items:
        # Count \resumeItem{...} occurrences
        items = re.findall(r"\\resumeItem\s*\{", new_content)
        if len(items) > spec.max_items:
            problems.append(f"Region '{spec.id}': {len(items)} bullets exceeds max_items={spec.max_items}")
        if spec.max_words_per_item:
            # Each \resumeItem{...} content
            for m in re.finditer(r"\\resumeItem\s*\{([^}]*)\}", new_content):
                wc = count_words(m.group(1))
                if wc > spec.max_words_per_item:
                    problems.append(f"Region '{spec.id}': bullet has {wc} words, exceeds {spec.max_words_per_item}")
    return problems


def apply_edits(tex: str, edits: dict[str, str]) -> tuple[str, list[str]]:
    """Apply edits keyed by region_id. Returns (new_tex, violations).
    If any region has constraint violations, that edit is REJECTED and
    listed in violations. Other edits still apply.
    """
    regions = find_editable_regions(tex)
    region_by_id = {r.spec.id: r for r in regions}

    violations = []
    accepted_edits: dict[str, str] = {}
    for region_id, new_content in edits.items():
        region = region_by_id.get(region_id)
        if not region:
            violations.append(f"Region '{region_id}' not found in template")
            continue
        probs = check_region_constraints(region, new_content)
        if probs:
            violations.extend(probs)
            continue
        accepted_edits[region_id] = new_content

    # Apply edits BACK-TO-FRONT so offsets stay valid
    sorted_regions = sorted(regions, key=lambda r: r.start, reverse=True)
    out = tex
    for region in sorted_regions:
        if region.spec.id in accepted_edits:
            new_content = accepted_edits[region.spec.id]
            out = out[:region.start] + new_content + out[region.end:]

    return out, violations


def get_region_contents(tex: str) -> dict[str, str]:
    """Extract all current region contents — useful for show-the-user / diffing."""
    return {r.spec.id: r.original_content for r in find_editable_regions(tex)}


def parse_safe(tex: str) -> dict[str, Any]:
    """Parse LaTeX to AST and return summary info. Verifies the document
    can be walked without errors.
    """
    try:
        w = LatexWalker(tex)
        nodes, _, _ = w.get_latex_nodes()
        return {
            "ok": True,
            "node_count": len(nodes),
            "env_names": list({n.environmentname for n in nodes if hasattr(n, "environmentname")}),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
