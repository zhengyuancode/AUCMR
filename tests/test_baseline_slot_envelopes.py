"""Offline regression checks for slot-named model response envelopes."""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import patch

from eval.baselines import (
    _build_pre_constraint_draft,
    _enforce_evidence_whitelist,
    _normalize_slot_response,
    _normalize_uncited_slot_response,
    _unwrap_slot_payload,
)


class SlotEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.goal = {
            "raw_text": "Open a previous saved game.",
            "evidence_refs": ["P12"],
        }
        self.flow = {
            "claims": [{
                "content": "Choose a saved game file.",
                "evidence_refs": ["P12"],
                "step_number": 1,
            }],
        }

    def normalize(self, slot, payload):
        return _enforce_evidence_whitelist(
            _normalize_slot_response(slot, payload), ["P12"],
        )

    def test_recorded_goal_reply_matches_flat_payload(self):
        result = self.normalize("goal", {"goal": self.goal})
        self.assertEqual(result, self.normalize("goal", self.goal))
        self.assertEqual(result["raw_text"], "Open a previous saved game.")
        self.assertEqual(result["evidence_card_ids"], ["P12"])
        self.assertEqual(len(result["claims"]), 1)

    def test_list_slots_preserve_claims_and_step_numbers(self):
        for slot in ("preconditions", "main_flow", "alternative_flow", "exception_flow", "postconditions"):
            with self.subTest(slot=slot):
                result = self.normalize(slot, {slot: self.flow})
                self.assertEqual(result, self.normalize(slot, self.flow))
                self.assertEqual(result["claims"], self.flow["claims"])

    def test_simple_slots_preserve_same_named_payload(self):
        for slot in ("goal", "trigger", "primary_actor"):
            with self.subTest(slot=slot):
                self.assertEqual(self.normalize(slot, {slot: self.goal}), self.normalize(slot, self.goal))

    def test_pre_constraint_draft_preserves_candidates(self):
        self.assertEqual(
            _build_pre_constraint_draft("goal", {"goal": self.goal}),
            _build_pre_constraint_draft("goal", self.goal),
        )
        result = self.normalize("goal", {"goal": self.goal})
        self.assertEqual(result["_pre_constraint_draft"]["claims"][0]["content"], self.goal["raw_text"])
        self.assertEqual(result["_pre_constraint_draft"]["exposed_evidence_refs"], ["P12"])

    def test_invalid_and_missing_citations_still_rejected(self):
        for refs in ([], ["P999"], ["P12", "P999"], "P12"):
            for slot, content in (("goal", {"raw_text": "Unsupported."}), ("main_flow", {"claims": [{"content": "Unsupported.", "step_number": 1}]})):
                with self.subTest(refs=refs, slot=slot):
                    payload = copy.deepcopy(content)
                    target = payload if slot == "goal" else payload["claims"][0]
                    target["evidence_refs"] = refs
                    result = self.normalize(slot, {slot: payload})
                    self.assertEqual(result["claims"], [])
                    self.assertEqual(result["raw_text"], "")
                    self.assertEqual(result["evidence_card_ids"], [])
                    self.assertEqual(len(result["_pre_constraint_draft"]["claims"]), 1)
        self.assertEqual(self.normalize("goal", {"goal": {"raw_text": "Uncited."}})["claims"], [])

    def test_other_slot_is_not_substituted(self):
        result = self.normalize("goal", {"trigger": self.goal})
        self.assertEqual(result["claims"], [])
        self.assertEqual(result["raw_text"], "")
        self.assertEqual(result["_pre_constraint_draft"]["claims"], [])

    def test_empty_and_non_dict_wrappers_stay_empty(self):
        for payload in (None, [], {}, {"goal": None}, {"goal": "text"}, {"goal": [self.goal]}):
            with self.subTest(payload=payload):
                self.assertEqual(self.normalize("goal", payload)["claims"], [])

    def test_flat_payload_and_common_envelopes_unchanged(self):
        expected = self.normalize("goal", self.goal)
        for key in ("answer", "result", "output"):
            with self.subTest(key=key):
                self.assertEqual(self.normalize("goal", {key: self.goal}), expected)
                self.assertEqual(_unwrap_slot_payload({key: self.goal}), self.goal)
        self.assertEqual(_unwrap_slot_payload(self.goal), self.goal)

    def test_existing_flat_and_common_envelope_precedence_preserved(self):
        other = {"raw_text": "Do not select this.", "evidence_refs": ["P12"]}
        mixed = dict(self.goal, goal=other)
        self.assertEqual(self.normalize("goal", mixed), self.normalize("goal", self.goal))
        self.assertEqual(self.normalize("goal", {"answer": self.goal, "goal": other}), self.normalize("goal", self.goal))

    def test_uncited_direct_baseline_preserves_text_without_adding_refs(self):
        for slot, payload in (("goal", self.goal), ("main_flow", self.flow)):
            with self.subTest(slot=slot):
                result = _normalize_uncited_slot_response(slot, {slot: payload})
                self.assertEqual(result, _normalize_uncited_slot_response(slot, payload))
                self.assertTrue(result["claims"])
                self.assertTrue(all(c["evidence_refs"] == [] for c in result["claims"]))

    def test_input_payload_is_not_mutated(self):
        payload = {"goal": copy.deepcopy(self.goal)}
        before = copy.deepcopy(payload)
        self.normalize("goal", payload)
        self.assertEqual(payload, before)

    def test_vdocrag_replays_recorded_response_without_network(self):
        from eval.vendor.vdocrag_adapter import _generate_slot

        pages = ["2.jpg", "12.jpg", "3.jpg", "14.jpg", "13.jpg"]
        with patch("config.settings.chat_completion", return_value=json.dumps({"goal": self.goal})) as call:
            result = _generate_slot("goal", "Open a Saved Game", "Player", {}, pages)
        call.assert_called_once()
        self.assertEqual(call.call_args.kwargs["image_paths"], pages)
        self.assertEqual(result["claims"], [{"content": self.goal["raw_text"], "evidence_refs": ["P12"]}])
        self.assertEqual(result["_pre_constraint_draft"]["exposed_evidence_refs"], ["P2", "P12", "P3", "P14", "P13"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
