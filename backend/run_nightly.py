"""Nightly orchestrator (M6) — chains the modules so mornings are review-only.

Flow:
  1. Matching   (matcher.run) → writes matches.db with band + fit + jd_text.
  2. Tailoring  → every pending queue item is tailored (per-item isolation, rule 7),
                  so each match arrives at review already tailored.
  3. Pacing     → release_ready promotes approved rows to 'ready_to_apply' within
                  human-scale caps (rule 11). Nothing is submitted (rule 1).

Kept as its own orchestrator (not inside the matcher) so each module stays pure and
owns its store (rule 8). Run: `python -m backend.run_nightly --profile-id default`.

Flags let CI / tests exercise the chain without the heavy matcher models:
  --skip-matching   run only tailoring + pacing over an existing matches.db
  --skip-pacing     don't release anything (review-only)
"""

from __future__ import annotations

import argparse
import asyncio
import sys


def _run_matcher(profile_id: str) -> int:
    try:
        from backend.matcher.run import main as matcher_main
    except ImportError:
        from matcher.run import main as matcher_main
    argv_backup = sys.argv
    sys.argv = ["matcher", "--profile-id", profile_id]
    try:
        return matcher_main()
    finally:
        sys.argv = argv_backup


def run_nightly(
    profile_id: str = "default",
    *,
    skip_matching: bool = False,
    skip_pacing: bool = False,
) -> dict:
    # Imported lazily: pulls in the FastAPI app + pipeline, heavier than the matcher CLI.
    try:
        import backend.main as app_main
        from backend.tracker import pacing as tracker_pacing
    except ImportError:
        import main as app_main  # type: ignore
        from tracker import pacing as tracker_pacing  # type: ignore

    summary: dict = {"profile_id": profile_id}

    if not skip_matching:
        print(f"[nightly] 1/3 matching (profile={profile_id}) …")
        summary["matcher_exit"] = _run_matcher(profile_id)
    else:
        print("[nightly] 1/3 matching skipped")
        summary["matcher_exit"] = None

    print("[nightly] 2/3 tailoring pending queue items …")
    summary["tailoring"] = asyncio.run(app_main.tailor_pending_queue(profile_id))
    print(f"           {summary['tailoring']}")

    if not skip_pacing:
        print("[nightly] 3/3 pacing release …")
        released = tracker_pacing.release_ready(profile_id)
        summary["pacing"] = {"released": len(released["released"]), "held": len(released["held"])}
        print(f"           released={summary['pacing']['released']} held={summary['pacing']['held']}")
    else:
        print("[nightly] 3/3 pacing skipped")
        summary["pacing"] = None

    print(f"[nightly] done: {summary}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="SmartApplyAI nightly orchestrator")
    parser.add_argument("--profile-id", default="default")
    parser.add_argument("--skip-matching", action="store_true")
    parser.add_argument("--skip-pacing", action="store_true")
    args = parser.parse_args()
    run_nightly(
        args.profile_id,
        skip_matching=args.skip_matching,
        skip_pacing=args.skip_pacing,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
