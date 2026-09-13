"""Tests for gui_api.AuditApi - the GUI's business logic, independent of
pywebview. Every test uses a plain recording `notify` callback instead of a
real window, and runs inside a throwaway working directory so it never
touches the real data/ or config/ folders."""

import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gui_api
from auditCore import ensureDataDirs, isCancelled, resetCancel, saveJson, RESULTS_FILE


class _Recorder:
    """A notify() callback that records every (event, payload) call."""

    def __init__(self):
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, event, payload):
        with self._lock:
            self.calls.append((event, payload))

    def events(self):
        return [event for event, _ in self.calls]

    def last(self, event):
        for e, payload in reversed(self.calls):
            if e == event:
                return payload
        return None

    def wait_for(self, event, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if event in self.events():
                return self.last(event)
            time.sleep(0.01)
        raise AssertionError(f"event {event!r} was never recorded: {self.events()}")


class ApiTestCase(unittest.TestCase):
    """Base class: runs every test inside a fresh temp working directory."""

    def setUp(self):
        self.origin = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        self.recorder = _Recorder()
        resetCancel()
        self.api = gui_api.AuditApi(self.recorder)

    def tearDown(self):
        resetCancel()
        os.chdir(self.origin)
        self.tmp.cleanup()


class VersionAndDirsTests(ApiTestCase):
    def test_getVersionReturnsAString(self):
        self.assertIsInstance(self.api.get_version(), str)

    def test_constructionCreatesDataDirs(self):
        self.assertTrue(os.path.isdir(os.path.join("data", "courseModules")))
        self.assertTrue(os.path.isdir(os.path.join("data", "sortedModules")))

    def test_constructionRegistersLoginPromptHandler(self):
        import browser

        self.assertEqual(browser._PROMPT_HANDLER, self.api._handleLoginPrompt)
        browser.setPromptHandler(None)


class RunAuditTests(ApiTestCase):
    def test_notBusyBeforeAnyRun(self):
        self.assertFalse(self.api.busy)
        self.assertFalse(self.api.is_busy())

    def test_runCompleteAuditReportsStartedAndFinished(self):
        with mock.patch("runAudit.main", return_value={}) as run_main:
            result = self.api.run_complete_audit(headless=True)

        self.assertTrue(result["started"])
        self.recorder.wait_for("run_finished")
        run_main.assert_called_once_with(["--headless"])
        self.assertEqual(self.recorder.last("run_finished")["error"], None)
        self.assertFalse(self.recorder.last("run_finished")["cancelled"])

    def test_runCompleteAuditNonHeadlessPassesNoFlag(self):
        with mock.patch("runAudit.main", return_value={}) as run_main:
            self.api.run_complete_audit(headless=False)
            self.recorder.wait_for("run_finished")

        run_main.assert_called_once_with([])

    def test_secondRunWhileBusyIsRejected(self):
        release = threading.Event()

        with mock.patch("runAudit.main", side_effect=lambda *a: release.wait(5)):
            first = self.api.run_complete_audit()
            second = self.api.run_complete_audit()

            self.assertTrue(first["started"])
            self.assertFalse(second["started"])
            self.assertIn("already running", second["error"])

            release.set()
            self.recorder.wait_for("run_finished")

    def test_emptyCourseIdIsRejectedWithoutStartingAThread(self):
        result = self.api.run_individual_audit("   ")
        self.assertFalse(result["started"])
        self.assertFalse(self.api.busy)

    def test_individualAuditPassesTrimmedCourseId(self):
        with mock.patch("individualAudit.main") as ind_main:
            self.api.run_individual_audit("  101  ", headless=True)
            self.recorder.wait_for("run_finished")

        ind_main.assert_called_once_with("101", headless=True)

    def test_exceptionDuringAuditIsReportedNotRaised(self):
        with mock.patch("runAudit.main", side_effect=RuntimeError("boom")):
            self.api.run_complete_audit()
            payload = self.recorder.wait_for("run_finished")

        self.assertEqual(payload["error"], "boom")

    def test_stdoutDuringAuditIsForwardedAsLogLines(self):
        def fake_main(*args):
            print("line one")
            print("line two")

        with mock.patch("runAudit.main", side_effect=fake_main):
            self.api.run_complete_audit()
            self.recorder.wait_for("run_finished")

        logged = [payload for event, payload in self.recorder.calls if event == "log"]
        self.assertIn("line one", logged)
        self.assertIn("line two", logged)

    def test_stdoutIsRestoredAfterTheRun(self):
        original_stdout = sys.stdout
        with mock.patch("runAudit.main", return_value={}):
            self.api.run_complete_audit()
            self.recorder.wait_for("run_finished")

        self.assertIs(sys.stdout, original_stdout)

    def test_stopAuditWithNothingRunningReturnsError(self):
        result = self.api.stop_audit()
        self.assertFalse(result["stopped"])

    def test_stopAuditSetsCancellationFlagWhileBusy(self):
        release = threading.Event()
        with mock.patch("runAudit.main", side_effect=lambda *a: release.wait(5)):
            self.api.run_complete_audit()
            result = self.api.stop_audit()
            self.assertTrue(result["stopped"])
            self.assertTrue(isCancelled())

            release.set()
            self.recorder.wait_for("run_finished")

    def test_cancelledRunIsReportedAsCancelled(self):
        def fake_main(*args):
            from auditCore import requestCancel

            requestCancel()

        with mock.patch("runAudit.main", side_effect=fake_main):
            self.api.run_complete_audit()
            payload = self.recorder.wait_for("run_finished")

        self.assertTrue(payload["cancelled"])


class ResultsTests(ApiTestCase):
    def test_noResultsFileReturnsEmptySummary(self):
        result = self.api.get_results()
        self.assertEqual(result["entries"], [])
        self.assertEqual(result["summary"]["total"], 0)

    def test_malformedEntriesAreFilteredOut(self):
        saveJson(RESULTS_FILE, [{"type": "youtube", "has_captions": True}, "junk", 5])
        result = self.api.get_results()
        self.assertEqual(len(result["entries"]), 1)

    def test_captionLabelsAreIncluded(self):
        result = self.api.get_results()
        self.assertIn("auto_generated", result["captionLabels"])

    def test_openExternalWithUrlCallsWebbrowser(self):
        with mock.patch("gui_api.webbrowser.open") as opener:
            result = self.api.open_external("https://example.com")

        opener.assert_called_once_with("https://example.com")
        self.assertTrue(result["opened"])

    def test_openExternalWithNoUrlDoesNothing(self):
        with mock.patch("gui_api.webbrowser.open") as opener:
            result = self.api.open_external("")

        opener.assert_not_called()
        self.assertFalse(result["opened"])

    def test_openResultsFileWithNoFileReturnsError(self):
        result = self.api.open_results_file()
        self.assertFalse(result["opened"])
        self.assertIn("No results file", result["error"])

    def test_openResultsFileLaunchesPlatformOpener(self):
        saveJson(RESULTS_FILE, [])
        with mock.patch.object(gui_api.sys, "platform", "darwin"), \
             mock.patch("gui_api.subprocess.Popen") as popen:
            result = self.api.open_results_file()

        self.assertTrue(result["opened"])
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0][0], "open")

    def test_openResultsFileHandlesLaunchFailure(self):
        saveJson(RESULTS_FILE, [])
        with mock.patch.object(gui_api.sys, "platform", "linux"), \
             mock.patch("gui_api.subprocess.Popen", side_effect=OSError("no xdg-open")):
            result = self.api.open_results_file()

        self.assertFalse(result["opened"])
        self.assertIn("no xdg-open", result["error"])


class SettingsTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.tmp_config = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_config.cleanup)
        self.canvas_path = os.path.join(self.tmp_config.name, "canvasAPI.py")
        self.panopto_path = os.path.join(self.tmp_config.name, "panoptoKey.py")
        self._patches = [
            mock.patch.object(gui_api, "CANVAS_CONFIG", self.canvas_path),
            mock.patch.object(gui_api, "PANOPTO_CONFIG", self.panopto_path),
        ]
        for patch in self._patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_saveThenGetRoundTrips(self):
        result = self.api.save_settings("tok", "cid", "sec")
        self.assertTrue(result["saved"])

        settings = self.api.get_settings()
        self.assertEqual(settings, {"canvasToken": "tok", "clientId": "cid", "clientSecret": "sec"})

    def test_emptySettingsFilesReadAsEmptyStrings(self):
        settings = self.api.get_settings()
        self.assertEqual(settings, {"canvasToken": "", "clientId": "", "clientSecret": ""})

    def test_saveFailureIsReported(self):
        with mock.patch("gui_api.writeConfigValue", return_value="disk full"):
            result = self.api.save_settings("tok", "cid", "sec")

        self.assertFalse(result["saved"])
        self.assertIn("disk full", result["error"])

    def test_valuesAreTrimmed(self):
        self.api.save_settings("  tok  ", "  cid  ", "  sec  ")
        settings = self.api.get_settings()
        self.assertEqual(settings["canvasToken"], "tok")


class ResetDataTests(ApiTestCase):
    def test_resetDataDelegatesToDataReset(self):
        with mock.patch("gui_api.dataReset.resetDataFiles", return_value=4) as reset_files:
            result = self.api.reset_data()

        reset_files.assert_called_once()
        self.assertEqual(result["removed"], 4)


class LoginPromptBridgeTests(ApiTestCase):
    def test_handleLoginPromptBlocksUntilConfirmLogin(self):
        released = threading.Event()

        def worker():
            self.api._handleLoginPrompt("Panopto Login", "Please log in.")
            released.set()

        thread = threading.Thread(target=worker)
        thread.start()

        payload = self.recorder.wait_for("login_prompt")
        self.assertEqual(payload["title"], "Panopto Login")

        self.assertFalse(released.wait(0.2))  # still blocked

        confirm_result = self.api.confirm_login()
        self.assertTrue(confirm_result["acknowledged"])

        self.assertTrue(released.wait(5))
        thread.join(5)

    def test_confirmLoginWithNothingWaitingIsANoOp(self):
        result = self.api.confirm_login()
        self.assertFalse(result["acknowledged"])


class LineBufferedNotifierTests(unittest.TestCase):
    def test_writeSplitsOnNewlinesAndBuffersPartialTail(self):
        calls = []
        writer = gui_api._LineBufferedNotifier(lambda event, payload: calls.append((event, payload)))

        writer.write("one\ntwo\npartial")
        writer.flush()

        self.assertEqual(calls, [("log", "one"), ("log", "two"), ("log", "partial")])

    def test_flushWithNothingBufferedIsANoOp(self):
        calls = []
        writer = gui_api._LineBufferedNotifier(lambda event, payload: calls.append((event, payload)))
        writer.flush()
        self.assertEqual(calls, [])

    def test_isattyIsFalse(self):
        writer = gui_api._LineBufferedNotifier(lambda event, payload: None)
        self.assertFalse(writer.isatty())


if __name__ == "__main__":
    unittest.main()
