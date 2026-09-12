"""Tests for gui.py's application logic.

gui.py needs a real display to run for real, which CI and many dev machines
don't have. Rather than skip GUI coverage entirely, this stubs tkinter with
lightweight fakes that record how they were used, so every callback, the
background worker, cancellation, and the results table's filtering/sorting are
exercised without ever opening a window. This trades fidelity to Tcl/Tk's own
behavior for tests that run anywhere, fast and deterministically.
"""

import os
import queue
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ----------------------------------------------------------------------
# Minimal tkinter stand-in, installed into sys.modules before gui.py is
# imported so ``import tkinter as tk`` resolves to these fakes everywhere.
class _Widget:
    def __init__(self, master=None, **kw):
        self.master = master
        self.kw = dict(kw)
        self._children = []
        if isinstance(master, _Widget):
            master._children.append(self)

    def pack(self, **kw):
        return self

    def grid(self, **kw):
        return self

    def configure(self, **kw):
        self.kw.update(kw)
        return self

    config = configure

    def columnconfigure(self, *a, **kw):
        pass

    def rowconfigure(self, *a, **kw):
        pass

    def bind(self, *a, **kw):
        pass

    def winfo_children(self):
        return self._children

    def focus_set(self):
        pass

    def destroy(self):
        pass

    def grab_set(self):
        pass

    def transient(self, *a):
        pass

    def resizable(self, *a):
        pass

    def title(self, *a):
        pass

    def geometry(self, *a):
        pass

    def minsize(self, *a):
        pass

    def attributes(self, *a):
        pass

    def insert(self, *a, **kw):
        pass

    def delete(self, *a, **kw):
        pass

    def see(self, *a):
        pass

    def start(self, *a):
        pass

    def stop(self, *a):
        pass

    def yview(self, *a):
        pass

    def xview(self, *a):
        pass

    def set(self, *a):
        pass

    def heading(self, *a, **kw):
        pass

    def column(self, *a, **kw):
        pass

    def tag_configure(self, *a, **kw):
        pass

    def get_children(self):
        return []

    def selection(self):
        return []

    def item(self, *a, **kw):
        return ()

    def after(self, ms, fn=None, *a):
        # Only fire zero-delay callbacks synchronously; record delayed ones so
        # a self-rescheduling poller does not recurse forever in tests.
        if fn and ms == 0:
            fn(*a)

    def protocol(self, *a):
        pass

    def mainloop(self):
        pass


class _Var:
    def __init__(self, master=None, value=None, **kw):
        self._v = value

    def get(self):
        return self._v

    def set(self, v):
        self._v = v


class _Style:
    def theme_names(self):
        return ("clam", "default")

    def theme_use(self, name):
        pass

    def configure(self, *a, **kw):
        pass


def _installTkinterStub():
    tk = types.ModuleType("tkinter")
    for name in ("Tk", "Toplevel", "Frame", "Label", "Button", "Entry", "Text", "Checkbutton"):
        setattr(tk, name, type(name, (_Widget,), {}))
    tk.StringVar = type("StringVar", (_Var,), {})
    tk.BooleanVar = type("BooleanVar", (_Var,), {})
    tk.Misc = _Widget

    messagebox = types.ModuleType("tkinter.messagebox")
    messagebox.showinfo = mock.Mock()
    messagebox.showerror = mock.Mock()
    messagebox.askyesno = mock.Mock(return_value=False)
    tk.messagebox = messagebox

    ttk = types.ModuleType("tkinter.ttk")
    for name in (
        "Frame", "Label", "Button", "Entry", "Checkbutton", "Combobox",
        "Treeview", "Scrollbar", "Progressbar", "LabelFrame", "Notebook",
    ):
        setattr(ttk, name, type(name, (_Widget,), {}))
    ttk.Style = _Style
    tk.ttk = ttk

    filedialog = types.ModuleType("tkinter.filedialog")

    sys.modules["tkinter"] = tk
    sys.modules["tkinter.ttk"] = ttk
    sys.modules["tkinter.messagebox"] = messagebox
    sys.modules["tkinter.filedialog"] = filedialog

    return tk, ttk, messagebox


tk, ttk, messagebox = _installTkinterStub()
sys.modules.pop("gui", None)
import gui  # noqa: E402  (must follow the sys.modules stub installation)
from auditCore import isCancelled, resetCancel  # noqa: E402


def _newApp():
    root = tk.Tk()
    return gui.AuditApp(root), root


