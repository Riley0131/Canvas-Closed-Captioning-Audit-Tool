"""Desktop interface for the Closed Captioning Audit tool.

Rebuilt around three fixes to the original window:

* audits run in a worker thread inside this process instead of shelling out to
  ``python runAudit.py``. The old approach froze the window for the whole run
  and failed outright in the packaged executable, where no interpreter or
  source files are on disk;
* console output streams into a log pane and a progress bar reports activity,
  so a long audit no longer looks hung behind a modal dialog;
* results open in a sortable, filterable table that reports the caption source
  of each video rather than dumping raw JSON into a text editor.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk
from typing import Any, Callable, Dict, List, Optional

import browser as browserSession
from auditCore import (
    CAPTION_AUTO,
    CAPTION_HUMAN,
    CAPTION_KIND_LABELS,
    CAPTION_NONE,
    CAPTION_UNKNOWN,
    RESULTS_FILE,
    ensureDataDirs,
    isCancelled,
    loadJson,
    requestCancel,
    resetCancel,
    summarizeResults,
)
from configStore import (
    CANVAS_CONFIG,
    PANOPTO_CONFIG,
    readConfigValue,
    writeConfigValue,
)

try:
    from config.version import version
except Exception:  # pragma: no cover - config may be missing
    version = "unknown"

PAD = 10


# ----------------------------------------------------------------------
class _QueueWriter:
    """File-like object that forwards writes to a queue."""

    encoding = "utf-8"

    def __init__(self, sink: "queue.Queue[str]") -> None:
        self._sink = sink
        self._buffer = ""

    def isatty(self) -> bool:
        return False

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, _, self._buffer = self._buffer.partition("\n")
            self._sink.put(line)
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._sink.put(self._buffer)
            self._buffer = ""


class AuditApp:
    """The main application window."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.logQueue: "queue.Queue[str]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.headless = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Ready")

        root.title(f"UCCS Closed Captioning Audit {version}")
        root.geometry("820x560")
        root.minsize(700, 480)

        self._configureStyle()
        self._buildLayout()

        browserSession.setPromptHandler(self._promptFromWorker)
        self.root.after(100, self._drainLog)
        self.root.protocol("WM_DELETE_WINDOW", self._onClose)

    # ------------------------------------------------------------------
    # layout
    def _configureStyle(self) -> None:
        style = ttk.Style()
        # macOS's native 'aqua' theme renders correctly and looks native;
        # switching away from it has caused blank/undrawn ttk widgets on some
        # Tk builds. 'clam' is the better choice on Windows/Linux, where the
        # platform default theme looks dated.
        if sys.platform != "darwin" and "clam" in style.theme_names():
            style.theme_use("clam")

        # "Segoe UI" is a Windows-only font family; naming it directly here
        # left the header and buttons invisible/using a fallback face on
        # macOS and Linux. The Tk named fonts below always resolve to
        # whatever the current platform's real default font is.
        style.configure("Header.TLabel", font=("TkDefaultFont", 18, "bold"))
        style.configure("Sub.TLabel", foreground="#555555")
        style.configure("Danger.TButton", foreground="#8b0000")
        style.configure("Accent.TButton", font=("TkDefaultFont", 10, "bold"))

    def _buildLayout(self) -> None:
        container = ttk.Frame(self.root, padding=PAD)
        container.pack(fill="both", expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(2, weight=1)

        header = ttk.Frame(container)
        header.grid(row=0, column=0, sticky="ew", pady=(0, PAD))
        ttk.Label(header, text="Closed Captioning Audit", style="Header.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            header,
            text="Check Canvas courses for captioned video and identify the caption source.",
            style="Sub.TLabel",
        ).pack(anchor="w")

        actions = ttk.LabelFrame(container, text="Audit", padding=PAD)
        actions.grid(row=1, column=0, sticky="ew")
        for column in range(4):
            actions.columnconfigure(column, weight=1)

        self.runButton = ttk.Button(
            actions,
            text="Run Complete Audit",
            style="Accent.TButton",
            command=self.runCompleteAudit,
        )
        self.runButton.grid(row=0, column=0, sticky="ew", padx=4)

        self.courseButton = ttk.Button(
            actions, text="Audit One Course...", command=self.promptIndividualAudit
        )
        self.courseButton.grid(row=0, column=1, sticky="ew", padx=4)

        self.resultsButton = ttk.Button(
            actions, text="View Results", command=self.showResults
        )
        self.resultsButton.grid(row=0, column=2, sticky="ew", padx=4)

        self.stopButton = ttk.Button(
            actions, text="Stop", command=self.stopAudit, state="disabled"
        )
        self.stopButton.grid(row=0, column=3, sticky="ew", padx=4)

        ttk.Checkbutton(
            actions,
            text="Run the browser hidden (headless) - only works without an interactive login",
            variable=self.headless,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(PAD, 0))

        logFrame = ttk.LabelFrame(container, text="Activity", padding=PAD)
        logFrame.grid(row=2, column=0, sticky="nsew", pady=PAD)
        logFrame.columnconfigure(0, weight=1)
        logFrame.rowconfigure(0, weight=1)

        self.log = tk.Text(
            logFrame,
            wrap="none",
            height=12,
            state="disabled",
            background="#1e1e1e",
            foreground="#d4d4d4",
            insertbackground="#d4d4d4",
            relief="flat",
            font=("TkFixedFont", 9),
        )
        self.log.grid(row=0, column=0, sticky="nsew")

        scrollY = ttk.Scrollbar(logFrame, orient="vertical", command=self.log.yview)
        scrollY.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scrollY.set)

        footer = ttk.Frame(container)
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)

        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=160)
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, PAD))

        ttk.Label(footer, textvariable=self.status, style="Sub.TLabel").grid(
            row=0, column=1, sticky="e", padx=(0, PAD)
        )
        ttk.Button(footer, text="Settings", command=self.promptSettings).grid(
            row=0, column=2, padx=4
        )
        ttk.Button(
            footer, text="Reset Data", style="Danger.TButton", command=self.resetData
        ).grid(row=0, column=3)

    # ------------------------------------------------------------------
    # logging plumbing
    def _appendLog(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drainLog(self) -> None:
        try:
            while True:
                self._appendLog(self.logQueue.get_nowait())
        except queue.Empty:
            pass

        self.root.after(100, self._drainLog)

    def _setRunning(self, running: bool, status: str) -> None:
        state = "disabled" if running else "normal"
        for widget in (self.runButton, self.courseButton, self.resultsButton):
            widget.configure(state=state)
        self.stopButton.configure(state="normal" if running else "disabled")

        if running:
            self.progress.start(12)
        else:
            self.progress.stop()

        self.status.set(status)

    # ------------------------------------------------------------------
    # worker management
    def _runInBackground(self, label: str, work: Callable[[], Any]) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Audit", "An audit is already running.")
            return

        resetCancel()
        ensureDataDirs()
        self._appendLog(f"--- {label} ---")
        self._setRunning(True, f"{label} in progress...")

        def target() -> None:
            writer = _QueueWriter(self.logQueue)
            original_stdout, original_stderr = sys.stdout, sys.stderr
            sys.stdout = sys.stderr = writer
            try:
                work()
                self.root.after(0, lambda: self._onFinished(label, None))
            except Exception as exc:  # surfaced in the log and a dialog
                self.root.after(0, lambda exc=exc: self._onFinished(label, exc))
            finally:
                writer.flush()
                sys.stdout, sys.stderr = original_stdout, original_stderr

        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _onFinished(self, label: str, error: Optional[Exception]) -> None:
        self._setRunning(False, "Ready")

        if error is not None:
            self._appendLog(f"ERROR: {error}")
            messagebox.showerror(label, f"{label} failed:\n{error}")
            return

        cancelled = isCancelled()
        outcome = "stopped early" if cancelled else "complete"
        self._appendLog(f"--- {label} {outcome} ---")
        self.status.set(f"{label} {outcome}")

        if messagebox.askyesno(label, f"{label} {outcome}. View the results now?"):
            self.showResults()

    def _promptFromWorker(self, title: str, message: str) -> None:
        """Show a login prompt on the main thread and block the worker."""

        if threading.current_thread() is threading.main_thread():
            messagebox.showinfo(title, message, parent=self.root)
            return

        done = threading.Event()

        def show() -> None:
            try:
                messagebox.showinfo(title, message, parent=self.root)
            finally:
                done.set()

        self.root.after(0, show)
        done.wait()

    # ------------------------------------------------------------------
    # actions
    def runCompleteAudit(self) -> None:
        import runAudit

        headless = self.headless.get()
        self._runInBackground(
            "Complete audit",
            lambda: runAudit.main(["--headless"] if headless else []),
        )

    def promptIndividualAudit(self) -> None:
        popup = tk.Toplevel(self.root)
        popup.title("Audit a single course")
        popup.transient(self.root)
        popup.resizable(False, False)
        popup.grab_set()

        frame = ttk.Frame(popup, padding=PAD * 2)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Canvas course ID:").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(frame, width=28)
        entry.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, PAD))
        entry.focus_set()

        def confirm(_event: Any = None) -> None:
            course = entry.get().strip()
            if not course:
                messagebox.showerror("Course ID", "Please enter a course ID.", parent=popup)
                return

            popup.destroy()

            import individualAudit

            headless = self.headless.get()
            self._runInBackground(
                f"Audit of course {course}",
                lambda: individualAudit.main(course, headless=headless),
            )

        entry.bind("<Return>", confirm)
        ttk.Button(frame, text="Run audit", command=confirm).grid(
            row=2, column=0, sticky="ew", padx=(0, 4)
        )
        ttk.Button(frame, text="Cancel", command=popup.destroy).grid(
            row=2, column=1, sticky="ew"
        )

    def stopAudit(self) -> None:
        requestCancel()
        self.status.set("Stopping after the current video...")
        self._appendLog("Cancellation requested; finishing the current video.")

    def resetData(self) -> None:
        confirmed = messagebox.askyesno(
            "Reset data",
            "Delete every cached course, module and audit result?\n\n"
            "This cannot be undone.",
            icon="warning",
            default="no",
        )
        if not confirmed:
            return

        import dataReset

        removed = dataReset.resetDataFiles()
        self._appendLog(f"Reset removed {removed} file(s).")
        messagebox.showinfo("Reset", f"Removed {removed} data file(s).")

    def showResults(self) -> None:
        entries = loadJson(RESULTS_FILE, default=[])
        if not isinstance(entries, list) or not entries:
            messagebox.showinfo(
                "Results", "No audit results yet. Run an audit first."
            )
            return

        ResultsWindow(self.root, entries)

    def promptSettings(self) -> None:
        SettingsDialog(self.root)

    def _onClose(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(
                "Quit", "An audit is still running. Stop it and quit?"
            ):
                return
            requestCancel()

        self.root.destroy()


# ----------------------------------------------------------------------
class ResultsWindow(tk.Toplevel):
    """Sortable, filterable view of ``data/audited_videos.json``."""

    COLUMNS = (
        ("type", "Platform", 90),
        ("captions", "Captions", 90),
        ("kind", "Caption source", 190),
        ("confidence", "Confidence", 90),
        ("course", "Course", 90),
        ("url", "URL", 380),
    )

    def __init__(self, parent: tk.Misc, entries: List[Dict[str, Any]]) -> None:
        super().__init__(parent)
        self.entries = [entry for entry in entries if isinstance(entry, dict)]
        self.filterVar = tk.StringVar(value="All")
        self._sortColumn: Optional[str] = None
        self._sortReverse = False

        self.title("Audit results")
        self.geometry("980x600")
        self.minsize(720, 420)

        self._buildSummary()
        self._buildTable()
        self._populate()

    def _buildSummary(self) -> None:
        summary = summarizeResults(self.entries)

        frame = ttk.LabelFrame(self, text="Summary", padding=PAD)
        frame.pack(fill="x", padx=PAD, pady=(PAD, 0))

        captioned = summary["withCaptions"]
        total = summary["total"] or 1
        ttk.Label(
            frame,
            text=(
                f"{summary['total']} videos audited - "
                f"{captioned} captioned ({captioned / total:.0%}), "
                f"{summary['withoutCaptions']} missing captions"
            ),
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w")

        breakdown = " | ".join(
            f"{CAPTION_KIND_LABELS.get(kind, kind)}: {count}"
            for kind, count in sorted(summary["byCaptionKind"].items())
        )
        if breakdown:
            ttk.Label(frame, text=breakdown, style="Sub.TLabel").pack(
                anchor="w", pady=(4, 0)
            )

        platforms = " | ".join(
            f"{platform}: {bucket['withCaptions']}/{bucket['total']} captioned"
            for platform, bucket in sorted(summary["byType"].items())
        )
        if platforms:
            ttk.Label(frame, text=platforms, style="Sub.TLabel").pack(anchor="w")

    def _buildTable(self) -> None:
        controls = ttk.Frame(self, padding=(PAD, PAD, PAD, 0))
        controls.pack(fill="x")

        ttk.Label(controls, text="Show:").pack(side="left")
        options = ["All", "Missing captions"] + [
            CAPTION_KIND_LABELS[kind]
            for kind in (CAPTION_AUTO, CAPTION_HUMAN, CAPTION_UNKNOWN, CAPTION_NONE)
        ]
        combo = ttk.Combobox(
            controls,
            textvariable=self.filterVar,
            values=options,
            state="readonly",
            width=28,
        )
        combo.pack(side="left", padx=PAD)
        combo.bind("<<ComboboxSelected>>", lambda _event: self._populate())

        ttk.Button(controls, text="Open results file", command=self._openFile).pack(
            side="right"
        )
        ttk.Label(
            controls, text="Double-click a row to open the video", style="Sub.TLabel"
        ).pack(side="right", padx=PAD)

        frame = ttk.Frame(self, padding=PAD)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            frame,
            columns=[key for key, _, _ in self.COLUMNS],
            show="headings",
            selectmode="browse",
        )
        for key, heading, width in self.COLUMNS:
            self.tree.heading(
                key, text=heading, command=lambda k=key: self._sortBy(k)
            )
            self.tree.column(key, width=width, anchor="w", stretch=(key == "url"))

        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.tag_configure("missing", background="#ffe8e8")
        self.tree.tag_configure("auto", background="#fff6e0")
        self.tree.bind("<Double-1>", self._openSelected)

        scrollY = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        scrollY.grid(row=0, column=1, sticky="ns")
        scrollX = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        scrollX.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=scrollY.set, xscrollcommand=scrollX.set)

    def _rows(self) -> List[Dict[str, Any]]:
        selected = self.filterVar.get()
        rows = []

        for entry in self.entries:
            has_captions = bool(entry.get("has_captions"))
            kind = entry.get("caption_kind") or (
                CAPTION_UNKNOWN if has_captions else CAPTION_NONE
            )
            label = CAPTION_KIND_LABELS.get(kind, kind)

            if selected == "Missing captions" and has_captions:
                continue
            if selected not in ("All", "Missing captions") and label != selected:
                continue

            rows.append(
                {
                    "type": str(entry.get("type", "")),
                    "captions": "Yes" if has_captions else "No",
                    "kind": label,
                    "confidence": str(entry.get("caption_confidence", "") or ""),
                    "course": str(entry.get("course_id", "") or ""),
                    "url": str(entry.get("url", "")),
                    "_kind": kind,
                    "_hasCaptions": has_captions,
                }
            )

        if self._sortColumn:
            rows.sort(
                key=lambda row: row.get(self._sortColumn, "").lower(),
                reverse=self._sortReverse,
            )

        return rows

    def _populate(self) -> None:
        self.tree.delete(*self.tree.get_children())

        for row in self._rows():
            tags = []
            if not row["_hasCaptions"]:
                tags.append("missing")
            elif row["_kind"] == CAPTION_AUTO:
                tags.append("auto")

            self.tree.insert(
                "",
                "end",
                values=[row[key] for key, _, _ in self.COLUMNS],
                tags=tags,
            )

    def _sortBy(self, column: str) -> None:
        self._sortReverse = self._sortColumn == column and not self._sortReverse
        self._sortColumn = column
        self._populate()

    def _openSelected(self, _event: Any = None) -> None:
        selection = self.tree.selection()
        if not selection:
            return

        values = self.tree.item(selection[0], "values")
        url = values[-1] if values else ""
        if url:
            webbrowser.open(url)

    def _openFile(self) -> None:
        path = os.path.abspath(RESULTS_FILE)
        if not os.path.exists(path):
            messagebox.showerror("Results", "The results file no longer exists.")
            return

        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            messagebox.showerror("Results", f"Could not open the file:\n{exc}")


