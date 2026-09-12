"""Embedded Canvas media auditor.

Canvas-hosted media pages cannot be inspected through the API, so this stage
loads each page in a browser and looks for the player's caption control.

It now shares one browser (and therefore one Canvas login) with the Panopto
stage, de-duplicates URLs before visiting them, and buffers results instead of
rewriting the whole results file per video.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from auditCore import (
    CAPTION_NONE,
    CAPTION_UNKNOWN,
    ResultWriter,
    isCancelled,
    loadJson,
    sortedModulesPath,
)
from browser import BrowserSession

try:
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
except Exception:  # pragma: no cover - Selenium optional for unit tests
    TimeoutException = WebDriverException = Exception  # type: ignore
    By = None  # type: ignore
    EC = None  # type: ignore
    WebDriverWait = None  # type: ignore

CANVAS_LOGIN_URL = os.environ.get(
    "CANVAS_LOGIN_URL", "https://canvas.uccs.edu/login"
)

# Canvas' media player exposes the captions toggle under a handful of labels
# depending on the player version in use.
CAPTION_SELECTORS = (
    "button.controls-button[aria-label='Enable Captions']",
    "button[aria-label*='Caption' i]",
    "button[title*='Caption' i]",
    "[data-testid*='caption' i]",
    "track[kind='captions'], track[kind='subtitles']",
)


def truncateCanvasUrl(links: Iterable[str]) -> List[str]:
    """Strip the API prefix so links point at the user-facing Canvas page."""

    return [link.replace("/api/v1", "") for link in links if isinstance(link, str)]


def collectCanvasVideos(courses: Iterable[Any]) -> List[Tuple[str, str]]:
    """Return ``[(course_id, url), ...]`` for cached Canvas media links."""

    videos: List[Tuple[str, str]] = []
    seen: set = set()

    for course in courses:
        payload = loadJson(sortedModulesPath(course))
        if not isinstance(payload, dict):
            print(f"Canvas: no cached modules for course {course}, skipping.")
            continue

        for url in truncateCanvasUrl(payload.get("canvas", []) or []):
            if url in seen:
                continue
            seen.add(url)
            videos.append((str(course), url))

    return videos


def inspectCanvasPage(driver: Any, url: str, timeout: int = 5) -> Optional[Dict[str, Any]]:
    """Inspect one Canvas page.

    Returns ``None`` when the page holds no media preview at all, otherwise a
    result dict describing caption availability.
    """

    try:
        driver.get(url)
    except WebDriverException as exc:
        print(f"Canvas: could not load {url}: {exc}")
        return None

    if WebDriverWait is None or By is None:
        return None

    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.ID, "media_preview"))
        )
    except TimeoutException:
        return None  # not a video page

    has_captions = False
    for selector in CAPTION_SELECTORS:
        try:
            if driver.find_elements(By.CSS_SELECTOR, selector):
                has_captions = True
                break
        except Exception:
            continue

    return {
        "has_captions": has_captions,
        # Canvas Studio does not expose whether a caption track was machine
        # generated, so the source stays unknown rather than being guessed.
        "caption_kind": CAPTION_UNKNOWN if has_captions else CAPTION_NONE,
        "caption_confidence": "low",
        "caption_language": None,
        "caption_signals": [
            "canvas player exposes a caption control"
            if has_captions
            else "canvas player exposes no caption control"
        ],
        "caption_detection_method": "canvas_player_dom",
    }


def auditVideos(
    videos: Sequence[Tuple[str, str]],
    browser: Optional[BrowserSession] = None,
    results: Optional[ResultWriter] = None,
    include_course_ids: bool = False,
    timeout: int = 5,
) -> Dict[str, bool]:
    """Audit Canvas pages, returning ``{url: page_contained_video}``."""

    session = browser or BrowserSession()
    owns_browser = browser is None
    owns_writer = results is None
    writer = results or ResultWriter()

    is_video: Dict[str, bool] = {}

    try:
        if not session.ensureLogin(CANVAS_LOGIN_URL, "Canvas"):
            print("Canvas: no browser available, skipping embedded video audit.")
            return is_video

        driver = session.driver()
        if driver is None:
            return is_video

        for index, (course_id, url) in enumerate(videos, start=1):
            if isCancelled():
                print("Canvas: audit cancelled.")
                break

            info = inspectCanvasPage(driver, url, timeout=timeout)
            if info is None:
                is_video[url] = False
                continue

            is_video[url] = True
            entry: Dict[str, Any] = {"type": "Canvas", "url": url}
            entry.update(info)
            if include_course_ids:
                entry["course_id"] = course_id
            writer.add(**entry)
            print(f"Canvas [{index}/{len(videos)}] {info['caption_kind']} - {url}")
    finally:
        if owns_writer:
            writer.flush()
        if owns_browser:
            session.close()

    return is_video


def main(
    courses: Sequence[Any],
    browser: Optional[BrowserSession] = None,
    results: Optional[ResultWriter] = None,
    include_course_ids: bool = False,
) -> int:
    """Audit every cached Canvas media page for ``courses``."""

    videos = collectCanvasVideos(courses)
    if not videos:
        print("Canvas: no embedded media pages found.")
        return 0

    print(f"Canvas: checking {len(videos)} page(s) for embedded media.")
    auditVideos(
        videos,
        browser=browser,
        results=results,
        include_course_ids=include_course_ids,
    )
    return len(videos)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python sortEmbeddedVideos.py <courseID1> <courseID2> ...")
    else:
        main(sys.argv[1:], include_course_ids=True)
