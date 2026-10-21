"""Network-denied tests for the shared request, billing, and recovery runtime."""
import copy
import io
import json
import socket
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from eval.evaluation_runtime import EvaluationRuntime, RuntimeBlocked, RuntimeResponseError


MESSAGES = [{"role": "user", "content": "Return the constructed JSON fixture."}]
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5C8AAAAASUVORK5CYII="


def binding():
    return {"binding_status": "BOUND", "provider": "CONSTRUCTED",
            "api_model_id": "fixture-model", "base_url": "https://example.invalid/v1",
            "api_key_env": "AUCMR_RUNTIME_TEST_KEY", "parameters": {"max_tokens": 256},
            "capabilities": {"images": True},
            "billing": {"complete": True, "verified_upper_bound": True,
                        "all_charges_in_token_usage": True, "reasoning_in_output": True,
                        "input_cny_per_million": 1, "output_cny_per_million": 1},
            "per_request_upper_bound": {"input": 0.0001, "output": 0.0001,
                                        "reasoning": 0, "images": 0, "other": 0}}


def response(body='{"fixture": true}', *, finish="stop", usage=None):
    return {"id": "provider-fixture-id", "model": "fixture-model",
            "choices": [{"message": {"role": "assistant", "content": body}, "finish_reason": finish}],
            "usage": usage if usage is not None else {"prompt_tokens": 100, "completion_tokens": 100}}


