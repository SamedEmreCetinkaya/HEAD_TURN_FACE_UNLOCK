#!/usr/bin/python3 -IB
"""Runs every test with line coverage and holds each module to its floor (a ratchet).

Usage: python3 -IB TOOLS/CI/COVERAGE.py run|reset

  run    The tests run under coverage.py. A module below its floor in COVERAGE_FLOOR.txt fails
         the run; a module above it raises its floor in the file (commit the change). A new
         module enters at its measurement; a removed module leaves the file.
  reset  Sets every floor to the measurement: the deliberate way to lower one. Say why in
         the commit.

Areas: every module of SOURCE/head_turn_face_unlock and TOTAL. Percentages are rounded down
to one decimal, so the same code always gives the same file. The tests that need the
installed networks are skipped here even where they are installed (`make test` runs them),
so a clone without them measures the same. Needs coverage.py (python3-coverage); `make
doctor` says how to install it.
"""

import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
PACKAGE = os.path.join(ROOT, "SOURCE", "head_turn_face_unlock")
TESTS = os.path.join(ROOT, "TESTS")
FLOOR_FILE = os.path.join(ROOT, "TOOLS", "CI", "COVERAGE_FLOOR.txt")
HEADER = ("# Coverage floors: `make coverage` compares every module with these.\n"
          "# Not edited by hand: the tool raises a floor when coverage rises, and\n"
          "# `make coverage-floor` lowers floors on purpose. See TOOLS/CI/COVERAGE.py.\n")


def measure() -> dict[str, float] | None:
    """Percent of statements run per module, or None when a test failed."""
    try:
        import coverage
    except ImportError:
        print("coverage.py is missing: make doctor", file=sys.stderr)
        sys.exit(2)
    for variable in ("HEAD_TURN_FACE_UNLOCK_MODELS", "HEAD_TURN_FACE_UNLOCK_RUNTIME"):
        os.environ[variable] = os.path.join(ROOT, "NOT_INSTALLED")
    sys.path.insert(0, TESTS)
    tracker = coverage.Coverage(source=[PACKAGE], data_file=None, branch=False)
    tracker.start()
    suite = unittest.defaultTestLoader.discover(TESTS, pattern="test_*.py", top_level_dir=TESTS)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    tracker.stop()
    if not result.wasSuccessful():
        return None
    areas: dict[str, float] = {}
    total_statements = total_missing = 0
    for name in sorted(os.listdir(PACKAGE)):
        if not name.endswith(".py"):
            continue
        _, statements, _, missing, _ = tracker.analysis2(os.path.join(PACKAGE, name))
        total_statements += len(statements)
        total_missing += len(missing)
        areas[name[:-3]] = _percent(len(statements) - len(missing), len(statements))
    areas["TOTAL"] = _percent(total_statements - total_missing, total_statements)
    return areas


def _percent(covered: int, statements: int) -> float:
    return math.floor(1000 * covered / statements) / 10 if statements else 100.0


def read_floors() -> dict[str, float]:
    floors: dict[str, float] = {}
    try:
        with open(FLOOR_FILE, encoding="utf-8") as handle:
            for line in handle:
                line = line.split("#", 1)[0].strip()
                if line:
                    area, value = line.split()
                    floors[area] = float(value)
    except FileNotFoundError:
        pass
    return floors


def write_floors(floors: dict[str, float]) -> None:
    ordered = sorted((area for area in floors if area != "TOTAL")) + ["TOTAL"]
    with open(FLOOR_FILE, "w", encoding="utf-8") as handle:
        handle.write(HEADER)
        for area in ordered:
            handle.write(f"{area} {floors[area]:.1f}\n")


def main(argv: list[str]) -> int:
    if argv not in (["run"], ["reset"]):
        print(__doc__, file=sys.stderr)
        return 2
    measured = measure()
    if measured is None:
        print("RED: a test failed", file=sys.stderr)
        return 1
    if argv == ["reset"]:
        write_floors(measured)
        print(f"floors set to the measurement ({os.path.relpath(FLOOR_FILE, ROOT)})")
        return 0
    floors = read_floors()
    below = [(area, floors[area], value) for area, value in measured.items() if area in floors and value < floors[area]]
    raised = [(area, floors.get(area), value) for area, value in measured.items() if value > floors.get(area, -1.0)]
    for area, value in measured.items():
        print(f"  {area:16s} {value:5.1f}%  (floor {floors.get(area, value):.1f})")
    if below:
        for area, floor, value in below:
            print(f"RED: {area} fell to {value:.1f}% below its floor {floor:.1f}%", file=sys.stderr)
        print("Add tests, or lower the floor on purpose with: make coverage-floor", file=sys.stderr)
        return 1
    if raised or set(floors) != set(measured):
        write_floors({area: max(value, floors.get(area, 0.0)) for area, value in measured.items()})
        for area, previous, value in raised:
            print(f"floor raised: {area} {previous if previous is not None else 'new'} -> {value:.1f} "
                  "(commit COVERAGE_FLOOR.txt)")
    print("coverage: every module at or above its floor")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
