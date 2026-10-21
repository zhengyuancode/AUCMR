"""Focused acceptance tests. Synthetic materials only; networking is blocked."""
import csv
import json
import socket
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from eval.l4_agent.engine import authorize, evaluate_run, request_for, validate_run
from eval.l4_agent.export import export_records
from eval.l4_agent.fixtures import FixtureClient, make_fixtures, TEXT
from eval.l4_agent.inputs import adapt, assert_current, freeze, local_path, validate_config
from eval.l4_agent.protocol import (L4Error, REPO, SLOTS, digest, network_blocked,
                                    new_run, read_json, save_new)
from eval.l4_agent.records import evaluate_response, validate_record, validate_refs


class OfflinePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = network_blocked()
        cls.network.__enter__()
        cls.addClassCleanup(cls.network.__exit__, None, None, None)
        cls.run_dir = new_run("test-pipeline")
        cls.config = make_fixtures(cls.run_dir)
        cls.manifest = freeze(cls.config, cls.run_dir)
        cls.client = FixtureClient(read_json(cls.run_dir / "fixtures/fixture_manifest.json")["scenarios"])
        cls.records = evaluate_run(cls.run_dir, cls.client, mock=True)
        cls.summary = export_records(cls.records, cls.config, cls.run_dir / "exports")
        cls.artifacts = {a["artifact_id"]: a for a in cls.manifest["artifacts"]}

    def record(self, tid="F-01", method="Fixture-Method-A"):
        return next(r for r in self.records if r["task_id"] == tid and r["method"] == method)

    def response(self):
        r = self.record()
        return json.loads(r["raw_response"]), self.artifacts[r["artifact_id"]], r["source_access"]

    def test_end_to_end_and_immutable_records(self):
        config, manifest, records = validate_run(self.run_dir)
        self.assertEqual(records, self.records)
        self.assertEqual(len(records), 22)
        self.assertEqual(self.client.calls, 26)
        self.assertTrue(all(r["research_status"] == "NOT_RESEARCH_DATA" for r in records))
        with self.assertRaises(L4Error):
            evaluate_run(self.run_dir, self.client, mock=True)
        with self.assertRaises(FileExistsError):
            export_records(records, config, self.run_dir / "exports")

    def test_seven_slots_exact_content_order_and_steps(self):
        r = self.record()
        a = self.artifacts[r["artifact_id"]]
        request = r["request_transcript"][0]["request"]
        original = read_json(local_path(a["path"]))
        self.assertEqual(set(request["artifact"]["slots"]), set(SLOTS))
        self.assertNotIn("actor", request["artifact"]["slots"])
        for slot in SLOTS:
            for i, claim in enumerate(request["artifact"]["slots"][slot]["claims"]):
                self.assertEqual(claim["content"], original["slots"][slot]["claims"][i]["content"])
                self.assertEqual(claim.get("step_number"), original["slots"][slot]["claims"][i].get("step_number"))
        self.assertEqual(request["artifact"]["slots"]["main_flow"]["raw_text"], TEXT["main_flow"])

    def test_anonymous_allowlist_no_old_results_or_paths(self):
        request = self.record()["request_transcript"][0]["request"]
        payload = json.dumps(request)
        for forbidden in ("Fixture-Method-A", "Fixture-Method-B", "PRIVATE_OLD_SCORE", "PRIVATE_REPAIR_RECORD",
                          "pre_constraint_slots", "old_human_score", "evidence_refs", "quality_flags", str(REPO)):
            self.assertNotIn(forbidden, payload)
        self.assertEqual(set(request["artifact"]["task"]), {"name", "actor"})
        self.assertNotIn("method", request["artifact"])

    def test_equal_source_opportunities_without_citations(self):
        a, b = self.record(), self.record(method="Fixture-Method-B")
        ra, rb = (r["request_transcript"][0]["request"] for r in (a, b))
        self.assertEqual(ra["source_policy"], rb["source_policy"])
        self.assertEqual(ra["artifact"]["sources"], rb["artifact"]["sources"])
        self.assertEqual(a["pcs_mean"], 3)
        self.assertEqual(a["processing_status"], "COMPLETED")

    def test_valid_empty_slot_uses_fixed_zero(self):
        r = self.record("F-04")
        self.assertEqual(r["pcs"]["alternative_flow"]["status"], "EMPTY_SLOT")
        self.assertEqual(r["pcs"]["alternative_flow"]["score"], 0)
        self.assertAlmostEqual(r["pcs_mean"], 18 / 7)

    def test_whole_empty_and_refusal_prc_separate(self):
        for task, state in (("F-07", "EMPTY_OUTPUT"), ("F-08", "REFUSED")):
            r = self.record(task)
            self.assertEqual(r["processing_status"], state)
            self.assertEqual(r["pcs_mean"], 0)
            self.assertEqual(r["prc"]["status"], "NO_EVALUABLE_CONTENT")
            self.assertIsNone(r["prc"]["score"])
            self.assertEqual(r["model_requests"], 0)

    def test_structure_missing_and_parse_errors_not_zero(self):
        for task in ("F-06", "F-09", "F-10"):
            r = self.record(task)
            self.assertEqual(r["processing_status"], "TECHNICAL_FAILURE")
            self.assertIsNone(r["pcs_mean"])
            self.assertIsNone(r["prc"]["score"])
            self.assertTrue(all(v["score"] is None for v in r["pcs"].values()))

    def test_unresolved_search_miss_not_contradiction(self):
        r = self.record("F-05")
        self.assertEqual(r["processing_status"], "UNRESOLVED")
        self.assertIsNone(r["pcs"]["goal"]["score"])
        self.assertEqual(r["pcs"]["goal"]["checks"][0]["finding"], "UNRESOLVED")
        self.assertIsNone(r["pcs_mean"])
        self.assertIsNone(r["prc"]["score"])
        self.assertEqual(r["pcs"]["trigger"]["score"], 3)
        self.assertTrue(any(e["result"]["status"] == "NO_MATCH" for e in r["source_access"]))

    def test_partial_response_preserves_completed_items(self):
        r = self.record("F-11")
        self.assertIsNone(r["pcs_mean"])
        self.assertIsNone(r["pcs"]["trigger"]["score"])
        self.assertEqual(r["pcs"]["goal"]["score"], 3)
        self.assertEqual(r["prc"]["score"], 5)
        self.assertEqual(r["parse_status"], "INVALID_RESPONSE")

    def test_conflict_and_wrong_slot_have_locations(self):
        a, b = self.record("F-02"), self.record("F-03")
        self.assertEqual(a["pcs"]["preconditions"]["checks"][0]["finding"], "CONTRADICTED")
        self.assertTrue(a["prc"]["modifications"][0]["source_refs"])
        self.assertEqual(b["pcs"]["goal"]["checks"][0]["finding"], "WRONG_SLOT")
        self.assertEqual(b["prc"]["modifications"][0]["location"], "goal:0")

    def test_score_ranges_types_and_missing_items(self):
        for invalid in (-1, 4, True, 2.5, "3", None):
            data, a, accesses = self.response()
            data["pcs"]["goal"]["score"] = invalid
            result = evaluate_response(json.dumps(data), a, accesses)
            self.assertIsNone(result["pcs"]["goal"]["score"])
            self.assertEqual(result["parse_status"], "INVALID_RESPONSE")
        for invalid in (0, 6, False, "5", None):
            data, a, accesses = self.response()
            data["prc"]["score"] = invalid
            self.assertIsNone(evaluate_response(json.dumps(data), a, accesses)["prc"]["score"])
        data, a, accesses = self.response()
        del data["pcs"]["goal"]
        self.assertIsNone(evaluate_response(json.dumps(data), a, accesses)["pcs"]["goal"]["score"])

    def test_invalid_assertion_and_unread_source_references(self):
        for change in ("id", "page", "quote", "access"):
            data, a, accesses = self.response()
            check = data["pcs"]["goal"]["checks"][0]
            if change == "id":
                check["claim_id"] = "invented-id"
            elif change == "page":
                check["source_refs"][0]["pdf_page"] = 2
            elif change == "quote":
                check["source_refs"][0]["quote"] = "not on the original page"
            else:
                check["source_refs"][0]["access_id"] = "not-read"
            self.assertIsNone(evaluate_response(json.dumps(data), a, accesses)["pcs"]["goal"]["score"])

    def test_corrupt_duplicate_and_extra_scores(self):
        _, a, accesses = self.response()
        for raw in ("{", "[]", '{"pcs":{},"pcs":{},"prc":{}}', '{"pcs":NaN,"prc":{}}'):
            self.assertEqual(evaluate_response(raw, a, accesses)["parse_status"], "PARSE_FAILED")
        data, _, _ = self.response()
        data["pcs"]["actor"] = data["pcs"]["goal"]
        self.assertEqual(evaluate_response(json.dumps(data), a, accesses)["parse_status"], "PARSE_FAILED")

    def test_prc_requires_concrete_edits_and_is_independent(self):
        data, a, accesses = self.response()
        data["prc"]["modifications"] = []
        self.assertIsNone(evaluate_response(json.dumps(data), a, accesses)["prc"]["score"])
        data, a, accesses = self.response()
        data["prc"]["score"] = 2
        self.assertIsNone(evaluate_response(json.dumps(data), a, accesses)["prc"]["score"])
        # Equal PCS does not force equal PRC: conflict and wrong-slot have same PCS.
        self.assertEqual(self.record("F-02")["pcs_mean"], self.record("F-03")["pcs_mean"])
        self.assertNotEqual(self.record("F-02")["prc"]["score"], self.record("F-03")["prc"]["score"])

    def test_common_denominators_and_failure_counts(self):
        a, b = self.summary["groups"]
        self.assertEqual(a["counts"], {"planned": 11, "completed": 4, "empty_output": 1, "refusal": 1, "unresolved": 1, "technical_failure": 4})
        self.assertEqual(b["counts"]["technical_failure"], 5)
        self.assertEqual(a["available_pcs"]["denominator"], 6)
        self.assertEqual(a["available_prc"]["denominator"], 5)
        self.assertEqual(a["common_pcs"]["task_ids"], b["common_pcs"]["task_ids"])
        self.assertEqual(a["common_prc"]["task_ids"], b["common_prc"]["task_ids"])
        self.assertNotIn("F-02", a["common_pcs"]["task_ids"])
        self.assertIn("F-11", a["common_prc"]["task_ids"])
        self.assertEqual(len(a["all_target_statuses"]), 11)

    def test_predeclared_comparison_groups_are_not_pooled(self):
        from eval.l4_agent.export import summarize
        config = deepcopy(self.config)
        records = deepcopy(self.records)
        for task in config["targets"]:
            if task["task_id"] == "F-01":
                task["comparison_group"] = "DIAGNOSTIC"
        for record in records:
            if record["task_id"] == "F-01":
                record["comparison_group"] = "DIAGNOSTIC"
        groups = summarize(records, config)["groups"]
        self.assertEqual(len(groups), 4)
        for group in groups:
            self.assertEqual("F-01" in group["planned_task_ids"], group["comparison_group"] == "DIAGNOSTIC")

    def test_csv_and_json_agree(self):
        exported = self.run_dir / "exports"
        with (exported / "artifacts.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), len(self.records))
        for row, record in zip(rows, self.records):
            self.assertEqual(json.loads(row["pcs"]), record["pcs"])
            self.assertEqual(json.loads(row["prc"]), record["prc"])
            self.assertEqual(float(row["pcs_mean"]) if row["pcs_mean"] else None, record["pcs_mean"])
        with (exported / "summary.csv").open(encoding="utf-8-sig", newline="") as f:
            groups = list(csv.DictReader(f))
        for row, group in zip(groups, self.summary["groups"]):
            for key in ("counts", "available_pcs", "available_prc", "common_pcs", "common_prc", "all_target_statuses"):
                self.assertEqual(json.loads(row[key]), group[key])

    def test_live_gates_before_client_and_no_keys(self):
        c = deepcopy(self.config)
        with patch.dict("os.environ", {"AUCMR_API_KEY": "MUST_NOT_BE_USED"}):
            with self.assertRaises(L4Error):
                authorize(c, mock=False)
            c["live_authorized"] = True
            with self.assertRaises(L4Error):
                authorize(c, mock=False, explicit_live=True)
        self.assertEqual(self.client.calls, 26)
        c["data_class"] = "REAL_INPUT_STRUCTURE_ONLY"
        with self.assertRaises(L4Error):
            authorize(c, mock=True)

    def test_mock_label_cannot_use_real_paths(self):
        run = new_run("test-mock-boundary")
        c = make_fixtures(run)
        c["input_bindings"][0]["path"] = "outputs/not-a-fixture.json"
        freeze(c, run)
        with self.assertRaises(L4Error):
            evaluate_run(run, self.client, mock=True)
        self.assertEqual(self.client.calls, 26)

    def test_network_block_is_active(self):
        with self.assertRaisesRegex(L4Error, "NETWORK_BLOCKED"):
            socket.create_connection(("127.0.0.1", 1))
        with self.assertRaisesRegex(L4Error, "NETWORK_BLOCKED"):
            socket.getaddrinfo("example.invalid", 443)

    def test_input_and_version_drift_rejected(self):
        run = new_run("test-drift")
        c = make_fixtures(run)
        freeze(c, run)
        p = local_path(c["input_bindings"][0]["path"])
        original = p.read_bytes()
        p.write_bytes(original + b" ")
        with self.assertRaises(L4Error):
            assert_current(run)
        # Patch current protocol/prompt/rubric signatures without editing any source files.
        with patch("eval.l4_agent.inputs.versions", return_value={"prompt": "changed"}):
            with self.assertRaises(L4Error):
                assert_current(self.run_dir)
        changed = deepcopy(self.config)
        changed["model"]["config_id"] = "OTHER_MODEL"
        with self.assertRaises(ValueError):
            validate_record(self.record(), self.artifacts[self.record()["artifact_id"]], self.manifest, changed)

    def test_missing_matrix_and_per_method_policy_rejected(self):
        c = deepcopy(self.config)
        c["input_bindings"].pop()
        with self.assertRaises(L4Error):
            validate_config(c)
        c = deepcopy(self.config)
        c["source_policy"]["per_method"] = {}
        with self.assertRaises(L4Error):
            validate_config(c)

    def test_record_tampering_rejected(self):
        r = deepcopy(self.record())
        r["pcs"]["goal"]["score"] = 1
        with self.assertRaises(ValueError):
            validate_record(r, self.artifacts[r["artifact_id"]], self.manifest, self.config)
        r["record_sha256"] = digest({k: v for k, v in r.items() if k != "record_sha256"})
        with self.assertRaises(ValueError):
            validate_record(r, self.artifacts[r["artifact_id"]], self.manifest, self.config)

    def test_no_model_call_for_unbound_or_unauthorized_evaluate_run(self):
        run = new_run("test-live-gate")
        c = make_fixtures(run)
        freeze(c, run)
        client = unittest.mock.Mock()
        with self.assertRaises(L4Error):
            evaluate_run(run, client, mock=False)
        client.respond.assert_not_called()
        self.assertFalse((run / "records.json").exists())

    def test_source_hash_drift_and_snapshot_config_drift(self):
        run = new_run("test-source-drift")
        c = make_fixtures(run)
        freeze(c, run)
        source = local_path(c["sources"][0]["path"])
        with source.open("ab") as f:
            f.write(b"\n% synthetic drift regression\n")
        with self.assertRaises(L4Error):
            assert_current(run)
        run2 = new_run("test-model-drift")
        c2 = make_fixtures(run2)
        freeze(c2, run2)
        snapshot = run2 / "config.snapshot.json"
        c2["model"]["config_id"] = "MODIFIED"
        snapshot.write_text(json.dumps(c2), encoding="utf-8")
        with self.assertRaises(L4Error):
            assert_current(run2)

    def test_unreadable_bound_source_prevents_judge(self):
        run = new_run("test-missing-source")
        c = make_fixtures(run)
        c["sources"][0]["path"] = (run / "fixtures/not-created.pdf").relative_to(REPO).as_posix()
        freeze(c, run)
        client = FixtureClient(read_json(run / "fixtures/fixture_manifest.json")["scenarios"])
        records = evaluate_run(run, client, mock=True)
        self.assertEqual(client.calls, 0)
        r = next(r for r in records if r["task_id"] == "F-01")
        self.assertEqual(r["processing_status"], "TECHNICAL_FAILURE")
        self.assertEqual(r["pcs"]["goal"]["status"], "SOURCE_UNREADABLE")
        self.assertIsNone(r["pcs_mean"])
        self.assertIsNone(r["prc"]["score"])

    def test_malformed_raw_text_and_identity_are_not_rewritten(self):
        run = new_run("test-input-errors")
        c = make_fixtures(run)
        binding, task = c["input_bindings"][0], c["targets"][0]
        path = local_path(binding["path"])
        data = read_json(path)
        data["slots"]["goal"] = {"claims": [], "raw_text": 123}
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(adapt(binding, task, c["methods"])["state"], "STRUCTURE_ERROR")
        data["slots"]["goal"] = {"claims": [{"content": "Fixture-Method-A says save the file."}]}
        path.write_text(json.dumps(data), encoding="utf-8")
        a = adapt(binding, task, c["methods"])
        self.assertEqual(a["state"], "IDENTITY_EXPOSURE")
        self.assertEqual(a["slots"]["goal"]["claims"][0]["content"], "Fixture-Method-A says save the file.")

    def test_refusal_with_missing_slots_not_fabricated_as_zero(self):
        run = new_run("test-refusal-structure")
        c = make_fixtures(run)
        path = local_path(c["input_bindings"][0]["path"])
        data = read_json(path)
        data["quality_flags"] = [{"flag_type": "unresolvable_use_case"}]
        data["slots"] = {}
        path.write_text(json.dumps(data), encoding="utf-8")
        freeze(c, run)
        client = FixtureClient(read_json(run / "fixtures/fixture_manifest.json")["scenarios"])
        records = evaluate_run(run, client, mock=True)
        r = records[0]
        self.assertTrue(r["explicit_refusal"])
        self.assertEqual(r["artifact_state"], "STRUCTURE_ERROR")
        self.assertIsNone(r["pcs_mean"])
        self.assertEqual(r["prc"]["status"], "NO_EVALUABLE_CONTENT")
        self.assertTrue(all(v["score"] is None for v in r["pcs"].values()))

    def test_forged_rule_only_score_rejected_even_if_rehashed(self):
        r = deepcopy(self.record())
        r["parse_status"] = "RULE_ONLY"
        r["raw_response"] = None
        r["record_sha256"] = digest({k:v for k,v in r.items() if k != "record_sha256"})
        with self.assertRaises(ValueError):
            validate_record(r, self.artifacts[r["artifact_id"]], self.manifest, self.config)

    def test_model_request_limit_and_transport_failure(self):
        from eval.l4_agent.engine import _judge_one
        r = self.record()
        a = self.artifacts[r["artifact_id"]]
        task = self.config["targets"][0]
        class LoopClient:
            def respond(self, request, history):
                return json.dumps({"tool_calls": [{"name": "source_index", "arguments": {"source_id": "SRC-FIXTURE"}}]})
        result = _judge_one(a, task, self.manifest["sources"], self.config, self.manifest, new_run("test-loop"), LoopClient())
        self.assertEqual(result["parse_status"], "REQUEST_LIMIT")
        self.assertEqual(result["model_requests"], self.config["source_policy"]["request_rounds"])
        self.assertIsNone(result["pcs_mean"])
        client = unittest.mock.Mock()
        client.respond.side_effect = RuntimeError("a private SDK message")
        result = _judge_one(a, task, self.manifest["sources"], self.config, self.manifest, new_run("test-transport"), client)
        self.assertEqual(result["parse_status"], "CLIENT_OR_TOOL_ERROR")
        self.assertNotIn("private SDK", json.dumps(result))

    def test_l3_binding_compatibility_structural_only(self):
        l3 = read_json(REPO / "config/agent_evaluation_l3.json")
        l4 = read_json(REPO / "config/agent_evaluation_l4.json")
        cohort = read_json(REPO / "eval/agent_evaluation/design/cohort.json")
        self.assertEqual(l4["input_bindings"], [{"task_id": b["target_id"], "method": b["method"], "path": b["path"]} for b in l3["input_bindings"]])
        self.assertEqual(l4["methods"], cohort["methods"])
        self.assertEqual(l4["targets"], [{"task_id": t["target_id"], "comparison_group": t["planned_group"], **{k:t[k] for k in ("project_id", "name", "actor")}} for t in cohort["targets"]])
        self.assertNotIn("source_review_status", json.dumps(l4))


if __name__ == "__main__":
    unittest.main()
