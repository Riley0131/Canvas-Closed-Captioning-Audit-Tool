"""Tests for ci/ai_review.py's pure logic and GitHub API plumbing.

Every network call (git subprocess, GitHub REST, the Gemini SDK) is
monkeypatched, so this suite needs no network access, no repo history beyond
this checkout, and no google-generativeai installation.
"""

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CI_DIR = os.path.join(ROOT, "ci")
sys.path.insert(0, ROOT)

spec = importlib.util.spec_from_file_location("ai_review", os.path.join(CI_DIR, "ai_review.py"))
ai_review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ai_review)  # type: ignore[union-attr]


def _clearEnv(*names):
    for name in names:
        os.environ.pop(name, None)


class BaseHeadResolutionTests(unittest.TestCase):
    def tearDown(self):
        _clearEnv("GITHUB_SHA", "GITHUB_EVENT_NAME")

    def test_pullRequestPayloadUsesBaseAndHeadShas(self):
        os.environ["GITHUB_EVENT_NAME"] = "pull_request"
        payload = {"pull_request": {"base": {"sha": "base123"}, "head": {"sha": "head456"}}}

        base, head = ai_review._determine_base_and_head(payload)

        self.assertEqual((base, head), ("base123", "head456"))

    def test_pushPayloadUsesBeforeAndAfter(self):
        os.environ["GITHUB_EVENT_NAME"] = "push"
        os.environ["GITHUB_SHA"] = "fallback"
        payload = {"before": "before123", "after": "after456"}

        base, head = ai_review._determine_base_and_head(payload)

        self.assertEqual((base, head), ("before123", "after456"))

    def test_zeroBeforeShaFallsBackToParentCommit(self):
        os.environ["GITHUB_EVENT_NAME"] = "push"
        payload = {"before": "0" * 40, "after": "after456"}

        with mock.patch.object(ai_review, "_run_git_command", return_value="parent789\n") as run:
            base, head = ai_review._determine_base_and_head(payload)

        run.assert_called_once_with("rev-parse", "after456^1")
        self.assertEqual((base, head), ("parent789", "after456"))

    def test_noPayloadFallsBackToGitParent(self):
        os.environ["GITHUB_SHA"] = "headsha"

        with mock.patch.object(ai_review, "_run_git_command", return_value="parentsha\n"):
            base, head = ai_review._determine_base_and_head(None)

        self.assertEqual((base, head), ("parentsha", "headsha"))

    def test_gitFailureLeavesBaseNone(self):
        os.environ["GITHUB_SHA"] = "headsha"

        with mock.patch.object(ai_review, "_run_git_command", side_effect=RuntimeError("boom")):
            base, head = ai_review._determine_base_and_head(None)

        self.assertIsNone(base)
        self.assertEqual(head, "headsha")


class CollectDiffTests(unittest.TestCase):
    def tearDown(self):
        _clearEnv("AI_REVIEW_MAX_DIFF_CHARS")

    def test_diffWithBaseUsesTwoDotDiff(self):
        with mock.patch.object(ai_review, "_run_git_command", return_value="diff --git a b\n") as run:
            result = ai_review._collect_diff("base", "head")

        run.assert_called_once_with("diff", "base", "head")
        self.assertEqual(result, "diff --git a b")

    def test_noBaseDiffsAgainstEmptyTree(self):
        calls = []

        def fake_git(*args):
            calls.append(args)
            if args[0] == "hash-object":
                return "4b825dc\n"
            return "the whole diff\n"

        with mock.patch.object(ai_review, "_run_git_command", side_effect=fake_git):
            result = ai_review._collect_diff(None, "head")

        self.assertIn(("diff", "4b825dc", "head"), calls)
        self.assertEqual(result, "the whole diff")

    def test_longDiffIsTruncated(self):
        os.environ["AI_REVIEW_MAX_DIFF_CHARS"] = "10"
        with mock.patch.object(ai_review, "_run_git_command", return_value="x" * 100):
            result = ai_review._collect_diff("base", "head")

        self.assertTrue(result.startswith("x" * 10))
        self.assertIn("truncated", result.lower())
        self.assertLess(len(result), 100)


