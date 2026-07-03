#!/usr/bin/env python3
"""Demo runner for teach package workflow."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(CURRENT_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from knowledge import rating  # noqa: E402
import teach.fsrs as fsrs  # noqa: E402
import teach.gaps as teach_gaps  # noqa: E402
import teach.lesson as teach_lesson  # noqa: E402
import teach.store as store  # noqa: E402
import main as backend_main  # noqa: E402


def _pick_target_skill(pid: str, skills: list[str]) -> str:
    for skill in skills:
        prof = rating.get_proficiency(pid, skill)
        if prof is None or prof < 4:
            return skill
    return skills[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Teach demo flow")
    parser.add_argument("--pid", default="default")
    parser.add_argument("--llm", default="ollama")
    args = parser.parse_args()

    pid = args.pid
    # Manual gaps.yaml seeds + matcher-derived missing_skills (frequency-weighted).
    matcher_ranked = teach_gaps.matcher_gap_skills(profile_id=pid)
    skills = teach_gaps.load_gap_skills_merged(profile_id=pid)
    if not skills:
        print("No skills found in gaps.yaml or matcher missing_skills")
        return 1

    print("1) Seeding gap skills (gaps.yaml + matcher missing_skills)")
    if matcher_ranked:
        top = ", ".join(f"{name}×{freq}" for name, freq in matcher_ranked[:5])
        print(f"   matcher gaps (most common): {top}")
    for s in skills:
        rating.ensure_skill(pid, s, category="domains")
        print(f" - seeded: {s}")

    target = _pick_target_skill(pid, skills)
    print(f"\n2) Generating lesson for: {target}")
    generated = teach_lesson.lesson(
        target,
        pid=pid,
        llm_callable=backend_main.call_llm,
        llm_prefer=args.llm,
    )
    print("\n----- LESSON START -----")
    print(generated["lesson"])
    print("----- LESSON END -----\n")

    print("3) FSRS schedule skills")
    for s in skills:
        st = store.ensure_state(pid, s)
        updated = fsrs.apply_review(st, grade="good")
        store.save_state(pid, s, updated)
        print(f" - {s}: next due {updated['due_date']} (reps={updated['reps']})")

    target_learn = skills[0]
    print(f"\n4) Marking learned and checking KB write-back for: {target_learn}")
    before = rating.get_proficiency(pid, target_learn)
    evidence = f"self-study {date.today().isoformat()}"
    new_prof = min(5, (before or 2) + 1)
    rating.set_rating_by_name(
        pid=pid,
        skill_name=target_learn,
        proficiency=new_prof,
        evidence=evidence,
        source="teach.learned",
        category="domains",
    )
    backend_main._mirror_pdata_json(pid)
    after = rating.get_proficiency(pid, target_learn)
    print(f" - before={before} after={after} evidence='{evidence}'")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
