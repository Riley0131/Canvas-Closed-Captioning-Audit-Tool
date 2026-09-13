"""Tests for youtubeVideo.py's caption-source inspection and orchestration."""

import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import youtubeVideo
from auditCore import CAPTION_AUTO, CAPTION_HUMAN, CAPTION_NONE, ResultWriter, ensureDataDirs, saveJson, sortedModulesPath


def _transcript(is_generated, language="English"):
    return types.SimpleNamespace(is_generated=is_generated, language=language)


class InspectVideoTests(unittest.TestCase):
    def test_manuallyProvidedTrackIsPreferredOverAutoTrack(self):
        transcripts = [_transcript(True, "English (auto)"), _transcript(False, "English")]
        with mock.patch.object(youtubeVideo, "_listTranscripts", return_value=transcripts):
            info = youtubeVideo.inspectVideo("https://youtu.be/abc12345678")

        self.assertTrue(info["has_captions"])
        self.assertEqual(info["caption_kind"], CAPTION_HUMAN)
        self.assertEqual(info["caption_confidence"], "high")
        self.assertEqual(info["caption_language"], "English")

    def test_onlyAutoTrackAvailableIsReportedAsAuto(self):
        transcripts = [_transcript(True, "English (auto)")]
        with mock.patch.object(youtubeVideo, "_listTranscripts", return_value=transcripts):
            info = youtubeVideo.inspectVideo("https://youtu.be/abc12345678")

        self.assertEqual(info["caption_kind"], CAPTION_AUTO)

    def test_noTranscriptsListedMeansNoCaptions(self):
        with mock.patch.object(youtubeVideo, "_listTranscripts", return_value=[]):
            info = youtubeVideo.inspectVideo("https://youtu.be/abc12345678")

        self.assertFalse(info["has_captions"])
        self.assertEqual(info["caption_kind"], CAPTION_NONE)

    def test_lookupFailureIsReportedAsNoCaptions(self):
        with mock.patch.object(
            youtubeVideo, "_listTranscripts", side_effect=RuntimeError("TranscriptsDisabled")
        ), mock.patch.object(youtubeVideo.time, "sleep") as sleep_mock:
            info = youtubeVideo.inspectVideo("https://youtu.be/abc12345678")

        self.assertFalse(info["has_captions"])
        self.assertEqual(info["caption_kind"], CAPTION_NONE)
        self.assertTrue(any("lookup failed" in s for s in info["caption_signals"]))
        sleep_mock.assert_called_once()

    def test_unparseableUrlSkipsTheApiCallEntirely(self):
        with mock.patch.object(youtubeVideo, "_listTranscripts") as listed:
            info = youtubeVideo.inspectVideo("https://example.com/not-youtube")

        listed.assert_not_called()
        self.assertEqual(info["caption_kind"], CAPTION_NONE)

    def test_auditVideoIsABooleanShortcut(self):
        with mock.patch.object(youtubeVideo, "_listTranscripts", return_value=[_transcript(False)]):
            self.assertTrue(youtubeVideo.auditVideo("https://youtu.be/abc12345678"))

        with mock.patch.object(youtubeVideo, "_listTranscripts", return_value=[]):
            self.assertFalse(youtubeVideo.auditVideo("https://youtu.be/abc12345678"))


class ListTranscriptsCompatibilityTests(unittest.TestCase):
    def test_usesListTranscriptsWhenAvailable(self):
        fake_api = mock.Mock()
        fake_api.list_transcripts = mock.Mock(return_value=["legacy-result"])

        with mock.patch.object(youtubeVideo, "YouTubeTranscriptApi", fake_api):
            result = youtubeVideo._listTranscripts("abc")

        self.assertEqual(result, ["legacy-result"])
        fake_api.list_transcripts.assert_called_once_with("abc")

    def test_fallsBackToInstanceListForNewerApi(self):
        fake_instance = mock.Mock()
        fake_instance.list = mock.Mock(return_value=["new-result"])
        fake_api_class = mock.Mock(return_value=fake_instance)
        fake_api_class.list_transcripts = None

        with mock.patch.object(youtubeVideo, "YouTubeTranscriptApi", fake_api_class):
            result = youtubeVideo._listTranscripts("abc")

        self.assertEqual(result, ["new-result"])

    def test_missingLibraryRaises(self):
        with mock.patch.object(youtubeVideo, "YouTubeTranscriptApi", None):
            with self.assertRaises(RuntimeError):
                youtubeVideo._listTranscripts("abc")


class MainOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.origin = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        ensureDataDirs()

    def tearDown(self):
        os.chdir(self.origin)
        self.tmp.cleanup()

    def test_mainWritesOneEntryPerVideoWithCourseId(self):
        saveJson(
            sortedModulesPath("101"),
            {"youtube": ["https://youtu.be/abc12345678"], "canvas": [], "panopto": [], "other": []},
        )

        with mock.patch.object(youtubeVideo, "_listTranscripts", return_value=[_transcript(True)]):
            count = youtubeVideo.main(["101"], include_course_ids=True)

        self.assertEqual(count, 1)
        from auditCore import loadJson, RESULTS_FILE

        entries = loadJson(RESULTS_FILE)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["course_id"], "101")
        self.assertEqual(entries[0]["caption_kind"], CAPTION_AUTO)

    def test_mainWithNoVideosReturnsZeroAndWritesNothing(self):
        count = youtubeVideo.main(["999"])
        self.assertEqual(count, 0)

    def test_mainRespectsCancellation(self):
        saveJson(
            sortedModulesPath("101"),
            {
                "youtube": ["https://youtu.be/abc12345678", "https://youtu.be/def12345678"],
                "canvas": [], "panopto": [], "other": [],
            },
        )

        from auditCore import requestCancel, resetCancel

        resetCancel()
        try:
            with mock.patch.object(youtubeVideo, "_listTranscripts", return_value=[_transcript(True)]):
                # Cancel after the very first video is processed.
                original = youtubeVideo.isCancelled
                calls = {"n": 0}

                def fake_is_cancelled():
                    calls["n"] += 1
                    return calls["n"] > 1

                with mock.patch.object(youtubeVideo, "isCancelled", side_effect=fake_is_cancelled):
                    count = youtubeVideo.main(["101"])
        finally:
            resetCancel()

        from auditCore import loadJson, RESULTS_FILE

        entries = loadJson(RESULTS_FILE)
        self.assertEqual(len(entries), 1)


if __name__ == "__main__":
    unittest.main()