class PromptBuildingTests(unittest.TestCase):
    def test_promptEmbedsDiffAndRepoName(self):
        prompt = ai_review._build_prompt("diff content", "owner/repo", "pull_request")

        self.assertIn("owner/repo", prompt)
        self.assertIn("pull_request", prompt)
        self.assertIn("diff content", prompt)
        self.assertIn("```diff", prompt)

    def test_missingRepoAndEventFallBackToGenericWording(self):
        prompt = ai_review._build_prompt("x", "", "")

        self.assertIn("this repository", prompt)
        self.assertIn("workflow run", prompt)


class TestSummaryFormattingTests(unittest.TestCase):
    def tearDown(self):
        _clearEnv("TEST_OUTCOME", "TEST_TOTAL", "TEST_FAILED", "TEST_ERRORS", "TEST_SKIPPED")

    def test_parseReadsAllFields(self):
        os.environ.update(
            TEST_OUTCOME="success", TEST_TOTAL="62", TEST_FAILED="0", TEST_ERRORS="0", TEST_SKIPPED="1"
        )

        summary = ai_review._parse_test_summary_from_env()

        self.assertEqual(
            summary, {"outcome": "success", "total": 62, "failed": 0, "errors": 0, "skipped": 1}
        )

    def test_parseHandlesMissingAndNonNumericValues(self):
        os.environ["TEST_TOTAL"] = "not-a-number"
        summary = ai_review._parse_test_summary_from_env()

        self.assertEqual(summary["outcome"], "unknown")
        self.assertIsNone(summary["total"])
        self.assertIsNone(summary["failed"])

    def test_formatSuccessIncludesIconAndCounts(self):
        text = ai_review._format_test_summary(
            {"outcome": "success", "total": 62, "failed": 0, "errors": 0, "skipped": 0}
        )

        self.assertIn("✅", text)
        self.assertIn("62 total", text)
        self.assertIn("Tests: success", text)

    def test_formatFailureUsesFailureIcon(self):
        text = ai_review._format_test_summary(
            {"outcome": "failure", "total": 10, "failed": 2, "errors": 1, "skipped": 0}
        )

        self.assertIn("❌", text)
        self.assertIn("2 failed", text)
        self.assertIn("1 errors", text)

    def test_formatUnknownOutcomeUsesWarningIcon(self):
        text = ai_review._format_test_summary({"outcome": "unknown"})
        self.assertIn("⚠️", text)

    def test_formatWithNoCountsOmitsParentheses(self):
        text = ai_review._format_test_summary({"outcome": "unknown"})
        self.assertNotIn("(", text)


