"""Tests for panoptoVideo.PanoptoAuditor's API path and orchestration.

The browser-backed path needs a real Selenium driver and is exercised only at
the level of "does it call the right helpers" using a fake BrowserSession; the
REST API path is tested against a mocked requests.Session.
"""

import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import panoptoVideo
from auditCore import CAPTION_AUTO, CAPTION_HUMAN, CAPTION_NONE, CAPTION_UNKNOWN
from panoptoCaptions import CaptionClassification


def _resp(status_code=200, json_data=None, text=""):
    response = mock.Mock()
    response.status_code = status_code
    if json_data is not None:
        response.json.return_value = json_data
    else:
        response.json.side_effect = ValueError("no json")
    response.text = text
    return response


class TokenCachingTests(unittest.TestCase):
    def setUp(self):
        self.auditor = panoptoVideo.PanoptoAuditor(client_id="id", client_secret="secret")
        self.addCleanup(self.auditor.close)

    def test_tokenIsFetchedAndCached(self):
        token_response = _resp(200, {"access_token": "tok1", "expires_in": 3600})

        with mock.patch.object(self.auditor._session, "post", return_value=token_response) as post:
            first = self.auditor._getToken("https://x.panopto.com")
            second = self.auditor._getToken("https://x.panopto.com")

        self.assertEqual(first, "tok1")
        self.assertEqual(second, "tok1")
        post.assert_called_once()  # second call served from cache

    def test_expiredTokenIsRefetched(self):
        token_response = _resp(200, {"access_token": "tok1", "expires_in": 0})

        with mock.patch.object(self.auditor._session, "post", return_value=token_response) as post:
            self.auditor._getToken("https://x.panopto.com")
            # Force it into the past explicitly, since expires_in=0 could still
            # land marginally in the future depending on clock resolution.
            self.auditor._tokens["https://x.panopto.com"].expires_at = time.time() - 1
            self.auditor._getToken("https://x.panopto.com")

        self.assertEqual(post.call_count, 2)

    def test_failedTokenRequestReturnsNone(self):
        with mock.patch.object(self.auditor._session, "post", return_value=_resp(401, text="bad creds")):
            self.assertIsNone(self.auditor._getToken("https://x.panopto.com"))

    def test_missingAccessTokenFieldReturnsNone(self):
        with mock.patch.object(self.auditor._session, "post", return_value=_resp(200, {})):
            self.assertIsNone(self.auditor._getToken("https://x.panopto.com"))

    def test_networkErrorReturnsNone(self):
        import requests

        with mock.patch.object(self.auditor._session, "post", side_effect=requests.RequestException("down")):
            self.assertIsNone(self.auditor._getToken("https://x.panopto.com"))

    def test_noCredentialsSkipsAndReturnsNone(self):
        auditor = panoptoVideo.PanoptoAuditor(client_id="", client_secret="")
        self.addCleanup(auditor.close)

        self.assertIsNone(auditor._classifyViaApi("https://x.panopto.com", "session1"))


class ClassifyViaApiTests(unittest.TestCase):
    def setUp(self):
        self.auditor = panoptoVideo.PanoptoAuditor(client_id="id", client_secret="secret")
        self.addCleanup(self.auditor.close)
        self.auditor._tokens["https://x.panopto.com"] = panoptoVideo._ApiToken("tok", time.time() + 3600)

    def test_machineTranscriptionFlagClassifiesAsAuto(self):
        captions_resp = _resp(200, {"MachineTranscription": True, "Captions": [{"Caption": "hi"}]})
        session_resp = _resp(200, {})

        with mock.patch.object(self.auditor._session, "get", side_effect=[captions_resp, session_resp]):
            result = self.auditor._classifyViaApi("https://x.panopto.com", "session1")

        self.assertEqual(result.kind, CAPTION_AUTO)
        self.assertEqual(result.method, "panopto_api")

    def test_notFoundStatusMeansNoCaptions(self):
        with mock.patch.object(self.auditor._session, "get", side_effect=[_resp(404), _resp(404)]):
            result = self.auditor._classifyViaApi("https://x.panopto.com", "session1")

        self.assertEqual(result.kind, CAPTION_NONE)

    def test_serverErrorOnBothEndpointsReturnsNone(self):
        with mock.patch.object(self.auditor._session, "get", side_effect=[_resp(500), _resp(500)]):
            result = self.auditor._classifyViaApi("https://x.panopto.com", "session1")

        self.assertIsNone(result)

    def test_networkErrorOnBothEndpointsReturnsNone(self):
        import requests

        with mock.patch.object(
            self.auditor._session, "get", side_effect=requests.RequestException("down")
        ):
            result = self.auditor._classifyViaApi("https://x.panopto.com", "session1")

        self.assertIsNone(result)

    def test_nonJsonTextResponseIsStillClassified(self):
        # The second endpoint errors outright (not 200/204/404) so it does not
        # contribute an empty payload that would swamp the lone text result.
        srt_like = _resp(200, text="1\n00:00:01,000 --> 00:00:02,000\n>> Hello there.\n")
        srt_like.json.side_effect = ValueError("not json")

        with mock.patch.object(self.auditor._session, "get", side_effect=[srt_like, _resp(500)]):
            result = self.auditor._classifyViaApi("https://x.panopto.com", "session1")

        self.assertEqual(result.kind, CAPTION_HUMAN)