class MainWindowTests(unittest.TestCase):
    def test_windowBuildsWithoutError(self):
        app, _root = _newApp()
        self.assertIsNotNone(app.runButton)
        self.assertIsNotNone(app.stopButton)

    def test_logDrainMovesQueuedLinesIntoTheLogWidget(self):
        app, _root = _newApp()
        with mock.patch.object(app, "_appendLog") as append_log:
            app.logQueue.put("line one")
            app.logQueue.put("line two")
            app._drainLog()

        self.assertEqual(append_log.call_count, 2)

    def test_runningStateTogglesButtonsAndProgress(self):
        app, _root = _newApp()

        app._setRunning(True, "busy")
        self.assertEqual(app.runButton.kw.get("state"), "disabled")
        self.assertEqual(app.stopButton.kw.get("state"), "normal")

        app._setRunning(False, "Ready")
        self.assertEqual(app.runButton.kw.get("state"), "normal")
        self.assertEqual(app.stopButton.kw.get("state"), "disabled")


class QueueWriterTests(unittest.TestCase):
    def test_writeSplitsOnNewlinesAndBuffersThePartialTail(self):
        sink: "queue.Queue[str]" = queue.Queue()
        writer = gui._QueueWriter(sink)

        writer.write("one\ntwo\npartial")
        writer.flush()

        self.assertEqual([sink.get(), sink.get(), sink.get()], ["one", "two", "partial"])
        self.assertTrue(sink.empty())

    def test_flushWithNothingBufferedIsANoOp(self):
        sink: "queue.Queue[str]" = queue.Queue()
        writer = gui._QueueWriter(sink)
        writer.flush()
        self.assertTrue(sink.empty())

    def test_isattyIsFalse(self):
        writer = gui._QueueWriter(queue.Queue())
        self.assertFalse(writer.isatty())


class ResultsWindowTests(unittest.TestCase):
    def setUp(self):
        _app, self.root = _newApp()
        self.entries = [
            {"type": "panopto", "url": "https://p/1", "has_captions": True,
             "caption_kind": "auto_generated", "caption_confidence": "high", "course_id": "101"},
            {"type": "panopto", "url": "https://p/2", "has_captions": True,
             "caption_kind": "human_edited", "caption_confidence": "medium"},
            {"type": "youtube", "url": "https://y/3", "has_captions": False},
            {"type": "Canvas", "url": "https://c/4", "has_captions": True},  # legacy row, no caption_kind
            "not a dict",
        ]

    def test_malformedEntriesAreIgnoredAtConstruction(self):
        window = gui.ResultsWindow(self.root, self.entries)
        self.assertEqual(len(window.entries), 4)

    def test_allFilterShowsEveryValidRow(self):
        window = gui.ResultsWindow(self.root, self.entries)
        self.assertEqual(len(window._rows()), 4)

    def test_missingCaptionsFilterShowsOnlyUncaptioned(self):
        window = gui.ResultsWindow(self.root, self.entries)
        window.filterVar.set("Missing captions")
        rows = window._rows()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://y/3")

    def test_autoGeneratedFilterMatchesOnlyThatKind(self):
        window = gui.ResultsWindow(self.root, self.entries)
        window.filterVar.set("Auto-generated")
        rows = window._rows()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://p/1")

    def test_legacyRowWithNoCaptionKindIsTreatedAsUnknown(self):
        window = gui.ResultsWindow(self.root, self.entries)
        window.filterVar.set("Captions (source unknown)")
        rows = window._rows()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://c/4")

    def test_sortTogglesDirectionOnRepeatedClicks(self):
        window = gui.ResultsWindow(self.root, self.entries)
        window._sortBy("url")
        first_order = [row["url"] for row in window._rows()]
        window._sortBy("url")
        second_order = [row["url"] for row in window._rows()]

        self.assertEqual(first_order, list(reversed(second_order)))

    def test_summaryCountsMatchEntries(self):
        # _buildSummary runs in __init__; just confirm no exception and that
        # summarizeResults produced sane numbers via the same entries.
        from auditCore import summarizeResults

        window = gui.ResultsWindow(self.root, self.entries)
        summary = summarizeResults(window.entries)
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["withCaptions"], 3)


class SettingsDialogTests(unittest.TestCase):
    def test_saveWritesAllThreeCredentials(self):
        _app, root = _newApp()

        with tempfile.TemporaryDirectory() as tmp:
            canvas_path = os.path.join(tmp, "canvasAPI.py")
            panopto_path = os.path.join(tmp, "panoptoKey.py")

            with mock.patch.object(gui, "CANVAS_CONFIG", canvas_path), \
                 mock.patch.object(gui, "PANOPTO_CONFIG", panopto_path):
                dialog = gui.SettingsDialog(root)
                dialog.fields["CANVAS_API_TOKEN"].set("tok")
                dialog.fields["Client_ID"].set("cid")
                dialog.fields["Client_Secret"].set("sec")
                dialog._save()

            from configStore import readConfigValue

            self.assertEqual(readConfigValue(canvas_path, "CANVAS_API_TOKEN"), "tok")
            self.assertEqual(readConfigValue(panopto_path, "Client_ID"), "cid")
            self.assertEqual(readConfigValue(panopto_path, "Client_Secret"), "sec")

    def test_writeFailureShowsErrorAndDoesNotCloseDialog(self):
        _app, root = _newApp()
        dialog = gui.SettingsDialog(root)

        with mock.patch.object(gui, "writeConfigValue", return_value="disk full"), \
             mock.patch.object(dialog, "destroy") as destroy, \
             mock.patch.object(messagebox, "showerror") as show_error:
            dialog._save()

        show_error.assert_called_once()
        destroy.assert_not_called()


