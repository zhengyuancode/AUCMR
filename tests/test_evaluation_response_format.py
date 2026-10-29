"""Offline coverage of lossless JSON wrapping and evaluator integration."""
import json
import tempfile
import unittest
from pathlib import Path

from eval.evaluation_response_format import normalize_json_envelope
from eval.agent_evaluation.transport import deny_network


class ResponseFormatTests(unittest.TestCase):
    def test_bare_and_whole_fences(self):
        for raw in ['{"status":"ok"}', '```json\n{"status":"ok"}\n```',
                    ' \n```\r\n{"status":"ok"}\r\n```\n ']:
            with self.subTest(raw=raw):
                body, meta = normalize_json_envelope(raw)
                self.assertEqual(json.loads(body), {"status":"ok"})
                self.assertEqual(meta['fence_removed'], '```' in raw)
                self.assertEqual(meta['raw_json_valid'], '```' not in raw)
                self.assertEqual(len(meta['normalized_text_sha256']), 64)

    def test_no_prose_multiblock_or_json_repair(self):
        from eval.l4_agent.records import parse_response, InvalidResponse
        invalid = ['Here is JSON:\n```json\n{}\n```', '```json\n{}\n```\nDone',
                   '```json\n{}\n```\n```json\n{}\n```', '```python\n{}\n```',
                   '```json\n{}', '```json\n{"a":}\n```', '```json\n[]\n```',
                   '```json\n{"a":1,"a":2}\n```', '```json\n{"a":NaN}\n```',
                   '```json\r{}\r```', '```json\u2028{}\u2028```']
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(InvalidResponse):
                parse_response(raw)
        self.assertEqual(parse_response('```json\n{"a":1}\n```'), {'a':1})

    def test_real_observed_glm_shape(self):
        # Recorded shape, no private IDs, keys or external test-file dependency.
        raw='```json\n{"status": "ok"}\n```'
        body, meta=normalize_json_envelope(raw)
        self.assertFalse(meta['raw_json_valid'])
        self.assertEqual(json.loads(body), {'status':'ok'})

    def test_l2_schema_and_original_response_preserved(self):
        from eval.l2_independent.fixtures import fixture_cases
        from eval.l2_independent.evaluation import evaluate, MockClient, parse_response
        with deny_network():
            config, cases=fixture_cases()
            case=cases[0];raw='```json\n'+json.dumps(case['response'])+'\n```'
            record=evaluate(case['artifact'],config,MockClient(raw,config['model']),'test')
            self.assertEqual(record['status'],case['expected_status'])
            self.assertEqual(record['raw_response'],raw)
            self.assertTrue(record['response_format']['fence_removed'])
            self.assertEqual(parse_response('```json\n{"wrong":1}\n```',case['artifact'])['parse_status'],'INVALID_SCHEMA')

    def test_runtime_receipt_usage_and_resume(self):
        from tests.test_evaluation_runtime import FakeTransport, binding, response, MESSAGES
        from eval.evaluation_runtime import EvaluationRuntime
        raw=response('```json\n{"fixture":true}\n```')
        with tempfile.TemporaryDirectory() as temp, deny_network():
            root=Path(temp);transport=FakeTransport(raw)
            runtime=EvaluationRuntime(binding(),root/'run','L4',root/'budget.sqlite',500,False,transport)
            result=runtime.request(MESSAGES,task_id='fenced')
            self.assertEqual(json.loads(result['normalized_body']),{'fixture':True})
            self.assertEqual(result['body'],raw['choices'][0]['message']['content'])
            self.assertEqual(result['raw_usage'],raw['usage'])
            self.assertTrue(result['response_format']['fence_removed'])
            receipt=json.loads(Path(result['evidence_path']).read_text())
            self.assertEqual(receipt['raw_response'],raw)
            runtime.request(MESSAGES,task_id='fenced')
            self.assertEqual(len(transport.calls),1)

    def test_historical_rejection_immutable(self):
        from tests.test_evaluation_runtime import FakeTransport, binding, response, MESSAGES
        from eval.evaluation_runtime import EvaluationRuntime, RuntimeResponseError
        with tempfile.TemporaryDirectory() as temp, deny_network():
            root=Path(temp);transport=FakeTransport(response('```json\n{"fixture":true}\n```'))
            runtime=EvaluationRuntime(binding(),root/'run','L2',root/'budget.sqlite',500,False,transport)
            result=runtime.request(MESSAGES,task_id='old')
            path=Path(result['evidence_path']);record=json.loads(path.read_text())
            record.update(state='REJECTED_RESPONSE',error='INVALID_JSON_RESPONSE')
            record['result'].pop('response_format')
            runtime._save(path,record);before=path.read_bytes()
            with self.assertRaises(RuntimeResponseError):runtime.request(MESSAGES,task_id='old')
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(len(transport.calls),1)

    def test_l3_fenced_pipeline_trace_and_resume(self):
        from eval.agent_evaluation.common import DESIGN, read_json, read_jsonl
        from eval.agent_evaluation.fixtures import FixtureScript, fixture_manifest
        from eval.agent_evaluation.orchestrator import Orchestrator
        from eval.agent_evaluation.transport import MockTransport
        with tempfile.TemporaryDirectory() as temp, deny_network():
            root=Path(temp)/'fixtures/fenced'
            manifest,specs=fixture_manifest(root,ids=['C01'],methods=['AUCMR'])
            script=FixtureScript(specs,manifest['sources'])
            transport=MockTransport(lambda t,m,a:'```json\n'+json.dumps(script(t,m,a))+'\n```')
            config=read_json(DESIGN/'config.json')
            result=Orchestrator(root,config,manifest,transport).run(['C01'])
            calls=len(transport.calls)
            self.assertGreater(calls,0)
            self.assertEqual(Orchestrator(root,config,manifest,transport).run(['C01']),result)
            self.assertEqual(len(transport.calls),calls)
            traces=read_jsonl(root/'request_trace.jsonl');self.assertTrue(traces)
            for trace in traces:
                self.assertTrue(trace['response']['response_format']['fence_removed'])
                self.assertTrue(trace['response']['body'].startswith('```json'))
                self.assertIsInstance(json.loads(trace['response']['normalized_body']),dict)

    def test_all_layer_fingerprints_include_normalizer(self):
        from eval.implementation import implementation_manifest
        for layer in ['l2','l3','l4']:
            self.assertIn('eval/evaluation_response_format.py',implementation_manifest(layer))


if __name__=='__main__':
    unittest.main()
