"""Tests for pullModules.py's Canvas API plumbing, with requests mocked out."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pullModules


def _resp(status_code=200, json_data=None, headers=None, text=""):
    response = mock.Mock()
    response.status_code = status_code
    response.json.return_value = json_data if json_data is not None else []
    response.headers = headers or {}
    response.text = text
    return response


class NextLinkTests(unittest.TestCase):
    def test_parsesNextFromMultipleRelValues(self):
        header = (
            '<https://x/a?page=2>; rel="next", '
            '<https://x/a?page=1>; rel="prev", '
            '<https://x/a?page=9>; rel="last"'
        )
        self.assertEqual(pullModules._getNextLink(header), "https://x/a?page=2")

    def test_noNextRelReturnsNone(self):
        header = '<https://x/a?page=1>; rel="prev"'
        self.assertIsNone(pullModules._getNextLink(header))

    def test_emptyHeaderReturnsNone(self):
        self.assertIsNone(pullModules._getNextLink(None))
        self.assertIsNone(pullModules._getNextLink(""))

    def test_backwardsCompatibleAliasExists(self):
        self.assertIs(pullModules._get_next_link, pullModules._getNextLink)


class PaginateTests(unittest.TestCase):
    def test_followsLinkHeaderAcrossPages(self):
        page1 = _resp(200, [{"id": 1}], {"Link": '<https://x/p2>; rel="next"'})
        page2 = _resp(200, [{"id": 2}], {})

        with mock.patch.object(pullModules.SESSION, "get", side_effect=[page1, page2]) as get:
            result = pullModules._paginate("https://x/p1", "test")

        self.assertEqual(result, [{"id": 1}, {"id": 2}])
        self.assertEqual(get.call_count, 2)

    def test_stopsOnNon200Status(self):
        with mock.patch.object(pullModules.SESSION, "get", return_value=_resp(500, text="oops")):
            result = pullModules._paginate("https://x/p1", "test")

        self.assertEqual(result, [])

    def test_stopsOnRequestException(self):
        import requests

        with mock.patch.object(pullModules.SESSION, "get", side_effect=requests.RequestException("down")):
            result = pullModules._paginate("https://x/p1", "test")

        self.assertEqual(result, [])

    def test_nonListPayloadIsWrappedInAList(self):
        with mock.patch.object(pullModules.SESSION, "get", return_value=_resp(200, {"id": 1})):
            result = pullModules._paginate("https://x/p1", "test")

        self.assertEqual(result, [{"id": 1}])

    def test_invalidJsonStopsGracefully(self):
        bad = mock.Mock(status_code=200, headers={})
        bad.json.side_effect = ValueError("bad json")

        with mock.patch.object(pullModules.SESSION, "get", return_value=bad):
            result = pullModules._paginate("https://x/p1", "test")

        self.assertEqual(result, [])


class GetCoursesTests(unittest.TestCase):
    def test_onlyDictItemsAreKept(self):
        payload = [{"id": 1, "name": "A"}, "junk", {"id": 2, "name": "B"}]
        with mock.patch.object(pullModules, "_paginate", return_value=payload):
            courses = pullModules.get_courses()

        self.assertEqual(len(courses), 2)
        self.assertTrue(all(isinstance(c, dict) for c in courses))


class ResolveExternalToolTests(unittest.TestCase):
    def test_panoptoToolWithSessionlessLaunchIsMarked(self):
        item = {"url": "https://canvas/api/item/1", "title": "Panopto Recording", "external_url": ""}

        with mock.patch.object(
            pullModules.SESSION,
            "get",
            return_value=_resp(200, {"external_url": "https://x.panopto.com/y", "url": "https://x.panopto.com/launch"}),
        ):
            result = pullModules._resolveExternalTool(item)

        self.assertEqual(len(result), 1)
        self.assertIn("_panopto_video=true", result[0])
        self.assertTrue(result[0].startswith("https://x.panopto.com/launch"))

    def test_panoptoDetectedFromExternalUrlAloneWithNoSessionlessLaunch(self):
        item = {"url": "https://canvas/api/item/1", "title": "Recording", "external_url": ""}

        with mock.patch.object(
            pullModules.SESSION,
            "get",
            return_value=_resp(200, {"external_url": "https://x.panopto.com/y", "url": None}),
        ):
            result = pullModules._resolveExternalTool(item)

        self.assertEqual(result, ["https://x.panopto.com/y"])

    def test_nonPanoptoToolReturnsResolvedUrl(self):
        item = {"url": "https://canvas/api/item/1", "title": "Some Tool", "external_url": ""}

        with mock.patch.object(
            pullModules.SESSION, "get", return_value=_resp(200, {"external_url": "", "url": "https://other.example/x"})
        ):
            result = pullModules._resolveExternalTool(item)

        self.assertEqual(result, ["https://other.example/x"])

    def test_failedLookupFallsBackToOriginalLink(self):
        item = {"url": "https://canvas/api/item/1", "title": "x", "external_url": ""}

        with mock.patch.object(pullModules.SESSION, "get", return_value=_resp(404)):
            result = pullModules._resolveExternalTool(item)

        self.assertEqual(result, ["https://canvas/api/item/1"])

    def test_networkErrorFallsBackToOriginalLink(self):
        import requests

        item = {"url": "https://canvas/api/item/1", "title": "x", "external_url": ""}

        with mock.patch.object(pullModules.SESSION, "get", side_effect=requests.RequestException("down")):
            result = pullModules._resolveExternalTool(item)

        self.assertEqual(result, ["https://canvas/api/item/1"])

    def test_itemWithNoUrlReturnsEmptyList(self):
        self.assertEqual(pullModules._resolveExternalTool({"title": "x"}), [])


class CollectModuleItemsTests(unittest.TestCase):
    def test_regularLinksAndExternalUrlsAreBothCollected(self):
        items = [
            {"type": "Page", "url": "https://a/page", "external_url": "https://a/ext"},
            {"type": "Page", "url": "https://b/page"},
        ]
        with mock.patch.object(pullModules, "_paginate", return_value=items):
            urls = pullModules._collectModuleItems("https://items", "101")

        self.assertEqual(urls, ["https://a/page", "https://a/ext", "https://b/page"])

    def test_externalToolPanoptoLaunchSuppressesExternalUrlDuplicate(self):
        items = [
            {
                "type": "ExternalTool",
                "url": "https://a/tool",
                "external_url": "https://a/tool-ext",
                "title": "Panopto",
            }
        ]
        with mock.patch.object(pullModules, "_paginate", return_value=items), \
             mock.patch.object(
                 pullModules,
                 "_resolveExternalTool",
                 return_value=["https://x.panopto.com/launch&_panopto_video=true"],
             ):
            urls = pullModules._collectModuleItems("https://items", "101")

        self.assertEqual(urls, ["https://x.panopto.com/launch&_panopto_video=true"])

    def test_nonDictItemsAreSkipped(self):
        with mock.patch.object(pullModules, "_paginate", return_value=["junk", None]):
            self.assertEqual(pullModules._collectModuleItems("https://items", "101"), [])


class GetCourseModulesTests(unittest.TestCase):
    def test_urlsAreDedupedAcrossModules(self):
        modules = [{"items_url": "https://items/1"}, {"items_url": "https://items/2"}]

        def fake_paginate(url, label):
            if label.startswith("modules for course"):
                return modules
            return []

        with mock.patch.object(pullModules, "_paginate", side_effect=fake_paginate), \
             mock.patch.object(
                 pullModules,
                 "_collectModuleItems",
                 side_effect=[["https://a"], ["https://a", "https://b"]],
             ):
            urls = pullModules.getCourseModules("101")

        self.assertEqual(sorted(urls), ["https://a", "https://b"])

    def test_noModulesReturnsEmptyList(self):
        with mock.patch.object(pullModules, "_paginate", return_value=[]):
            self.assertEqual(pullModules.getCourseModules("101"), [])


if __name__ == "__main__":
    unittest.main()
