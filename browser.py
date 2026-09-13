"""Shared Selenium plumbing.

Both the Panopto and the embedded-Canvas auditors used to build their own
ChromeDriver, which meant ``webdriver-manager`` resolved a driver twice and the
operator had to log in twice per audit. :class:`BrowserSession` keeps one
browser alive for the whole run and remembers which sites have been logged into.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

try:  # Selenium is only needed for the browser-backed stages.
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
except Exception:  # pragma: no cover - import guard for test environments
    webdriver = None  # type: ignore
    WebDriverException = Exception  # type: ignore
    Options = None  # type: ignore
    Service = None  # type: ignore

try:
    from webdriver_manager.chrome import ChromeDriverManager
except Exception:  # pragma: no cover
    ChromeDriverManager = None  # type: ignore

# Resolving a driver binary is slow, so cache the path for the whole process.
_DRIVER_PATH: Optional[str] = None

# The GUI replaces this so login prompts appear on its main thread instead of
# spawning a second Tk root from a worker thread.
_PROMPT_HANDLER: Optional[Any] = None


def setPromptHandler(handler: Optional[Any]) -> None:
    """Route :func:`promptContinue` through ``handler(title, message)``."""

    global _PROMPT_HANDLER
    _PROMPT_HANDLER = handler


def _resolveDriverPath() -> Optional[str]:
    global _DRIVER_PATH

    if _DRIVER_PATH is not None:
        return _DRIVER_PATH

    override = os.environ.get("CHROMEDRIVER_PATH")
    if override and os.path.exists(override):
        _DRIVER_PATH = override
        return _DRIVER_PATH

    if ChromeDriverManager is None:
        return None

    try:
        _DRIVER_PATH = ChromeDriverManager().install()
    except Exception as exc:  # pragma: no cover - network/driver failures
        print(f"Unable to resolve ChromeDriver: {exc}")
        return None

    return _DRIVER_PATH


def buildChromeOptions(headless: bool = False) -> Any:
    """Chrome options tuned for fast page scans."""

    options = Options()
    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,900")
    # Chrome's own logging is noisy on Windows and slows the console down.
    options.add_argument("--log-level=3")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])
    # Media and images are irrelevant to caption detection; skipping them cuts
    # page load time substantially on video-heavy pages.
    options.add_experimental_option(
        "prefs",
        {
            "profile.managed_default_content_settings.images": 2,
            "profile.default_content_setting_values.notifications": 2,
        },
    )
    return options


class BrowserSession:
    """Lazily started Chrome session shared between audit stages."""

    def __init__(self, headless: bool = False, page_load_timeout: int = 30) -> None:
        self.headless = headless
        self.page_load_timeout = page_load_timeout
        self._driver: Optional[Any] = None
        self._logged_in: Dict[str, bool] = {}

    # ------------------------------------------------------------------
    @property
    def started(self) -> bool:
        return self._driver is not None

    def driver(self) -> Optional[Any]:
        """Return the live driver, starting Chrome on first use."""

        if self._driver is not None:
            return self._driver

        if webdriver is None:
            print("Selenium is not installed; browser-backed checks are unavailable.")
            return None

        driver_path = _resolveDriverPath()
        if driver_path is None:
            print("ChromeDriver is unavailable; browser-backed checks are unavailable.")
            return None

        try:
            self._driver = webdriver.Chrome(
                service=Service(driver_path),
                options=buildChromeOptions(self.headless),
            )
            self._driver.set_page_load_timeout(self.page_load_timeout)
        except WebDriverException as exc:
            print(f"Unable to start Chrome: {exc}")
            self._driver = None

        return self._driver

    def ensureLogin(self, login_url: str, site_name: str) -> bool:
        """Open ``login_url`` once per site and wait for the operator.

        Returns ``False`` when no browser could be started.
        """

        driver = self.driver()
        if driver is None:
            return False

        if self._logged_in.get(site_name):
            return True

        try:
            driver.get(login_url)
        except WebDriverException as exc:
            print(f"Could not open {site_name} login page: {exc}")

        promptContinue(
            f"{site_name} Login",
            f"Please log into {site_name} in the browser window, then press Continue.",
        )
        self._logged_in[site_name] = True
        return True

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass
        self._driver = None
        self._logged_in.clear()

    def __enter__(self) -> "BrowserSession":
        return self

    def __exit__(self, exc_type, exc, exc_tb) -> None:
        self.close()


def promptContinue(title: str, message: str) -> None:
    """Block until the operator confirms.

    The GUI registers a handler (see :func:`setPromptHandler`) that shows its
    own login dialog; this terminal fallback only runs when a script is
    invoked directly (``python runAudit.py``) without going through the GUI.
    """

    if _PROMPT_HANDLER is not None:
        _PROMPT_HANDLER(title, message)
        return

    try:
        input(f"{title}: {message}\nPress Enter once you are done...")
    except EOFError:
        pass
