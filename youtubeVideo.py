"""YouTube caption auditor.

YouTube reports whether a caption track was produced by its own speech
recognition or uploaded by the channel owner, so the audit records the same
``caption_kind`` vocabulary Panopto results use.

The old implementation slept five seconds before every single video, which made
a large audit take hours. The delay is now only applied after a failed lookup,
which is when throttling is actually a risk.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from auditCore import (
    CAPTION_AUTO,
    CAPTION_HUMAN,
    CAPTION_NONE,
    ResultWriter,
    dedupe,
    isCancelled,
    loadCourseIds,
    loadJson,
    sortedModulesPath,
)

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except Exception:  # pragma: no cover - dependency optional for unit tests
    YouTubeTranscriptApi = None  # type: ignore

# Seconds to wait after a failed lookup before trying the next video.
THROTTLE_BACKOFF = float(os.environ.get("YOUTUBE_BACKOFF", "3"))


def normalize_youtube_url(url: str) -> Optional[str]:
    """Normalize a YouTube URL to the canonical ``watch?v=`` form.

    Args:
        url: A YouTube URL in any common form.

    Returns:
        The normalized URL, or ``None`` when ``url`` is not a YouTube link.
    """

    video_id = extractVideoId(url)
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else None


def extractVideoId(url: Any) -> Optional[str]:
    """Return the 11 character video ID from any common YouTube URL form."""

    if not isinstance(url, str):
        return None

    candidate = None
    if "youtu.be/" in url:
        candidate = url.split("youtu.be/")[-1]
    elif "watch?v=" in url:
        candidate = url.split("watch?v=")[-1]
    elif "/embed/" in url:
        candidate = url.split("/embed/")[-1]
    elif "/shorts/" in url:
        candidate = url.split("/shorts/")[-1]

    if not candidate:
        return None

    for separator in ("?", "&", "#", "/"):
        candidate = candidate.split(separator)[0]

    return candidate or None


def collectYoutubeVideos(courses: Iterable[Any]) -> List[Tuple[str, str]]:
    """Return ``[(course_id, url), ...]`` for the unique YouTube links cached."""

    videos: List[Tuple[str, str]] = []
    seen: set = set()

    for course in courses:
        payload = loadJson(sortedModulesPath(course))
        if not isinstance(payload, dict):
            print(f"YouTube: no cached modules for course {course}.")
            continue

        for item in payload.get("youtube", []) or []:
            video_id = extractVideoId(item)
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)
            videos.append((str(course), item))

    return videos


def get_youtube_videos(courses: Iterable[Any]) -> List[str]:
    """Collect the unique YouTube URLs cached for ``courses``."""

    return dedupe(url for _, url in collectYoutubeVideos(courses))


def _listTranscripts(video_id: str) -> Any:
    """Return a transcript listing, supporting both API generations."""

    if YouTubeTranscriptApi is None:
        raise RuntimeError("youtube-transcript-api is not installed")

    lister = getattr(YouTubeTranscriptApi, "list_transcripts", None)
    if callable(lister):  # youtube-transcript-api < 1.0
        return lister(video_id)

    return YouTubeTranscriptApi().list(video_id)


def inspectVideo(url: str) -> Dict[str, Any]:
    """Determine whether a video has captions and where they came from.

    Returns a dict with ``has_captions``, ``caption_kind``,
    ``caption_confidence``, ``caption_language`` and ``caption_signals``.
    """

    unknown = {
        "has_captions": False,
        "caption_kind": CAPTION_NONE,
        "caption_confidence": "low",
        "caption_language": None,
        "caption_signals": [],
        "caption_detection_method": "youtube_transcript_api",
    }

    video_id = extractVideoId(url)
    if not video_id:
        unknown["caption_signals"] = ["could not parse a video id from the URL"]
        return unknown

    try:
        transcripts = list(_listTranscripts(video_id))
    except Exception as exc:
        message = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
        print(f"YouTube: no transcript for {url} ({message})")
        if THROTTLE_BACKOFF:
            time.sleep(THROTTLE_BACKOFF)
        unknown["caption_signals"] = [f"lookup failed: {message}"]
        return unknown

    if not transcripts:
        unknown["caption_signals"] = ["no caption tracks published"]
        return unknown

    # A manually created track beats an automatic one: if the owner uploaded
    # captions, that is what viewers see by default.
    manual = [t for t in transcripts if not getattr(t, "is_generated", False)]
    chosen = manual[0] if manual else transcripts[0]
    generated = bool(getattr(chosen, "is_generated", False))

    return {
        "has_captions": True,
        "caption_kind": CAPTION_AUTO if generated else CAPTION_HUMAN,
        "caption_confidence": "high",
        "caption_language": getattr(chosen, "language", None),
        "caption_signals": [
            "youtube reports the track as automatically generated"
            if generated
            else "youtube reports the track as manually provided"
        ],
        "caption_detection_method": "youtube_transcript_api",
    }


def auditVideo(url: str) -> bool:
    """Backwards compatible helper: ``True`` when the video has captions."""

    return bool(inspectVideo(url)["has_captions"])


def main(
    courses: Optional[Sequence[Any]] = None,
    include_course_ids: bool = False,
    results: Optional[ResultWriter] = None,
) -> int:
    """Audit the YouTube videos of ``courses``. Returns the number audited."""

    if courses is None:
        courses = loadCourseIds()

    videos = collectYoutubeVideos(courses or [])
    if not videos:
        print("YouTube: no videos found.")
        return 0

    print(f"YouTube: auditing {len(videos)} video(s).")

    owns_writer = results is None
    writer = results or ResultWriter()

    try:
        for index, (course_id, url) in enumerate(videos, start=1):
            if isCancelled():
                print("YouTube: audit cancelled.")
                break

            info = inspectVideo(url)
            entry: Dict[str, Any] = {"type": "youtube", "url": url}
            entry.update(info)
            if include_course_ids:
                entry["course_id"] = course_id
            writer.add(**entry)
            print(
                f"YouTube [{index}/{len(videos)}] {info['caption_kind']} - {url}"
            )
    finally:
        if owns_writer:
            writer.flush()

    return len(videos)


if __name__ == "__main__":
    main()
