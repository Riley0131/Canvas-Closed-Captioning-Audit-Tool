# Closed Captioning Audit

A toolkit for auditing Canvas courses to confirm that instructional videos provide accessible captioning. The project bundles a command-line workflow and a desktop GUI that automate pulling course content from the Canvas API, checking caption availability on supported video platforms, **identifying whether captions were machine generated or human edited**, and writing consolidated results for accessibility reviewers.

## Repository structure

| Path | Purpose |
| --- | --- |
| `runAudit.py` | Orchestrates a full audit and prints a summary. Shares one browser session and one buffered results writer across all stages. |
| `individualAudit.py` | Audits a single Canvas course through the same pipeline. |
| `pullModules.py` | Fetches courses via the Canvas API, downloads module contents concurrently, and classifies links by platform. |
| `youtubeVideo.py` | Checks YouTube captions and reports whether the track is auto-generated or owner-provided. |
| `panoptoVideo.py` | Checks Panopto recordings via the REST API, the viewer endpoints, and finally the player DOM. |
| `panoptoCaptions.py` | Classifies caption text and metadata as auto-generated or human edited. Pure logic, fully unit tested. |
| `sortEmbeddedVideos.py` | Inspects Canvas-hosted media pages for a caption control. |
| `auditCore.py` | Shared data folder layout, JSON helpers, buffered result writer, summary aggregation, cancellation flag. |
| `browser.py` | One shared Chrome session and login prompt for every browser-backed stage. |
| `configStore.py` | Reads and writes the credential files under `config/`. |
| `gui.py` | Thin pywebview window: creates the window, translates its API's progress callbacks into JavaScript. |
| `gui_api.py` | The GUI's actual logic (run an audit, load results, save settings) - toolkit-independent and unit tested on its own. |
| `webui/` | The GUI's page: `index.html`, `style.css`, `app.js`. |
| `dataReset.py` | Clears cached JSON results inside the `data/` directory tree. |
| `config/` | User tokens (`canvasAPI.py`, `panoptoKey.py`) and the displayed app version (`version.py`). |
| `tests/` | Unit tests for every module above plus the CI scripts themselves — no network, browser, display or credentials needed. |
| `ci/ai_review.py` | Posts a test-summary + AI-review comment on PRs and pushes. |
| `ci/summarize_tests.py` | Turns the pytest JUnit report into GitHub Actions job outputs. |
| `.github/workflows/ci.yml` | Runs the test suite, then the comment-posting job, on every push and PR. |
| `requirements.txt` | Python dependencies required by the scripts and GUI. |
| `versionNotes` | High-level changelog for historical releases. |

The `data/` folder and its `courseModules/` and `sortedModules/` subdirectories are created automatically on first run.

## Prerequisites

