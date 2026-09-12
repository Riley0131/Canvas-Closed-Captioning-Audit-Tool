"""Tests for browser.py's shared Selenium session and login plumbing."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import browser
from selenium.common.exceptions import WebDriverException


class BuildChromeOptionsTests(unittest.TestCase):
    def test_headlessAddsHeadlessFlag(self):
        options = browser.buildChromeOptions(headless=True)
        self.assertIn("--headless=new", options.arguments)

    def test_nonHeadlessOmitsHeadlessFlag(self):
        options = browser.buildChromeOptions(headless=False)
        self.assertNotIn("--headless=new", options.arguments)

    def test_commonArgumentsAreAlwaysPresent(self):
        options = browser.buildChromeOptions()
        for expected in ("--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"):
            self.assertIn(expected, options.arguments)


class DriverResolutionTests(unittest.TestCase):
    def setUp(self):
        browser._DRIVER_PATH = None

    def tearDown(self):
        browser._DRIVER_PATH = None

    def test_envOverrideIsUsedWhenPathExists(self):
        with mock.patch.object(os.environ, "get", side_effect=lambda k, d=None: "/tmp" if k == "CHROMEDRIVER_PATH" else d), \
             mock.patch("os.path.exists", return_value=True):
            path = browser._resolveDriverPath()

        self.assertEqual(path, "/tmp")

    def test_driverPathIsCachedAcrossCalls(self):
        browser._DRIVER_PATH = "/cached/path"
        with mock.patch.object(browser, "ChromeDriverManager") as manager:
            path = browser._resolveDriverPath()

        manager.assert_not_called()
        self.assertEqual(path, "/cached/path")

    def test_managerFailureReturnsNone(self):
        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.object(browser, "ChromeDriverManager") as manager:
            os.environ.pop("CHROMEDRIVER_PATH", None)
            manager.return_value.install.side_effect = RuntimeError("network down")
            path = browser._resolveDriverPath()

        self.assertIsNone(path)


class BrowserSessionTests(unittest.TestCase):
    def test_startedIsFalseBeforeFirstDriverCall(self):
        session = browser.BrowserSession()
        self.assertFalse(session.started)

    def test_driverIsLazilyCreatedAndCached(self):
        session = browser.BrowserSession()
        fake_driver = mock.Mock()

        with mock.patch.object(browser, "_resolveDriverPath", return_value="/path"), \
             mock.patch.object(browser, "webdriver") as fake_webdriver:
            fake_webdriver.Chrome.return_value = fake_driver

            first = session.driver()
            second = session.driver()

        self.assertIs(first, fake_driver)
        self.assertIs(second, fake_driver)
        fake_webdriver.Chrome.assert_called_once()
        self.assertTrue(session.started)

    def test_noChromedriverAvailableReturnsNone(self):
        session = browser.BrowserSession()
        with mock.patch.object(browser, "_resolveDriverPath", return_value=None):
            self.assertIsNone(session.driver())
        self.assertFalse(session.started)

    def test_chromeStartupFailureReturnsNone(self):
        session = browser.BrowserSession()
        with mock.patch.object(browser, "_resolveDriverPath", return_value="/path"), \
             mock.patch.object(browser, "webdriver") as fake_webdriver:
            fake_webdriver.Chrome.side_effect = WebDriverException("no chrome binary")
            result = session.driver()

        self.assertIsNone(result)

    def test_ensureLoginPromptsOnlyOncePerSite(self):
        session = browser.BrowserSession()
        fake_driver = mock.Mock()

        with mock.patch.object(session, "driver", return_value=fake_driver), \
             mock.patch.object(browser, "promptContinue") as prompt:
            first = session.ensureLogin("https://x/login", "Site")
            second = session.ensureLogin("https://x/login", "Site")

        self.assertTrue(first)
        self.assertTrue(second)
        prompt.assert_called_once()
        fake_driver.get.assert_called_once_with("https://x/login")

    def test_ensureLoginReturnsFalseWithoutADriver(self):
        session = browser.BrowserSession()
        with mock.patch.object(session, "driver", return_value=None):
            self.assertFalse(session.ensureLogin("https://x/login", "Site"))

    def test_ensureLoginSurvivesPageLoadFailure(self):
        session = browser.BrowserSession()
        fake_driver = mock.Mock()
        fake_driver.get.side_effect = WebDriverException("timeout")

        with mock.patch.object(session, "driver", return_value=fake_driver), \
             mock.patch.object(browser, "promptContinue"):
            self.assertTrue(session.ensureLogin("https://x/login", "Site"))

    def test_closeQuitsDriverAndResetsState(self):
        session = browser.BrowserSession()
        fake_driver = mock.Mock()
        session._driver = fake_driver
        session._logged_in["Site"] = True

        session.close()

        fake_driver.quit.assert_called_once()
        self.assertFalse(session.started)
        self.assertEqual(session._logged_in, {})

    def test_closeToleratesQuitException(self):
        session = browser.BrowserSession()
        fake_driver = mock.Mock()
        fake_driver.quit.side_effect = RuntimeError("already dead")
        session._driver = fake_driver

        session.close()  # should not raise
        self.assertFalse(session.started)

    def test_closeOnNeverStartedSessionIsANoOp(self):
        browser.BrowserSession().close()

    def test_contextManagerClosesOnExit(self):
        with browser.BrowserSession() as session:
            fake_driver = mock.Mock()
            session._driver = fake_driver

        fake_driver.quit.assert_called_once()


class PromptContinueTests(unittest.TestCase):
    def tearDown(self):
        browser.setPromptHandler(None)

    def test_customHandlerIsUsedWhenSet(self):
        handler = mock.Mock()
        browser.setPromptHandler(handler)

        browser.promptContinue("Title", "Message")

        handler.assert_called_once_with("Title", "Message")

    def test_noHandlerAndNoTkFallsBackToInput(self):
        with mock.patch.object(browser, "tk", None), \
             mock.patch("builtins.input", return_value="") as fake_input:
            browser.promptContinue("Title", "Message")

        fake_input.assert_called_once()

    def test_tkFailureFallsBackToInput(self):
        fake_tk = mock.Mock()
        fake_tk.Tk.side_effect = RuntimeError("no display")

        with mock.patch.object(browser, "tk", fake_tk), \
             mock.patch("builtins.input", return_value="") as fake_input:
            browser.promptContinue("Title", "Message")

        fake_input.assert_called_once()


if __name__ == "__main__":
    unittest.main()
