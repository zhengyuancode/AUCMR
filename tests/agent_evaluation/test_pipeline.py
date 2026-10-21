"""Integration evidence for source isolation, C review, stability, resume and budgets."""
import copy
import tempfile
import unittest
from pathlib import Path

from eval.agent_evaluation.common import DESIGN, EvaluationError, digest, read_json, read_jsonl
from eval.agent_evaluation.fixtures import FixtureScript, fixture_manifest
from eval.agent_evaluation.orchestrator import Orchestrator
from eval.agent_evaluation.transport import MockTransport, deny_network


class PipelineTests(unittest.TestCase):
    def test_imports_and_literal_credentials_fail_closed(self):
        import sys
        import ast
        from eval.agent_evaluation.common import PACKAGE, write_json
        from eval.agent_evaluation.inventory import inventory
        for path in PACKAGE.rglob("*.py"):
            if "design" not in path.parts:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 10))
        for name in ("torch", "transformers", "config.settings", "services.model_service", "pymilvus"):
            self.assertNotIn(name, sys.modules)
        with tempfile.TemporaryDirectory() as temp:
            config = read_json(DESIGN/"config.json")
            config["models"]["A"]["api_key"] = "CONSTRUCTED_NOT_A_REAL_KEY"
            path = Path(temp)/"config.json"
            write_json(path, config)
            with deny_network(), self.assertRaises(EvaluationError):
                inventory(path, Path(temp)/"run")
            self.assertFalse((Path(temp)/"run/config.snapshot.json").exists())

    def test_all_result_directions_export_without_filtering(self):
        from eval.agent_evaluation.aggregate import aggregate
        from eval.agent_evaluation.export import export_tables
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/"fixtures/export-directions"
            manifest, specs = fixture_manifest(root, ids=["C01"], methods=["AUCMR", "ReAct-Elaborate"])
            provenance = {k: "fixture" for k in ("protocol_version", "run_id", "input_manifest_hash", "cohort_hash", "source_hashes",
                "output_hashes", "prompt_hash", "model_binding_hash", "reference_versions", "report_scope", "semantic_status")}
            provenance["transport"] = "mock"
            result = aggregate(manifest, {}, {}, {})
            directions = ["SOURCE_OR_TECHNICALLY_INCOMPLETE", "RESULT_UNCERTAIN", "MIXED_OR_NO_ADVANTAGE",
                          "DESCRIPTIVE_ADVANTAGE_ON_OBSERVED_SET"]
            for direction in directions:
                candidate = copy.deepcopy(result)
                candidate["paired_comparisons"][0]["interpretation"] = direction
                destination = root/direction
                export_tables(destination, candidate, provenance, fixture=True)
                saved = read_json(destination/"paper_values.json")
                self.assertEqual(saved["paired_comparisons"][0]["interpretation"], direction)
                self.assertEqual(len(saved["content_table"]), 2)
                self.assertIsNone(saved["content_table"][0]["cp_confirmed"])
                self.assertIn("--", (destination/"table_l3_content.tex").read_text(encoding="utf-8"))

    def test_reference_tamper_and_live_fail_closed(self):
        from eval.agent_evaluation.references import verify_frozen
        from eval.agent_evaluation.common import Blocked
        from eval.agent_evaluation.transport import LiveTransport
        with self.assertRaises(EvaluationError):
            verify_frozen({"revision": 1, "scenario_version": "v1-fake", "scope_card": {}})
        with deny_network(), self.assertRaises(Blocked):
            LiveTransport(read_json(DESIGN/"config.json"))

    def test_source_to_export_resume_and_stability(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/"fixtures/integration"
            methods = read_json(DESIGN/"cohort.json")["methods"]
            manifest, specs = fixture_manifest(root, ids=["C01", "B07"], methods=methods)
            script = FixtureScript(specs, manifest["sources"])
            def scripted(task, messages, attempt):
                result = script(task, messages, attempt)
                if task["role"].startswith("STABILITY") and "records" in result:
                    for row in result["records"]:
                        if row["kind"] == "content":
                            row["payload"].update(label="UNRESOLVED", reasons=["PARTIAL_SUPPORT"])
                return result
            config = read_json(DESIGN/"config.json")
            transport = MockTransport(scripted)
            with deny_network():
                first = Orchestrator(root, config, manifest, transport)
                results = first.run(["C01", "B07"])
                before = len(transport.calls)
                again = Orchestrator(root, config, manifest, transport).run(["C01", "B07"])
            self.assertEqual(len(transport.calls), before)
            self.assertEqual(digest(results), digest(again))
            stability = read_jsonl(root/"stability.jsonl")
            self.assertTrue(stability)
            self.assertEqual({r["role_id"] for r in stability}, {"STABILITY_A", "STABILITY_B"})
            self.assertTrue(read_jsonl(root/"reviews_C.jsonl"))
            reconciled = read_json(root/"decisions_RECONCILED.json")
            for a in manifest["artifacts"]:
                if a["target_id"] == "C01":
                    self.assertEqual(next(iter(reconciled["CONTENT"][a["artifact_id"]].values()))["label"], "KEEP")
            tasks = read_jsonl(root/"task_inputs.jsonl")
            for task in tasks:
                material = task["envelope"]["materials"]
                role = task["task_id"]
                if "/source/" in role:
                    self.assertNotIn("artifact", material)
                if "c-blind" in role or "stability" in role:
                    self.assertNotIn("anonymous_first_decisions", material)
                if "c-final" in role:
                    self.assertIn("anonymous_first_decisions", material)
            altered = copy.deepcopy(config)
            altered["models"]["A"]["api_model_id"] = "changed-model"
            with self.assertRaises(EvaluationError):
                Orchestrator(root, altered, manifest, transport).run(["C01", "B07"])

    def test_query_page_and_request_limits(self):
        from eval.agent_evaluation.source_access import SourceAccess
        from eval.agent_evaluation.common import Blocked
        from eval.agent_evaluation.agents.executor import Executor
        from eval.agent_evaluation.budget import Budget
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/"fixtures/limits"
            manifest, _ = fixture_manifest(root, ids=["B08"], methods=["AUCMR"])
            source = SourceAccess(manifest["sources"], "B08", "SCENE_BUILD_A", root/"access.jsonl", search_max=1, pages_max=1)
            source.search("FIX-B08", "identity")
            with self.assertRaises(Blocked):
                source.search("FIX-B08", "next query")
            source.page("FIX-B08", 1)
            with self.assertRaises(Blocked):
                source.page("FIX-B08", 2)
            config = read_json(DESIGN/"config.json")
            config["judging"]["max_requests_per_task"] = 2
            transport = MockTransport(lambda *args: {"tool_calls": [{"name": "source_page", "arguments": {"source_id": "FIX-B08", "pdf_page": 1}}]})
            executor = Executor(root, config, transport, Budget(root/"budget.sqlite"))
            with deny_network(), self.assertRaises(Blocked):
                executor.execute(task_id="loop", role="CONTENT_A", view="A_FIRST", kinds=["content"],
                    materials={}, source=source, validate=lambda *args: None)
            self.assertEqual(len(transport.calls), 2)
