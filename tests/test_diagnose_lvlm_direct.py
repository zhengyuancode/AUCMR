"""Offline tests: no provider client or model is loaded."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from scripts.diagnose_lvlm_direct import capture_slot, classify, main


class DiagnosticTests(unittest.TestCase):
    def test_default_makes_no_calls(self):
        self.assertEqual(main([]), 0)

    def test_empty_parse_and_accepted_are_distinct(self):
        for payload, expected in [(None, "parse_failed_or_json_null"), ({}, "explicit_empty_object"), ({"goal": "text"}, "parsed_but_empty_needs_review")]:
            self.assertEqual(classify({"raw_response": "x", "parsed": payload}), expected)
        self.assertEqual(classify({"raw_response": "x", "parsed": {}, "normalized": {"claims": [{"content": "text"}]}}), "accepted")
        self.assertEqual(classify({"error_type": "RuntimeError"}), "call_error")
        self.assertEqual(classify({}), "no_call")

    def test_wrapper_preserves_inputs_return_and_restores_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            image = directory / "page.jpg"
            image.write_bytes(b"offline fixture")
            original = Mock(return_value='{"raw_text":"Record distribution"}')
            module = SimpleNamespace(_call_lvlm=original, _parse_json_with_repair=json.loads)
            expected = {"raw_text": "Record distribution", "claims": []}
            def filler(slot, filled, context):
                module._call_lvlm("unchanged prompt", [str(image)])
                return expected
            baseline = SimpleNamespace(slot_filler=filler)
            result = capture_slot(module, baseline, "goal", {}, {}, directory)
            self.assertIs(result, expected)
            self.assertIs(module._call_lvlm, original)
            original.assert_called_once_with("unchanged prompt", [str(image)])
            record = json.loads((directory / "goal.json").read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "accepted")
            self.assertEqual(record["parsed"], {"raw_text": "Record distribution"})
            self.assertEqual(len(record["images"][0]["sha256"]), 64)

    def test_failure_records_type_without_exception_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            module = SimpleNamespace(_call_lvlm=Mock(side_effect=RuntimeError("SECRET")))
            def filler(*args):
                return module._call_lvlm("prompt", [])
            with self.assertRaises(RuntimeError):
                capture_slot(module, SimpleNamespace(slot_filler=filler), "goal", {}, {}, Path(tmp))
            text = (Path(tmp) / "goal.json").read_text(encoding="utf-8")
            self.assertNotIn("SECRET", text)
            self.assertEqual(json.loads(text)["status"], "call_error")


if __name__ == "__main__":
    unittest.main()
