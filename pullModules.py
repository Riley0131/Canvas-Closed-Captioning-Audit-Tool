"""Canvas course and module ingestion.

Pulls the courses the token owner is enrolled in, walks every module item, and
sorts the discovered links by platform.

Performance notes: all Canvas traffic now goes through one pooled
:class:`requests.Session` with retry/backoff, module pages are fetched
concurrently, and the extra per-item lookup only runs for external tools (the
only item type that can hide a Panopto launch behind it).
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Optional

import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except Exception:  # pragma: no cover - very old urllib3
    Retry = None  # type: ignore

from auditCore import (
    COURSES_FILE,
    COURSE_IDS_FILE,
    courseModulesPath,
    dedupe,
    ensureDataDirs,
    saveJson,
    sortedModulesPath,
)

try:
    from config.canvasAPI import CANVAS_API_TOKEN
except Exception:  # pragma: no cover - config file may be absent
    CANVAS_API_TOKEN = ""

CANVAS_API_TOKEN = os.environ.get("CANVAS_API_TOKEN") or CANVAS_API_TOKEN
CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "https://canvas.uccs.edu/api/v1")
HEADERS = {"Authorization": f"Bearer {CANVAS_API_TOKEN}"}

PER_PAGE = 100
REQUEST_TIMEOUT = 30
# Canvas tolerates a handful of parallel requests comfortably; this is the main
# speed-up for courses with many modules.
MAX_WORKERS = int(os.environ.get("CANVAS_MAX_WORKERS", "8"))

VERBOSE = os.environ.get("AUDIT_VERBOSE", "").lower() in {"1", "true", "yes"}


def _log(message: str) -> None:
    """Print detail lines only when verbose output is requested."""

    if VERBOSE:
        print(message)


def _buildSession() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)

    if Retry is not None:
        retry = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        adapter = HTTPAdapter(
            max_retries=retry, pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS * 2
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)

    return session


SESSION = _buildSession()


def _getNextLink(link_header: Optional[str]) -> Optional[str]:
    """Return the ``rel=next`` URL from a Canvas pagination header."""

    if not link_header:
        return None

    for part in link_header.split(","):
        section = part.strip()
        if 'rel="next"' not in section:
            continue

        start = section.find("<")
        end = section.find(">", start + 1)
        if start != -1 and end != -1:
            return section[start + 1 : end]

    return None


# Backwards compatible alias for callers that used the old private helper.
_get_next_link = _getNextLink


def _paginate(url: str, label: str) -> List[Any]:
    """Follow Canvas pagination and return every item across all pages."""

    collected: List[Any] = []
    params: Optional[Dict[str, int]] = {"per_page": PER_PAGE}

    while url:
        try:
            response = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            print(f"Error fetching {label}: {exc}")
            break

        if response.status_code != 200:
            print(f"Error fetching {label}: {response.status_code} - {response.text[:160]}")
            break

        try:
            payload = response.json()
        except ValueError:
            print(f"Error fetching {label}: response was not JSON")
            break

        if isinstance(payload, list):
            collected.extend(payload)
        else:
            collected.append(payload)

        url = _getNextLink(response.headers.get("Link"))
        params = None  # the next-link already carries the query string

    return collected


def get_courses() -> List[Dict[str, Any]]:
    """Fetch every course the token owner is enrolled in."""

    print("Fetching courses from Canvas...")
    courses = _paginate(f"{CANVAS_BASE_URL}/courses", "courses")
    print(f"Found {len(courses)} course(s).")
    return [course for course in courses if isinstance(course, dict)]


def _resolveExternalTool(item: Dict[str, Any]) -> List[str]:
    """Resolve a module item of type ``ExternalTool`` into usable URLs.

    Panopto is surfaced in Canvas as an LTI tool, so the launch URL has to be
    resolved through the module item endpoint. Panopto launches are tagged with
    a marker parameter so the sorter can recognise them later.
    """

    link = item.get("url")
    if not link:
        return []

    title = str(item.get("title", "")).lower()
    external_url = str(item.get("external_url", "")).lower()
    is_panopto = "panopto" in title or "panopto" in external_url

    try:
        response = SESSION.get(link, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        _log(f"Error resolving external tool {link}: {exc}")
        return [link]

    if response.status_code != 200:
        return [link]

    try:
        data = response.json()
    except ValueError:
        return [link]

    tool_url = str(data.get("external_url", "") or "")
    sessionless_url = data.get("url")
    is_panopto = is_panopto or "panopto" in tool_url.lower()

    if not is_panopto:
        return [str(data.get("url") or link)]

    if sessionless_url:
        separator = "&" if "?" in str(sessionless_url) else "?"
        marked = f"{sessionless_url}{separator}_panopto_video=true"
        _log(f"Found Panopto launch URL: {marked}")
        return [marked]

    return [tool_url or link]


def _collectModuleItems(items_url: str, course_id: Any) -> List[str]:
    """Return every URL exposed by the items of a single module."""

    urls: List[str] = []

    for item in _paginate(items_url, f"module items for course {course_id}"):
        if not isinstance(item, dict):
            continue

        link = item.get("url")
        external = item.get("external_url")

        if item.get("type") == "ExternalTool" and link:
            resolved = _resolveExternalTool(item)
            urls.extend(resolved)
            # The launch URL already represents this item; adding external_url
            # as well would double count the same video.
            if any("_panopto_video=true" in url for url in resolved):
                continue
        elif link:
            urls.append(link)

        if external:
            urls.append(external)

    return urls


def getCourseModules(course_id: Any) -> List[str]:
    """Fetch every module item URL for a course.

    Args:
        course_id: The Canvas course ID.

    Returns:
        A de-duplicated list of URLs found in the course's modules.
    """

    modules = _paginate(f"{CANVAS_BASE_URL}/courses/{course_id}/modules", f"modules for course {course_id}")
    items_urls = [
        module.get("items_url")
        for module in modules
        if isinstance(module, dict) and module.get("items_url")
    ]

    urls: List[str] = []
    if items_urls:
        workers = max(1, min(MAX_WORKERS, len(items_urls)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(lambda u: _collectModuleItems(u, course_id), items_urls):
                urls.extend(result)

    urls = dedupe(url for url in urls if isinstance(url, str) and url)
    print(f"Course {course_id}: found {len(urls)} module URL(s).")
    return urls


def sortUrls(urls: Optional[Iterable[Any]]) -> Dict[str, List[str]]:
    """Bucket URLs by video platform.

    Args:
        urls: URLs discovered in a course.

    Returns:
        A dict with ``youtube``, ``canvas``, ``panopto`` and ``other`` keys.
    """

    buckets: Dict[str, List[str]] = {
        "youtube": [],
        "canvas": [],
        "panopto": [],
        "other": [],
    }

    if not urls:
        return buckets

    for url in urls:
        if not isinstance(url, str):
            continue

        lower = url.lower()

        if "youtu" in lower:
            buckets["youtube"].append(url)
        elif "panopto" in lower or "_panopto_video=true" in lower:
            buckets["panopto"].append(url)
        elif "canvas" in lower and "files" in lower:
            buckets["canvas"].append(url)
        else:
            buckets["other"].append(url)

    for key, values in buckets.items():
        buckets[key] = dedupe(values)

    _log(
        "Sorted URLs - "
        + ", ".join(f"{key}: {len(value)}" for key, value in buckets.items())
    )
    return buckets


def cacheCourse(course_id: Any) -> List[str]:
    """Pull one course's modules, cache them, and cache the sorted buckets."""

    ensureDataDirs()
    modules = getCourseModules(course_id)
    saveJson(courseModulesPath(course_id), modules)
    saveJson(sortedModulesPath(course_id), sortUrls(modules))
    return modules


def main() -> List[str]:
    """Pull every course, cache its modules, and sort them by platform.

    Returns:
        The list of course IDs that were processed.
    """

    ensureDataDirs()

    courses = get_courses()
    course_ids = [str(course["id"]) for course in courses if "id" in course]

    saveJson(COURSES_FILE, courses)
    saveJson(COURSE_IDS_FILE, course_ids)

    for index, course_id in enumerate(course_ids, start=1):
        print(f"[{index}/{len(course_ids)}] Pulling modules for course {course_id}")
        cacheCourse(course_id)

    return course_ids


if __name__ == "__main__":
    main()
