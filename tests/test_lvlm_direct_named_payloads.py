"""Recorded LVLM responses replayed offline; no model or API calls."""
import copy
import json
from pathlib import Path
import unittest
from eval.baselines import _normalize_uncited_slot_response as normalize
from eval.baselines import _normalize_slot_response, _enforce_evidence_whitelist


class LvlmNamedPayloadTests(unittest.TestCase):
    def test_recorded_responses_preserve_all_text(self):
        rows = json.loads((Path(__file__).parent / "fixtures/lvlm_direct_microcare_responses.json").read_text(encoding="utf-8"))
        expected_counts = {"goal": 1, "preconditions": 4, "trigger": 1, "main_flow": 10, "alternative_flow": 2, "exception_flow": 4, "postconditions": 6}
        for row in rows:
            slot, parsed = row["slot"], row["parsed"]
            with self.subTest(slot=slot):
                result = normalize(slot, parsed)
                self.assertEqual(len(result["claims"]), expected_counts[slot])
                payload = parsed.get(slot, parsed.get("claims"))
                expected = [payload] if isinstance(payload, str) else [x["content"] for x in payload]
                self.assertEqual([x["content"] for x in result["claims"]], expected)
                self.assertTrue(all(x["evidence_refs"] == [] for x in result["claims"]))
                if slot == "main_flow":
                    self.assertEqual([x["step_number"] for x in result["claims"]], list(range(1, 11)))
                if slot == "alternative_flow":
                    self.assertTrue(all("step_number" not in x for x in result["claims"]))

    def test_existing_precedence_and_input_immutability(self):
        payload = {"raw_text": "Original", "goal": "Do not select"}
        before = copy.deepcopy(payload)
        self.assertEqual(normalize("goal", payload)["raw_text"], "Original")
        self.assertEqual(payload, before)
        self.assertEqual(normalize("goal", {"answer": {"raw_text": "Answer"}, "goal": "Other"})["raw_text"], "Answer")
        flow = {"main_flow": [{"content": "Do it", "step_number": 1, "evidence_refs": ["P1"]}]}
        before = copy.deepcopy(flow)
        normalize("main_flow", flow)
        self.assertEqual(flow, before)

    def test_empty_wrong_slot_and_wrong_types_stay_empty(self):
        for slot, payload in [("goal", {}), ("goal", {"trigger": "Other"}), ("goal", {"goal": None}), ("goal", {"goal": []}), ("main_flow", {"main_flow": "Text"}), ("main_flow", {"main_flow": []}), ("main_flow", {"main_flow": [None, "text", {}]})]:
            with self.subTest(slot=slot, payload=payload):
                self.assertFalse(normalize(slot, payload)["claims"])

    def test_cited_methods_still_reject_uncited_named_payloads(self):
        for slot, payload in [("goal", {"goal": "Text"}), ("main_flow", {"main_flow": [{"content": "Text"}]})]:
            result = _enforce_evidence_whitelist(_normalize_slot_response(slot, payload), ["P1"])
            self.assertEqual(result["claims"], [])


if __name__ == "__main__":
    unittest.main()