* **Python**: 3.10 or newer.
* **Pip packages**: `pip install -r requirements.txt` (`requests`, `selenium`, `webdriver-manager`, `youtube-transcript-api`, `pywebview`).
* **Google Chrome**: Selenium downloads a matching ChromeDriver via `webdriver-manager`, so Chrome must be installed. Set `CHROMEDRIVER_PATH` to use a driver you manage yourself.
* **Canvas access**: the auditing account must be enrolled in the target courses. The browser-backed stages require an interactive login once per run.
* **Linux only**: the GUI needs a GTK + WebKitGTK runtime available as a system package (package name varies by distro and release - see the [pywebview install docs](https://pywebview.flowrl.com/guide/installation.html) if `python gui.py` reports a missing GTK/WebKit dependency). macOS and Windows use their OS's built-in web engine, nothing extra to install there.

## Initial setup

1. **Clone the repository** and change into it.
2. **Create a virtual environment (optional but recommended)**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Windows: venv\Scripts\activate
   ```
3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
4. **Configure credentials** — either through the GUI's **Settings** dialog, by editing the files under `config/`, or with environment variables (which take precedence):

   | Setting | Config file | Environment variable |
   | --- | --- | --- |
   | Canvas API token | `config/canvasAPI.py` → `CANVAS_API_TOKEN` | `CANVAS_API_TOKEN` |
   | Panopto client ID | `config/panoptoKey.py` → `Client_ID` | `PANOPTO_CLIENT_ID` |
   | Panopto client secret | `config/panoptoKey.py` → `Client_Secret` | `PANOPTO_CLIENT_SECRET` |
   | Canvas instance | — | `CANVAS_BASE_URL` (default `https://canvas.uccs.edu/api/v1`) |

   Panopto credentials are optional. Without them the audit falls back to the viewer endpoints and the player UI, which still detects captions but less often identifies their source.

## Running audits

### Full audit (CLI)
```bash
python runAudit.py                 # every course the token can see
python runAudit.py --course 12345  # one or more specific courses
python runAudit.py --skip-browser  # YouTube only, no Chrome required
python runAudit.py --headless      # hide Chrome (only works without an interactive login)
```
A run writes `data/courses.json`, `data/courses_ids.json`, `data/courseModules/modules_<id>.json`, `data/sortedModules/sorted_modules_<id>.json` and `data/audited_videos.json`, then prints a summary.

### Graphical interface
```bash
python gui.py
```
The window is a local web page rendered by the OS's own web engine (WKWebView on macOS, WebView2 on Windows, WebKitGTK on Linux) via [pywebview](https://pywebview.flowrl.com/) - not Tkinter. See [Why pywebview instead of Tkinter](#why-pywebview-instead-of-tkinter) below for why.

* **Run Complete Audit** / **Audit One Course...** run in the background; the window stays responsive and streams progress into the activity log.
* **Stop** ends the run after the current video, keeping everything recorded so far.
* **View Results** opens a sortable, filterable table with a summary and per-video caption source. Rows without captions are highlighted red, auto-generated captions amber. Click a row to open the video.
* **Settings** edits the Canvas and Panopto credentials.
* **Reset Data** clears cached JSON after a confirmation prompt.

### Individual course audit
```bash
python individualAudit.py <course_id>
```

### Resetting cached data
```bash
python dataReset.py
```

### Running the tests
```bash
pip install pytest      # or: python -m unittest discover -s tests
pytest tests -v
```
250+ tests across 16 files cover the caption classifier, URL handling, the
Canvas/Panopto/YouTube API plumbing (network calls mocked), the GUI's logic
(`gui_api.py`, with no GUI toolkit involved at all - see
[Why pywebview instead of Tkinter](#why-pywebview-instead-of-tkinter)), and
the CI scripts themselves. The suite needs no network access, no browser, no
display and no credentials.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and every pull request:

1. **`test`** installs dependencies and runs the full suite above, failing the
   build if anything fails.
2. **`ai-review`** always runs afterward and posts one comment with the test
   pass/fail summary plus an AI-generated review of the diff (Gemini, if
   `GEMINI_API_KEY` is configured — optional). It comments on the pull request
   for a `pull_request` event, on the open PR containing a pushed commit for a
   `push` event, or on the commit itself if that commit isn't on any open PR.
   A later push to the same PR edits the existing comment instead of piling up
   new ones.

See `AI_REVIEW_SETUP.md` for the setup steps and troubleshooting.

## Caption source detection

Beyond *does this video have captions*, the audit reports **where those captions came from**:

| `caption_kind` | Meaning |
| --- | --- |
| `auto_generated` | Machine/ASR output that nobody has corrected. |
| `human_edited` | Uploaded, ordered, or corrected by a person. |
| `unknown` | Captions exist but the source could not be established. |
| `none` | No captions found. |

### How Panopto sessions are classified

`panoptoVideo.py` tries the cheapest source first and stops as soon as one returns a high-confidence answer:

1. **Panopto REST API** — `/Panopto/api/v1/sessions/{id}/captions` and the session record, using an OAuth client-credentials token (requires the Panopto credentials).
2. **Viewer `DeliveryInfo.aspx`** — requested from inside the authenticated browser session, so it works with only a normal Panopto login.
3. **Generated SRT** — `GenerateSRT.ashx`, also through the browser session.
4. **Player DOM** — proves only that a caption control exists, so it yields `unknown` rather than a source.

`panoptoCaptions.py` then scores whatever came back. Because caption payload field names differ between Panopto releases, it does not depend on one key: it walks the whole payload looking for **metadata signals** (any field that looks like a machine/ASR flag, an upload/import flag, an "edited" flag, or a caption-source string) and falls back to **text signals** when the metadata says nothing:

* speaker labels (`>>`), non-speech cues (`[MUSIC]`, `♪`) and transcription markers (`[inaudible]`) indicate a human wrote or corrected the file — speech recognition does not emit them;
* text with no capital letters or no sentence punctuation indicates raw ASR output.

Metadata outranks text. When a metadata flag says *machine generated* but the text looks edited, the verdict follows the metadata and records a note — that combination usually means ASR captions someone later corrected.

YouTube results use YouTube's own `is_generated` flag, which is authoritative. Canvas Studio exposes no caption-source information, so Canvas entries report `unknown` rather than guessing.

## Working with the results

`data/audited_videos.json` is a list of dictionaries:

```json
{
  "type": "youtube" | "Canvas" | "panopto",
  "url": "https://…",
  "has_captions": true,
  "caption_kind": "auto_generated",
  "caption_confidence": "high" | "medium" | "low",
  "caption_language": "English",
  "caption_signals": ["metadata:Delivery.MachineTranscription=True (machine generated flag)"],
  "caption_detection_method": "panopto_api",
  "session_id": "…",
  "course_id": "12345"
}
```

`caption_signals` records exactly which evidence produced the verdict, so a reviewer can check a borderline call instead of taking it on trust. `course_id` is present for individual-course audits. Entries written by older versions only have `type`, `url` and `has_captions`; the GUI treats those as `unknown`.

## Performance notes

The audit used to spend most of its time waiting. The current version:

* reuses one pooled `requests.Session` per Canvas host, with retry and backoff on 429/5xx;
* fetches a course's module pages concurrently (`CANVAS_MAX_WORKERS`, default 8);
* resolves the extra module-item lookup only for external tools, the only item type that can hide a Panopto launch;
* reads video links from the cached `sortedModules/` files instead of re-querying Canvas for every stage;
* shares one Chrome instance and one login across the Panopto and Canvas stages;
* detects Panopto caption controls with a single injected script instead of transferring thousands of elements and reading nine attributes from each;
* buffers writes to `audited_videos.json` instead of rewriting the whole file after every video;
* skips the fixed five-second pause before every YouTube lookup, backing off only after a failure (`YOUTUBE_BACKOFF`).

Set `AUDIT_VERBOSE=1` for per-URL debug output.

## Maintaining video platform support

Link discovery and caption checking are separate, so adding a platform is mechanical:

1. **Classify the URLs**: extend `sortUrls()` in `pullModules.py` with a new bucket key.
2. **Implement a checker**: follow `youtubeVideo.py` — read links from the sorted JSON, and write entries through the shared `ResultWriter` using the `caption_kind` vocabulary above.
3. **Wire it into orchestration**: call it from `runPipeline()` in `runAudit.py`, passing the shared `ResultWriter` (and the shared `BrowserSession` if it needs a browser).
4. **Document credentials**: add a config file under `config/` and an environment-variable override.

To remove a platform, drop its branch from `sortUrls()` and its call from `runPipeline()`.

## Security considerations

* Treat `config/canvasAPI.py` and `config/panoptoKey.py` as secrets. Prefer the environment variables listed above on shared machines.
* **`config/panoptoKey.py` is currently tracked in git and contains a real client secret.** Rotate that Panopto client secret in the Panopto admin console, then stop tracking the file:
  ```bash
  git rm --cached config/panoptoKey.py
  echo "config/panoptoKey.py" >> .gitignore
  ```
  Rotation is required regardless: the old value stays in the repository's history.
* Rotate the Canvas token regularly and invalidate it immediately if leakage is suspected.
* Store audit outputs on secured drives — course rosters and titles may be sensitive.

## Troubleshooting & tips

* **Invalid or expired tokens**: Canvas requests fail with authorization errors. Generate a new token and re-run.
* **Chrome will not start**: make sure Chrome is installed and current, or point `CHROMEDRIVER_PATH` at a driver you manage.
* **Headless mode finds nothing**: the Panopto and Canvas stages need a logged-in session. Headless mode suppresses the login prompt, so use it only where authentication is already handled.
* **Panopto captions report `unknown`**: without OAuth credentials the audit can often only see that a caption control exists. Add the Panopto client ID and secret for authoritative answers.
* **Rate limits**: lower `CANVAS_MAX_WORKERS` or raise `YOUTUBE_BACKOFF` if throttling responses appear.
* **`python gui.py` exits immediately with "pywebview is not installed"**: run `pip install -r requirements.txt`. On macOS, if it specifically mentions Cocoa/WebKit/PyObjC, run `pip install pyobjc` (this normally installs automatically as part of `pywebview`, but a stale or user-scoped Python environment can miss it).
* **GUI window opens but the page is blank or never loads**: this means `webui/index.html` couldn't be found or loaded, not a rendering bug - `pywebview` uses the OS's own browser engine, which doesn't have the class of blank-window bugs Tkinter did. Check the terminal output for a `evaluate_js`/`JavascriptException` or a 404 for `style.css`/`app.js`; running `python gui.py` from the repository root (not the packaged executable) is the fastest way to rule out a packaging path issue.
* **Linux: `python gui.py` fails with a GTK or WebKit import error**: install your distro's GTK 3 + WebKit2GTK development/runtime packages - see [pywebview's install docs](https://pywebview.flowrl.com/guide/installation.html) for the current package names, which vary by distro and release.
* **The packaged `CC-Auditor` executable shows a blank window or can't find `webui/`**: rebuild it - the `.spec` file bundles `webui/` explicitly and `gui.py` resolves it via `sys._MEIPASS` when frozen, but only if the executable was built with the current `.spec`. An executable built before this change won't have `webui/` bundled at all.

## Why pywebview instead of Tkinter

Earlier versions of this GUI used Tkinter. On macOS, Tkinter's Tcl/Tk backend has a long-standing bug (unrelated to this app - any Tkinter program is affected) where windows render entirely blank on macOS Big Sur (11) and later when the bundled Tcl/Tk is older than 8.6.13, which is what most Python installs still ship. Font names and ttk themes also don't carry across platforms the way Tkinter's docs suggest, which caused a second, smaller set of rendering issues on macOS.

Switching to [pywebview](https://pywebview.flowrl.com/) sidesteps both problems: the window is a small local web page (`webui/`) rendered by the operating system's own browser engine - WKWebView on macOS, WebView2 on Windows, WebKitGTK on Linux - the same rendering code each platform's real browser uses, not a separate UI toolkit bundled with Python. Layout and styling are ordinary HTML/CSS, so there's no cross-platform font/theme guessing game either.

The GUI's actual behavior lives in `gui_api.py`, which has no dependency on `webview` at all - it's a plain Python class that reports progress through a `notify(event, payload)` callback. `gui.py` is a thin adapter that creates the pywebview window and turns those callbacks into JavaScript calls. This split is what makes `gui_api.py` fully unit tested (`tests/test_guiApi.py`) without a display, browser engine, or GUI toolkit of any kind.

## Additional resources

* Canvas API documentation: <https://canvas.instructure.com/doc/api/>
* YouTube Transcript API documentation: <https://pypi.org/project/youtube-transcript-api/>
