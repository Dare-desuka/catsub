"""Unit tests for the standalone CatSub SRT quality audit."""
import tempfile
import unittest
from pathlib import Path

from quality_check import audit_srt, parse_srt


class ParseSrtTests(unittest.TestCase):
    def test_accepts_comma_and_period_millisecond_separators(self):
        cues, errors = parse_srt(
            "1\n00:00:01,000 --> 00:00:02.500\nHalo\n\n"
            "2\n00:00:03,000 --> 00:00:04,000\nDunia\n"
        )
        self.assertEqual(errors, [])
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].start, 1000)
        self.assertEqual(cues[0].end, 2500)

    def test_reports_malformed_timestamp(self):
        _, errors = parse_srt("1\nnot a timestamp\nTeks\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("timestamp tidak valid", errors[0])


class AuditTests(unittest.TestCase):
    def test_marks_untranslated_placeholder_as_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.srt"
            path.write_text("1\n00:00:01,000 --> 00:00:02,000\n[BELUM_DITERJEMAHKAN] Halo\n", encoding="utf-8")
            errors, _ = audit_srt(path)
        self.assertTrue(any("marker gagal" in error for error in errors))

    def test_reports_source_script_as_warning_not_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.srt"
            path.write_text("1\n00:00:01,000 --> 00:00:03,000\nこれは日本語の文章です\n", encoding="utf-8")
            errors, warnings = audit_srt(path, lang="ja")
        self.assertEqual(errors, [])
        self.assertTrue(any("aksara bahasa sumber" in warning for warning in warnings))

    def test_detects_invalid_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.srt"
            path.write_text("1\n00:00:02,000 --> 00:00:01,000\nTeks\n", encoding="utf-8")
            errors, _ = audit_srt(path)
        self.assertTrue(any("rentang waktu tidak valid" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
