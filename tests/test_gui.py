"""Tests for gui.py's thin pywebview wiring.

gui_api.AuditApi (the actual logic) is tested on its own in test_guiApi.py
without any GUI toolkit involved. These tests cover only the translation
layer in gui.py: building the notify() callback, wiring the window's close
confirmation, and main()'s setup - using a lightweight stand-in for the
`webview` package so nothing here needs a real window or display.
"""

import json
import os
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class _FakeEvent:
    """Stand-in for webview.Event: supports += and records handlers."""

    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class _FakeWindow:
    def __init__(self):
        self.evaluate_js = mock.Mock()
        self.create_confirmation_dialog = mock.Mock(return_value=True)
        self.events = types.SimpleNamespace(closing=_FakeEvent())


def _installWebviewStub():
    fake_module = types.ModuleType("webview")
    fake_module.create_window = mock.Mock(return_value=_FakeWindow())
    fake_module.start = mock.Mock()
    sys.modules["webview"] = fake_module
    return fake_module


_installWebviewStub()
sys.modules.pop("gui", None)
import gui  # noqa: E402  (must follow the webview stub installation)


class BuildNotifierTests(unittest.TestCase):
    def test_noWindowYetIsANoOp(self):
        notify = gui.buildNotifier({})
        notify("log", "a line")  # must not raise

    def test_callsEvaluateJsWithEncodedEventAndPayload(self):
        window = mock.Mock()
        notify = gui.buildNotifier({"window": window})

        notify("run_started", {"label": "Complete audit"})

        window.evaluate_js.assert_called_once()
        script = window.evaluate_js.call_args.args[0]
        self.assertTrue(script.startswith("window.dispatchAppEvent("))

        # The JSON payload embedded in the script round-trips correctly.
        encoded = script[len("window.dispatchAppEvent(") : -1]
        decoded = json.loads(encoded)
        self.assertEqual(decoded, {"event": "run_started", "payload": {"label": "Complete audit"}})

    def test_evaluateJsFailureIsSwallowed(self):
        window = mock.Mock()
        window.evaluate_js.side_effect = RuntimeError("window closed")
        notify = gui.buildNotifier({"window": window})

        notify("log", "line")  # must not raise

    def test_lookupIsLazyPerCall(self):
        holder = {}
        notify = gui.buildNotifier(holder)

        notify("log", "before window exists")  # no-op, no window yet

        window = mock.Mock()
        holder["window"] = window
        notify("log", "after window exists")

        window.evaluate_js.assert_called_once()


class ConfirmQuitWhileBusyTests(unittest.TestCase):
    def test_notBusyClosesImmediatelyWithoutPromptingUser(self):
        window = mock.Mock()
        api = mock.Mock(busy=False)

        result = gui.confirmQuitWhileBusy(window, api)

        self.assertTrue(result)
        window.create_confirmation_dialog.assert_not_called()

    def test_busyAsksForConfirmation(self):
        window = mock.Mock()
        window.create_confirmation_dialog.return_value = True
        api = mock.Mock(busy=True)

        result = gui.confirmQuitWhileBusy(window, api)

        self.assertTrue(result)
        window.create_confirmation_dialog.assert_called_once()

    def test_busyAndDeclinedKeepsWindowOpen(self):
        window = mock.Mock()
        window.create_confirmation_dialog.return_value = False
        api = mock.Mock(busy=True)

        self.assertFalse(gui.confirmQuitWhileBusy(window, api))


class MainTests(unittest.TestCase):
    def test_mainCreatesWindowAndStartsTheEventLoop(self):
        fake_window = _FakeWindow()
        fake_webview = sys.modules["webview"]
        fake_webview.create_window.return_value = fake_window
        fake_webview.create_window.reset_mock()
        fake_webview.start.reset_mock()

        with mock.patch("gui.AuditApi") as fake_api_cls:
            fake_api = mock.Mock(busy=False)
            fake_api_cls.return_value = fake_api

            gui.main()

        fake_webview.create_window.assert_called_once()
        kwargs = fake_webview.create_window.call_args.kwargs
        self.assertEqual(kwargs["url"], gui.INDEX_HTML)
        self.assertIs(kwargs["js_api"], fake_api)

        fake_webview.start.assert_called_once()
        self.assertEqual(len(fake_window.events.closing.handlers), 1)

    def test_closingHandlerWiredByMainConsultsApiBusyState(self):
        fake_window = _FakeWindow()
        fake_webview = sys.modules["webview"]
        fake_webview.create_window.return_value = fake_window

        with mock.patch("gui.AuditApi") as fake_api_cls:
            fake_api = mock.Mock(busy=True)
            fake_api_cls.return_value = fake_api

            gui.main()

        handler = fake_window.events.closing.handlers[0]
        fake_window.create_confirmation_dialog.return_value = False

        self.assertFalse(handler())
        fake_window.create_confirmation_dialog.assert_called_once()


class IndexHtmlPathTests(unittest.TestCase):
    def test_indexHtmlPointsAtAnExistingFile(self):
        self.assertTrue(os.path.exists(gui.INDEX_HTML))
        self.assertTrue(gui.INDEX_HTML.endswith("index.html"))


class ResolveBaseDirTests(unittest.TestCase):
    """A frozen PyInstaller build extracts data files under sys._MEIPASS, not
    next to the script - resolveBaseDir() must prefer that when present."""

    def test_nonFrozenUsesScriptDirectory(self):
        with mock.patch.object(gui.sys, "frozen", False, create=True):
            self.assertEqual(gui.resolveBaseDir(), ROOT)

    def test_frozenUsesMeipass(self):
        with mock.patch.object(gui.sys, "frozen", True, create=True), \
             mock.patch.object(gui.sys, "_MEIPASS", "/tmp/frozen-bundle", create=True):
            self.assertEqual(gui.resolveBaseDir(), "/tmp/frozen-bundle")

    def test_frozenWithoutMeipassFallsBackToScriptDirectory(self):
        with mock.patch.object(gui.sys, "frozen", True, create=True):
            if hasattr(gui.sys, "_MEIPASS"):
                del gui.sys._MEIPASS
            self.assertEqual(gui.resolveBaseDir(), ROOT)


if __name__ == "__main__":
    unittest.main()
