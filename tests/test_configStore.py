"""Tests for reading and writing the config/ credential files."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from configStore import readConfigValue, writeConfigValue


class ConfigStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "canvasAPI.py")

    def tearDown(self):
        self.tmp.cleanup()

    def test_valueRoundTrips(self):
        self.assertIsNone(writeConfigValue(self.path, "CANVAS_API_TOKEN", "abc123"))
        self.assertEqual(readConfigValue(self.path, "CANVAS_API_TOKEN"), "abc123")

    def test_commentsAndOtherKeysSurvive(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("# do not share this file\nOTHER = 'keep me'\nTOKEN = 'old'\n")

        writeConfigValue(self.path, "TOKEN", "new")
        contents = open(self.path, encoding="utf-8").read()

        self.assertIn("# do not share this file", contents)
        self.assertIn("OTHER = 'keep me'", contents)
        self.assertEqual(readConfigValue(self.path, "TOKEN"), "new")

    def test_missingKeyIsAppended(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("EXISTING = '1'")  # no trailing newline

        writeConfigValue(self.path, "Client_ID", "xyz")

        self.assertEqual(readConfigValue(self.path, "EXISTING"), "1")
        self.assertEqual(readConfigValue(self.path, "Client_ID"), "xyz")

    def test_fileIsCreatedWhenAbsent(self):
        path = os.path.join(self.tmp.name, "nested", "panoptoKey.py")
        self.assertIsNone(writeConfigValue(path, "Client_Secret", "s3cret"))
        self.assertEqual(readConfigValue(path, "Client_Secret"), "s3cret")

    def test_missingFileReadsAsEmpty(self):
        self.assertEqual(readConfigValue("/nonexistent/file.py", "TOKEN"), "")

    def test_similarKeyNamesAreNotConfused(self):
        writeConfigValue(self.path, "Client_ID", "one")
        writeConfigValue(self.path, "Client_ID_BACKUP", "two")

        self.assertEqual(readConfigValue(self.path, "Client_ID"), "one")
        self.assertEqual(readConfigValue(self.path, "Client_ID_BACKUP"), "two")

    def test_valuesContainingEqualsAreRead(self):
        writeConfigValue(self.path, "Client_Secret", "abc+/de=")
        self.assertEqual(readConfigValue(self.path, "Client_Secret"), "abc+/de=")


if __name__ == "__main__":
    unittest.main()
