#!/usr/bin/env python3
"""Post a test summary and an AI-assisted code review to GitHub.

Runs inside a GitHub Actions workflow after the test job. It:

1. Reads the test job's outcome from ``TEST_*`` environment variables and
   formats it as a short summary.
2. Collects the diff for the current event (a pull request or a push) and, if
   ``GEMINI_API_KEY`` is set, asks Gemini for a short review of it.
3. Posts the combined summary as a comment. On a pull request event it comments
   directly on that PR; on a push it looks up the open PR associated with the
   pushed commit and comments there, editing its own previous comment instead
   of piling up new ones; if no open PR exists (e.g. a push straight to the
   default branch) it falls back to a commit comment.

Every network call is isolated behind a small function so the formatting and
targeting logic can be unit tested without hitting GitHub or Gemini.
"""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path
from typing import Any, Dict, Optional

import requests

GITHUB_API = "https://api.github.com"

# Marks a comment as ours so a later run edits it instead of adding a new one.
COMMENT_MARKER = "<!-- ai-code-review-bot: do not edit below this line -->"


# ----------------------------------------------------------------------
# git / event plumbing
def _run_git_command(*args: str) -> str:
    """Run a git command and return its stdout."""

    result = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed with code {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout


def _load_event_payload() -> Optional[dict]:
    """Load the GitHub event payload if it exists."""

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return None

    path = Path(event_path)
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except json.JSONDecodeError:
            return None


_ZERO_SHA = "0" * 40


def _determine_base_and_head(payload: Optional[dict]) -> tuple[Optional[str], str]:
    """Determine the base and head SHAs for the current run."""

    head = os.environ.get("GITHUB_SHA") or "HEAD"
    base = None
    event_name = os.environ.get("GITHUB_EVENT_NAME")

    if payload:
        if event_name == "pull_request":
            pull_request = payload.get("pull_request", {})
            base = pull_request.get("base", {}).get("sha")
            head = pull_request.get("head", {}).get("sha", head)
        elif event_name == "push":
            base = payload.get("before")
            head = payload.get("after", head)

    if base == _ZERO_SHA:
        # A branch was just created; there is no real "before" commit.
        base = None

    if not base:
        # Fall back to the previous commit if the base SHA is not available.
        try:
            base = _run_git_command("rev-parse", f"{head}^1").strip()
        except RuntimeError:
            base = None

    return base, head


def _collect_diff(base: Optional[str], head: str) -> str:
    """Collect the diff between ``base`` and ``head``, truncated if huge."""

    diff_args = ["diff"]
    if base:
        diff_args.extend([base, head])
    else:
        empty_tree = _run_git_command("hash-object", "-t", "tree", "/dev/null").strip()
        diff_args.extend([empty_tree, head])

    diff = _run_git_command(*diff_args).strip()

    max_chars = int(os.environ.get("AI_REVIEW_MAX_DIFF_CHARS", "12000"))
    if len(diff) > max_chars:
        truncated_marker = textwrap.dedent(
            f"""
            Diff truncated to the first {max_chars} characters to keep the prompt a
            manageable size.
            """
        ).strip()
        diff = diff[:max_chars] + "\n" + truncated_marker

    return diff


def _build_prompt(diff: str, repo: str, event_name: str) -> str:
    """Construct the prompt for the AI model."""

    instructions = textwrap.dedent(
        f"""
        You are an experienced software engineer performing a code review. Review the
        provided git diff from {repo or 'this repository'} ({event_name or 'workflow run'} event) and provide:
        1. High-level summary of the changes.
        2. Potential bugs, regressions, or logical issues.
        3. Suggestions for improvement or missing tests.

        Respond using markdown with clear section headings. If there are no issues
        for a section, explicitly state that none were found. Keep it concise.
        """
    ).strip()

    return f"{instructions}\n\nHere is the diff to review:\n\n```diff\n{diff}\n```"


# ----------------------------------------------------------------------
# test summary formatting
def _int_or_none(value: Optional[str]) -> Optional[int]:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _parse_test_summary_from_env() -> Dict[str, Any]:
    """Read the test job's outcome and counts from ``TEST_*`` env vars."""

    return {
        "outcome": os.environ.get("TEST_OUTCOME") or "unknown",
        "total": _int_or_none(os.environ.get("TEST_TOTAL")),
        "failed": _int_or_none(os.environ.get("TEST_FAILED")),
        "errors": _int_or_none(os.environ.get("TEST_ERRORS")),
        "skipped": _int_or_none(os.environ.get("TEST_SKIPPED")),
    }


_OUTCOME_ICONS = {"success": "✅", "failure": "❌", "cancelled": "⚪", "error": "⚠️"}


def _format_test_summary(summary: Dict[str, Any]) -> str:
    """Render the test summary dict as one markdown line."""

    outcome = summary.get("outcome", "unknown")
    icon = _OUTCOME_ICONS.get(outcome, "⚠️")

    details = []
    for label, key in (("total", "total"), ("failed", "failed"), ("errors", "errors"), ("skipped", "skipped")):
        value = summary.get(key)
        if value is not None:
            details.append(f"{value} {label}")

    header = f"{icon} **Tests: {outcome}**"
    if details:
        header += " (" + ", ".join(details) + ")"

    return header


# ----------------------------------------------------------------------
# AI review
def _load_genai() -> Any:
    """Import ``google.generativeai`` lazily so this module stays importable
    (and testable) even when the package is not installed."""

    import google.generativeai as genai  # type: ignore

    return genai


def _generate_ai_review(diff: str, repo: str, event_name: str) -> Optional[str]:
    """Ask Gemini to review ``diff``. Returns ``None`` on any failure."""

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY is not set; skipping AI review.")
        return None

    try:
        genai = _load_genai()
    except Exception as exc:  # pragma: no cover - import guard
        print(f"Warning: google-generativeai is not available: {exc}")
        return None

    model_name = os.environ.get("AI_REVIEW_MODEL", "gemini-2.5-flash")
    prompt = _build_prompt(diff, repo, event_name)

    genai.configure(api_key=api_key)

    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    try:
        model = genai.GenerativeModel(model_name, safety_settings=safety_settings)
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.2,
                max_output_tokens=800,
            ),
        )
    except Exception as exc:  # pragma: no cover - external API call
        print(f"Warning: failed to generate AI review: {exc}")
        return None

    if not response.candidates or not response.candidates[0].content.parts:
        finish_reason = response.candidates[0].finish_reason if response.candidates else "UNKNOWN"
        print(f"Warning: Gemini did not return valid content. Finish reason: {finish_reason}")
        return None

    return response.text.strip()


