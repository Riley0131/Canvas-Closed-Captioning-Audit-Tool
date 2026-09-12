"""Tests for reading cached module data back out of the data folder."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import panoptoVideo
import sortEmbeddedVideos
import youtubeVideo
from auditCore import ensureDataDirs, saveJson, sortedModulesPath


class CachedDiscoveryTests(unittest.TestCase):
    """Each test runs inside a throwaway working directory."""

    def setUp(self):
        self.origin = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        ensureDataDirs()

    def tearDown(self):
        os.chdir(self.origin)
        self.tmp.cleanup()

    def _cache(self, course_id, **buckets):
        payload = {"youtube": [], "canvas": [], "panopto": [], "other": []}
        payload.update(buckets)
        saveJson(sortedModulesPath(course_id), payload)

    def test_panoptoLinksAreReadFromTheCache(self):
        self._cache(
            "101",
            panopto=[
                "https://x.hosted.panopto.com/Panopto/Pages/Viewer.aspx?id=1",
                "https://example.com/not-panopto",
            ],
        )

        links = panoptoVideo.collectPanoptoLinks(["101"])

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0][0], "101")

    def test_sameSessionInTwoCoursesIsAuditedOnce(self):
        viewer = "https://x.hosted.panopto.com/Panopto/Pages/Viewer.aspx?id=12345678-1234-1234-1234-123456789012"
        embed = "https://x.hosted.panopto.com/Panopto/Pages/Embed.aspx?id=12345678-1234-1234-1234-123456789012"
        self._cache("101", panopto=[viewer])
        self._cache("102", panopto=[embed])

        self.assertEqual(len(panoptoVideo.collectPanoptoLinks(["101", "102"])), 1)

    def test_youtubeLinksAreDedupedByVideoId(self):
        self._cache(
            "101",
            youtube=[
                "https://youtu.be/dQw4w9WgXcQ",
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "https://youtu.be/otherVideo1",
            ],
        )

        videos = youtubeVideo.collectYoutubeVideos(["101"])
        self.assertEqual(len(videos), 2)
        self.assertTrue(all(course == "101" for course, _ in videos))

    def test_canvasLinksHaveTheApiPrefixRemoved(self):
        self._cache("101", canvas=["https://canvas.uccs.edu/api/v1/courses/1/files/2"])

        videos = sortEmbeddedVideos.collectCanvasVideos(["101"])
        self.assertEqual(videos, [("101", "https://canvas.uccs.edu/courses/1/files/2")])

    def test_missingCacheFilesAreSkippedQuietly(self):
        self.assertEqual(panoptoVideo.collectPanoptoLinks([]), [])
        self.assertEqual(youtubeVideo.collectYoutubeVideos(["999"]), [])
        self.assertEqual(sortEmbeddedVideos.collectCanvasVideos(["999"]), [])

    def test_corruptCacheFileIsSkipped(self):
        path = sortedModulesPath("101")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{{{")

        self.assertEqual(youtubeVideo.collectYoutubeVideos(["101"]), [])

    def test_dataFoldersAreCreatedOnDemand(self):
        self.assertTrue(os.path.isdir(os.path.join("data", "courseModules")))
        self.assertTrue(os.path.isdir(os.path.join("data", "sortedModules")))


if __name__ == "__main__":
    unittest.main()
