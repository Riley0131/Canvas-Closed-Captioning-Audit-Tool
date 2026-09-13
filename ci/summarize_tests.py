#!/usr/bin/env python3
"""Turn a pytest JUnit XML report into GitHub Actions job outputs.

Run after the test step (with ``if: always()`` so it runs even when tests
failed) so the ``ai-review`` job can read ``needs.test.outputs.*`` and include
the counts in its comment regardless of whether the run passed.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from typing import Dict

RESULTS_FILE = os.environ.get("TEST_RESULTS_FILE", "test-results.xml")


def totalsFromJunit(path: str) -> Dict[str, int]:
    """Sum up test/failure/error/skip counts across every ``<testsuite>``."""

    tree = ET.parse(path)
    root = tree.getroot()

    # pytest emits a single <testsuite> for one file, or wraps multiple in a
    # <testsuites> root - normalise to a list either way.
    suites = [root] if root.tag == "testsuite" else list(root)

    totals = {"total": 0, "failed": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        totals["total"] += int(suite.get("tests", 0) or 0)
        totals["failed"] += int(suite.get("failures", 0) or 0)
        totals["errors"] += int(suite.get("errors", 0) or 0)
        totals["skipped"] += int(suite.get("skipped", 0) or 0)

    return totals


def outcomeFor(counts: Dict[str, int]) -> str:
    return "success" if counts["failed"] == 0 and counts["errors"] == 0 else "failure"


def writeGithubOutput(outcome: str, counts: Dict[str, int]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return

    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"outcome={outcome}\n")
        for key, value in counts.items():
            handle.write(f"{key}={value}\n")


def main() -> None:
    if os.path.exists(RESULTS_FILE):
        counts = totalsFromJunit(RESULTS_FILE)
        outcome = outcomeFor(counts)
    else:
        counts = {"total": 0, "failed": 0, "errors": 0, "skipped": 0}
        outcome = "error"
        print(
            f"Warning: {RESULTS_FILE} was not found; the test run may have "
            "crashed before writing results."
        )

    print(f"Outcome: {outcome}, counts: {counts}")
    writeGithubOutput(outcome, counts)


if __name__ == "__main__":
    main()
