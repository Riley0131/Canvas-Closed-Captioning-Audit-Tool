# CI Pipeline Setup

The repository runs one workflow, `.github/workflows/ci.yml`, on every pull
request and every push to any branch. It has two jobs:

1. **`test`** — installs `requirements.txt` and `pytest`, then runs the full
   suite under `tests/` (`pytest tests -v --junitxml=test-results.xml`). The
   job fails if any test fails. A step afterwards (`ci/summarize_tests.py`)
   always runs — even when tests failed — and parses the JUnit XML into
   `total` / `failed` / `errors` / `skipped` counts plus a pass/fail
   `outcome`, exposed as job outputs for the next job to read. The XML report
   is uploaded as a workflow artifact.

2. **`ai-review`** — runs regardless of whether `test` passed
   (`if: always()`), and posts one comment combining the test summary with an
   AI-generated review of the diff (`ci/ai_review.py`):
   - **Pull request event** → comments directly on that PR.
   - **Push event** → looks up the open PR containing the pushed commit (via
     `GET /repos/{repo}/commits/{sha}/pulls`) and comments there.
   - **Push with no open PR** (e.g. a direct push to the default branch) →
     falls back to a comment on the commit itself.

   Every comment carries a hidden marker. A later run on the same PR edits
   that comment in place instead of adding a new one each time, so a PR that
   gets pushed to five times ends up with one evolving comment, not five.

## What posts even without an AI key

The AI review step (Gemini) is optional. If `GEMINI_API_KEY` is not
configured, or the request fails for any reason, the comment still posts —
it just says the AI review is unavailable and shows the test summary on its
own. Nothing in the pipeline hard-fails because a key is missing.

## Prerequisites

- Repository admin access to configure secrets.
- (Optional) A Google account for a Gemini API key.

## Setup

### 1. Get a free Gemini API key (optional, for the AI review section)

1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Sign in and click **Get API Key** → **Create API key in new project**.
3. Copy the key.

Free tier: 15 requests/minute, 1,500/day, no credit card required.

### 2. Add it as a repository secret

**Settings → Secrets and variables → Actions → New repository secret**
- Name: `GEMINI_API_KEY`
- Secret: the key from step 1

### 3. Confirm workflow permissions

**Settings → Actions → General → Workflow permissions**: select **Read and
write permissions**. The workflow needs this to post PR and commit comments
(it declares `contents: write`, `pull-requests: write`, `issues: write` in
`ci.yml`, but the org/repo default can still block it).

### 4. Verify the files are in place

- `.github/workflows/ci.yml` — the workflow.
- `ci/ai_review.py` — test summary + AI review + comment posting.
- `ci/summarize_tests.py` — JUnit XML → job outputs.
- `ci/requirements.txt` — dependencies for the `ai-review` job
  (`google-generativeai`, `requests`).
- `tests/` — the suite the `test` job runs.

### 5. Test it

```bash
git checkout -b test-ci
echo "# test" >> readme.md
git add readme.md
git commit -m "Test the CI pipeline"
git push -u origin test-ci
```

Open a PR from that branch and check the **Actions** tab: `test` should run
the suite, then `ai-review` should post a comment on the PR with the test
summary and (if configured) an AI review of the diff. Push another commit to
the same PR and confirm the same comment updates instead of a new one
appearing.

## Optional configuration

| Variable | Where | Effect |
| --- | --- | --- |
| `AI_REVIEW_MODEL` | repo variable | Gemini model to use (default `gemini-2.5-flash`) |
| `AI_REVIEW_MAX_DIFF_CHARS` | repo variable | Max diff size sent to the model (default `12000`) |

Repo variables live under **Settings → Secrets and variables → Actions →
Variables**.

To change which branches a push triggers the workflow on, edit the `push:`
block in `.github/workflows/ci.yml` (currently every branch, `'**'`).

## Troubleshooting

- **No comment appears on a fork PR** — forked pull requests run with a
  read-only `GITHUB_TOKEN` by default, so posting fails. The workflow logs the
  failure but does not fail the build (`ci/ai_review.py` catches posting
  errors and continues); the test summary still shows up in the workflow's
  step summary either way.
- **"GEMINI_API_KEY is not set"** — this is an informational log line, not an
  error; the comment still posts without the AI section. Add the secret if
  you want the AI section.
- **Comment posts to the wrong place / not at all on a push** — the lookup
  uses the commit's associated open PRs; a push whose commit isn't on any
  open PR (e.g. it merged already, or went straight to the default branch)
  is expected to fall back to a commit comment.
- **Test job fails but you don't see why in the comment** — the comment only
  carries counts, not the full failure output; check the `test` job's own
  logs or download the `test-results` artifact.

## Running the tests locally

```bash
pip install -r requirements.txt pytest
pytest tests -v
```

No network access, browser, or credentials are required — every external
call (Canvas, Panopto, YouTube, Selenium, the GitHub API, Gemini) is mocked
in the test suite.
