"""Constructed arithmetic and bounded-request regression; no provider access."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eval.evaluation_billing import normalize_usage, validate_billing
from eval.evaluation_request_budget import check_request
from eval.evaluation_runtime import EvaluationRuntime, RuntimeBlocked
from eval.agent_evaluation.transport import normalize_response
from tests.test_evaluation_runtime import PNG, FakeTransport, response


def binding():
    return {"binding_status": "BOUND", "provider": "CONSTRUCTED",
            "api_model_id": "fixture", "base_url": "https://example.invalid/v1",
            "api_key_env": "AUCMR_RUNTIME_TEST_KEY", "parameters": {"max_tokens": 100},
            "capabilities": {"images": True},
            "billing": {"complete": True, "verified_upper_bound": True,
                        "all_charges_in_token_usage": True, "reasoning_in_output": True,
                        "input_cny_per_million": 2, "output_cny_per_million": 6,
                        "usage_contract": {"verified": True, "evidence": "CONSTRUCTED_OFFLINE_FIXTURE",
                            "cached_input_path": ["prompt_tokens_details", "cached_tokens"],
                            "missing_cache": "unknown", "image_tokens_in_prompt": True},
                        "tariff": {"version": "CONSTRUCTED_V1", "source": "CONSTRUCTED_OFFLINE_FIXTURE",
                            "currency": "CNY", "tiers": [tier(0, 32000, 1, 3, .2), tier(32000, 128000, 2, 6, .4)]}},
            "per_request_upper_bound": {"input": .2, "output": .1, "reasoning": 0, "images": 0, "other": 0},
            "request_limits": {"verified": True, "evidence": "CONSTRUCTED_OFFLINE_FIXTURE",
                "max_input_tokens": 65536, "context_tokens": 128000, "text_tokens_per_utf8_byte": 1,
                "message_overhead_tokens": 8, "content_part_overhead_tokens": 4, "request_overhead_tokens": 8, "max_request_bytes": 1000000,
                "image_tokens_by_detail": {"auto": 100, "high": 100, "low": 100},
                "max_image_bytes": 100000, "max_image_base64_bytes": 140000,
                "max_image_pixels": 10000000, "max_images": 24}}


def tier(lower, upper, inp, out, cache):
    return {"min_input_tokens": lower, "max_input_tokens_exclusive": upper,
            "input_cny_per_million": inp, "output_cny_per_million": out, "cached_input_cny_per_million": cache}


def usage(inp=10000, out=1000, cache=0):
    return {"usage": {"prompt_tokens": inp, "completion_tokens": out,
                      "prompt_tokens_details": {"cached_tokens": cache}}}


def messages(images=0):
    return [{"role": "user", "content": [{"type": "text", "text": "读取来源 page"}] +
            [{"type": "image_url", "image_url": {"url": PNG}} for _ in range(images)]}]


class BillingTests(unittest.TestCase):
    def test_four_server_failures_both_paths(self):
        for inp_rate, out_rate, cache_rate, cache, expected in ((1, 3, .2, 0, .013), (1, 3, .2, 8000, .0066),
                                                           (6, 30, 1.2, 8000, .0516), (12, 36, 1.5, 8000, .072)):
            with self.subTest(expected=expected):
                b = binding()
                b['billing']['tariff']['tiers'] = [tier(0, None, inp_rate, out_rate, cache_rate)]
                raw = usage(cache=cache)
                result = normalize_usage(raw, b)
                self.assertEqual(result['cost_cny'], expected)
                self.assertEqual(result['list_price_cost_cny'], expected)
                self.assertIsNone(result['invoice_cost_cny'])
                self.assertEqual(normalize_response(raw, b)['cost_cny'], expected)
                runtime = object.__new__(EvaluationRuntime)
                runtime.binding, runtime.is_live = b, False
                self.assertEqual(runtime._normalize(raw, 'fixture')['cost_cny'], expected)

    def test_half_open_tier_boundaries(self):
        for inp, expected in ((31999, .034999), (32000, .07), (127999, .261998), (128000, None)):
            with self.subTest(inp=inp):
                self.assertEqual(normalize_usage(usage(inp=inp), binding())['cost_cny'], expected)

    def test_missing_cache_is_upper_estimate_not_zero_or_invoice(self):
        raw = usage()
        del raw['usage']['prompt_tokens_details']
        result = normalize_usage(raw, binding())
        self.assertEqual(result['cost_cny'], .013)
        self.assertEqual(result['cost_basis'], 'USAGE_RATE_UPPER_BOUND')
        self.assertIsNone(result['usage']['cached_input_tokens'])
        self.assertIsNone(result['list_price_cost_cny'])

    def test_documented_cache_absence_and_invalid_presence_are_distinct(self):
        b, raw = binding(), usage()
        b['billing']['usage_contract']['missing_cache'] = 'zero_documented'
        del raw['usage']['prompt_tokens_details']
        self.assertEqual(normalize_usage(raw, b)['cost_basis'], 'USAGE_LIST_PRICE')
        raw['usage']['prompt_tokens_details'] = {'cached_tokens': None}
        self.assertIsNone(normalize_usage(raw, b)['cost_cny'])

    def test_invalid_usage_never_zero(self):
        for field, bad in (('prompt_tokens', -1), ('completion_tokens', True), ('completion_tokens', '10'), ('prompt_tokens', None)):
            with self.subTest(field=field, value=bad):
                raw = usage()
                raw['usage'][field] = bad
                self.assertIsNone(normalize_usage(raw, binding())['cost_cny'])
        self.assertIsNone(normalize_usage({}, binding())['cost_cny'])

    def test_invalid_cache_is_unknown(self):
        for cache in (10001, -1, True, '8', None):
            with self.subTest(cache=cache):
                self.assertIsNone(normalize_usage(usage(cache=cache), binding())['cost_cny'])
        raw = usage()
        raw['usage']['prompt_tokens_details'] = None
        self.assertIsNone(normalize_usage(raw, binding())['cost_cny'])

    def test_image_and_reasoning_tokens_are_not_added_twice(self):
        raw = usage(cache=8000)
        raw['usage']['prompt_tokens_details']['image_tokens'] = 9000
        raw['usage']['completion_tokens_details'] = {'reasoning_tokens': 800}
        self.assertEqual(normalize_usage(raw, binding())['cost_cny'], .0066)

    def test_separate_reasoning_requires_usage(self):
        b = binding()
        b['billing'].update(reasoning_in_output=False, reasoning_cny_per_million=4)
        raw = usage()
        self.assertIsNone(normalize_usage(raw, b)['cost_cny'])
        raw['usage']['completion_tokens_details'] = {'reasoning_tokens': 100}
        self.assertEqual(normalize_usage(raw, b)['cost_cny'], .0134)

    def test_bad_tariffs_and_unverified_contract_block(self):
        changes = [('min_input_tokens', 1), ('max_input_tokens_exclusive', 0), ('input_cny_per_million', 'NaN'),
                   ('cached_input_cny_per_million', -1)]
        for field, value in changes:
            with self.subTest(field=field):
                b = binding()
                b['billing']['tariff']['tiers'][0][field] = value
                with self.assertRaises(ValueError):
                    validate_billing(b)
        b = binding()
        b['billing']['usage_contract']['verified'] = False
        with self.assertRaises(ValueError):
            validate_billing(b)

    def test_cache_more_expensive_upper_uses_max_rate(self):
        b, raw = binding(), usage()
        b['billing']['tariff']['tiers'][0]['cached_input_cny_per_million'] = 5
        del raw['usage']['prompt_tokens_details']
        self.assertEqual(normalize_usage(raw, b)['cost_cny'], .053)

    def test_raw_usage_and_binding_not_modified(self):
        b, raw = binding(), usage(cache=8000)
        before = copy.deepcopy([b, raw])
        result = normalize_usage(raw, b)
        self.assertEqual([b, raw], before)
        self.assertEqual(result['raw_usage'], raw['usage'])
        self.assertEqual(len(result['billing_sha256']), 64)


class RequestTests(unittest.TestCase):
    def test_server_audit_is_read_only_and_does_not_echo_keys(self):
        from scripts.check_evaluation_budget import audit
        with tempfile.TemporaryDirectory() as directory:
            root, b = Path(directory), binding()
            b['binding_status'] = 'NOT_BOUND'
            configs = {'l2': {'model': b}, 'l3': {'models': {k: b for k in 'ABC'}}, 'l4': {'model': b}}
            for layer, config in configs.items():
                (root / (layer + '.json')).write_text(json.dumps(config), encoding='utf-8')
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            with patch.dict('os.environ', {'AUCMR_RUNTIME_TEST_KEY': 'constructed-secret'}), patch.object(EvaluationRuntime, '__init__', side_effect=AssertionError('NO_RUNTIME')):
                report = audit(root)
            self.assertTrue(report['draft_unchanged'])
            self.assertFalse(report['billing_request_bindings_ready'])
            self.assertEqual(report['files_written'], 0)
            self.assertNotIn('constructed-secret', json.dumps(report))
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)

    def test_complete_history_and_duplicate_images_count(self):
        b, m = binding(), messages(2)
        m += [{'role': 'assistant', 'content': 'history'}, *messages(1)]
        report = check_request(b, m)
        self.assertEqual(report['image_count'], 3)
        self.assertEqual(report['image_token_upper_bound'], 300)
        self.assertEqual(report['chat_overhead_token_upper_bound'], 56)
        self.assertEqual(report['text_utf8_bytes'], 2 * len('读取来源 page'.encode()) + 7)

    def test_input_context_and_protocol_cap(self):
        b, m = binding(), messages()
        for limits in ({'max_input_tokens': 1}, {'context_tokens': 100}):
            with self.subTest(limits=limits):
                c = copy.deepcopy(b)
                c['request_limits'].update(limits)
                with self.assertRaisesRegex(ValueError, 'CONTEXT_LIMIT'):
                    check_request(c, m)
        with self.assertRaisesRegex(ValueError, 'CONTEXT_LIMIT'):
            check_request(b, m, input_limit=1)

    def test_serialized_body_limit_includes_base64(self):
        b = binding()
        b['request_limits']['max_request_bytes'] = 100
        with self.assertRaisesRegex(ValueError, 'REQUEST_BYTE'):
            check_request(b, messages(1))

    def test_image_limits_and_unknown_detail(self):
        for key, value in (('max_image_bytes', 1), ('max_image_base64_bytes', 1), ('max_images', 1)):
            with self.subTest(key=key):
                b = binding()
                b['request_limits'][key] = value
                with self.assertRaises(ValueError):
                    check_request(b, messages(2))
        m = messages(1)
        m[0]['content'][1]['image_url']['detail'] = 'xhigh'
        with self.assertRaisesRegex(ValueError, 'REQUEST_BOUND_NOT_BOUND'):
            check_request(binding(), m)

    def test_unverified_limits_and_unknown_fields_block(self):
        b = binding()
        b['request_limits']['verified'] = False
        with self.assertRaisesRegex(ValueError, 'NOT_VERIFIED'):
            check_request(b, messages())
        m = messages()
        m[0]['extra_content'] = 'not counted'
        with self.assertRaises(ValueError):
            check_request(binding(), m)
        for extra in ({'tools': [{'description': 'unaccounted prompt'}]},
                      {'response_format': {'type': 'json_schema', 'json_schema': {'description': 'unaccounted'}}}):
            with self.subTest(extra=extra):
                b = binding()
                b['parameters'].update(extra)
                with self.assertRaises(ValueError):
                    check_request(b, messages())

    def test_reservation_must_cover_full_request_and_output(self):
        for field in ('input', 'output'):
            with self.subTest(field=field):
                b = binding()
                b['per_request_upper_bound'][field] = 0
                with self.assertRaisesRegex(ValueError, 'RESERVATION_BELOW'):
                    check_request(b, messages(1))

    def test_upper_bound_tier_selection_and_output_rejected_ambiguity(self):
        b = binding()
        report = check_request(b, [{'role': 'user', 'content': 'x' * 32000}])
        self.assertEqual(report['required_reservation_cny']['output'], '0.0006')
        b['parameters']['max_completion_tokens'] = 100
        with self.assertRaisesRegex(ValueError, 'AMBIGUOUS'):
            check_request(b, messages())

    def test_pre_dispatch_block_has_no_reservation_no_call(self):
        with tempfile.TemporaryDirectory() as directory:
            b, fake = binding(), FakeTransport()
            b['request_limits']['max_input_tokens'] = 1
            runtime = EvaluationRuntime(b, Path(directory) / 'run', 'L2', Path(directory) / 'budget.sqlite', transport=fake)
            with self.assertRaises(RuntimeBlocked):
                runtime.request(messages(), task_id='blocked')
            self.assertEqual(fake.calls, [])
            self.assertEqual(runtime.budget.summary()['states'], [])

    def test_live_legacy_binding_cannot_send(self):
        from tests.test_evaluation_runtime import binding as old_binding
        with tempfile.TemporaryDirectory() as directory, patch('eval.evaluation_runtime.build_opener') as http:
            runtime = EvaluationRuntime(old_binding(), Path(directory), 'L2', Path(directory) / 'budget.sqlite', live_authorized=True)
            with self.assertRaisesRegex(RuntimeBlocked, 'TARIFF_NOT_BOUND'):
                runtime.request(messages(), task_id='blocked')
            http.assert_not_called()
            self.assertEqual(runtime.budget.summary()['states'], [])

    def test_new_tariff_resume_settles_once(self):
        raw = response(usage={'prompt_tokens': 100, 'completion_tokens': 100, 'prompt_tokens_details': {'cached_tokens': 80}})
        with tempfile.TemporaryDirectory() as directory:
            fake, root = FakeTransport(raw), Path(directory)
            runtime = EvaluationRuntime(binding(), root / 'run', 'L4', root / 'budget.sqlite', transport=fake)
            request = [{'role': 'user', 'content': 'x' * 200}]
            first = runtime.request(request, task_id='paid')
            restored = EvaluationRuntime(binding(), root / 'run', 'L4', root / 'budget.sqlite', transport=fake)
            second = restored.request(request, task_id='paid')
            self.assertEqual(first['cost_cny'], .000336)
            self.assertEqual(second['cost_cny'], first['cost_cny'])
            self.assertTrue(second['cached'])
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(restored.budget.summary()['accounted_cny'], .000336)

    def test_usage_breaks_bound_keeps_cost_and_blocks_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root, fake = Path(directory), FakeTransport(response())
            runtime = EvaluationRuntime(binding(), root / 'run', 'L2', root / 'budget.sqlite', transport=fake)
            for _ in range(2):
                with self.assertRaises(RuntimeBlocked) as raised:
                    runtime.request(messages(), task_id='bad-bound')
                self.assertEqual(raised.exception.receipt['cost_cny'], .0004)
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(runtime.budget.summary()['states'], ['UNKNOWN'])

    def test_tariff_without_legacy_rates_works(self):
        with tempfile.TemporaryDirectory() as directory:
            b, root = binding(), Path(directory)
            del b['billing']['input_cny_per_million']
            del b['billing']['output_cny_per_million']
            runtime = EvaluationRuntime(b, root / 'run', 'L2', root / 'budget.sqlite', transport=FakeTransport())
            result = runtime.request([{'role': 'user', 'content': 'x' * 200}], task_id='tier-only')
            self.assertEqual(result['cost_cny'], .0004)

    def test_oversized_usage_stops_campaign_without_resending(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = FakeTransport(response(usage={'prompt_tokens': 10**1000, 'completion_tokens': 100}))
            runtime = EvaluationRuntime(binding(), root / 'run', 'L2', root / 'budget.sqlite', transport=fake)
            for _ in range(2):
                with self.assertRaises(RuntimeBlocked):
                    runtime.request(messages(), task_id='oversized')
            self.assertEqual(runtime.budget.summary()['states'], ['UNKNOWN'])
            self.assertEqual(runtime.budget.summary()['accounted_cny'], .3)
            self.assertEqual(len(fake.calls), 1)

    def test_ledger_nonfinite_or_sqlite_overflow_becomes_unknown(self):
        from eval.agent_evaluation.budget import Budget
        for value in (float('inf'), float('nan'), 1e100, True):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                ledger = Budget(Path(directory) / 'budget.sqlite')
                ledger.reserve('id', 'constructed', binding()['per_request_upper_bound'])
                with self.assertRaises(ValueError):
                    ledger.settle('id', value, force_unknown=True)
                self.assertEqual(ledger.summary()['states'], ['UNKNOWN'])
                self.assertEqual(ledger.summary()['accounted_cny'], .3)


if __name__ == '__main__':
    unittest.main()