class CommentBodyTests(unittest.TestCase):
    def tearDown(self):
        _clearEnv("GITHUB_SHA", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")

    def test_bodyIncludesMarkerAndAiReview(self):
        body = ai_review._build_comment_body("✅ **Tests: success**", "Looks fine.", True)

        self.assertTrue(body.startswith(ai_review.COMMENT_MARKER))
        self.assertIn("AI code review", body)
        self.assertIn("Looks fine.", body)

    def test_bodyWithoutAiReviewButDiffPresentExplainsWhy(self):
        body = ai_review._build_comment_body("✅ **Tests: success**", None, True)

        self.assertIn("AI review unavailable", body)
        self.assertNotIn("AI code review", body)

    def test_bodyWithNoDiffSaysSo(self):
        body = ai_review._build_comment_body("✅ **Tests: success**", None, False)

        self.assertIn("No code changes to review", body)

    def test_bodyIncludesShortCommitShaAndRunUrl(self):
        os.environ["GITHUB_SHA"] = "abcdef0123456789"
        os.environ["GITHUB_REPOSITORY"] = "owner/repo"
        os.environ["GITHUB_RUN_ID"] = "42"

        body = ai_review._build_comment_body("summary", None, False)

        self.assertIn("abcdef012345", body)
        self.assertIn("https://github.com/owner/repo/actions/runs/42", body)

    def test_runUrlIsUnknownWithoutEnv(self):
        self.assertEqual(ai_review._run_url(), "unknown")


class GenerateAiReviewTests(unittest.TestCase):
    def tearDown(self):
        _clearEnv("GEMINI_API_KEY", "AI_REVIEW_MODEL")

    def test_missingApiKeyReturnsNoneWithoutImportingGenai(self):
        _clearEnv("GEMINI_API_KEY")
        with mock.patch.object(ai_review, "_load_genai") as load:
            result = ai_review._generate_ai_review("diff", "repo", "push")

        load.assert_not_called()
        self.assertIsNone(result)

    def test_genaiImportFailureReturnsNoneGracefully(self):
        os.environ["GEMINI_API_KEY"] = "key"
        with mock.patch.object(ai_review, "_load_genai", side_effect=ImportError("nope")):
            result = ai_review._generate_ai_review("diff", "repo", "push")

        self.assertIsNone(result)

    def _fakeGenaiModule(self, response_text="Great work.", has_parts=True):
        fake_part = types.SimpleNamespace()
        fake_content = types.SimpleNamespace(parts=[fake_part] if has_parts else [])
        fake_candidate = types.SimpleNamespace(content=fake_content, finish_reason="STOP")
        fake_response = types.SimpleNamespace(
            candidates=[fake_candidate] if has_parts else [],
            text=response_text,
        )

        fake_model = mock.Mock()
        fake_model.generate_content.return_value = fake_response

        fake_module = types.SimpleNamespace(
            configure=mock.Mock(),
            GenerativeModel=mock.Mock(return_value=fake_model),
            types=types.SimpleNamespace(GenerationConfig=mock.Mock()),
        )
        return fake_module, fake_model

    def test_successfulReviewReturnsStrippedText(self):
        os.environ["GEMINI_API_KEY"] = "key"
        fake_module, fake_model = self._fakeGenaiModule(response_text="  Nice diff.  ")

        with mock.patch.object(ai_review, "_load_genai", return_value=fake_module):
            result = ai_review._generate_ai_review("diff", "repo", "push")

        self.assertEqual(result, "Nice diff.")
        fake_module.configure.assert_called_once_with(api_key="key")

    def test_customModelNameIsRespected(self):
        os.environ["GEMINI_API_KEY"] = "key"
        os.environ["AI_REVIEW_MODEL"] = "gemini-custom"
        fake_module, fake_model = self._fakeGenaiModule()

        with mock.patch.object(ai_review, "_load_genai", return_value=fake_module):
            ai_review._generate_ai_review("diff", "repo", "push")

        args, kwargs = fake_module.GenerativeModel.call_args
        self.assertEqual(args[0], "gemini-custom")

    def test_apiExceptionReturnsNone(self):
        os.environ["GEMINI_API_KEY"] = "key"
        fake_module, fake_model = self._fakeGenaiModule()
        fake_model.generate_content.side_effect = RuntimeError("rate limited")

        with mock.patch.object(ai_review, "_load_genai", return_value=fake_module):
            result = ai_review._generate_ai_review("diff", "repo", "push")

        self.assertIsNone(result)

    def test_emptyCandidatesReturnsNone(self):
        os.environ["GEMINI_API_KEY"] = "key"
        fake_module, _ = self._fakeGenaiModule(has_parts=False)

        with mock.patch.object(ai_review, "_load_genai", return_value=fake_module):
            result = ai_review._generate_ai_review("diff", "repo", "push")

        self.assertIsNone(result)


class GithubApiTests(unittest.TestCase):
    def _response(self, status_code=200, json_data=None, text=""):
        response = mock.Mock()
        response.status_code = status_code
        response.json.return_value = json_data if json_data is not None else []
        response.text = text
        return response

    def test_findOpenPrReturnsFirstOpenMatch(self):
        payload = [{"number": 1, "state": "closed"}, {"number": 2, "state": "open"}]
        with mock.patch.object(ai_review.requests, "get", return_value=self._response(200, payload)):
            result = ai_review._find_open_pr_for_commit("owner/repo", "tok", "sha")

        self.assertEqual(result, 2)

    def test_findOpenPrReturnsNoneWhenNoneOpen(self):
        payload = [{"number": 1, "state": "closed"}]
        with mock.patch.object(ai_review.requests, "get", return_value=self._response(200, payload)):
            result = ai_review._find_open_pr_for_commit("owner/repo", "tok", "sha")

        self.assertIsNone(result)

    def test_findOpenPrReturnsNoneOnErrorStatus(self):
        with mock.patch.object(ai_review.requests, "get", return_value=self._response(404)):
            result = ai_review._find_open_pr_for_commit("owner/repo", "tok", "sha")

        self.assertIsNone(result)

    def test_findOpenPrReturnsNoneOnNetworkError(self):
        with mock.patch.object(
            ai_review.requests, "get", side_effect=ai_review.requests.RequestException("down")
        ):
            result = ai_review._find_open_pr_for_commit("owner/repo", "tok", "sha")

        self.assertIsNone(result)

    def test_findExistingCommentMatchesMarker(self):
        payload = [
            {"id": 1, "body": "unrelated comment"},
            {"id": 2, "body": f"intro\n{ai_review.COMMENT_MARKER}\nrest"},
        ]
        with mock.patch.object(ai_review.requests, "get", return_value=self._response(200, payload)):
            result = ai_review._find_existing_bot_comment("owner/repo", "tok", 5)

        self.assertEqual(result, 2)

    def test_findExistingCommentReturnsNoneWhenNoMarker(self):
        payload = [{"id": 1, "body": "just a comment"}]
        with mock.patch.object(ai_review.requests, "get", return_value=self._response(200, payload)):
            result = ai_review._find_existing_bot_comment("owner/repo", "tok", 5)

        self.assertIsNone(result)

    def test_upsertCreatesWhenNoExistingComment(self):
        with mock.patch.object(ai_review, "_find_existing_bot_comment", return_value=None), \
             mock.patch.object(ai_review.requests, "post", return_value=self._response(201)) as post, \
             mock.patch.object(ai_review.requests, "patch") as patch:
            ai_review._upsert_pr_comment("owner/repo", "tok", 5, "body text")

        post.assert_called_once()
        patch.assert_not_called()
        self.assertIn("/issues/5/comments", post.call_args.args[0])

    def test_upsertEditsExistingComment(self):
        with mock.patch.object(ai_review, "_find_existing_bot_comment", return_value=99), \
             mock.patch.object(ai_review.requests, "patch", return_value=self._response(200)) as patch, \
             mock.patch.object(ai_review.requests, "post") as post:
            ai_review._upsert_pr_comment("owner/repo", "tok", 5, "body text")

        patch.assert_called_once()
        post.assert_not_called()
        self.assertIn("/issues/comments/99", patch.call_args.args[0])

    def test_upsertRaisesOnFailureStatus(self):
        with mock.patch.object(ai_review, "_find_existing_bot_comment", return_value=None), \
             mock.patch.object(ai_review.requests, "post", return_value=self._response(403, text="forbidden")):
            with self.assertRaises(RuntimeError):
                ai_review._upsert_pr_comment("owner/repo", "tok", 5, "body")

    def test_createCommitCommentPostsToCorrectUrl(self):
        with mock.patch.object(ai_review.requests, "post", return_value=self._response(201)) as post:
            ai_review._create_commit_comment("owner/repo", "tok", "sha123", "body")

        self.assertIn("/commits/sha123/comments", post.call_args.args[0])

    def test_createCommitCommentRaisesOnFailure(self):
        with mock.patch.object(ai_review.requests, "post", return_value=self._response(500, text="oops")):
            with self.assertRaises(RuntimeError):
                ai_review._create_commit_comment("owner/repo", "tok", "sha123", "body")


class MainOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.env_backup = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env_backup)

    def _writeEventPayload(self, payload):
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(payload, handle)
        handle.close()
        self.addCleanup(os.remove, handle.name)
        return handle.name

    def test_pullRequestEventPostsToThatPr(self):
        event_path = self._writeEventPayload(
            {"pull_request": {"number": 7, "base": {"sha": "b"}, "head": {"sha": "h"}}}
        )
        summary_path = tempfile.NamedTemporaryFile(delete=False).name
        self.addCleanup(os.remove, summary_path)

        os.environ.update(
            GITHUB_EVENT_NAME="pull_request",
            GITHUB_EVENT_PATH=event_path,
            GITHUB_REPOSITORY="owner/repo",
            GITHUB_TOKEN="tok",
            GITHUB_SHA="h",
            GITHUB_STEP_SUMMARY=summary_path,
        )
        _clearEnv("GEMINI_API_KEY")

        with mock.patch.object(ai_review, "_run_git_command", return_value="some diff"), \
             mock.patch.object(ai_review, "_upsert_pr_comment") as upsert, \
             mock.patch.object(ai_review, "_create_commit_comment") as commit_comment, \
             mock.patch.object(ai_review, "_find_open_pr_for_commit") as find_pr:
            ai_review.main()

        find_pr.assert_not_called()  # PR number came straight from the payload
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.args[2], 7)
        commit_comment.assert_not_called()

        summary_contents = open(summary_path, encoding="utf-8").read()
        self.assertIn(ai_review.COMMENT_MARKER, summary_contents)

    def test_pushEventWithOpenPrCommentsOnIt(self):
        event_path = self._writeEventPayload({"before": "b", "after": "h"})

        os.environ.update(
            GITHUB_EVENT_NAME="push",
            GITHUB_EVENT_PATH=event_path,
            GITHUB_REPOSITORY="owner/repo",
            GITHUB_TOKEN="tok",
            GITHUB_SHA="h",
        )
        os.environ.pop("GITHUB_STEP_SUMMARY", None)
        _clearEnv("GEMINI_API_KEY")

        with mock.patch.object(ai_review, "_run_git_command", return_value="some diff"), \
             mock.patch.object(ai_review, "_find_open_pr_for_commit", return_value=11), \
             mock.patch.object(ai_review, "_upsert_pr_comment") as upsert, \
             mock.patch.object(ai_review, "_create_commit_comment") as commit_comment:
            ai_review.main()

        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.args[2], 11)
        commit_comment.assert_not_called()

    def test_pushEventWithNoOpenPrFallsBackToCommitComment(self):
        event_path = self._writeEventPayload({"before": "b", "after": "h"})

        os.environ.update(
            GITHUB_EVENT_NAME="push",
            GITHUB_EVENT_PATH=event_path,
            GITHUB_REPOSITORY="owner/repo",
            GITHUB_TOKEN="tok",
            GITHUB_SHA="h",
        )
        os.environ.pop("GITHUB_STEP_SUMMARY", None)
        _clearEnv("GEMINI_API_KEY")

        with mock.patch.object(ai_review, "_run_git_command", return_value="some diff"), \
             mock.patch.object(ai_review, "_find_open_pr_for_commit", return_value=None), \
             mock.patch.object(ai_review, "_upsert_pr_comment") as upsert, \
             mock.patch.object(ai_review, "_create_commit_comment") as commit_comment:
            ai_review.main()

        upsert.assert_not_called()
        commit_comment.assert_called_once()
        self.assertEqual(commit_comment.call_args.args[2], "h")

    def test_missingTokenSkipsPostingButStillWritesSummary(self):
        event_path = self._writeEventPayload({"before": "b", "after": "h"})
        summary_path = tempfile.NamedTemporaryFile(delete=False).name
        self.addCleanup(os.remove, summary_path)

        os.environ.update(
            GITHUB_EVENT_NAME="push",
            GITHUB_EVENT_PATH=event_path,
            GITHUB_REPOSITORY="owner/repo",
            GITHUB_SHA="h",
            GITHUB_STEP_SUMMARY=summary_path,
        )
        _clearEnv("GITHUB_TOKEN", "GEMINI_API_KEY")

        with mock.patch.object(ai_review, "_run_git_command", return_value="some diff"), \
             mock.patch.object(ai_review, "_upsert_pr_comment") as upsert, \
             mock.patch.object(ai_review, "_create_commit_comment") as commit_comment:
            ai_review.main()

        upsert.assert_not_called()
        commit_comment.assert_not_called()
        self.assertTrue(open(summary_path, encoding="utf-8").read())

    def test_commentPostingFailureDoesNotRaise(self):
        event_path = self._writeEventPayload(
            {"pull_request": {"number": 3, "base": {"sha": "b"}, "head": {"sha": "h"}}}
        )

        os.environ.update(
            GITHUB_EVENT_NAME="pull_request",
            GITHUB_EVENT_PATH=event_path,
            GITHUB_REPOSITORY="owner/repo",
            GITHUB_TOKEN="tok",
            GITHUB_SHA="h",
        )
        os.environ.pop("GITHUB_STEP_SUMMARY", None)
        _clearEnv("GEMINI_API_KEY")

        with mock.patch.object(ai_review, "_run_git_command", return_value="some diff"), \
             mock.patch.object(ai_review, "_upsert_pr_comment", side_effect=RuntimeError("403")):
            try:
                ai_review.main()
            except Exception as exc:  # pragma: no cover - failure path
                self.fail(f"main() should not raise on a posting failure: {exc}")


if __name__ == "__main__":
    unittest.main()
