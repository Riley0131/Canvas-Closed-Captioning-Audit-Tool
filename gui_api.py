"""Business logic behind the desktop GUI, independent of the GUI toolkit.

:class:`AuditApi` holds every action the interface can trigger - run an
audit, view results, edit settings, reset data - and reports progress back
through a small ``notify(event, payload)`` callback supplied by the caller.
It imports nothing from ``webview`` (or any other GUI toolkit), so it can be
exercised by tests with a plain recording callback; ``gui.py`` is the thin
layer that wires this to an actual window and translates ``notify`` calls
into JavaScript.

Every public method (no leading underscore) becomes callable from the web
page as ``pywebview.api.<name>(...)`` - see ``gui.py``. Methods that start
long-running work return immediately after starting a background thread;
the thread reports back through ``notify`` rather than the method's return
value, so the page never blocks waiting for an audit to finish.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import webbrowser
from typing import Any, Callable, Dict, Optional

import browser as browserSession
import dataReset
import individualAudit
import runAudit
from auditCore import (
    CAPTION_KIND_LABELS,
    RESULTS_FILE,
    ensureDataDirs,
    isCancelled,
    loadJson,
    requestCancel,
    resetCancel,
    summarizeResults,
)
from configStore import CANVAS_CONFIG, PANOPTO_CONFIG, readConfigValue, writeConfigValue

try:
    from config.version import version
except Exception:  # pragma: no cover - config may be missing
    version = "unknown"

NotifyFn = Callable[[str, Any], None]


class _LineBufferedNotifier:
    """File-like object that turns stdout writes into one ``notify("log", …)``
    call per completed line, mirroring the old queue-based log pane."""

    encoding = "utf-8"

    def __init__(self, notify: NotifyFn) -> None:
        self._notify = notify
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, _, self._buffer = self._buffer.partition("\n")
            self._notify("log", line)
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._notify("log", self._buffer)
            self._buffer = ""

    def isatty(self) -> bool:
        return False


class AuditApi:
    """Exposed to the web page as ``pywebview.api``."""

    def __init__(self, notify: NotifyFn) -> None:
        self._notify = notify
        self._worker: Optional[threading.Thread] = None
        self._login_event: Optional[threading.Event] = None
        ensureDataDirs()
        browserSession.setPromptHandler(self._handleLoginPrompt)

    # ------------------------------------------------------------------
    # window metadata
    def get_version(self) -> str:
        """The version string shown in the header."""

        return version

    # ------------------------------------------------------------------
    # running audits
    @property
    def busy(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def is_busy(self) -> bool:
        return self.busy

    def run_complete_audit(self, headless: bool = False) -> Dict[str, Any]:
        """Start a full audit across every course the Canvas token can see."""

        return self._start(
            "Complete audit",
            lambda: runAudit.main(["--headless"] if headless else []),
        )

    def run_individual_audit(self, course_id: str, headless: bool = False) -> Dict[str, Any]:
        """Start an audit of a single course."""

        course_id = (course_id or "").strip()
        if not course_id:
            return {"started": False, "error": "Please enter a course ID."}

        return self._start(
            f"Audit of course {course_id}",
            lambda: individualAudit.main(course_id, headless=headless),
        )

    def stop_audit(self) -> Dict[str, Any]:
        """Ask the running audit to stop after its current video."""

        if not self.busy:
            return {"stopped": False, "error": "No audit is running."}

        requestCancel()
        self._notify("status", "Stopping after the current video...")
        return {"stopped": True}

    def _start(self, label: str, work: Callable[[], Any]) -> Dict[str, Any]:
        if self.busy:
            return {"started": False, "error": "An audit is already running."}

        resetCancel()
        self._notify("run_started", {"label": label})

        def target() -> None:
            writer = _LineBufferedNotifier(self._notify)
            original_out, original_err = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = writer
            error = None
            try:
                work()
            except Exception as exc:  # pragma: no cover - defensive
                error = str(exc)
            finally:
                writer.flush()
                sys.stdout, sys.stderr = original_out, original_err
                self._notify(
                    "run_finished",
                    {"label": label, "error": error, "cancelled": isCancelled()},
                )

        self._worker = threading.Thread(target=target, daemon=True)
        self._worker.start()
        return {"started": True}

    # ------------------------------------------------------------------
    # results
    def get_results(self) -> Dict[str, Any]:
        """Everything the results view needs: entries, a summary, and labels."""

        raw = loadJson(RESULTS_FILE, default=[])
        entries = [entry for entry in raw if isinstance(entry, dict)]

        return {
            "entries": entries,
            "summary": summarizeResults(entries),
            "captionLabels": CAPTION_KIND_LABELS,
        }

    def open_external(self, url: str) -> Dict[str, Any]:
        """Open ``url`` in the operator's default browser."""

        if not url:
            return {"opened": False}

        webbrowser.open(url)
        return {"opened": True}

    def open_results_file(self) -> Dict[str, Any]:
        """Open ``data/audited_videos.json`` in the system's default viewer."""

        path = os.path.abspath(RESULTS_FILE)
        if not os.path.exists(path):
            return {"opened": False, "error": "No results file yet. Run an audit first."}

        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            return {"opened": False, "error": str(exc)}

        return {"opened": True}

    # ------------------------------------------------------------------
    # settings
    def get_settings(self) -> Dict[str, str]:
        return {
            "canvasToken": readConfigValue(CANVAS_CONFIG, "CANVAS_API_TOKEN"),
            "clientId": readConfigValue(PANOPTO_CONFIG, "Client_ID"),
            "clientSecret": readConfigValue(PANOPTO_CONFIG, "Client_Secret"),
        }

    def save_settings(
        self, canvas_token: str = "", client_id: str = "", client_secret: str = ""
    ) -> Dict[str, Any]:
        for path, key, value in (
            (CANVAS_CONFIG, "CANVAS_API_TOKEN", canvas_token),
            (PANOPTO_CONFIG, "Client_ID", client_id),
            (PANOPTO_CONFIG, "Client_Secret", client_secret),
        ):
            error = writeConfigValue(path, key, (value or "").strip())
            if error:
                return {"saved": False, "error": f"Could not save {key}: {error}"}

        return {"saved": True}

    # ------------------------------------------------------------------
    # data reset
    def reset_data(self) -> Dict[str, Any]:
        """Delete cached course/module/result JSON. The page confirms first."""

        return {"removed": dataReset.resetDataFiles()}

    # ------------------------------------------------------------------
    # login prompt bridge - browser.py calls this from the audit worker
    # thread when a Selenium-driven stage needs an interactive login.
    def _handleLoginPrompt(self, title: str, message: str) -> None:
        event = threading.Event()
        self._login_event = event
        self._notify("login_prompt", {"title": title, "message": message})
        event.wait()

    def confirm_login(self) -> Dict[str, Any]:
        """Called by the page when the operator clicks Continue on the login
        prompt; releases the worker thread waiting in _handleLoginPrompt."""

        event, self._login_event = self._login_event, None
        if event is not None:
            event.set()
            return {"acknowledged": True}

        return {"acknowledged": False}
