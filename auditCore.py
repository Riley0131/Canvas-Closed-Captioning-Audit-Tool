"""Shared helpers for the Closed Captioning Audit tool.

Centralises the pieces every audit stage needs: data folder layout, JSON
helpers, and a batched result writer. Older versions of the tool re-read and
re-wrote ``data/audited_videos.json`` once per video, which made a run cost
O(n^2) file I/O. :class:`ResultWriter` loads the file once, buffers entries in
memory, and flushes them in a single write.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, Iterable, List, Optional

DATA_DIR = "data"
COURSE_MODULES_DIR = os.path.join(DATA_DIR, "courseModules")
SORTED_MODULES_DIR = os.path.join(DATA_DIR, "sortedModules")

COURSES_FILE = os.path.join(DATA_DIR, "courses.json")
COURSE_IDS_FILE = os.path.join(DATA_DIR, "courses_ids.json")
RESULTS_FILE = os.path.join(DATA_DIR, "audited_videos.json")

# Caption classifications shared by every auditor so the results file has a
# single vocabulary regardless of which platform produced the entry.
CAPTION_NONE = "none"
CAPTION_AUTO = "auto_generated"
CAPTION_HUMAN = "human_edited"
CAPTION_UNKNOWN = "unknown"

CAPTION_KIND_LABELS = {
    CAPTION_NONE: "No captions",
    CAPTION_AUTO: "Auto-generated",
    CAPTION_HUMAN: "Edited / human-provided",
    CAPTION_UNKNOWN: "Captions (source unknown)",
}


# Cooperative cancellation. Audit stages check :func:`isCancelled` between
# videos so the GUI's Stop button interrupts a long run without killing the
# process and losing buffered results.
_CANCEL = threading.Event()


def requestCancel() -> None:
    """Ask running audit stages to stop after the current video."""

    _CANCEL.set()


def resetCancel() -> None:
    """Clear a previous cancellation request before starting a new run."""

    _CANCEL.clear()


def isCancelled() -> bool:
    """True when a caller has asked the current audit to stop."""

    return _CANCEL.is_set()


def ensureDataDirs() -> None:
    """Create the data folders the audit scripts write into.

    Previously a fresh clone crashed on the first run because ``data/`` and its
    two subfolders had to be created by hand.
    """

    for folder in (DATA_DIR, COURSE_MODULES_DIR, SORTED_MODULES_DIR):
        os.makedirs(folder, exist_ok=True)


def loadJson(path: str, default: Any = None) -> Any:
    """Return the JSON contents of ``path`` or ``default`` when unreadable."""

    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def saveJson(path: str, payload: Any) -> None:
    """Write ``payload`` to ``path`` as indented JSON, creating parent dirs."""

    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=4)


def sortedModulesPath(course_id: Any) -> str:
    return os.path.join(SORTED_MODULES_DIR, f"sorted_modules_{course_id}.json")


def courseModulesPath(course_id: Any) -> str:
    return os.path.join(COURSE_MODULES_DIR, f"modules_{course_id}.json")


def loadCourseIds() -> List[str]:
    payload = loadJson(COURSE_IDS_FILE, default=[])
    if isinstance(payload, list):
        return [str(item) for item in payload]
    return []


def dedupe(items: Iterable[Any]) -> List[Any]:
    """Return ``items`` without duplicates, preserving the original order."""

    seen = set()
    unique = []
    for item in items:
        try:
            key = item
            hash(key)
        except TypeError:  # unhashable entries are passed through untouched
            unique.append(item)
            continue

        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    return unique


class ResultWriter:
    """Buffered writer for ``data/audited_videos.json``.

    Use as a context manager so results are flushed even when an audit stage
    raises::

        with ResultWriter() as results:
            results.add(type="panopto", url=url, has_captions=True)
    """

    def __init__(self, file_path: str = RESULTS_FILE, flush_every: int = 25) -> None:
        self.file_path = file_path
        self.flush_every = max(1, flush_every)
        self._pending: List[Dict[str, Any]] = []
        self._existing: Optional[List[Any]] = None

    # ------------------------------------------------------------------
    def add(self, **entry: Any) -> Dict[str, Any]:
        """Buffer a single result entry and flush when the buffer is full."""

        self._pending.append(entry)
        if len(self._pending) >= self.flush_every:
            self.flush()
        return entry

    def flush(self) -> None:
        """Persist buffered entries to disk."""

        if not self._pending:
            return

        if self._existing is None:
            existing = loadJson(self.file_path, default=[])
            self._existing = existing if isinstance(existing, list) else []

        self._existing.extend(self._pending)
        self._pending.clear()
        saveJson(self.file_path, self._existing)

    @property
    def count(self) -> int:
        """Number of entries recorded through this writer."""

        return len(self._pending) + (
            0 if self._existing is None else len(self._existing)
        )

    # ------------------------------------------------------------------
    def __enter__(self) -> "ResultWriter":
        return self

    def __exit__(self, exc_type, exc, exc_tb) -> None:
        self.flush()


def summarizeResults(entries: Optional[Iterable[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Aggregate audit results into counts for reporting.

    Returns totals plus per-platform and per-caption-kind breakdowns, which the
    GUI renders as a summary instead of dumping raw JSON at the user.
    """

    if entries is None:
        loaded = loadJson(RESULTS_FILE, default=[])
        entries = loaded if isinstance(loaded, list) else []

    summary: Dict[str, Any] = {
        "total": 0,
        "withCaptions": 0,
        "withoutCaptions": 0,
        "byType": {},
        "byCaptionKind": {},
    }

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        summary["total"] += 1

        has_captions = bool(entry.get("has_captions"))
        if has_captions:
            summary["withCaptions"] += 1
        else:
            summary["withoutCaptions"] += 1

        video_type = str(entry.get("type", "unknown")).lower()
        bucket = summary["byType"].setdefault(
            video_type, {"total": 0, "withCaptions": 0, "withoutCaptions": 0}
        )
        bucket["total"] += 1
        bucket["withCaptions" if has_captions else "withoutCaptions"] += 1

        kind = entry.get("caption_kind")
        if not kind:
            kind = CAPTION_UNKNOWN if has_captions else CAPTION_NONE
        summary["byCaptionKind"][kind] = summary["byCaptionKind"].get(kind, 0) + 1

    return summary