class FakeTransport:
    mode = "mock"

    def __init__(self, result=None):
        self.result = response() if result is None else result
        self.calls = []

    def request(self, model_binding, messages, task, attempt):
        self.calls.append(copy.deepcopy((model_binding, messages, task, attempt)))
        if isinstance(self.result, Exception):
            raise self.result
        return copy.deepcopy(self.result)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for target, name in ((socket, "create_connection"), (socket, "getaddrinfo"),
                             (socket.socket, "connect"), (socket.socket, "connect_ex")):
            self.stack.enter_context(patch.object(target, name, side_effect=AssertionError("NETWORK_DENIED")))
        self.stack.enter_context(patch.dict("os.environ", {}, clear=True))

    def runtime(self, transport=None, *, layer="L2", config=None, cap=500, run="run", live=False):
        return EvaluationRuntime(binding() if config is None else config, self.temp / run, layer,
                                 self.temp / "budget.sqlite", cap, live, transport)

    def test_network_guard_is_active(self):
        with self.assertRaisesRegex(AssertionError, "NETWORK_DENIED"):
            socket.create_connection(("example.invalid", 443))

    def test_live_requires_authorization_binding_and_env_key(self):
        with patch("eval.evaluation_runtime.build_opener") as opener:
            for auth, bound, key in ((False, "BOUND", "constructed"), (True, "NOT_BOUND", "constructed"),
                                     (True, "BOUND", None)):
                config = binding()
                config["binding_status"] = bound
                env = {"AUCMR_RUNTIME_TEST_KEY": key} if key else {}
                with self.subTest(auth=auth, bound=bound, key=bool(key)), patch.dict("os.environ", env, clear=True):
                    runtime = self.runtime(config=config, live=auth)
                    with self.assertRaises(RuntimeBlocked):
                        runtime.request(MESSAGES, task_id="task")
                    self.assertEqual(runtime.budget.summary()["states"], [])
            opener.assert_not_called()

    def test_http_payload_preserves_image_data_url_and_raw_usage(self):
        messages = [{"role": "user", "content": [{"type": "text", "text": "Read this page"},
                    {"type": "image_url", "image_url": {"url": PNG, "detail": "high"}}]}]
        raw = response()
        raw["usage"]["completion_tokens_details"] = {"reasoning_tokens": 12}
        with patch.dict("os.environ", {"AUCMR_RUNTIME_TEST_KEY": "CONSTRUCTED_NOT_A_REAL_SECRET"}):
            with patch("eval.evaluation_runtime.build_opener") as opener:
                opener.return_value.open.return_value = io.BytesIO(json.dumps(raw).encode())
                runtime = self.runtime(layer="L4", live=True)
                result = runtime.request(messages, task_id="page-1")
                sent = opener.return_value.open.call_args.args[0]
                payload = json.loads(sent.data)
                self.assertEqual(sent.full_url, "https://example.invalid/v1/chat/completions")
                self.assertEqual(payload["messages"], messages)
                self.assertEqual(payload["messages"][0]["content"][1]["image_url"]["url"], PNG)
                self.assertFalse(payload["stream"])
                self.assertEqual(payload["model"], "fixture-model")
                self.assertEqual(result["raw_usage"], raw["usage"])
                self.assertEqual(result["usage"]["reasoning_tokens"], 12)
                self.assertEqual(result["cost_cny"], 0.0002)
                saved = Path(result["evidence_path"]).read_text(encoding="utf-8")
                self.assertNotIn("CONSTRUCTED_NOT_A_REAL_SECRET", saved)
                self.assertIn("image_url", saved)

    def test_mock_has_no_key_requirement_and_zero_real_calls(self):
        fake = FakeTransport()
        runtime = self.runtime(fake)
        result = runtime.request(MESSAGES, task_id="fixture")
        self.assertFalse(runtime.is_live)
        self.assertTrue(result["offline"])
        self.assertEqual(result["real_model_calls"], 0)
        self.assertEqual(runtime.real_model_calls, 0)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(result["provider_request_id"], "provider-fixture-id")

    def test_two_layers_share_cap_and_have_distinct_request_ids(self):
        first = self.runtime(FakeTransport(), layer="L2", cap=0.0005)
        second = self.runtime(FakeTransport(), layer="L4", cap=0.0005)
        a = first.request(MESSAGES, task_id="same-task")
        b = second.request(MESSAGES, task_id="same-task")
        self.assertNotEqual(a["request_id"], b["request_id"])
        self.assertEqual(first.budget.summary()["accounted_cny"], 0.0004)
        with self.assertRaises(RuntimeBlocked):
            first.request(MESSAGES, task_id="third-task")
        self.assertEqual(first.budget.summary()["states"], ["SETTLED", "SETTLED"])
        self.assertEqual(len(first.transport.calls), 1)

    def test_resume_uses_paid_response_even_with_new_attempt_number(self):
        fake = FakeTransport()
        runtime = self.runtime(fake)
        original = runtime.request(MESSAGES, task_id="task", attempt=1)
        restored = self.runtime(fake)
        recovered = restored.request(MESSAGES, task_id="task", attempt=2)
        self.assertTrue(recovered["cached"])
        self.assertEqual(recovered["request_id"], original["request_id"])
        self.assertEqual(recovered["usage"], original["usage"])
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(restored.budget.summary()["accounted_cny"], 0.0002)

    def test_crash_after_response_before_settlement_recovers_without_request(self):
        fake = FakeTransport()
        runtime = self.runtime(fake)
        with patch.object(runtime.budget, "settle", side_effect=OSError("CONSTRUCTED_CRASH")):
            with self.assertRaises(OSError):
                runtime.request(MESSAGES, task_id="task")
        self.assertEqual(runtime.budget.summary()["states"], ["RESERVED"])
        recovered = self.runtime(fake).request(MESSAGES, task_id="task")
        self.assertTrue(recovered["cached"])
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(runtime.budget.summary()["states"], ["SETTLED"])

    def test_crash_after_settlement_before_completion_save_recovers(self):
        fake = FakeTransport()
        runtime = self.runtime(fake)
        save = runtime._save

        def interrupt_complete(path, record):
            if record["state"] == "COMPLETE":
                raise OSError("CONSTRUCTED_CRASH")
            save(path, record)

        with patch.object(runtime, "_save", side_effect=interrupt_complete):
            with self.assertRaises(OSError):
                runtime.request(MESSAGES, task_id="task")
        self.assertEqual(runtime.budget.summary()["states"], ["SETTLED"])
        result = self.runtime(fake).request(MESSAGES, task_id="task")
        self.assertTrue(result["cached"])
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(runtime.budget.summary()["accounted_cny"], 0.0002)

    def test_missing_usage_preserves_response_and_blocks_campaign(self):
        raw = response()
        raw.pop("usage")
        fake = FakeTransport(raw)
        runtime = self.runtime(fake)
        with self.assertRaises(RuntimeBlocked) as caught:
            runtime.request(MESSAGES, task_id="task")
        error = caught.exception
        self.assertIsNone(error.receipt["cost_cny"])
        self.assertIsNotNone(error.request_id)
        self.assertEqual(json.loads(Path(error.evidence_path).read_text())["raw_response"], raw)
        for task in ("task", "other-task"):
            with self.assertRaises(RuntimeBlocked):
                runtime.request(MESSAGES, task_id=task, attempt=2)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(runtime.budget.summary()["states"], ["UNKNOWN"])

    def test_timeout_is_not_retried_and_exception_text_is_not_logged(self):
        fake = FakeTransport(TimeoutError("DO_NOT_SAVE_CONSTRUCTED_CREDENTIAL"))
        runtime = self.runtime(fake)
        with self.assertRaises(RuntimeBlocked) as caught:
            runtime.request(MESSAGES, task_id="task")
        saved = Path(caught.exception.evidence_path).read_text()
        self.assertNotIn("DO_NOT_SAVE_CONSTRUCTED_CREDENTIAL", saved)
        self.assertEqual(json.loads(saved)["transport_error"], "TimeoutError")
        with self.assertRaises(RuntimeBlocked):
            runtime.request(MESSAGES, task_id="task", attempt=2)
        self.assertEqual(len(fake.calls), 1)
        self.assertFalse(caught.exception.retryable)

    def test_truncation_and_invalid_json_keep_cost_without_repeating_request(self):
        for index, raw in enumerate((response(finish="length"), response("not JSON"), response("[]"))):
            with self.subTest(index=index):
                fake = FakeTransport(raw)
                runtime = self.runtime(fake, run="run-" + str(index))
                for attempt in (1, 2):
                    with self.assertRaises(RuntimeResponseError) as caught:
                        runtime.request(MESSAGES, task_id="task", attempt=attempt)
                    self.assertEqual(caught.exception.receipt["cost_cny"], 0.0002)
                    self.assertTrue(Path(caught.exception.evidence_path).is_file())
                self.assertEqual(len(fake.calls), 1)
        self.assertEqual(runtime.budget.summary()["accounted_cny"], 0.0006)

    def test_charge_over_bound_is_unknown_and_retains_actual_cost(self):
        fake = FakeTransport(response(usage={"prompt_tokens": 1000, "completion_tokens": 100}))
        runtime = self.runtime(fake)
        with self.assertRaisesRegex(RuntimeBlocked, "UNKNOWN_BILLING") as caught:
            runtime.request(MESSAGES, task_id="task")
        self.assertEqual(caught.exception.receipt["cost_cny"], 0.0011)
        self.assertEqual(runtime.budget.summary()["states"], ["UNKNOWN"])
        self.assertEqual(runtime.budget.summary()["accounted_cny"], 0.0011)

    def test_reasoning_separate_charge_requires_its_usage(self):
        config = binding()
        config["billing"].update(reasoning_in_output=False, reasoning_cny_per_million=2)
        config["per_request_upper_bound"]["reasoning"] = 0.0001
        raw = response()
        raw["usage"]["completion_tokens_details"] = {"reasoning_tokens": 10}
        runtime = self.runtime(FakeTransport(raw), config=config)
        self.assertEqual(runtime.request(MESSAGES, task_id="known")["cost_cny"], 0.00022)
        runtime.transport.result = response()
        with self.assertRaises(RuntimeBlocked):
            runtime.request(MESSAGES, task_id="missing-reasoning")
        self.assertEqual(runtime.budget.summary()["states"], ["SETTLED", "UNKNOWN"])

    def test_cache_integrity_and_mode_cannot_be_silently_changed(self):
        fake = FakeTransport()
        runtime = self.runtime(fake)
        result = runtime.request(MESSAGES, task_id="task")
        path = Path(result["evidence_path"])
        saved = json.loads(path.read_text())
        saved["raw_response"]["choices"][0]["message"]["content"] = '{"tampered": true}'
        path.write_text(json.dumps(saved))
        with self.assertRaisesRegex(RuntimeBlocked, "integrity"):
            runtime.request(MESSAGES, task_id="task")
        self.assertEqual(len(fake.calls), 1)
        with self.assertRaisesRegex(RuntimeBlocked, "Mock and real"):
            self.runtime(live=True)

    def test_credentials_and_unsafe_payloads_are_rejected_before_request(self):
        with self.assertRaises(RuntimeBlocked):
            bad = binding()
            bad["api_key"] = "NEVER_ALLOWED"
            self.runtime(FakeTransport(), config=bad)
        for change in ("endpoint", "override", "price", "capability", "image"):
            with self.subTest(change=change):
                config = binding()
                messages = copy.deepcopy(MESSAGES)
                if change == "endpoint":
                    config["base_url"] = "https://user:secret@example.invalid/v1"
                elif change == "override":
                    config["parameters"]["messages"] = []
                elif change == "price":
                    config["billing"]["input_cny_per_million"] = None
                else:
                    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": PNG}}]}]
                    if change == "capability":
                        config["capabilities"]["images"] = False
                    else:
                        messages[0]["content"][0]["image_url"]["url"] = "https://example.invalid/page.png"
                fake = FakeTransport()
                runtime = self.runtime(fake, config=config)
                with self.assertRaises(RuntimeBlocked):
                    runtime.request(messages, task_id=change)
                self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
