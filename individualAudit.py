"""Audit a single Canvas course.

Pulls just that course's modules and then runs the same pipeline a full audit
uses, so both entry points stay in step.
"""

from __future__ import annotations

import sys
from typing import Any, Dict

import pullModules
import runAudit
from auditCore import ensureDataDirs


def main(courseID: Any, headless: bool = False) -> Dict[str, int]:
    """Audit one course and append the results to ``data/audited_videos.json``.

    Args:
        courseID: The Canvas course ID to audit.
        headless: Run Chrome without a visible window.

    Returns:
        Counts of videos audited per platform.
    """

    ensureDataDirs()
    course_id = str(courseID)

    pullModules.cacheCourse(course_id)

    counts = runAudit.runPipeline(
        [course_id], include_course_ids=True, headless=headless
    )
    runAudit.printSummary()
    return counts


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python individualAudit.py <courseID>")
    else:
        main(sys.argv[1])