# ----------------------------------------------------------------------
# comment body
def _run_url() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if repo and run_id:
        return f"https://github.com/{repo}/actions/runs/{run_id}"
    return "unknown"


def _build_comment_body(
    test_summary_md: str, ai_review_text: Optional[str], diff_present: bool
) -> str:
    """Assemble the full comment body, including the identifying marker."""

    parts = [COMMENT_MARKER, "## 🤖 Automated review", "", test_summary_md, ""]

    if ai_review_text:
        parts.append("### AI code review")
        parts.append("")
        parts.append(ai_review_text)
    elif diff_present:
        parts.append(
            "_AI review unavailable for this run (no API key configured or the "
            "request failed). See the workflow logs for details._"
        )
    else:
        parts.append("_No code changes to review._")

    parts.append("")
    sha = os.environ.get("GITHUB_SHA", "")[:12]
    parts.append(f"<sub>Commit: `{sha}` • Workflow run: {_run_url()}</sub>")

    return "\n".join(parts)


def _write_summary(body: str) -> None:
    """Append the comment body to the GitHub Actions step summary if possible."""

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return

    with Path(summary_file).open("a", encoding="utf-8") as handle:
        handle.write(body)
        handle.write("\n")


# ----------------------------------------------------------------------
# GitHub API
def _api_headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _find_open_pr_for_commit(repo: str, token: str, sha: str) -> Optional[int]:
    """Return the number of an open PR containing ``sha``, if any."""

    url = f"{GITHUB_API}/repos/{repo}/commits/{sha}/pulls"
    try:
        response = requests.get(url, headers=_api_headers(token), timeout=15)
    except requests.RequestException as exc:
        print(f"Warning: could not look up PRs for commit {sha}: {exc}")
        return None

    if response.status_code != 200:
        return None

    for pr in response.json():
        if pr.get("state") == "open":
            return pr.get("number")

    return None


