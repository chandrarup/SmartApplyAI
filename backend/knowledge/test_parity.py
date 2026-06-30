#!/usr/bin/env python3
"""Verify SQLite store round-trips master_data.json without shape drift."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_JSON = os.path.join(BACKEND_DIR, "profiles", "default", "master_data.json")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from knowledge import store  # noqa: E402


def _diff(path: str, a: Any, b: Any) -> list[str]:
  """Return human-readable diff lines (order-sensitive)."""
  lines: list[str] = []

  if type(a) is not type(b):
    lines.append(f"{path}: type {type(a).__name__} != {type(b).__name__}")
    return lines

  if isinstance(a, dict):
    keys_a = list(a.keys())
    keys_b = list(b.keys())
    if keys_a != keys_b:
      lines.append(f"{path}: key order/content mismatch")
      lines.append(f"  expected keys: {keys_a}")
      lines.append(f"  actual keys:   {keys_b}")
    for key in set(keys_a) | set(keys_b):
      if key not in a:
        lines.append(f"{path}.{key}: missing in expected")
      elif key not in b:
        lines.append(f"{path}.{key}: missing in actual")
      else:
        lines.extend(_diff(f"{path}.{key}", a[key], b[key]))
    return lines

  if isinstance(a, list):
    if len(a) != len(b):
      lines.append(f"{path}: list length {len(a)} != {len(b)}")
    for i, (x, y) in enumerate(zip(a, b)):
      lines.extend(_diff(f"{path}[{i}]", x, y))
    if len(a) > len(b):
      for i in range(len(b), len(a)):
        lines.append(f"{path}[{i}]: extra in expected: {a[i]!r}")
    elif len(b) > len(a):
      for i in range(len(a), len(b)):
        lines.append(f"{path}[{i}]: extra in actual: {b[i]!r}")
    return lines

  if a != b:
    lines.append(f"{path}: {a!r} != {b!r}")
  return lines


def _load_source_json() -> dict:
  with open(DEFAULT_JSON, encoding="utf-8") as f:
    return json.load(f)


def test_json_vs_get_profile() -> bool:
  source = _load_source_json()
  loaded = store.get_profile("default")
  diffs = _diff("root", source, loaded)
  if diffs:
    print("FAIL: profiles/default/master_data.json vs get_profile('default')")
    for line in diffs[:40]:
      print(" ", line)
    if len(diffs) > 40:
      print(f"  ... and {len(diffs) - 40} more diff lines")
    return False
  print("PASS: profiles/default/master_data.json vs get_profile('default')")
  return True


def test_round_trip() -> bool:
  source = _load_source_json()
  pid = "__parity_roundtrip__"
  try:
    store.save_profile(pid, source)
    roundtripped = store.get_profile(pid)
    diffs = _diff("root", source, roundtripped)
    if diffs:
      print("FAIL: save_profile → get_profile round-trip")
      for line in diffs[:40]:
        print(" ", line)
      if len(diffs) > 40:
        print(f"  ... and {len(diffs) - 40} more diff lines")
      return False
    print("PASS: save_profile → get_profile round-trip")
    return True
  finally:
    with store._connect() as conn:
      conn.execute("DELETE FROM sections WHERE profile_id = ?", (pid,))
      conn.execute("DELETE FROM skills WHERE profile_id = ?", (pid,))
      conn.commit()


def main() -> int:
  if not os.path.isfile(DEFAULT_JSON):
    print(f"FAIL: missing {DEFAULT_JSON}")
    return 1
  if not store.profile_exists("default"):
    print("FAIL: profile 'default' not in knowledge DB — run migrate.py first")
    return 1

  ok = test_json_vs_get_profile() and test_round_trip()
  print("OVERALL:", "PASS" if ok else "FAIL")
  return 0 if ok else 1


if __name__ == "__main__":
  raise SystemExit(main())
