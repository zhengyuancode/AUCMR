"""Run the real L4 runner through the shared runtime with a disconnected transport."""
import base64
import json
import unittest
from copy import deepcopy
from unittest.mock import patch

from eval.l4_agent import engine
from eval.l4_agent.engine import validate_run
from eval.l4_agent.fixtures import FixtureClient, make_fixtures
from eval.l4_agent.protocol import L4Error, REPO, network_blocked, new_run, read_json, save_new
from eval.l4_agent.runner import run_evaluation


def fixture_binding():
    return {"config_id": "L4_TRANSPORT_FIXTURE_V1", "binding_status": "BOUND",
            "provider": "offline-fixture", "model_id": "fixture-model", "supports_images": True,
            "capabilities": {"images": True}, "base_url": "https://example.invalid/v1",
            "api_key_env": "L4_UNUSED_FIXTURE_KEY", "parameters": {"max_tokens": 4096, "temperature": 0},
            "billing": {"complete": True, "verified_upper_bound": True,
                        "all_charges_in_token_usage": True, "reasoning_in_output": True,
                        "input_cny_per_million": 1, "output_cny_per_million": 2},
            "per_request_upper_bound": {"input": 1, "output": 1, "reasoning": 0, "images": 0, "other": 0}}


class FixtureTransport:
    mode = "mock"

    def __init__(self, scenarios, *, no_usage=False, truncate_first=False):
        self.client = FixtureClient(scenarios)
        self.requests = []
        self.no_usage = no_usage
        self.truncate_first = truncate_first

    def request(self, binding, messages, task, attempt):
        self.requests.append(deepcopy(messages))
        request = json.loads(messages[1]["content"])
        history = []
        for index in range(2, len(messages), 2):
            calls = json.loads(messages[index]["content"])["tool_calls"]
            results = json.loads(messages[index + 1]["content"][0]["text"])["tool_results"]
            history.append({"tool_calls": calls, "tool_results": results})
        body = self.client.respond(request, history)
        finish = "length" if self.truncate_first and len(self.requests) == 1 else "stop"
        raw = {"id": "offline-" + str(len(self.requests)), "model": "fixture-model",
               "choices": [{"finish_reason": finish, "message": {"content": body}}]}
        if not self.no_usage:
            raw["usage"] = {"prompt_tokens": 100, "completion_tokens": 50,
                            "completion_tokens_details": {"reasoning_tokens": 0}}
        return raw