def _find_existing_bot_comment(repo: str, token: str, pr_number: int) -> Optional[int]:
    """Return the id of our previous comment on ``pr_number``, if any."""

    url = f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments"
    try:
        response = requests.get(
            url, headers=_api_headers(token), params={"per_page": 100}, timeout=15
        )
    except requests.RequestException:
        return None

    if response.status_code != 200:
        return None

    for comment in reversed(response.json()):
        if COMMENT_MARKER in (comment.get("body") or ""):
            return comment.get("id")

    return None


def _upsert_pr_comment(repo: str, token: str, pr_number: int, body: str) -> None:
    """Create our comment on ``pr_number``, or edit our existing one."""

    headers = _api_headers(token)
    existing_id = _find_existing_bot_comment(repo, token, pr_number)

    if existing_id is not None:
        url = f"{GITHUB_API}/repos/{repo}/issues/comments/{existing_id}"
        response = requests.patch(url, headers=headers, json={"body": body}, timeout=15)
    else:
        url = f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments"
        response = requests.post(url, headers=headers, json={"body": body}, timeout=15)

    if response.status_code >= 300:
        raise RuntimeError(
            f"GitHub API returned {response.status_code} posting to PR #{pr_number}: "
            f"{response.text[:200]}"
        )


def _create_commit_comment(repo: str, token: str, sha: str, body: str) -> None:
    """Post a comment on commit ``sha`` (used when no open PR is found)."""

    url = f"{GITHUB_API}/repos/{repo}/commits/{sha}/comments"
    response = requests.post(url, headers=_api_headers(token), json={"body": body}, timeout=15)

    if response.status_code >= 300:
        raise RuntimeError(
            f"GitHub API returned {response.status_code} commenting on {sha}: "
            f"{response.text[:200]}"
        )


# ----------------------------------------------------------------------
def main() -> None:
    payload = _load_event_payload()
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")

    base, head = _determine_base_and_head(payload)
    diff = _collect_diff(base, head)

    test_summary_md = _format_test_summary(_parse_test_summary_from_env())

    ai_review_text = None
    if diff:
        ai_review_text = _generate_ai_review(diff, repo, event_name)
    else:
        print("No changes detected in the diff.")

    body = _build_comment_body(test_summary_md, ai_review_text, bool(diff))

    print(body)
    _write_summary(body)

    if not token or not repo:
        print("GITHUB_TOKEN or GITHUB_REPOSITORY not set; skipping comment posting.")
        return

    pr_number = None
    if event_name == "pull_request" and payload:
        pr_number = payload.get("pull_request", {}).get("number")

    if pr_number is None and head and head != "HEAD":
        pr_number = _find_open_pr_for_commit(repo, token, head)

    try:
        if pr_number is not None:
            _upsert_pr_comment(repo, token, pr_number, body)
            print(f"Posted/updated comment on PR #{pr_number}.")
        elif head and head != "HEAD":
            _create_commit_comment(repo, token, head, body)
            print(f"Posted commit comment on {head}.")
        else:
            print("No PR or commit target resolved; nothing posted.")
    except Exception as exc:
        # Posting is best-effort: a permissions issue (e.g. a fork PR without
        # secrets) should not fail the whole workflow.
        print(f"Warning: failed to post comment to GitHub: {exc}")


if __name__ == "__main__":
    main()
