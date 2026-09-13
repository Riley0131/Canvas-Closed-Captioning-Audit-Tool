"""Desktop interface for the Closed Captioning Audit tool.

Built on `pywebview <https://pywebview.flowrl.com/>`_ instead of Tkinter. The
window is a small local web page (``webui/``) rendered by the operating
system's own web engine - WKWebView on macOS, WebView2 on Windows, WebKitGTK
on Linux - the same rendering code each platform's real browser uses, rather
than a separate UI toolkit bundled with Python. Tcl/Tk (Tkinter's backend)
has a long-standing bug where windows render entirely blank on macOS Big Sur
and later unless the bundled Tcl/Tk is newer than the version most Python
installs still ship; using the platform's own web engine sidesteps that
family of bugs entirely.

This module is intentionally thin: it creates the window and translates
:class:`gui_api.AuditApi`'s ``notify(event, payload)`` calls into JavaScript.
All of the actual behaviour - starting audits, reading settings, resetting
data - lives in :mod:`gui_api`, which has no dependency on ``webview`` and is
unit tested on its own.
"""

from __future__ import annotations

import json
import os
import sys

try:
    import webview
except ImportError as exc:  # pragma: no cover - depends on the environment
    sys.stderr.write(
        "pywebview is not installed.\n"
        "Run `pip install -r requirements.txt` and try again.\n"
        "On macOS you also need PyObjC: `pip install pyobjc`.\n"
    )
    raise SystemExit(1) from exc

from gui_api import AuditApi


def resolveBaseDir() -> str:
    """Directory to resolve bundled assets (``webui/``) against.

    A plain ``__file__``-relative path breaks once this is packaged by
    PyInstaller: a frozen build unpacks its data files to a temporary
    directory named in ``sys._MEIPASS``, not next to the executable.
    """

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS  # type: ignore[attr-defined]

    return os.path.dirname(os.path.abspath(__file__))


WEBUI_DIR = os.path.join(resolveBaseDir(), "webui")
INDEX_HTML = os.path.join(WEBUI_DIR, "index.html")


def buildNotifier(window_holder: dict):
    """Return a ``notify(event, payload)`` callback that runs JS in the window.

    The window object is looked up lazily through ``window_holder`` because
    :class:`AuditApi` must exist before ``webview.create_window`` returns the
    window it will be given as ``js_api`` - see :func:`main`.
    """

    def notify(event: str, payload) -> None:
        window = window_holder.get("window")
        if window is None:
            return

        message = json.dumps({"event": event, "payload": payload})
        try:
            window.evaluate_js(f"window.dispatchAppEvent({message})")
        except Exception:
            # The window may already be closing; dropping the update is fine.
            pass

    return notify


def confirmQuitWhileBusy(window, api: AuditApi) -> bool:
    """``events.closing`` handler: block a close while an audit is running."""

    if not api.busy:
        return True

    return window.create_confirmation_dialog(
        "Quit", "An audit is still running. Stop it and quit?"
    )


def main() -> None:
    window_holder: dict = {}
    api = AuditApi(buildNotifier(window_holder))

    window = webview.create_window(
        "Closed Captioning Audit",
        url=INDEX_HTML,
        js_api=api,
        width=980,
        height=680,
        min_size=(760, 520),
    )
    window_holder["window"] = window

    window.events.closing += lambda: confirmQuitWhileBusy(window, api)

    webview.start()


if __name__ == "__main__":
    main()
