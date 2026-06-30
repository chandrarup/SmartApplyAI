#!/usr/bin/env python3
"""One-shot idempotent migration: master_data.json → SQLite knowledge store."""

from __future__ import annotations

import json
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES_DIR = os.path.join(BACKEND_DIR, "profiles")
LEGACY_PATH = os.path.join(BACKEND_DIR, "master_data.json")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from knowledge import store  # noqa: E402


def _load_json(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  WARN: could not read {path}: {exc}")
        return None


def _warn_unknown_keys(data: dict, pid: str) -> list[str]:
    unknown = [k for k in data if k not in store.KNOWN_SECTION_KEYS]
    for key in unknown:
        print(f"  WARN [{pid}]: unknown top-level key {key!r} (stored in sections anyway)")
    return unknown


def _migrate_profile(pid: str, data: dict) -> None:
    unknown = _warn_unknown_keys(data, pid)
    store.save_profile(pid, data)
    skills_count = sum(
        len(v) for v in (data.get("skills") or {}).values() if isinstance(v, list)
    )
    section_count = len(data)
    print(
        f"  {pid}: sections={section_count}, skills_exploded={skills_count}, "
        f"unknown_keys={len(unknown)}"
    )


def run() -> None:
    print(f"Knowledge DB: {store.DB_PATH}")
    migrated_default = False

    if os.path.isdir(PROFILES_DIR):
        for entry in sorted(os.listdir(PROFILES_DIR)):
            profile_dir = os.path.join(PROFILES_DIR, entry)
            if not os.path.isdir(profile_dir):
                continue
            master_path = os.path.join(profile_dir, "master_data.json")
            data = _load_json(master_path)
            if data is None:
                continue
            pid = entry
            print(f"Migrating profile {pid!r} from {master_path}")
            _migrate_profile(pid, data)
            if pid == "default":
                migrated_default = True

    if not migrated_default:
        legacy = _load_json(LEGACY_PATH)
        if legacy is not None:
            print(f"Migrating legacy default from {LEGACY_PATH}")
            _migrate_profile("default", legacy)
        else:
            print("No profile master_data.json files found; nothing migrated.")
    else:
        legacy = _load_json(LEGACY_PATH)
        if legacy is not None:
            print(
                f"Skipping legacy {LEGACY_PATH} — profiles/default/master_data.json "
                "already migrated."
            )

    print("Migration complete.")


if __name__ == "__main__":
    run()
