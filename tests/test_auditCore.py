"""Tests for the shared data helpers."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auditCore import (
    CAPTION_AUTO,
    CAPTION_NONE,
    ResultWriter,
    dedupe,
    isCancelled,
    loadJson,
    requestCancel,
    resetCancel,
    saveJson,
    summarizeResults,
)


class ResultWriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "results.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_entriesAreWrittenOnExit(self):
        with ResultWriter(self.path) as writer:
            writer.add(type="youtube", url="a", has_captions=True)
            writer.add(type="panopto", url="b", has_captions=False)
            self.assertFalse(os.path.exists(self.path), "should buffer until flush")

        self.assertEqual(len(loadJson(self.path)), 2)

    def test_bufferFlushesWhenFull(self):
        with ResultWriter(self.path, flush_every=2) as writer:
            writer.add(url="a")
            writer.add(url="b")
            self.assertEqual(len(loadJson(self.path)), 2)
            writer.add(url="c")

        self.assertEqual(len(loadJson(self.path)), 3)

    def test_existingResultsArePreserved(self):
        saveJson(self.path, [{"url": "old"}])

        with ResultWriter(self.path) as writer:
            writer.add(url="new")

        urls = [entry["url"] for entry in loadJson(self.path)]
        self.assertEqual(urls, ["old", "new"])

    def test_corruptResultsFileIsReplacedNotCrashed(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")

        with ResultWriter(self.path) as writer:
            writer.add(url="new")

        self.assertEqual(len(loadJson(self.path)), 1)

    def test_resultsSurviveAnExceptionInTheStage(self):
        with self.assertRaises(RuntimeError):
            with ResultWriter(self.path) as writer:
                writer.add(url="a")
                raise RuntimeError("stage blew up")

        self.assertEqual(len(loadJson(self.path)), 1)


class SummaryTests(unittest.TestCase):
    def test_countsByTypeAndCaptionKind(self):
        summary = summarizeResults(
            [
                {"type": "youtube", "has_captions": True, "caption_kind": CAPTION_AUTO},
                {"type": "youtube", "has_captions": False},
                {"type": "panopto", "has_captions": True, "caption_kind": CAPTION_AUTO},
            ]
        )

        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["withCaptions"], 2)
        self.assertEqual(summary["withoutCaptions"], 1)
        self.assertEqual(summary["byType"]["youtube"]["total"], 2)
        self.assertEqual(summary["byCaptionKind"][CAPTION_AUTO], 2)
        self.assertEqual(summary["byCaptionKind"][CAPTION_NONE], 1)

    def test_malformedEntriesAreIgnored(self):
        summary = summarizeResults([{"type": "youtube", "has_captions": True}, "junk", 5])
        self.assertEqual(summary["total"], 1)

    def test_emptyInput(self):
        self.assertEqual(summarizeResults([])["total"], 0)


class HelperTests(unittest.TestCase):
    def test_dedupePreservesOrder(self):
        self.assertEqual(dedupe(["b", "a", "b", "c", "a"]), ["b", "a", "c"])

    def test_loadJsonReturnsDefaultForMissingFile(self):
        self.assertEqual(loadJson("/nonexistent/path.json", default=[]), [])

    def test_saveJsonCreatesParentDirectories(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "deep", "file.json")
            saveJson(path, {"a": 1})
            self.assertEqual(loadJson(path), {"a": 1})

    def test_cancellationFlagRoundTrips(self):
        resetCancel()
        self.assertFalse(isCancelled())
        requestCancel()
        self.assertTrue(isCancelled())
        resetCancel()
        self.assertFalse(isCancelled())


if __name__ == "__main__":
    unittest.main()