class RuntimeRunnerTests(unittest.TestCase):
    def setUp(self):
        self.blocker = network_blocked()
        self.blocker.__enter__()
        self.addCleanup(self.blocker.__exit__, None, None, None)

    def fixtures(self):
        run = new_run("test-runtime")
        config = make_fixtures(run)
        config["model"] = fixture_binding()
        config["runtime"] = {"campaign_ledger": (run / "fixtures/budget.sqlite").relative_to(REPO).as_posix(),
                             "budget_cap_cny": 500}
        path = run / "requested_config.json"
        save_new(path, config)
        scenarios = read_json(run / "fixtures/fixture_manifest.json")["scenarios"]
        return run, path, config, scenarios

    def test_real_runner_with_fake_transport_images_and_accounting(self):
        run, path, _, scenarios = self.fixtures()
        transport = FixtureTransport(scenarios)
        report = run_evaluation(path, run_dir=run, transport=transport)
        _, _, records = validate_run(run)
        self.assertEqual(report["records"], 22)
        self.assertEqual(report["research_status"], "NOT_RESEARCH_DATA")
        self.assertEqual(report["real_model_calls"], 0)
        self.assertGreater(report["runtime_cost_cny"], 0)
        correct = next(r for r in records if r["task_id"] == "F-01")
        self.assertEqual(correct["pcs_mean"], 3)
        self.assertEqual(correct["prc"]["score"], 5)
        self.assertEqual(len(correct["runtime_receipts"]), 2)
        self.assertEqual(correct["runtime_receipts"][0]["usage"]["input_tokens"], 100)
        image_parts = [part for messages in transport.requests for message in messages
                       if isinstance(message["content"], list) for part in message["content"]
                       if part["type"] == "image_url"]
        self.assertTrue(image_parts)
        png = base64.b64decode(image_parts[0]["image_url"]["url"].split(",", 1)[1])
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        for messages in transport.requests:
            for message in messages:
                content = message["content"]
                texts = [content] if isinstance(content, str) else [p["text"] for p in content if p["type"] == "text"]
                self.assertNotIn("image_base64", "".join(texts))
                self.assertNotIn("Fixture-Method-A", "".join(texts))
        broken = next(r for r in records if r["task_id"] == "F-10")
        self.assertEqual(broken["processing_status"], "TECHNICAL_FAILURE")
        self.assertEqual(broken["raw_response"], "{ damaged JSON")
        self.assertGreater(broken["runtime_cost_cny"], 0)
        self.assertFalse(broken["stop_run"])

    def test_resume_keeps_completed_and_failed_records_without_redraw(self):
        run, path, _, scenarios = self.fixtures()
        run_evaluation(path, run_dir=run, transport=FixtureTransport(scenarios))
        before = {p.name: p.read_bytes() for p in (run / "records").glob("*.json")}
        # Simulate a process ending after all individual saves but before the combined save.
        (run / "records.json").unlink()
        transport = FixtureTransport(scenarios)
        report = run_evaluation(resume=run, transport=transport)
        self.assertEqual(transport.requests, [])
        self.assertEqual(report["records"], 22)
        self.assertEqual(before, {p.name: p.read_bytes() for p in (run / "records").glob("*.json")})
        self.assertTrue(any(read_json(p)["processing_status"] == "TECHNICAL_FAILURE" for p in (run / "records").glob("*.json")))

    def test_resume_after_interrupt_only_runs_unsaved_artifacts(self):
        run, path, _, scenarios = self.fixtures()
        original = engine._judge_one
        count = 0
        def interrupted(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 3:
                raise KeyboardInterrupt("constructed interruption")
            return original(*args, **kwargs)
        with patch("eval.l4_agent.engine._judge_one", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                run_evaluation(path, run_dir=run, transport=FixtureTransport(scenarios))
        before = {p.name: p.read_bytes() for p in (run / "records").glob("*.json")}
        self.assertEqual(len(before), 2)
        saved_ids = {read_json(p)["artifact_id"] for p in (run / "records").glob("*.json")}
        transport = FixtureTransport(scenarios)
        report = run_evaluation(resume=run, transport=transport)
        self.assertEqual(report["records"], 22)
        for name, content in before.items():
            self.assertEqual((run / "records" / name).read_bytes(), content)
        requested_ids = {json.loads(m[1]["content"])["artifact"]["artifact_id"] for m in transport.requests}
        self.assertFalse(saved_ids & requested_ids)

    def test_resume_after_partial_export_does_not_repeat_scores(self):
        run, path, _, scenarios = self.fixtures()
        def interrupted_export(records, config, destination):
            destination.mkdir()
            (destination / "interrupted.txt").write_text("constructed partial export", encoding="utf-8")
            raise OSError("constructed export interruption")
        with patch("eval.l4_agent.runner.export_records", side_effect=interrupted_export):
            with self.assertRaises(OSError):
                run_evaluation(path, run_dir=run, transport=FixtureTransport(scenarios))
        transport = FixtureTransport(scenarios)
        report = run_evaluation(resume=run, transport=transport)
        self.assertEqual(transport.requests, [])
        self.assertEqual(report["records"], 22)
        self.assertTrue((run / "exports/interrupted.txt").exists())
        self.assertNotEqual(report["export_dir"], str(run / "exports"))

    def test_unknown_charge_stops_campaign_and_preserves_null_cost(self):
        run, path, _, scenarios = self.fixtures()
        transport = FixtureTransport(scenarios, no_usage=True)
        with self.assertRaisesRegex(L4Error, "RUNTIME_BLOCKED"):
            run_evaluation(path, run_dir=run, transport=transport)
        self.assertEqual(len(transport.requests), 1)
        records = list((run / "records").glob("*.json"))
        self.assertEqual(len(records), 1)
        record = read_json(records[0])
        self.assertEqual(record["processing_status"], "TECHNICAL_FAILURE")
        self.assertIsNone(record["runtime_cost_cny"])
        self.assertIsNone(record["pcs_mean"])
        self.assertTrue(record["stop_run"])
        self.assertIsNone(record["runtime_receipts"][0]["cost_cny"])
        self.assertFalse((run / "records.json").exists())

    def test_truncated_paid_response_is_retained_and_not_retried(self):
        run, path, _, scenarios = self.fixtures()
        transport = FixtureTransport(scenarios, truncate_first=True)
        report = run_evaluation(path, run_dir=run, transport=transport)
        records = read_json(run / "records.json")
        first = records[0]
        self.assertEqual(first["processing_status"], "TECHNICAL_FAILURE")
        self.assertEqual(first["runtime_failure"], "RuntimeResponseError")
        self.assertEqual(first["runtime_receipts"][0]["finish_reason"], "length")
        self.assertGreater(first["runtime_cost_cny"], 0)
        self.assertFalse(first["stop_run"])
        self.assertEqual(report["records"], 22)

    def test_unbound_or_unauthorized_stops_before_runtime(self):
        run, path, config, _ = self.fixtures()
        config["data_class"] = "REAL_AGENT_EVALUATION"
        path.write_text(json.dumps(config), encoding="utf-8")
        with patch("eval.l4_agent.runner.EvaluationRuntime") as runtime:
            with self.assertRaisesRegex(L4Error, "LIVE_NOT_AUTHORIZED"):
                run_evaluation(path)
            runtime.assert_not_called()
        config["live_authorized"] = True
        config["model"]["binding_status"] = "NOT_BOUND"
        path.write_text(json.dumps(config), encoding="utf-8")
        with patch("eval.l4_agent.runner.EvaluationRuntime") as runtime:
            with self.assertRaisesRegex(L4Error, "MODEL_NOT_BOUND"):
                run_evaluation(path, explicit_live=True)
            runtime.assert_not_called()

    def test_resume_rejects_config_drift_and_campaign_change(self):
        run, path, _, scenarios = self.fixtures()
        run_evaluation(path, run_dir=run, transport=FixtureTransport(scenarios))
        with self.assertRaisesRegex(L4Error, "Campaign ledger cannot change"):
            run_evaluation(resume=run, transport=FixtureTransport(scenarios),
                           campaign_ledger=run / "fixtures/other.sqlite")
        frozen = read_json(run / "config.snapshot.json")
        frozen["model"]["model_id"] = "changed-model"
        (run / "config.snapshot.json").write_text(json.dumps(frozen), encoding="utf-8")
        with self.assertRaisesRegex(L4Error, "changed"):
            run_evaluation(resume=run, transport=FixtureTransport(scenarios))

    def test_mock_transport_cannot_use_real_batch_or_shared_live_ledger(self):
        run, path, config, scenarios = self.fixtures()
        config["data_class"] = "REAL_AGENT_EVALUATION"
        path.write_text(json.dumps(config), encoding="utf-8")
        transport = FixtureTransport(scenarios)
        with self.assertRaises(L4Error):
            run_evaluation(path, run_dir=run, transport=transport)
        self.assertEqual(transport.requests, [])
        config["data_class"] = "MOCK_ONLY"
        config["runtime"]["campaign_ledger"] = "outputs/evaluation_campaign/budget.sqlite"
        path.write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaisesRegex(L4Error, "separate ledger"):
            run_evaluation(path, run_dir=run, transport=transport)
        self.assertEqual(transport.requests, [])


if __name__ == "__main__":
    unittest.main()
