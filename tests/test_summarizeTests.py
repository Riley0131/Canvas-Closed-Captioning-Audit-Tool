"""Tests for ci/summarize_tests.py's JUnit XML parsing."""

import importlib.util
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CI_DIR = os.path.join(ROOT, "ci")
sys.path.insert(0, ROOT)
sys.path.insert(0, CI_DIR)

spec = importlib.util.spec_from_file_location(
    "summarize_tests", os.path.join(CI_DIR, "summarize_tests.py")
)
summarize_tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summarize_tests)  # type: ignore[union-attr]

SINGLE_SUITE = """<?xml version="1.0"?>
<testsuite name="pytest" errors="1" failures="2" skipped="3" tests="10" time="1.0">
</testsuite>
"""

MULTI_SUITE = """<?xml version="1.0"?>
<testsuites>
  <testsuite name="a" errors="0" failures="1" skipped="0" tests="5" time="0.1"></testsuite>
  <testsuite name="b" errors="1" failures="0" skipped="2" tests="7" time="0.2"></testsuite>
</testsuites>
"""

ALL_PASSING = """<?xml version="1.0"?>
<testsuite name="pytest" errors="0" failures="0" skipped="0" tests="62" time="0.5"></testsuite>
"""


class JunitParsingTests(unittest.TestCase):
    def _writeXml(self, content: str) -> str:
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".xml", delete=False, encoding="utf-8"
        )
        handle.write(content)
        handle.close()
        self.addCleanup(os.remove, handle.name)
        return handle.name

    def test_singleTestsuiteIsParsed(self):
        path = self._writeXml(SINGLE_SUITE)
        counts = summarize_tests.totalsFromJunit(path)

        self.assertEqual(counts, {"total": 10, "failed": 2, "errors": 1, "skipped": 3})

    def test_multipleTestsuitesAreSummed(self):
        path = self._writeXml(MULTI_SUITE)
        counts = summarize_tests.totalsFromJunit(path)

        self.assertEqual(counts, {"total": 12, "failed": 1, "errors": 1, "skipped": 2})

    def test_outcomeIsFailureWhenAnythingFailedOrErrored(self):
        self.assertEqual(
            summarize_tests.outcomeFor({"total": 1, "failed": 1, "errors": 0, "skipped": 0}),
            "failure",
        )
        self.assertEqual(
            summarize_tests.outcomeFor({"total": 1, "failed": 0, "errors": 1, "skipped": 0}),
            "failure",
        )

    def test_outcomeIsSuccessWhenNothingFailedOrErrored(self):
        path = self._writeXml(ALL_PASSING)
        counts = summarize_tests.totalsFromJunit(path)

        self.assertEqual(summarize_tests.outcomeFor(counts), "success")
        self.assertEqual(counts["total"], 62)


class GithubOutputTests(unittest.TestCase):
    def test_writeGithubOutputWritesEveryField(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_path = os.path.join(tmp, "output.txt")
            os.environ["GITHUB_OUTPUT"] = output_path
            try:
                summarize_tests.writeGithubOutput(
                    "success", {"total": 5, "failed": 0, "errors": 0, "skipped": 1}
                )
            finally:
                del os.environ["GITHUB_OUTPUT"]

            contents = open(output_path, encoding="utf-8").read()

        self.assertIn("outcome=success", contents)
        self.assertIn("total=5", contents)
        self.assertIn("skipped=1", contents)

    def test_noGithubOutputEnvVarIsANoOp(self):
        os.environ.pop("GITHUB_OUTPUT", None)
        # Should not raise even with nowhere to write.
        summarize_tests.writeGithubOutput("success", {"total": 0, "failed": 0, "errors": 0, "skipped": 0})

    def test_missingResultsFileYieldsErrorOutcome(self):
        original = summarize_tests.RESULTS_FILE
        summarize_tests.RESULTS_FILE = "/nonexistent/test-results.xml"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                output_path = os.path.join(tmp, "output.txt")
                os.environ["GITHUB_OUTPUT"] = output_path
                try:
                    summarize_tests.main()
                finally:
                    del os.environ["GITHUB_OUTPUT"]
                contents = open(output_path, encoding="utf-8").read()
            self.assertIn("outcome=error", contents)
        finally:
            summarize_tests.RESULTS_FILE = original


if __name__ == "__main__":
    unittest.main()
