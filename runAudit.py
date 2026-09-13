"""Audit orchestration.

Runs the audit stages in order and shares expensive resources between them: one
buffered results writer and one browser session (so the operator logs in once,
not once per stage).
"""

from __future__ import annotations

import argparse
from typing import Any, Dict, Optional, Sequence

import panoptoVideo
import pullModules
import sortEmbeddedVideos
import youtubeVideo
from auditCore import (
    CAPTION_KIND_LABELS,
    ResultWriter,
    ensureDataDirs,
    summarizeResults,
)
from browser import BrowserSession


def runPipeline(
    courses: Sequence[Any],
    include_course_ids: bool = False,
    headless: bool = False,
    skip_browser: bool = False,
) -> Dict[str, int]:
    """Audit every supported platform for ``courses``.

    Args:
        courses: Canvas course IDs whose modules have already been cached.
        include_course_ids: Tag each result with the course it came from.
        headless: Run Chrome without a visible window (skips interactive login).
        skip_browser: Only run checks that need no browser.

    Returns:
        Counts of videos audited per platform.
    """

    counts = {"youtube": 0, "panopto": 0, "canvas": 0}

    with ResultWriter() as results:
        counts["youtube"] = youtubeVideo.main(
            courses, include_course_ids=include_course_ids, results=results
        )

        if skip_browser:
            print("Skipping browser-backed stages (--skip-browser).")
            return counts

        with BrowserSession(headless=headless) as browser:
            counts["panopto"] = panoptoVideo.main(
                courses,
                include_course_ids=include_course_ids,
                browser=browser,
                results=results,
            )
            counts["canvas"] = sortEmbeddedVideos.main(
                courses,
                browser=browser,
                results=results,
                include_course_ids=include_course_ids,
            )

    return counts


def printSummary() -> Dict[str, Any]:
    """Print a human readable breakdown of ``data/audited_videos.json``."""

    summary = summarizeResults()

    print("\n=== Audit summary ===")
    print(f"Videos audited : {summary['total']}")
    print(f"With captions  : {summary['withCaptions']}")
    print(f"Without        : {summary['withoutCaptions']}")

    if summary["byCaptionKind"]:
        print("\nCaption source:")
        for kind, count in sorted(summary["byCaptionKind"].items()):
            print(f"  {CAPTION_KIND_LABELS.get(kind, kind):<28} {count}")

    if summary["byType"]:
        print("\nBy platform:")
        for platform, bucket in sorted(summary["byType"].items()):
            print(
                f"  {platform:<10} {bucket['total']:>4} total, "
                f"{bucket['withCaptions']} captioned"
            )

    return summary


def main(argv: Optional[Sequence[str]] = None) -> Dict[str, int]:
    """Run a complete audit across every course the Canvas token can see."""

    parser = argparse.ArgumentParser(description="Run a complete captioning audit.")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="run Chrome headless (only works when no interactive login is needed)",
    )
    parser.add_argument(
        "--skip-browser",
        action="store_true",
        help="only run the API-based checks (YouTube)",
    )
    parser.add_argument(
        "--course",
        action="append",
        dest="courses",
        help="audit only this course ID (repeatable); skips course discovery",
    )
    args = parser.parse_args(argv)

    ensureDataDirs()

    if args.courses:
        course_ids = [str(course) for course in args.courses]
        for course_id in course_ids:
            pullModules.cacheCourse(course_id)
    else:
        course_ids = pullModules.main()

    if not course_ids:
        print("No courses to audit.")
        return {}

    counts = runPipeline(
        course_ids,
        include_course_ids=bool(args.courses),
        headless=args.headless,
        skip_browser=args.skip_browser,
    )

    printSummary()
    return counts


if __name__ == "__main__":
    main()
