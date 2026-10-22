"""File-driven batch checks with constructed inputs and no model/network access."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from scripts.prepare_evaluation_batch import prepare, output_path, encoded
from eval.agent_evaluation.common import EvaluationError, read_json
from eval.agent_evaluation.inventory import collect
from eval.agent_evaluation.transport import deny_network

REPO = Path(__file__).resolve().parents[1]


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        for name in ("config/agent_evaluation_l2_independent.json", "config/agent_evaluation_l3.json",
                     "config/agent_evaluation_l4.json", "eval/agent_evaluation/design/cohort.json"):
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, target)
        import fitz
        (self.repo / "data").mkdir()
        with fitz.open() as pdf:
            pdf.new_page().insert_text((30, 30), "CONSTRUCTED SOURCE NOT RESEARCH DATA")
            pdf.save(self.repo / "data/source.pdf")

    def inputs(self, count=1, results=True):
        cases = [{"project": "constructed", "actor": "Operator", "use_case": "Action " + str(i)} for i in range(count)]
        config = {"paths": {"output": "outputs/constructed-new-batch"},
                  "experiment": {"use_cases": cases}, "text_pipeline": {"documents": [
                      {"project": "constructed", "file_name": "constructed", "pdf": "data/source.pdf"}]}}
        (self.repo / "generation.json").write_bytes(encoded(config))
        methods = read_json(self.repo / "config/agent_evaluation_l4.json")["methods"]
        if results:
            for case in cases:
                for method in methods:
                    path = self.repo / output_path(config["paths"]["output"], case, method)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(encoded({"actor": case["actor"], "use_case_name": case["use_case"],
                        "slots": {s: {"claims": []} for s in ("goal", "preconditions", "trigger", "main_flow",
                                                             "alternative_flow", "exception_flow", "postconditions")}}))
                    path.with_name(path.stem + "-evidence.json").write_bytes(b"{}")
        return cases

    def build(self, dest="config/batch", **kw):
        with deny_network():
            return prepare(self.repo, "generation.json", dest, **kw)

    def inspect(self, folder="config/batch"):
        with deny_network(), patch("eval.agent_evaluation.inventory.REPO", self.repo):
            return collect(read_json(self.repo / folder / "l3.json"))

    def test_arbitrary_count_and_new_document_loads_without_legacy_inputs(self):
        for count in (1, 3):
            self.inputs(count)
            folder = "config/batch" + str(count)
            result = self.build(folder)
            manifest = self.inspect(folder)
            self.assertEqual(result["registered_pairs"], count * 7)
            self.assertEqual(manifest["summary"]["registered_pairs"], count * 7)
            self.assertEqual(len(manifest["sources"]), 1)
            self.assertEqual(manifest["input_drift"], [])
            self.assertTrue(all(a["state"] == "VALID_EMPTY" for a in manifest["artifacts"]))

    def test_draft_preserves_missing_and_cannot_be_scored(self):
        self.inputs(results=False)
        with self.assertRaisesRegex(ValueError, "Incomplete batch"):
            self.build()
        self.assertFalse((self.repo / "config/batch").exists())
        result = self.build(draft=True)
        self.assertEqual(len(result["missing_results"]), 7)
        with self.assertRaisesRegex(EvaluationError, "Draft batch"):
            self.inspect()

    def test_output_drift_blocks_resume(self):
        self.inputs()
        self.build()
        config = read_json(self.repo / "config/batch/l3.json")
        path = self.repo / config["input_bindings"][0]["path"]
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(EvaluationError, "Frozen output changed"):
            self.inspect()

    def test_missing_outputs_can_be_explicitly_registered_without_dropping_pairs(self):
        self.inputs(results=False)
        result = self.build(allow_missing=True)
        manifest = self.inspect()
        self.assertTrue(result["missing_explicitly_registered"])
        self.assertEqual(manifest["summary"]["registered_pairs"], 7)
        self.assertTrue(all(a["state"] == "MISSING_FILE" for a in manifest["artifacts"]))

    def test_source_and_batch_drift_are_rejected(self):
        self.inputs()
        self.build()
        source = self.repo / "data/source.pdf"
        original = source.read_bytes()
        source.write_bytes(original + b"\n")
        with self.assertRaisesRegex(EvaluationError, "Frozen source changed"):
            self.inspect()
        source.write_bytes(original)
        batch = self.repo / "config/batch/batch.json"
        batch.write_bytes(batch.read_bytes() + b"\n")
        with self.assertRaisesRegex(EvaluationError, "External batch missing or changed"):
            self.inspect()

    def test_l2_l4_share_the_same_frozen_outputs(self):
        from eval.l2_independent.inputs import collect as collect_l2
        from eval.l4_agent.inputs import collect as collect_l4
        from eval.l4_agent.protocol import L4Error
        self.inputs()
        self.build()
        l2 = read_json(self.repo / "config/batch/l2.json")
        l4 = read_json(self.repo / "config/batch/l4.json")
        with deny_network(), patch("eval.l4_agent.inputs.REPO", self.repo):
            first = collect_l4(l4)
            second = collect_l2(l2, self.repo)
            self.assertEqual([a["input_sha256"] for a in first["artifacts"]], [a["input_hash"] for a in second])
            path = self.repo / l4["input_bindings"][0]["path"]
            path.write_bytes(path.read_bytes() + b"\n")
            with self.assertRaisesRegex(L4Error, "Frozen batch output changed"):
                collect_l4(l4)

    def test_task_changes_create_new_batch_and_never_overwrite(self):
        self.inputs()
        self.build()
        frozen = (self.repo / "config/batch/batch.json").read_bytes()
        self.inputs(2)
        with self.assertRaisesRegex(ValueError, "new destination"):
            self.build()
        self.build("config/second")
        self.assertEqual((self.repo / "config/batch/batch.json").read_bytes(), frozen)
        self.assertEqual(self.inspect("config/second")["summary"]["registered_pairs"], 14)

    def test_duplicate_tasks_and_wrong_identity_fail_before_writes(self):
        self.inputs()
        path = self.repo / "generation.json"
        config = read_json(path)
        config["experiment"]["use_cases"] *= 2
        path.write_bytes(encoded(config))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.build()
        cases = self.inputs()
        output = self.repo / output_path("outputs/constructed-new-batch", cases[0], "AUCMR")
        output.write_bytes(encoded({"actor": "Wrong", "use_case_name": cases[0]["use_case"]}))
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.build()


if __name__ == "__main__":
    unittest.main()
