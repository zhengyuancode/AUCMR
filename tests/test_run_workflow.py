import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import run


class RunWorkflowTests(unittest.TestCase):
    def test_load_config_sets_huggingface_endpoint_before_model_imports(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "experiment.json"
            path.write_text(json.dumps({
                "runtime": {"hf_endpoint": "https://hf-mirror.example/"},
            }), encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                _, loaded_path = run.load_config(str(path))
                self.assertEqual(loaded_path, path.resolve())
                self.assertEqual(os.environ["HF_ENDPOINT"], "https://hf-mirror.example")

    def _config(self, output: str, use_cases=None):
        return {
            "paths": {"output": output},
            "experiment": {
                "methods": ["LVLM-Direct"],
                "use_cases": use_cases or [
                    {"project": "jtvauctions", "actor": "Customer", "use_case": "Browse"},
                ],
            },
        }

    @staticmethod
    def _write_pair(root: Path, actor: str, use_case: str):
        target = root / "baselines" / "LVLM-Direct" / "jtvauctions"
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"{actor}_{use_case}.json"
        path.write_text(json.dumps({
            "method": "LVLM-Direct", "actor": actor, "use_case_name": use_case,
        }), encoding="utf-8")
        path.with_name(path.stem + "-evidence.json").write_text("{}", encoding="utf-8")

    def test_result_count_requires_paired_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self._config(str(root))
            target = root / "baselines" / "jtvauctions"
            target.mkdir(parents=True)
            result = target / "result.json"
            result.write_text(json.dumps({
                "method": "LVLM-Direct", "actor": "Customer", "use_case_name": "Browse",
            }), encoding="utf-8")
            self.assertEqual(run._result_counts(config, root)["LVLM-Direct"]["complete"], 0)
            result.with_name("result-evidence.json").write_text("{}", encoding="utf-8")
            self.assertEqual(run._result_counts(config, root)["LVLM-Direct"]["complete"], 1)

    def test_history_results_are_not_active_or_fresh(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self._config(str(root))
            target = root / "history" / "old-run" / "baselines" / "jtvauctions"
            target.mkdir(parents=True)
            result = target / "result.json"
            result.write_text(json.dumps({
                "method": "LVLM-Direct", "actor": "Customer", "use_case_name": "Browse",
            }), encoding="utf-8")
            result.with_name("result-evidence.json").write_text("{}", encoding="utf-8")
            self.assertEqual(run._result_counts(config, root)["LVLM-Direct"]["complete"], 0)
            self.assertEqual(run._latest_generation_mtime(root), 0.0)

    def test_archive_moves_results_and_preserves_baseline_caches(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "experiment.json"
            config = self._config(str(root))
            config_path.write_text(json.dumps(config), encoding="utf-8")
            self._write_pair(root, "Customer", "Browse")
            cache = root / "baselines" / "_composites" / "jtvauctions" / "pages.jpg"
            cache.parent.mkdir(parents=True)
            cache.write_bytes(b"cache")

            target = run.archive_outputs(config, config_path, "pre-v5")

            self.assertTrue((target / "baselines" / "LVLM-Direct" / "jtvauctions").is_dir())
            self.assertTrue((target / "archive_manifest.json").is_file())
            self.assertTrue(cache.is_file())
            self.assertEqual(run._result_counts(config, root)["LVLM-Direct"]["complete"], 0)

    def test_resume_runs_only_missing_baseline_use_case(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            use_cases = [
                {"project": "jtvauctions", "actor": "Customer", "use_case": "Browse"},
                {"project": "jtvauctions", "actor": "Manager", "use_case": "Inventory"},
            ]
            config = self._config(str(root), use_cases)
            self._write_pair(root, "Customer", "Browse")
            called = []

            class FakeBaseline:
                name = "LVLM-Direct"

                def run_and_save(self, use_case, actor, project, file_names):
                    called.append((project, actor, use_case))
                    return "created.json"

            with patch("eval.baselines.get_all_baselines", return_value=[FakeBaseline()]):
                run.run_generation(
                    config, root / "experiment.json", ["LVLM-Direct"], resume=True,
                )
            self.assertEqual(called, [("jtvauctions", "Manager", "Inventory")])

    def test_image_build_refuses_existing_collection(self):
        config = {
            "text_pipeline": {"documents": [{
                "project": "jtvauctions", "file_name": "jtvauctions",
                "page_images": "data/images/jtvauctions",
            }]},
        }
        with patch("run._collection_status", return_value={
            "reachable": True, "exists": True, "rows": 42,
        }):
            with self.assertRaisesRegex(RuntimeError, "Refusing to import"):
                run.run_image_build(config, Path("experiment.json"), ["jtvauctions"])

    def test_prepare_images_renders_every_pdf_page(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pdf = root / "two-pages.pdf"
            pdf.write_bytes(b"fake pdf for mocked renderer")
            config = {
                "text_pipeline": {"documents": [{
                    "project": "second_project",
                    "file_name": "second_project",
                    "pdf": str(pdf),
                    "page_images": str(root / "images"),
                    "parse_dir": str(root / "mineru"),
                }]},
            }

            class FakePdf:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return None

                def __len__(self):
                    return 2

            def render(_pdf, image_dir, expected):
                image_dir.mkdir(parents=True, exist_ok=True)
                for page in range(1, expected + 1):
                    (image_dir / f"{page}.png").write_bytes(b"png")

            fake_fitz = types.SimpleNamespace(open=lambda _path: FakePdf())
            with patch.dict(sys.modules, {"fitz": fake_fitz}), patch(
                "pipelines.text_index.ensure_page_images", side_effect=render,
            ):
                run.run_prepare_images(config, root / "experiment.json", ["second_project"])

            self.assertTrue((root / "images" / "1.png").is_file())
            self.assertTrue((root / "images" / "2.png").is_file())
            with patch.dict(sys.modules, {"fitz": fake_fitz}):
                status = run._page_image_status(
                    root / "experiment.json", config["text_pipeline"]["documents"][0],
                )
            self.assertTrue(status["complete"])
            self.assertEqual(status["expected_pages"], 2)

    def test_final_experiment_contains_two_projects_and_eighteen_candidates(self):
        config = json.loads((Path(run.__file__).parent / "experiment.json").read_text(encoding="utf-8"))
        use_cases = config["experiment"]["use_cases"]
        self.assertEqual(len(use_cases), 18)
        self.assertEqual(
            {item["project"] for item in use_cases},
            {"jtvauctions", "milstd6016b_sec4"},
        )
        self.assertEqual(
            sum(item["project"] == "milstd6016b_sec4" for item in use_cases), 9,
        )

    def test_latest_generation_marks_older_evaluation_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evaluation = root / "aggregated_metrics.json"
            evaluation.write_text("{}", encoding="utf-8")
            result = root / "baselines" / "jtvauctions" / "result.json"
            result.parent.mkdir(parents=True)
            result.write_text(json.dumps({"method": "LVLM-Direct"}), encoding="utf-8")
            evidence = result.with_name("result-evidence.json")
            evidence.write_text("{}", encoding="utf-8")
            old = result.stat().st_mtime - 10
            os.utime(evaluation, (old, old))
            self.assertGreater(run._latest_generation_mtime(root), evaluation.stat().st_mtime)


if __name__ == "__main__":
    unittest.main()