# ----------------------------------------------------------------------
class SettingsDialog(tk.Toplevel):
    """Edit the Canvas token and the Panopto API credentials."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("Settings")
        self.transient(parent)
        self.resizable(False, False)
        self.grab_set()

        frame = ttk.Frame(self, padding=PAD * 2)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        self.fields: Dict[str, tk.StringVar] = {}
        rows = (
            ("Canvas API token", CANVAS_CONFIG, "CANVAS_API_TOKEN"),
            ("Panopto client ID", PANOPTO_CONFIG, "Client_ID"),
            ("Panopto client secret", PANOPTO_CONFIG, "Client_Secret"),
        )

        for index, (label, path, key) in enumerate(rows):
            ttk.Label(frame, text=f"{label}:").grid(
                row=index, column=0, sticky="w", pady=4, padx=(0, PAD)
            )
            variable = tk.StringVar(value=readConfigValue(path, key))
            entry = ttk.Entry(frame, textvariable=variable, width=52, show="*")
            entry.grid(row=index, column=1, sticky="ew", pady=4)
            self.fields[key] = variable

        self.reveal = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame,
            text="Show values",
            variable=self.reveal,
            command=lambda: self._toggleReveal(frame),
        ).grid(row=len(rows), column=1, sticky="w", pady=(4, PAD))

        ttk.Label(
            frame,
            text=(
                "Credentials are stored in the config/ folder. Environment variables\n"
                "CANVAS_API_TOKEN, PANOPTO_CLIENT_ID and PANOPTO_CLIENT_SECRET\n"
                "override these values when set."
            ),
            style="Sub.TLabel",
            justify="left",
        ).grid(row=len(rows) + 1, column=0, columnspan=2, sticky="w", pady=(0, PAD))

        buttons = ttk.Frame(frame)
        buttons.grid(row=len(rows) + 2, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="Save", command=self._save).pack(side="left", padx=4)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left")

    def _toggleReveal(self, frame: ttk.Frame) -> None:
        show = "" if self.reveal.get() else "*"
        for child in frame.winfo_children():
            if isinstance(child, ttk.Entry):
                child.configure(show=show)

    def _save(self) -> None:
        targets = (
            (CANVAS_CONFIG, "CANVAS_API_TOKEN"),
            (PANOPTO_CONFIG, "Client_ID"),
            (PANOPTO_CONFIG, "Client_Secret"),
        )

        for path, key in targets:
            error = writeConfigValue(path, key, self.fields[key].get().strip())
            if error:
                messagebox.showerror(
                    "Settings", f"Could not save {key}:\n{error}", parent=self
                )
                return

        messagebox.showinfo(
            "Settings",
            "Saved. Restart the application for the new credentials to take effect.",
            parent=self,
        )
        self.destroy()


def performMacBlankWindowNudge(root: "tk.Tk") -> None:
    """Force a redraw to work around a known Tcl/Tk-on-macOS rendering bug.

    Tcl/Tk builds older than 8.6.13 render windows entirely blank on macOS
    Big Sur (11) and later until the window is resized or moved - the window
    frame appears but every widget inside it is invisible. This affects any
    Tkinter app, not just this one, and is not something the app's own
    layout code can avoid; the real fix is upgrading to a Python build with
    a newer bundled Tcl/Tk (see the README). Nudging the window's height by a
    pixel and back forces the affected Tk builds to repaint, which resolves
    the symptom without requiring the user to manually resize the window.
    """

    try:
        root.update_idletasks()
        width, height = root.winfo_width(), root.winfo_height()
    except tk.TclError:
        return

    if width <= 1 or height <= 1:
        return  # window is not mapped yet; nothing to nudge

    def _grow() -> None:
        try:
            root.geometry(f"{width}x{height + 1}")
        except tk.TclError:
            return
        root.after(60, _shrink)

    def _shrink() -> None:
        try:
            root.geometry(f"{width}x{height}")
        except tk.TclError:
            pass

    _grow()


def main() -> None:
    ensureDataDirs()
    root = tk.Tk()
    AuditApp(root)

    if sys.platform == "darwin":
        # Give the window manager a moment to actually map the window before
        # measuring and nudging it.
        root.after(150, lambda: performMacBlankWindowNudge(root))

    root.mainloop()


if __name__ == "__main__":
    main()