class IsDecisiveTests(unittest.TestCase):
    def test_highConfidenceDefiniteVerdictIsDecisive(self):
        attempts = [CaptionClassification(kind=CAPTION_AUTO, confidence="high")]
        self.assertTrue(panoptoVideo._isDecisive(attempts))

    def test_highConfidenceUnknownIsNotDecisive(self):
        attempts = [CaptionClassification(kind=CAPTION_UNKNOWN, confidence="high")]
        self.assertFalse(panoptoVideo._isDecisive(attempts))

    def test_lowConfidenceIsNotDecisive(self):
        attempts = [CaptionClassification(kind=CAPTION_AUTO, confidence="low")]
        self.assertFalse(panoptoVideo._isDecisive(attempts))

    def test_noneEntriesAreIgnored(self):
        self.assertFalse(panoptoVideo._isDecisive([None, None]))

    def test_emptyListIsNotDecisive(self):
        self.assertFalse(panoptoVideo._isDecisive([]))


class AuditResultTests(unittest.TestCase):
    def test_asEntryIncludesClassificationFields(self):
        classification = CaptionClassification(
            kind=CAPTION_AUTO, confidence="high", signals=["a signal"], method="panopto_api"
        )
        result = panoptoVideo.PanoptoAuditResult(
            url="https://x/y", has_captions=True, classification=classification, session_id="sid"
        )

        entry = result.asEntry(course_id="101")

        self.assertEqual(entry["type"], "panopto")
        self.assertEqual(entry["caption_kind"], CAPTION_AUTO)
        self.assertEqual(entry["session_id"], "sid")
        self.assertEqual(entry["course_id"], "101")

    def test_asEntryOmitsCourseIdWhenNotProvided(self):
        result = panoptoVideo.PanoptoAuditResult(url="https://x/y")
        entry = result.asEntry()

        self.assertNotIn("course_id", entry)


class AuditOrchestrationTests(unittest.TestCase):
    """Verify PanoptoAuditor.audit() stops early once the API is decisive."""

    def setUp(self):
        self.auditor = panoptoVideo.PanoptoAuditor(client_id="id", client_secret="secret")
        self.addCleanup(self.auditor.close)

    def test_decisiveApiResultSkipsBrowser(self):
        decisive = CaptionClassification(kind=CAPTION_AUTO, confidence="high")

        with mock.patch.object(self.auditor, "_classifyViaApi", return_value=decisive), \
             mock.patch.object(self.auditor, "_classifyViaBrowser") as browser_check:
            result = self.auditor.audit(
                "https://x.hosted.panopto.com/Panopto/Pages/Viewer.aspx?id=11111111-1111-1111-1111-111111111111"
            )

        browser_check.assert_not_called()
        self.assertTrue(result.has_captions)
        self.assertEqual(result.classification.kind, CAPTION_AUTO)

    def test_inconclusiveApiResultFallsBackToBrowser(self):
        weak = CaptionClassification(kind=CAPTION_UNKNOWN, confidence="low")
        strong = CaptionClassification(kind=CAPTION_HUMAN, confidence="medium")

        with mock.patch.object(self.auditor, "_classifyViaApi", return_value=weak), \
             mock.patch.object(self.auditor, "_classifyViaBrowser", return_value=strong) as browser_check:
            result = self.auditor.audit(
                "https://x.hosted.panopto.com/Panopto/Pages/Viewer.aspx?id=11111111-1111-1111-1111-111111111111"
            )

        browser_check.assert_called_once()
        self.assertEqual(result.classification.kind, CAPTION_HUMAN)

    def test_noCredentialsGoesStraightToBrowser(self):
        auditor = panoptoVideo.PanoptoAuditor(client_id="", client_secret="")
        self.addCleanup(auditor.close)

        with mock.patch.object(auditor, "_classifyViaBrowser", return_value=None) as browser_check:
            result = auditor.audit("https://x.hosted.panopto.com/Panopto/Pages/Viewer.aspx?id=1")

        browser_check.assert_called_once()
        self.assertEqual(result.classification.kind, CAPTION_UNKNOWN)


class CollectPanoptoLinksFallbackTests(unittest.TestCase):
    def test_fallsBackToCanvasWhenCacheEmpty(self):
        with mock.patch.object(panoptoVideo, "_panoptoLinksFromCache", return_value=[]), \
             mock.patch.object(
                 panoptoVideo,
                 "_panoptoLinksFromCanvas",
                 return_value=["https://x.hosted.panopto.com/Panopto/Pages/Viewer.aspx?id=1"],
             ) as canvas_fallback:
            links = panoptoVideo.collectPanoptoLinks(["101"])

        canvas_fallback.assert_called_once_with("101")
        self.assertEqual(len(links), 1)

    def test_cachedLinksSkipCanvasEntirely(self):
        with mock.patch.object(
            panoptoVideo,
            "_panoptoLinksFromCache",
            return_value=["https://x.hosted.panopto.com/Panopto/Pages/Viewer.aspx?id=1"],
        ), mock.patch.object(panoptoVideo, "_panoptoLinksFromCanvas") as canvas_fallback:
            links = panoptoVideo.collectPanoptoLinks(["101"])

        canvas_fallback.assert_not_called()
        self.assertEqual(len(links), 1)


if __name__ == "__main__":
    unittest.main()
