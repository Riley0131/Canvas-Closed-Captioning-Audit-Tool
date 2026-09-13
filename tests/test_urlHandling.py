"""Tests for link discovery, normalisation and sorting."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import panoptoVideo
import pullModules
import youtubeVideo


class PanoptoUrlTests(unittest.TestCase):
    def test_embedLinksBecomeViewerLinks(self):
        self.assertEqual(
            panoptoVideo.normalizePanoptoUrl(
                "https://uccs.hosted.panopto.com/Panopto/Pages/Embed.aspx?id=abc"
            ),
            "https://uccs.hosted.panopto.com/Panopto/Pages/Viewer.aspx?id=abc",
        )

    def test_markerParameterIsStripped(self):
        normalized = panoptoVideo.normalizePanoptoUrl(
            "https://canvas.uccs.edu/launch?url=x&_panopto_video=true"
        )
        self.assertNotIn("_panopto_video", normalized)
        self.assertIn("url=x", normalized)

    def test_embedLinkWithAccessTokenIsLeftAlone(self):
        url = "https://x.panopto.com/Panopto/Pages/Embed.aspx?id=a#access_token=1"
        self.assertIn("Embed.aspx", panoptoVideo.normalizePanoptoUrl(url))

    def test_sessionIdFromQuery(self):
        self.assertEqual(
            panoptoVideo.extractSessionId(
                "https://x.panopto.com/Panopto/Pages/Viewer.aspx?id=12345678-1234-1234-1234-123456789012"
            ),
            "12345678-1234-1234-1234-123456789012",
        )

    def test_sessionIdFromPathSegment(self):
        self.assertEqual(
            panoptoVideo.extractSessionId(
                "https://x.panopto.com/Panopto/Podcast/12345678-1234-1234-1234-123456789012"
            ),
            "12345678-1234-1234-1234-123456789012",
        )

    def test_sessionIdMissing(self):
        self.assertIsNone(panoptoVideo.extractSessionId("https://x.panopto.com/Panopto/"))

    def test_playerUrlRecognition(self):
        self.assertTrue(
            panoptoVideo.isPanoptoPlayerUrl(
                "https://x.hosted.panopto.com/Panopto/Pages/Viewer.aspx?id=1"
            )
        )
        self.assertTrue(
            panoptoVideo.isPanoptoPlayerUrl("https://canvas.edu/l?_panopto_video=true")
        )
        self.assertFalse(panoptoVideo.isPanoptoPlayerUrl("https://youtu.be/abc"))
        self.assertFalse(panoptoVideo.isPanoptoPlayerUrl(None))
        self.assertFalse(
            panoptoVideo.isPanoptoPlayerUrl("https://x.panopto.com/Panopto/Pages/Home.aspx")
        )

    def test_baseUrl(self):
        self.assertEqual(
            panoptoVideo.baseUrlOf("https://x.panopto.com/Panopto/Pages/Viewer.aspx?id=1"),
            "https://x.panopto.com",
        )
        self.assertIsNone(panoptoVideo.baseUrlOf("not-a-url"))


class YoutubeUrlTests(unittest.TestCase):
    def test_videoIdFromEveryCommonForm(self):
        cases = {
            "https://youtu.be/dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42": "dQw4w9WgXcQ",
            "https://www.youtube.com/embed/dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ": "dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ?si=xyz": "dQw4w9WgXcQ",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(youtubeVideo.extractVideoId(url), expected)

    def test_nonYoutubeUrlsReturnNone(self):
        self.assertIsNone(youtubeVideo.extractVideoId("https://vimeo.com/12345"))
        self.assertIsNone(youtubeVideo.extractVideoId(None))

    def test_normalizeProducesWatchUrl(self):
        self.assertEqual(
            youtubeVideo.normalize_youtube_url("https://youtu.be/dQw4w9WgXcQ"),
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )

    def test_inspectRejectsUnparseableUrl(self):
        info = youtubeVideo.inspectVideo("https://example.com/video")
        self.assertFalse(info["has_captions"])
        self.assertEqual(info["caption_kind"], "none")


class SortUrlsTests(unittest.TestCase):
    def test_urlsAreBucketedByPlatform(self):
        buckets = pullModules.sortUrls(
            [
                "https://youtu.be/a",
                "https://x.hosted.panopto.com/Panopto/Pages/Viewer.aspx?id=1",
                "https://canvas.uccs.edu/courses/1/files/2",
                "https://example.com/page",
            ]
        )

        self.assertEqual(len(buckets["youtube"]), 1)
        self.assertEqual(len(buckets["panopto"]), 1)
        self.assertEqual(len(buckets["canvas"]), 1)
        self.assertEqual(len(buckets["other"]), 1)

    def test_ltiLaunchesAreSortedAsPanopto(self):
        buckets = pullModules.sortUrls(["https://canvas.edu/l?x=1&_panopto_video=true"])
        self.assertEqual(len(buckets["panopto"]), 1)

    def test_duplicatesAndNonStringsAreDropped(self):
        buckets = pullModules.sortUrls(["https://youtu.be/a", "https://youtu.be/a", None, 7])
        self.assertEqual(buckets["youtube"], ["https://youtu.be/a"])

    def test_emptyInputReturnsAllBuckets(self):
        self.assertEqual(
            sorted(pullModules.sortUrls(None)),
            ["canvas", "other", "panopto", "youtube"],
        )


if __name__ == "__main__":
    unittest.main()
