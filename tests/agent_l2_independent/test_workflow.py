import contextlib
import copy
import csv
import io
import json
import unittest
from pathlib import Path
from uuid import uuid4

from eval.l2_independent.__main__ import main
from eval.l2_independent.bindings import prepare_inputs
from eval.l2_independent.common import (L2Error, REPO, digest, file_hash, new_run, read_json, write_json)
from eval.l2_independent.evaluation import evaluate, MockClient
from eval.l2_independent.fixtures import fixture_cases
from eval.l2_independent.inputs import collect, load_config, validate_config
from eval.l2_independent.integration import check_l3_alignment
from eval.l2_independent.offline import offline_guard
from eval.l2_independent.reporting import export_summary, summarize
from eval.l2_independent.runner import reexport, run_mock


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = new_run("verify") / "fixtures"
        cls.workspace.mkdir()
        write_json(cls.workspace / "marker.json", {"data_origin": "FIXTURE", "research_data_status": "NOT_RESEARCH_DATA"})

    def setUp(self):
        self.guard = offline_guard()
        self.guard.__enter__()
        self.addCleanup(self.guard.__exit__, None, None, None)
        self.config, self.cases = fixture_cases()
        self.records = [evaluate(c["artifact"], self.config, MockClient(c["response"], self.config["model"]), "test")
                        for c in self.cases]

    def test_counts_and_json_csv_agree(self):
        destination = self.workspace / ("export-" + uuid4().hex)
        result = export_summary(destination, self.records, self.config)
        row = result["per_document_method"][0]
        for key, value in {"expected_target_count": 13, "completed_checks": 3,
                           "complete_with_conflicts": 1, "complete_no_conflicts": 2,
                           "partial_or_unresolved": 2, "technical_failures": 6,
                           "no_evaluable_content": 2, "missing_files": 1,
                           "explicit_refusals": 1, "valid_empty_outputs": 1,
                           "confirmed_conflicts_completed": 1, "confirmed_conflicts_partial": 1,
                           "common_completed_target_count": 3}.items():
            self.assertEqual(row[key], value, key)
        self.assertEqual(row["comparison_status"], "INCOMPLETE_NO_RANKING")
        for name in ("per_artifact", "per_document_method", "per_document_method_group"):
            jrows = read_json(destination / (name + ".json"))
            with (destination / (name + ".csv")).open(encoding="utf-8", newline="") as stream:
                crows = list(csv.DictReader(stream))
            self.assertEqual(len(jrows), len(crows))
            for jrow, crow in zip(jrows, crows):
                self.assertEqual(set(jrow), set(crow))
                for key, value in jrow.items():
                    if isinstance(value, (dict, list)):
                        self.assertEqual(json.loads(crow[key]), value)
                    else:
                        self.assertEqual(crow[key], "" if value is None else str(value))
        self.assertEqual(read_json(destination / "summary.json"), result)

    def test_common_targets_not_per_method_cherry_picking(self):
        config = copy.deepcopy(self.config)
        config["targets"] = config["targets"][:3]
        config["methods"] = ["FIXTURE_METHOD", "OTHER_FIXTURE"]
        records = copy.deepcopy(self.records[:3])
        for n, case in enumerate(self.cases[:3]):
            a = copy.deepcopy(case["artifact"])
            a.update(method="OTHER_FIXTURE", artifact_id="OTHER-" + str(n))
            response = {"review_status": "COMPLETE", "conflicts": [], "unresolved": []}
            if n == 1:
                response = self.cases[3]["response"]
            if n == 2:
                a.update(input_status="MISSING_FILE", input_hash=None, assertions=[], empty_slots=[])
            records.append(evaluate(a, config, MockClient(response, config["model"]), "test"))
        result = summarize(records, config)
        for row in result["per_document_method"]:
            self.assertEqual(row["expected_target_count"], 3)
            self.assertEqual(row["common_completed_target_ids"], ["F01"])
            self.assertEqual(row["common_completed_target_count"], 1)
        with self.assertRaises(L2Error):
            summarize(records[:-1], config)
        with self.assertRaises(L2Error):
            summarize(records + records[:1], config)

    def test_partial_cannot_masquerade_as_complete(self):
        altered = copy.deepcopy(self.records)
        altered[3]["status"] = "COMPLETE_NO_CONFLICTS"
        with self.assertRaises(L2Error):
            summarize(altered, self.config)
        altered = copy.deepcopy(self.records)
        altered[7]["confirmed_conflict_count"] = 0
        with self.assertRaises(L2Error):
            summarize(altered, self.config)

    def test_batches_models_versions_cannot_be_mixed(self):
        for key in ("input_batch_id", "model_config_id", "protocol_version", "prompt_version", "cohort_group"):
            records = copy.deepcopy(self.records)
            records[0][key] = "OTHER"
            with self.assertRaises(L2Error):
                summarize(records, self.config)
        for state in ("COMPLETE_NO_CONFLICTS", "COMPLETE_WITH_CONFLICTS"):
            records = copy.deepcopy(self.records)
            records[4]["status"] = state
            records[4]["confirmed_conflict_count"] = 0
            records[4]["parse_status"] = "VALID"
            with self.assertRaises(L2Error):
                summarize(records, self.config)

    def test_no_mock_export_outside_fixture_directory(self):
        with self.assertRaises(L2Error):
            export_summary(self.workspace.parent / "formal", self.records, self.config)

    def test_end_to_end_cli_and_non_overwrite(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(["mock"])
        self.assertEqual(exit_code, 0)
        result = json.loads(output.getvalue())
        source = Path(result["run_dir"])
        manifest = read_json(source / "run_manifest.json")
        self.assertEqual(manifest["network_audit"]["network_attempts_blocked"], 0)
        self.assertEqual(manifest["real_model_calls"], 0)
        self.assertEqual(manifest["real_semantic_evaluation"], "NOT_RUN")
        destination = Path(reexport(source)["run_dir"])
        self.assertNotEqual(source, destination)
        self.assertEqual(read_json(source / "fixtures/export/summary.json"),
                         read_json(destination / "fixtures/export/summary.json"))
        with self.assertRaises(FileExistsError):
            write_json(source / "run_manifest.json", {"overwrite": True})

    def test_tampered_saved_run_is_not_reexported(self):
        source = Path(run_mock()["run_dir"])
        p = source / "fixtures" / "artifacts.json"
        p.write_text("[]", encoding="utf-8")  # only this test's freshly created fixture run
        with self.assertRaises(L2Error):
            reexport(source)

    def test_real_inventory_cannot_be_exported_as_scores(self):
        source = new_run("inventory")
        write_json(source / "run_manifest.json", {"kind": "INVENTORY", "data_origin": "REAL_INPUT_STRUCTURE_ONLY"})
        with self.assertRaises(L2Error):
            reexport(source)

    def test_real_local_contract_read_only_alignment(self):
        prepared = prepare_inputs(REPO / "config/agent_evaluation_l2_independent.json", repo=REPO)
        config = load_config(prepared["config"])
        report = check_l3_alignment(config, REPO)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(len(report["joins"]), len(config["input_bindings"]))
        self.assertFalse(report["l3_code_imported"])
        self.assertEqual(report["semantic_result_sharing"], "NONE")
        bad = copy.deepcopy(config)
        bad["input_bindings"][0]["sha256"] = "a" * 64
        with self.assertRaises(L2Error):
            check_l3_alignment(bad, REPO)

    def test_bindings_hash_missing_parse_and_structure_distinct(self):
        # All writes and mocked inputs stay in a new FIXTURE directory, never a real output.
        root = self.workspace / ("input-test-" + uuid4().hex)
        path = root / "outputs/example.json"
        data = {"use_case_name": "X", "actor": "User", "slots": {}}
        write_json(path, data)
        config = {
            "protocol_version": self.config["protocol_version"], "prompt_version": self.config["prompt_version"],
            "model": self.config["model"], "live_authorized": False, "data_origin": "REAL",
            "input_batch_id": "SYNTHETIC_BINDING_TEST", "batch_provenance_status": "NOT_RESEARCH_DATA",
            "methods": ["M"], "targets": [{"task_id": "T", "source_project": "P", "name": "X", "primary_actor": "User"}],
            "input_bindings": [{"task_id": "T", "method": "M", "path": "outputs/example.json", "sha256": file_hash(path)}],
        }
        self.assertEqual(collect(config, root)[0]["input_status"], "STRUCTURE_ERROR")
        path.write_bytes(b"{")
        artifact = collect(config, root)[0]
        self.assertEqual(artifact["input_status"], "PARSE_FAILED")
        self.assertFalse(artifact["hash_matches_binding"])
        config["input_bindings"][0]["path"] = "outputs/absent.json"
        config["input_bindings"][0]["sha256"] = None
        self.assertEqual(collect(config, root)[0]["input_status"], "MISSING_FILE")
        config["input_bindings"][0]["path"] = "outputs/../outside.json"
        with self.assertRaises(L2Error):
            collect(config, root)
        bad = copy.deepcopy(config)
        bad["methods"].append("missing-method")
        with self.assertRaises(L2Error):
            validate_config(bad)


if __name__ == "__main__":
    unittest.main()
