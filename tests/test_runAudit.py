"""Tests for runAudit.py's pipeline wiring and CLI."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import runAudit
from auditCore import ensureDataDirs, saveJson, RESULTS_FILE, loadJson


class RunPipelineTests(unittest.TestCase):
    def setUp(self):
        self.origin = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        ensureDataDirs()

    def tearDown(self):
        os.chdir(self.origin)
        self.tmp.cleanup()

    def test_allThreeStagesAreCalledWithSharedWriterAndBrowser(self):
        with mock.patch("youtubeVideo.main", return_value=2) as yt, \
             mock.patch("panoptoVideo.main", return_value=1) as pan, \
             mock.patch("sortEmbeddedVideos.main", return_value=3) as canvas, \
             mock.patch("runAudit.BrowserSession") as fake_browser_cls:
            fake_browser_cls.return_value.__enter__.return_value = "the-browser"
            fake_browser_cls.return_value.__exit__.return_value = False

            counts = runAudit.runPipeline(["101"], include_course_ids=True)

        self.assertEqual(counts, {"youtube": 2, "panopto": 1, "canvas": 3})
        yt.assert_called_once()
        pan.assert_called_once()
        canvas.assert_called_once()

        # All three stages should share the same ResultWriter instance.
        yt_writer = yt.call_args.kwargs["results"]
        pan_writer = pan.call_args.kwargs["results"]
        canvas_writer = canvas.call_args.kwargs["results"]
        self.assertIs(yt_writer, pan_writer)
        self.assertIs(pan_writer, canvas_writer)

        # Panopto and Canvas should share the same browser session.
        self.assertEqual(pan.call_args.kwargs["browser"], "the-browser")
        self.assertEqual(canvas.call_args.kwargs["browser"], "the-browser")

    def test_skipBrowserOnlyRunsYoutube(self):
        with mock.patch("youtubeVideo.main", return_value=5) as yt, \
             mock.patch("panoptoVideo.main") as pan, \
             mock.patch("sortEmbeddedVideos.main") as canvas:
            counts = runAudit.runPipeline(["101"], skip_browser=True)

        self.assertEqual(counts, {"youtube": 5, "panopto": 0, "canvas": 0})
        pan.assert_not_called()
        canvas.assert_not_called()

    def test_headlessFlagIsForwardedToBrowserSession(self):
        with mock.patch("youtubeVideo.main", return_value=0), \
             mock.patch("panoptoVideo.main", return_value=0), \
             mock.patch("sortEmbeddedVideos.main", return_value=0), \
             mock.patch("runAudit.BrowserSession") as fake_browser_cls:
            fake_browser_cls.return_value.__enter__.return_value = mock.Mock()
            fake_browser_cls.return_value.__exit__.return_value = False

            runAudit.runPipeline(["101"], headless=True)

        fake_browser_cls.assert_called_once_with(headless=True)


class PrintSummaryTests(unittest.TestCase):
    def setUp(self):
        self.origin = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        ensureDataDirs()

    def tearDown(self):
        os.chdir(self.origin)
        self.tmp.cleanup()

    def test_summaryReflectsWrittenResults(self):
        saveJson(
            RESULTS_FILE,
            [
                {"type": "youtube", "has_captions": True, "caption_kind": "auto_generated"},
                {"type": "panopto", "has_captions": False},
            ],
        )

        summary = runAudit.printSummary()

        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["withCaptions"], 1)


class CliMainTests(unittest.TestCase):
    def setUp(self):
        self.origin = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        ensureDataDirs()

    def tearDown(self):
        os.chdir(self.origin)
        self.tmp.cleanup()

    def test_explicitCoursesSkipDiscoveryAndCacheEachCourse(self):
        with mock.patch("pullModules.cacheCourse") as cache_course, \
             mock.patch("pullModules.main") as discover, \
             mock.patch.object(runAudit, "runPipeline", return_value={"youtube": 0}) as run_pipeline, \
             mock.patch.object(runAudit, "printSummary"):
            runAudit.main(["--course", "101", "--course", "102"])

        discover.assert_not_called()
        self.assertEqual(cache_course.call_count, 2)
        run_pipeline.assert_called_once()
        self.assertEqual(run_pipeline.call_args.args[0], ["101", "102"])
        self.assertTrue(run_pipeline.call_args.kwargs["include_course_ids"])

    def test_noExplicitCoursesRunsDiscovery(self):
        with mock.patch("pullModules.main", return_value=["201"]) as discover, \
             mock.patch.object(runAudit, "runPipeline", return_value={"youtube": 0}) as run_pipeline, \
             mock.patch.object(runAudit, "printSummary"):
            runAudit.main([])

        discover.assert_called_once()
        run_pipeline.assert_called_once()
        self.assertEqual(run_pipeline.call_args.args[0], ["201"])
        self.assertFalse(run_pipeline.call_args.kwargs["include_course_ids"])

    def test_noCoursesFoundReturnsEmptyWithoutRunningPipeline(self):
        with mock.patch("pullModules.main", return_value=[]), \
             mock.patch.object(runAudit, "runPipeline") as run_pipeline:
            result = runAudit.main([])

        self.assertEqual(result, {})
        run_pipeline.assert_not_called()

    def test_headlessAndSkipBrowserFlagsAreParsed(self):
        with mock.patch("pullModules.main", return_value=["1"]), \
             mock.patch.object(runAudit, "runPipeline", return_value={}) as run_pipeline, \
             mock.patch.object(runAudit, "printSummary"):
            runAudit.main(["--headless", "--skip-browser"])

        self.assertTrue(run_pipeline.call_args.kwargs["headless"])
        self.assertTrue(run_pipeline.call_args.kwargs["skip_browser"])


if __name__ == "__main__":
    unittest.main()