class BackgroundWorkerTests(unittest.TestCase):
    def test_workOutputIsCapturedAndFinishCallbackRuns(self):
        app, _root = _newApp()
        finished = threading.Event()

        def work():
            print("line from the stage")

        with mock.patch.object(app, "_onFinished", side_effect=lambda *a: finished.set()):
            app._runInBackground("Test stage", work)
            app.worker.join(5)

        self.assertTrue(finished.wait(5))

    def test_exceptionInWorkIsReportedNotRaised(self):
        app, _root = _newApp()
        seen = {}

        def work():
            raise RuntimeError("boom")

        def capture(label, error):
            seen["label"] = label
            seen["error"] = error

        with mock.patch.object(app, "_onFinished", side_effect=capture):
            app._runInBackground("Failing stage", work)
            app.worker.join(5)

        self.assertEqual(seen["label"], "Failing stage")
        self.assertIsInstance(seen["error"], RuntimeError)

    def test_secondRunWhileBusyIsRejected(self):
        app, _root = _newApp()
        release = threading.Event()

        def slow_work():
            release.wait(5)

        app._runInBackground("Slow stage", slow_work)
        try:
            with mock.patch.object(messagebox, "showinfo") as show_info:
                app._runInBackground("Second stage", lambda: None)
            show_info.assert_called_once()
        finally:
            release.set()
            app.worker.join(5)

    def test_onFinishedReportsCancelledRunAsStoppedEarly(self):
        app, _root = _newApp()
        resetCancel()
        from auditCore import requestCancel

        requestCancel()
        try:
            with mock.patch.object(messagebox, "askyesno", return_value=False) as ask:
                app._onFinished("Some stage", None)
        finally:
            resetCancel()

        self.assertIn("stopped early", ask.call_args.args[1])

    def test_onFinishedReportsNormalCompletion(self):
        app, _root = _newApp()
        resetCancel()

        with mock.patch.object(messagebox, "askyesno", return_value=False) as ask:
            app._onFinished("Some stage", None)

        self.assertIn("complete", ask.call_args.args[1])
        self.assertNotIn("stopped early", ask.call_args.args[1])


class CancellationTests(unittest.TestCase):
    def tearDown(self):
        resetCancel()

    def test_stopAuditSetsCancellationFlag(self):
        app, _root = _newApp()
        resetCancel()

        app.stopAudit()

        self.assertTrue(isCancelled())

    def test_promptFromWorkerDoesNotDeadlockWhenCalledFromAWorkerThread(self):
        app, _root = _newApp()
        finished = threading.Event()

        def worker():
            app._promptFromWorker("Login", "please log in")
            finished.set()

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(5)

        self.assertTrue(finished.is_set())

    def test_promptFromWorkerOnMainThreadShowsDirectly(self):
        app, _root = _newApp()
        with mock.patch.object(messagebox, "showinfo") as show_info:
            app._promptFromWorker("Login", "please log in")

        show_info.assert_called_once()


class ResetDataTests(unittest.TestCase):
    def test_confirmedResetInvokesDataReset(self):
        app, _root = _newApp()

        with mock.patch.object(messagebox, "askyesno", return_value=True), \
             mock.patch("dataReset.resetDataFiles", return_value=3) as reset_files, \
             mock.patch.object(messagebox, "showinfo") as show_info:
            app.resetData()

        reset_files.assert_called_once()
        show_info.assert_called_once()

    def test_declinedResetDoesNothing(self):
        app, _root = _newApp()

        with mock.patch.object(messagebox, "askyesno", return_value=False), \
             mock.patch("dataReset.resetDataFiles") as reset_files:
            app.resetData()

        reset_files.assert_not_called()


class CloseHandlingTests(unittest.TestCase):
    def test_closeWithNoWorkerDestroysImmediately(self):
        app, _root = _newApp()
        with mock.patch.object(app.root, "destroy") as destroy:
            app._onClose()
        destroy.assert_called_once()

    def test_closeWhileRunningAsksForConfirmation(self):
        app, _root = _newApp()
        release = threading.Event()
        app._runInBackground("Slow stage", lambda: release.wait(5))

        try:
            with mock.patch.object(messagebox, "askyesno", return_value=False) as ask, \
                 mock.patch.object(app.root, "destroy") as destroy:
                app._onClose()

            ask.assert_called_once()
            destroy.assert_not_called()
        finally:
            release.set()
            app.worker.join(5)


if __name__ == "__main__":
    unittest.main()
