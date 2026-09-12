"""Tests for sortEmbeddedVideos.py's Canvas media page inspection."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sortEmbeddedVideos
from auditCore import CAPTION_NONE, CAPTION_UNKNOWN, ResultWriter
from selenium.common.exceptions import TimeoutException, WebDriverException


class _FakeWait:
    """Stand-in for WebDriverWait: 'succeeds' unless told to time out."""

    def __init__(self, driver, timeout):
        pass

    def until(self, condition):
        if _FakeWait.should_timeout:
            raise TimeoutException("no media_preview")
        return True

    should_timeout = False


class InspectCanvasPageTests(unittest.TestCase):
    def setUp(self):
        _FakeWait.should_timeout = False
        self._wait_patch = mock.patch.object(sortEmbeddedVideos, "WebDriverWait", _FakeWait)
        self._wait_patch.start()
        self.addCleanup(self._wait_patch.stop)

    def test_pageWithNoMediaPreviewReturnsNone(self):
        _FakeWait.should_timeout = True
        driver = mock.Mock()

        result = sortEmbeddedVideos.inspectCanvasPage(driver, "https://canvas/x")

        self.assertIsNone(result)

    def test_pageWithCaptionControlReportsHasCaptions(self):
        driver = mock.Mock()
        driver.find_elements.side_effect = lambda by, selector: (
            ["<button>"] if "Caption" in selector else []
        )

        result = sortEmbeddedVideos.inspectCanvasPage(driver, "https://canvas/x")

        self.assertIsNotNone(result)
        self.assertTrue(result["has_captions"])
        self.assertEqual(result["caption_kind"], CAPTION_UNKNOWN)

    def test_pageWithNoCaptionControlReportsNone(self):
        driver = mock.Mock()
        driver.find_elements.return_value = []

        result = sortEmbeddedVideos.inspectCanvasPage(driver, "https://canvas/x")

        self.assertIsNotNone(result)
        self.assertFalse(result["has_captions"])
        self.assertEqual(result["caption_kind"], CAPTION_NONE)

    def test_oneSelectorRaisingIsSkippedInFavorOfTheNext(self):
        driver = mock.Mock()

        calls = []

        def fake_find_elements(by, selector):
            calls.append(selector)
            if selector == sortEmbeddedVideos.CAPTION_SELECTORS[0]:
                raise RuntimeError("stale element")
            if selector == sortEmbeddedVideos.CAPTION_SELECTORS[1]:
                return ["<button>"]
            return []

        driver.find_elements.side_effect = fake_find_elements

        result = sortEmbeddedVideos.inspectCanvasPage(driver, "https://canvas/x")

        self.assertTrue(result["has_captions"])

    def test_pageLoadFailureReturnsNone(self):
        driver = mock.Mock()
        driver.get.side_effect = WebDriverException("boom")

        result = sortEmbeddedVideos.inspectCanvasPage(driver, "https://canvas/x")

        self.assertIsNone(result)


class AuditVideosTests(unittest.TestCase):
    def test_noBrowserSkipsGracefully(self):
        fake_session = mock.Mock()
        fake_session.ensureLogin.return_value = False

        result = sortEmbeddedVideos.auditVideos(
            [("101", "https://canvas/x")], browser=fake_session
        )

        self.assertEqual(result, {})

    def test_pagesWithoutMediaAreMarkedFalseButNotWritten(self):
        fake_session = mock.Mock()
        fake_session.ensureLogin.return_value = True
        fake_driver = mock.Mock()
        fake_session.driver.return_value = fake_driver

        with mock.patch.object(sortEmbeddedVideos, "inspectCanvasPage", return_value=None):
            result = sortEmbeddedVideos.auditVideos(
                [("101", "https://canvas/x")], browser=fake_session
            )

        self.assertEqual(result, {"https://canvas/x": False})

    def test_videosWithCaptionsAreWrittenThroughTheWriter(self):
        fake_session = mock.Mock()
        fake_session.ensureLogin.return_value = True
        fake_session.driver.return_value = mock.Mock()

        info = {
            "has_captions": True,
            "caption_kind": CAPTION_UNKNOWN,
            "caption_confidence": "low",
            "caption_language": None,
            "caption_signals": ["canvas player exposes a caption control"],
            "caption_detection_method": "canvas_player_dom",
        }

        writer = ResultWriter(file_path="/tmp/does-not-matter.json")
        with mock.patch.object(sortEmbeddedVideos, "inspectCanvasPage", return_value=info), \
             mock.patch.object(writer, "flush"):
            result = sortEmbeddedVideos.auditVideos(
                [("101", "https://canvas/x")],
                browser=fake_session,
                results=writer,
                include_course_ids=True,
            )

        self.assertEqual(result, {"https://canvas/x": True})
        self.assertEqual(len(writer._pending), 1)
        self.assertEqual(writer._pending[0]["course_id"], "101")


if __name__ == "__main__":
    unittest.main()
