import copy
import importlib
import json
import os
import socket
import unittest
from unittest.mock import patch

from eval.l2_independent.common import L2Error, digest
from eval.l2_independent.evaluation import (MockClient, assert_reusable, cache_identity, evaluate,
                                          parse_response, require_live_authorization)
from eval.l2_independent.fixtures import fixture_cases
from eval.l2_independent.offline import OfflineViolation, offline_guard


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.guard = offline_guard()
        self.guard.__enter__()
        self.addCleanup(self.guard.__exit__, None, None, None)
        self.config, self.cases = fixture_cases()
        self.a = self.cases[0]["artifact"]
        self.response = self.cases[0]["response"]

    def test_fixture_statuses_and_calls(self):
        for case in self.cases:
            with self.subTest(task=case["artifact"]["task_id"]):
                client = MockClient(case["response"], self.config["model"])
                rec = evaluate(case["artifact"], self.config, client, "test")
                self.assertEqual(rec["status"], case["expected_status"])
                self.assertEqual(rec["research_data_status"], "NOT_RESEARCH_DATA")
                self.assertEqual(rec["evaluation_mode"], "MOCK_ONLY")
                self.assertEqual(rec["model_call_count"], len(client.calls))
                if rec["status"] in {"TECHNICAL_FAILURE", "NO_EVALUABLE_CONTENT"}:
                    self.assertIsNone(rec["confirmed_conflict_count"])
        # The clear contradiction sample tests the plumbing, not a model's detection ability.
        result = parse_response(json.dumps(self.response), self.a)
        self.assertEqual(result["conflicts"][0]["refs"][0]["assertion_id"], "main_flow:0")

    def test_invalid_ids_slots_and_bad_schema_cannot_pass(self):
        for mutation in ("unknown_id", "wrong_slot", "empty_refs"):
            body = copy.deepcopy(self.response)
            issue = body["conflicts"][0]
            if mutation == "unknown_id":
                issue["refs"][0]["assertion_id"] = "main_flow:5000"
            elif mutation == "wrong_slot":
                issue["refs"][0]["slot"] = "goal"
            else:
                issue["refs"] = []
            result = parse_response(json.dumps(body), self.a)
            self.assertEqual(result["status"], "TECHNICAL_FAILURE")
            self.assertTrue(result["validation_errors"])
        for body in ({}, [], {"review_status": "COMPLETE", "conflicts": "bad", "unresolved": []}):
            self.assertEqual(parse_response(json.dumps(body), self.a)["parse_status"], "INVALID_SCHEMA")

    def test_exact_dedup_not_same_pair_dedup(self):
        body = copy.deepcopy(self.response)
        exact = copy.deepcopy(body["conflicts"][0])
        distinct = copy.deepcopy(exact)
        distinct["incompatible_content"] = "A different claimed conflict on the same assertions."
        distinct["rationale"] = "Different alleged reason, retained for separate review."
        body["conflicts"].extend([exact, distinct])
        result = parse_response(json.dumps(body), self.a)
        self.assertEqual(len(result["conflicts"]), 2)
        self.assertEqual(result["duplicates_removed"], 1)
        self.assertEqual([x["issue_id"] for x in result["conflicts"]], ["C001", "C002"])

    def test_valid_conflict_survives_invalid_second_finding(self):
        body = copy.deepcopy(self.response)
        bad = copy.deepcopy(body["conflicts"][0])
        bad["refs"][0]["assertion_id"] = "main_flow:999"
        body["conflicts"].append(bad)
        result = parse_response(json.dumps(body), self.a)
        self.assertEqual(result["status"], "PARTIAL_UNRESOLVED")
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(len(result["validation_errors"]), 1)

    def test_partial_without_details_remains_partial(self):
        result = parse_response('{"review_status":"PARTIAL","conflicts":[],"unresolved":[]}', self.a)
        self.assertEqual(result["status"], "PARTIAL_UNRESOLVED")
        self.assertEqual(result["unresolved"][0]["origin"], "PROTOCOL_VALIDATOR")
        body = {"review_status": "COMPLETE", "conflicts": [], "unresolved": [
            self.cases[3]["response"]["unresolved"][0]]}
        self.assertEqual(parse_response(json.dumps(body), self.a)["status"], "PARTIAL_UNRESOLVED")

    def test_cache_drift_and_partial_reuse(self):
        rec = evaluate(self.a, self.config, MockClient(self.response, self.config["model"]), "test")
        self.assertTrue(assert_reusable(rec, self.a, self.config))
        for field, value in (("input_hash", "changed"), ("task_input", {"name": "new", "primary_actor": "User"})):
            modified = copy.deepcopy(self.a)
            modified[field] = value
            with self.assertRaises(L2Error):
                assert_reusable(rec, modified, self.config)
        for field in ("protocol_version", "prompt_version", "model"):
            changed = copy.deepcopy(self.config)
            changed[field] = {"different": True} if field == "model" else "changed"
            with self.assertRaises(L2Error):
                assert_reusable(rec, self.a, changed)
        with patch("eval.l2_independent.evaluation.prompt_hash", return_value="changed"):
            with self.assertRaises(L2Error):
                assert_reusable(rec, self.a, self.config)
        rec["status"] = "PARTIAL_UNRESOLVED"
        with self.assertRaises(L2Error):
            assert_reusable(rec, self.a, self.config)

    def test_real_mock_forbidden_and_live_gate_precedes_client(self):
        real = copy.deepcopy(self.a)
        real["data_origin"] = "REAL"
        client = MockClient(self.response, self.config["model"])
        with self.assertRaises(L2Error):
            evaluate(real, self.config, client, "test")
        self.assertEqual(len(client.calls), 0)
        client.is_live = True
        with patch.dict(os.environ, {"AUCMR_L2_KEY": "present-but-unused"}):
            with self.assertRaises(L2Error):
                evaluate(real, self.config, client, "test", explicit_live_authorization=True)
        self.assertEqual(len(client.calls), 0)
        cfg = copy.deepcopy(self.config)
        cfg["live_authorized"] = True
        with self.assertRaises(L2Error):
            evaluate(real, cfg, client, "test", explicit_live_authorization=True)
        self.assertEqual(len(client.calls), 0)
        bound = copy.deepcopy(cfg)
        bound["model"] = {"binding_status": "BOUND", "provider": "future", "model_id": "future",
                          "model_revision": "pin", "base_url": "https://invalid.example", "api_key_env": "AUCMR_L2_KEY",
                          "parameters": {}}
        with self.assertRaises(L2Error):
            require_live_authorization(bound)
        require_live_authorization(bound, True)  # gate only, no client/transport/network call

    def test_network_and_old_paths_blocked(self):
        with self.assertRaises(OfflineViolation):
            socket.create_connection(("127.0.0.1", 9))
        with socket.socket() as sock:
            with self.assertRaises(OfflineViolation):
                sock.connect(("127.0.0.1", 9))
        for module in ("eval.l2_judge", "eval.compute_metrics", "eval.agent_evaluation.runner", "torch"):
            with self.assertRaises(OfflineViolation):
                importlib.import_module(module)

    def test_client_identity_mismatch_and_exception_redaction(self):
        client = MockClient(self.response, {"other_model": True})
        with self.assertRaises(L2Error):
            evaluate(self.a, self.config, client, "test")
        client = MockClient(RuntimeError("SECRET_SHOULD_NOT_LEAK"), self.config["model"])
        rec = evaluate(self.a, self.config, client, "test")
        self.assertEqual(rec["parse_status"], "CALL_FAILED")
        self.assertNotIn("SECRET_SHOULD_NOT_LEAK", json.dumps(rec))

    def test_fresh_sessions_not_cross_artifact_context(self):
        client = MockClient({"review_status": "COMPLETE", "conflicts": [], "unresolved": []}, self.config["model"])
        for case in self.cases[:2]:
            evaluate(case["artifact"], self.config, client, "test")
        self.assertEqual([len(x) for x in client.calls], [2, 2])
        self.assertNotEqual(client.calls[0][1]["content"], client.calls[1][1]["content"])


if __name__ == "__main__":
    unittest.main()
