"""Tests for individualAudit.py's orchestration."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import individualAudit
from auditCore import ensureDataDirs


class IndividualAuditTests(unittest.TestCase):
    def setUp(self):
        self.origin = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        ensureDataDirs()

    def tearDown(self):
        os.chdir(self.origin)
        self.tmp.cleanup()

    def test_cachesCourseThenRunsPipelineForThatCourseOnly(self):
        with mock.patch("pullModules.cacheCourse") as cache_course, \
             mock.patch("runAudit.runPipeline", return_value={"youtube": 1}) as run_pipeline, \
             mock.patch("runAudit.printSummary"):
            counts = individualAudit.main(12345)

        cache_course.assert_called_once_with("12345")
        run_pipeline.assert_called_once_with(["12345"], include_course_ids=True, headless=False)
        self.assertEqual(counts, {"youtube": 1})

    def test_courseIdIsStringified(self):
        with mock.patch("pullModules.cacheCourse") as cache_course, \
             mock.patch("runAudit.runPipeline", return_value={}), \
             mock.patch("runAudit.printSummary"):
            individualAudit.main(999)

        cache_course.assert_called_once_with("999")

    def test_headlessFlagIsForwarded(self):
        with mock.patch("pullModules.cacheCourse"), \
             mock.patch("runAudit.runPipeline", return_value={}) as run_pipeline, \
             mock.patch("runAudit.printSummary"):
            individualAudit.main("101", headless=True)

        self.assertTrue(run_pipeline.call_args.kwargs["headless"])


if __name__ == "__main__":
    unittest.main()
