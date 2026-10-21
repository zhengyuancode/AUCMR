"""Runtime/CLI tests use constructed files, fake transport, and an isolated ledger only."""
import contextlib
import copy
import io
import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from eval.l2_independent.__main__ import main
from eval.l2_independent.bindings import prepare_inputs
from eval.l2_independent.common import L2Error, REPO, SLOTS, file_hash, new_run, read_json, write_json
from eval.l2_independent.execution import export_run, run_batch
from eval.l2_independent.integration import check_l3_alignment
from eval.l2_independent.offline import offline_guard
from eval.l2_independent.reporting import summarize


class FakeTransport:
    mode = "mock"

    def __init__(self, *, bad_json=False, unknown_usage=False):
        self.calls = []
        self.bad_json = bad_json
        self.unknown_usage = unknown_usage

    def request(self, binding, messages, task, attempt):
        self.calls.append(copy.deepcopy({"messages": messages, "task": task, "attempt": attempt}))
        body = {"review_status": "COMPLETE", "conflicts": [], "unresolved": []}
        if self.bad_json and len(self.calls) == 1:
            body = "{bad json"
        return {"id": "fake-" + str(len(self.calls)), "model": "synthetic-judge",
                "choices": [{"message": {"content": body}, "finish_reason": "stop"}],
                "usage": {} if self.unknown_usage else {"prompt_tokens": 30, "completion_tokens": 20}}


class RuntimeWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = new_run("verify") / "fixtures/runtime"
        cls.base.mkdir(parents=True)

    def setUp(self):
        self.guard = offline_guard()
        self.guard.__enter__()
        self.addCleanup(self.guard.__exit__, None, None, None)
        self.repo = self.base / uuid4().hex
        self.repo.mkdir()
        self.transport = FakeTransport()
        targets = [{"task_id": f"T{i}", "source_project": "SYNTHETIC", "name": f"Save record {i}",
                    "primary_actor": "Operator", "cohort_group": "MAIN"} for i in (1, 2)]
        bindings = []
        for target in targets:
            data = {"use_case_name": target["name"], "actor": "Operator",
                    "slots": {slot: {"claims": []} for slot in SLOTS}}
            data["slots"]["goal"]["claims"] = [{"content": "Save a record."}]
            data["slots"]["main_flow"]["claims"] = [{"content": "The system saves the record."}]
            relative = "outputs/synthetic/" + target["task_id"] + ".json"
            write_json(self.repo / relative, data)
            bindings.append({"task_id": target["task_id"], "method": "SYNTHETIC_METHOD",
                             "path": relative, "sha256": file_hash(self.repo / relative)})
        self.config = {
            "protocol_version": "AUCMR_L2_INDEPENDENT_1.0.0",
            "prompt_version": "whole_uc_relations_1.0.0",
            "data_origin": "FIXTURE", "live_authorized": False,
            "input_batch_id": "SYNTHETIC_RUNTIME_TEST", "batch_provenance_status": "NOT_RESEARCH_DATA",
            "targets": targets, "methods": ["SYNTHETIC_METHOD"], "input_bindings": bindings,
            "l3_alignment": {"manifest": {"path": "outputs/missing-windows-history.json", "sha256": "0" * 64}},
            "model": {"binding_status": "BOUND", "provider": "fixture", "model_id": "synthetic-judge",
                      "model_revision": "fixture-v1", "base_url": "https://invalid.example/v1",
                      "api_key_env": "UNUSED_FIXTURE_KEY", "parameters": {"max_tokens": 512, "temperature": 0},
                      "capabilities": {"images": False},
                      "billing": {"complete": True, "verified_upper_bound": True,
                                  "all_charges_in_token_usage": True, "input_cny_per_million": 1,
                                  "output_cny_per_million": 2, "reasoning_in_output": True},
                      "per_request_upper_bound": {"input": 0.1, "output": 0.1, "reasoning": 0, "images": 0, "other": 0}}}
        self.original = self.repo / "config.json"
        write_json(self.original, self.config)
        prepared = prepare_inputs(self.original, repo=self.repo, allow_fixture=True)
        self.bound = Path(prepared["config"])
        self.ledger = self.repo / "outputs/agent_l2_independent/fixtures/budget.sqlite"

    def run_args(self, **extra):
        return {"repo": self.repo, "transport": self.transport,
                "campaign_ledger": self.ledger, **extra}

    def cli(self, argv, transport=None):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = main(argv, repo=self.repo, transport=transport or self.transport)
        return code, json.loads(stream.getvalue())

    def test_cli_prepare_run_resume_and_export(self):
        code, prepared = self.cli(["prepare-inputs", "--config", str(self.original)])
        self.assertEqual(code, 0)
        argv = ["run", "--config", prepared["config"], "--campaign-ledger", str(self.ledger)]
        code, result = self.cli(argv)
        self.assertEqual(code, 0)
        self.assertEqual(result["completed_checks"], 2)
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual(result["research_data_status"], "NOT_RESEARCH_DATA")
        code, resumed = self.cli(argv + ["--resume", result["run_dir"]])
        self.assertEqual(code, 0)
        self.assertEqual(resumed["reused_records"], 2)
        self.assertEqual(len(self.transport.calls), 2)
        code, exported = self.cli(["export", "--run-dir", result["run_dir"]])
        self.assertEqual(code, 0)
        summary = read_json(Path(exported["tables"]) / "summary.json")
        self.assertEqual(summary["per_document_method"][0]["completed_check_denominator"], 2)
        self.assertTrue(all(r["research_data_status"] == "NOT_RESEARCH_DATA" for r in summary["per_artifact"]))
        with contextlib.closing(sqlite3.connect(self.ledger)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM reservations").fetchone()[0], 2)

    def test_server_binding_keeps_history_and_ignores_model_only_changes(self):
        config = read_json(self.bound)
        self.assertEqual(config["l3_alignment"], self.config["l3_alignment"])
        self.assertFalse(config["live_authorized"])
        config["model"]["model_revision"] = "new-explicit-binding"
        self.assertEqual(check_l3_alignment(config, self.repo)["status"], "PASS")
        config["input_bindings"][0]["sha256"] = "a" * 64
        with self.assertRaises(L2Error):
            check_l3_alignment(config, self.repo)

    def test_imported_manifest_and_local_content_must_match(self):
        manifest = self.repo / "imported.json"
        altered = copy.deepcopy(self.config["input_bindings"])
        altered[0]["sha256"] = "a" * 64
        write_json(manifest, {"bindings": altered})
        with self.assertRaises(L2Error):
            prepare_inputs(self.original, repo=self.repo, manifest_path=manifest, allow_fixture=True)
        file = self.repo / self.config["input_bindings"][0]["path"]
        file.write_text("{}", encoding="utf-8")
        with self.assertRaises(L2Error):
            run_batch(self.bound, **self.run_args())
        self.assertEqual(len(self.transport.calls), 0)

    def test_real_inputs_cannot_use_mock_or_missing_authorization(self):
        config = read_json(self.bound)
        config["data_origin"] = "REAL"
        real_config = self.repo / "synthetic-real-contract.json"
        write_json(real_config, config)
        with self.assertRaises(L2Error):
            run_batch(real_config, **self.run_args())
        with self.assertRaises(L2Error):
            run_batch(real_config, repo=self.repo, authorize_live=True, campaign_ledger=self.ledger)
        self.assertEqual(len(self.transport.calls), 0)

    def test_interrupted_paid_response_is_recovered_without_second_payment(self):
        from eval.l2_independent import execution
        original = execution._sealed_write
        saved = 0
        def interrupt_second_record(path, value):
            nonlocal saved
            if Path(path).name == "record.json":
                saved += 1
                if saved == 2:
                    raise KeyboardInterrupt()
            return original(path, value)
        with patch.object(execution, "_sealed_write", side_effect=interrupt_second_record):
            result = run_batch(self.bound, **self.run_args())
        self.assertEqual(result["status"], "INTERRUPTED")
        self.assertEqual(result["saved_records"], 1)
        self.assertEqual(len(self.transport.calls), 2)
        resumed = run_batch(self.bound, **self.run_args(resume=result["run_dir"]))
        self.assertEqual(resumed["status"], "PASS")
        self.assertEqual(resumed["reused_records"], 1)
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual({p.parent.name for p in Path(result["run_dir"]).glob("attempts/*/*/record.json")}, {"0001"})
        with contextlib.closing(sqlite3.connect(self.ledger)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM reservations").fetchone()[0], 2)

    def test_paid_bad_response_stays_failed_and_is_not_retried_for_score(self):
        self.transport.bad_json = True
        result = run_batch(self.bound, **self.run_args())
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["technical_failures"], 1)
        resumed = run_batch(self.bound, **self.run_args(resume=result["run_dir"]))
        self.assertEqual(resumed["technical_failures"], 1)
        self.assertEqual(len(self.transport.calls), 2)
        exported = export_run(result["run_dir"], repo=self.repo)
        rows = read_json(Path(exported["tables"]) / "per_artifact.json")
        failed = next(r for r in rows if r["status"] == "TECHNICAL_FAILURE")
        self.assertIsNone(failed["confirmed_conflict_count"])
        attempts = list(Path(result["run_dir"]).glob("attempts/*/*/record.json"))
        receipt = next(read_json(p)["value"] for p in attempts
                       if read_json(p)["value"]["status"] == "TECHNICAL_FAILURE")
        self.assertGreater(receipt["runtime_error_metadata"]["cost_cny"], 0)

    def test_unknown_usage_stops_batch_and_retains_pending_denominator(self):
        self.transport.unknown_usage = True
        result = run_batch(self.bound, **self.run_args())
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(len(self.transport.calls), 1)
        exported = export_run(result["run_dir"], repo=self.repo)
        summary = read_json(Path(exported["tables"]) / "summary.json")
        row = summary["per_document_method"][0]
        self.assertEqual(row["expected_target_count"], 2)
        self.assertEqual(row["technical_failures"], 2)
        self.assertEqual(row["completed_check_denominator"], 0)
        self.assertEqual(row["common_completed_target_count"], 0)

    def test_changed_saved_record_or_resume_policy_is_rejected(self):
        result = run_batch(self.bound, **self.run_args())
        with self.assertRaises(L2Error):
            run_batch(self.bound, **self.run_args(resume=result["run_dir"], max_attempts=3))
        record = next(Path(result["run_dir"]).glob("attempts/*/*/record.json"))
        body = read_json(record)
        body["value"]["status"] = "TECHNICAL_FAILURE"
        record.write_text(json.dumps(body), encoding="utf-8")
        with self.assertRaises(L2Error):
            export_run(result["run_dir"], repo=self.repo)
        self.assertEqual(len(self.transport.calls), 2)

    def test_read_failed_is_exported_as_technical_failure(self):
        result = run_batch(self.bound, **self.run_args())
        records = [read_json(p)["value"] for p in sorted(Path(result["run_dir"]).glob("attempts/*/*/record.json"))]
        config = read_json(self.bound)
        config["data_origin"] = "REAL"
        for record in records:
            record.update(data_origin="REAL", evaluation_mode="LIVE", research_data_status="LIVE_UNVALIDATED")
        records[0].update(input_status="READ_FAILED", input_hash=None, status="TECHNICAL_FAILURE",
                          parse_status="NOT_CALLED", conflicts=[], unresolved=[], unresolved_count=0,
                          confirmed_conflict_count=None, assertion_count_is_complete=False)
        summary = summarize(records, config)
        self.assertEqual(summary["per_document_method"][0]["technical_failures"], 1)
        self.assertEqual(summary["per_document_method"][0]["completed_check_denominator"], 1)


if __name__ == "__main__":
    unittest.main()
