import json
import tempfile
import unittest
from pathlib import Path

from eval.l4_agent.client import model_messages
from eval.l4_agent.engine import _judge_one
from eval.l4_agent.fixtures import FixtureClient, make_fixtures
from eval.l4_agent.inputs import collect
from eval.l4_agent.protocol import REPO, network_blocked, read_json, save_new, L4Error
from eval.l4_agent.records import validate_record


class FeedbackTests(unittest.TestCase):
    def test_failed_correction_cannot_keep_previous_partial_scores(self):
        with tempfile.TemporaryDirectory(dir=REPO/'outputs/agent_l4') as tmp, network_blocked():
            root = Path(tmp)
            config = make_fixtures(root)
            config.update(source_bootstrap=True, validation_feedback_once=True)
            manifest = collect(config)
            artifact, target = manifest['artifacts'][0], config['targets'][0]
            base = FixtureClient(read_json(root/'fixtures/fixture_manifest.json')['scenarios'])
            class Client:
                calls = 0
                def respond(self, request, history):
                    self.calls += 1
                    if self.calls == 2:
                        raise ValueError('invalid correction response')
                    pages = [item for h in history for item in h.get('tool_results', [])
                             if item['call']['name'] == 'source_page']
                    value = json.loads(base.respond(request, [{'tool_results': [None, None, pages[0]]}]))
                    value['pcs']['goal']['checks'][0]['source_refs'] = []
                    return json.dumps(value)
            result = _judge_one(artifact, target, manifest['sources'], config, manifest, root, Client())
            self.assertEqual(result['processing_status'], 'TECHNICAL_FAILURE')
            self.assertIsNone(result['prc']['score'])
            self.assertTrue(all(v['score'] is None or s in artifact['empty_slots'] for s,v in result['pcs'].items()))
            self.assertEqual(len(result['request_transcript']), 1)
            validate_record(result, artifact, manifest, config)

    def test_http_timeout_is_bounded_and_applied_without_retry(self):
        from unittest.mock import patch, MagicMock
        from eval.evaluation_runtime import EvaluationRuntime, RuntimeBlocked
        for invalid in (0, 601, True, "600"):
            with self.assertRaises(RuntimeBlocked):
                EvaluationRuntime({}, Path('.'), 'L4', Path('unused'), http_timeout_seconds=invalid)
        runtime = object.__new__(EvaluationRuntime)
        runtime.binding = {'base_url': 'https://example.invalid'}
        runtime.http_timeout_seconds = 600
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b'{}'
        with patch('eval.evaluation_runtime.build_opener', return_value=opener):
            self.assertEqual(runtime._http({}, 'test-only'), {})
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(opener.open.call_args.kwargs['timeout'], 600)

    def test_seed_continues_only_invalid_same_input_without_redrawing_scores(self):
        from copy import deepcopy
        from eval.l4_agent.records import seal
        with tempfile.TemporaryDirectory(dir=REPO/'outputs/agent_l4') as tmp, network_blocked():
            root = Path(tmp)
            config = make_fixtures(root)
            config.update(source_bootstrap=True, validation_feedback_once=True)
            manifest = collect(config)
            artifact, target = manifest['artifacts'][0], config['targets'][0]
            base = FixtureClient(read_json(root/'fixtures/fixture_manifest.json')['scenarios'])
            class Client:
                calls = 0
                bad = True
                def respond(self, request, history):
                    self.calls += 1
                    pages = [item for h in history for item in h.get('tool_results', [])
                             if item['call']['name'] == 'source_page']
                    raw = base.respond(request, [{'tool_results': [None, None, pages[0]]}])
                    if self.bad:
                        value = json.loads(raw)
                        value['pcs']['goal']['checks'][0]['source_refs'] = []
                        return json.dumps(value)
                    assert sum('validation_feedback' in h for h in history) == 2
                    return raw
            first = Client()
            prior = _judge_one(artifact, target, manifest['sources'], config, manifest, root, first)
            self.assertEqual(first.calls, 2)
            self.assertEqual(prior['processing_status'], 'TECHNICAL_FAILURE')
            path = root/'prior.json'
            save_new(path, prior)
            config.update(validation_feedback_max=3,
                          validation_seed_records={artifact['artifact_id']:path.relative_to(REPO).as_posix()})
            second = Client()
            second.bad = False
            result = _judge_one(artifact, target, manifest['sources'], config, manifest, root/'next', second)
            self.assertEqual(second.calls, 1)
            self.assertEqual(result['processing_status'], 'COMPLETED')
            self.assertEqual(result['validation_seed']['prior_responses_replayed_without_calls'], 2)
            good = deepcopy(result)
            good.pop('record_sha256')
            good_path = root/'good.json'
            save_new(good_path, seal(good))
            config['validation_seed_records'][artifact['artifact_id']] = good_path.relative_to(REPO).as_posix()
            with self.assertRaises(L4Error):
                _judge_one(artifact, target, manifest['sources'], config, manifest, root/'blocked', second)
            self.assertEqual(second.calls, 1)

    def test_quote_layout_normalization_does_not_change_words_or_negation(self):
        from eval.l4_agent.records import validate_refs, InvalidResponse
        access = [{'access_id': 'a', 'operation': 'source_page',
                   'arguments': {'source_id': 's', 'pdf_page': 1},
                   'result': {'status': 'OK', 'text': 'The system must\nnot   submit the bid.'}}]
        ref = {'access_id': 'a', 'source_id': 's', 'pdf_page': 1, 'kind': 'TEXT',
               'quote': 'The system must not submit the bid.'}
        validate_refs([ref], access)
        for quote in ['The system must submit the bid.', 'The system may not submit the bid.',
                      'The system must not submit a bid.', 'The system must not bid submit.']:
            with self.subTest(quote=quote), self.assertRaises(InvalidResponse):
                validate_refs([{**ref, 'quote': quote}], access)

    def test_bounded_feedback_preserves_bad_response_and_checks_corrected_result(self):
        for always_bad in (False, True):
            with self.subTest(always_bad=always_bad), tempfile.TemporaryDirectory(dir=REPO/'outputs/agent_l4') as tmp, network_blocked():
                root = Path(tmp)
                config = make_fixtures(root)
                config['validation_feedback_once'] = True
                manifest = collect(config)
                artifact = manifest['artifacts'][0]
                target = config['targets'][0]
                base = FixtureClient(read_json(root/'fixtures/fixture_manifest.json')['scenarios'])
                class Client:
                    calls = 0
                    def respond(self, request, history):
                        self.calls += 1
                        if self.calls == 3:
                            feedback = history[-1]['validation_feedback']
                            self_errors = feedback['errors']
                            assert 'goal:MISSING_SOURCE_REFERENCE' in self_errors
                            messages = model_messages(request, history)
                            assert messages[-2]['content'] == feedback['rejected_response']
                            assert 'validation_errors' in messages[-1]['content']
                        raw = base.respond(request, [h for h in history if 'validation_feedback' not in h])
                        if self.calls == 2 or (always_bad and self.calls == 3):
                            value = json.loads(raw)
                            value['pcs']['goal']['checks'][0]['source_refs'] = []
                            return json.dumps(value)
                        return raw
                client = Client()
                result = _judge_one(artifact, target, manifest['sources'], config, manifest, root, client)
                self.assertEqual(client.calls, 3)
                self.assertEqual(len(result['request_transcript']), 3)
                validate_record(result, artifact, manifest, config)
                if always_bad:
                    self.assertEqual(result['processing_status'], 'TECHNICAL_FAILURE')
                else:
                    self.assertEqual(result['validation_errors'], [])
                    self.assertEqual(result['pcs_mean'], 3)
                    self.assertEqual(result['prc']['score'], 5)
