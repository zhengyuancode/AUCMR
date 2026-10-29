"""Offline crash/restart checks using constructed source pages only."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eval.agent_evaluation.agents.executor import Executor
from eval.agent_evaluation.budget import Budget
from eval.agent_evaluation.common import DESIGN, Blocked, EvaluationError, read_json, read_jsonl
from eval.agent_evaluation.fixtures import fixture_manifest
from eval.agent_evaluation.source_access import SourceAccess
from eval.agent_evaluation.transport import MockTransport, deny_network


class RequestRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'fixtures' / 'recovery'
        self.manifest, _ = fixture_manifest(self.root, ids=['C01'], methods=['AUCMR'])
        self.config = read_json(DESIGN / 'config.json')
        self.budget = Budget(self.root / 'budget.sqlite')
        self.transport = MockTransport(lambda task, messages, attempt:
            {'tool_calls': [{'name': 'source_page', 'arguments': {'source_id': 'FIX-C01', 'pdf_page': 1}}]}
            if attempt == 1 else {'records': []})

    def execute(self, root=None, config=None):
        root = root or self.root
        source = SourceAccess(self.manifest['sources'], 'C01', 'CONTENT_A', root / 'access.jsonl')
        with deny_network():
            Executor(root, config or self.config, self.transport, self.budget).execute(
                task_id='same-task', role='CONTENT_A', view='A_FIRST', kinds=['content'],
                materials={}, source=source, validate=lambda *args: None)
        return source

    def test_implementation_change_rejects_completed_task(self):
        self.execute()
        count = len(self.transport.calls)
        with patch('eval.agent_evaluation.agents.executor.implementation_manifest', return_value={'changed':'code'}):
            with self.assertRaises(EvaluationError):
                self.execute()
        self.assertEqual(len(self.transport.calls), count)

    def test_retry_receives_failed_response_and_specific_parser_error(self):
        malformed = '{"records":[}'
        def scripted(task, messages, attempt):
            if attempt == 1:
                self.assertIn('系统提示词是评价规则，不是来源正文', messages[-1]['content'])
                return malformed
            self.assertEqual(messages[-2], {'role': 'assistant', 'content': malformed})
            self.assertIn('JSONDecodeError', messages[-1]['content'])
            self.assertIn('未读取来源时应先请求工具', messages[-1]['content'])
            return {'records': []}
        self.transport.scripted = scripted
        self.execute()
        self.assertEqual(len(self.transport.calls), 2)
        trace = read_jsonl(self.root / 'request_trace.jsonl')
        self.assertEqual(trace[0]['response']['body'], malformed)
        self.assertIn('JSONDecodeError', trace[0]['error'])
        self.execute()
        self.assertEqual(len(self.transport.calls), 2)

    def test_schema_error_feedback_keeps_location_without_schema_dump(self):
        from jsonschema import Draft202012Validator
        self.transport.scripted = lambda task, messages, attempt: {'records': [{'bad': 1}]} if attempt == 1 else {'records': []}
        source = SourceAccess(self.manifest['sources'], 'C01', 'CONTENT_A', self.root / 'access.jsonl')
        def validate(rows, access):
            Draft202012Validator({'type': 'array', 'items': {'type': 'object', 'additionalProperties': False}}).validate(rows)
        with deny_network():
            Executor(self.root, self.config, self.transport, self.budget).execute(
                task_id='schema-error', role='CONTENT_A', view='A_FIRST', kinds=['content'],
                materials={}, source=source, validate=validate)
        error = read_jsonl(self.root / 'request_trace.jsonl')[0]['error']
        self.assertIn('"path":[0]', error)
        self.assertIn('bad', error)
        self.assertNotIn('Failed validating', error)

    def test_extra_brace_is_diagnosed_but_not_silently_accepted(self):
        raw = '{"records":[{"kind":"scope","payload":{}}},{"kind":"scene_proposal","payload":{}}]}'
        def scripted(task, messages, attempt):
            if attempt == 1:
                return raw
            self.assertIn('单个多余', messages[-1]['content'])
            self.assertEqual(messages[-2]['content'], raw)
            return {'records': []}
        self.transport.scripted = scripted
        self.execute()
        trace = read_jsonl(self.root / 'request_trace.jsonl')
        self.assertIn('JSONDecodeError', trace[0]['error'])
        self.assertEqual(trace[0]['response']['body'], raw)
        self.assertEqual(len(self.transport.calls), 2)

    def test_incomplete_finish_or_missing_model_never_produces_records(self):
        for number, change in enumerate(({'finish_reason':'content_filter'}, {'finish_reason':None}, {'response_model':None})):
            original = self.transport.request
            def malformed(*args, **kwargs):
                result = original(*args, **kwargs)
                result.update(change)
                return result
            root = self.root / ('invalid-' + str(number))
            with patch.object(self.transport, 'request', side_effect=malformed):
                with self.assertRaises(EvaluationError):
                    self.execute(root=root)
            self.assertFalse(list((root / 'records').glob('*.json')))
            self.assertTrue(read_jsonl(root / 'request_trace.jsonl'))

    def test_resume_response_saved_before_settlement(self):
        with patch.object(self.budget, 'settle', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.execute()
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(self.budget.summary()['states'], ['RESERVED'])
        self.execute()
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual(self.budget.summary()['states'], ['SETTLED', 'SETTLED'])

    def test_resume_mid_task_reuses_paid_request_and_exact_tool_result(self):
        reserve = self.budget.reserve
        counter = 0
        def interrupt_second(*args):
            nonlocal counter
            counter += 1
            if counter == 2:
                raise KeyboardInterrupt
            return reserve(*args)
        with patch.object(self.budget, 'reserve', side_effect=interrupt_second), self.assertRaises(KeyboardInterrupt):
            self.execute()
        source = self.execute()
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual(len(read_jsonl(self.root / 'call_ledger.jsonl')), 2)
        self.assertEqual(len(read_jsonl(self.root / 'access.jsonl')), 1)
        self.assertEqual(len(source.read_pages), 1)
        self.execute()
        self.assertEqual(len(self.transport.calls), 2)

    def test_resume_preserves_actual_crossref_allowance(self):
        # A crossref argument inside the ordinary page allowance does not consume
        # an extra-page allowance; recovery must preserve that distinction.
        self.transport.scripted = lambda task, messages, attempt: (
            {'tool_calls': [{'name': 'source_page', 'arguments': {
                'source_id': 'FIX-C01', 'pdf_page': 1,
                'crossref': {'from_page': 1, 'quote': 'Constructed reference'}}}]}
            if attempt == 1 else {'records': []})
        reserve = self.budget.reserve
        counter = 0
        def interrupt_second(*args):
            nonlocal counter
            counter += 1
            if counter == 2:
                raise KeyboardInterrupt
            return reserve(*args)
        with patch.object(self.budget, 'reserve', side_effect=interrupt_second), self.assertRaises(KeyboardInterrupt):
            self.execute()
        source = self.execute()
        self.assertEqual(len(source.read_pages), 1)
        self.assertEqual(source.crossrefs, set())
        self.assertEqual(len(self.transport.calls), 2)

    def test_changed_interrupted_input_is_rejected(self):
        with patch.object(self.budget, 'settle', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.execute()
        altered = copy.deepcopy(self.config)
        altered['judging']['max_requests_per_task'] += 1
        with self.assertRaises(EvaluationError):
            self.execute(config=altered)
        self.assertEqual(len(self.transport.calls), 1)

    def test_missing_response_never_resends_possibly_paid_request(self):
        with patch.object(self.transport, 'request', side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.execute()
        with self.assertRaises(Blocked):
            self.execute()
        self.assertEqual(self.budget.summary()['states'], ['RESERVED'])

    def test_shared_ledger_separates_runs(self):
        self.execute()
        self.execute(root=self.root.parent / 'other-run')
        self.assertEqual(len(self.transport.calls), 4)
        self.assertEqual(len(self.budget.summary()['states']), 4)


if __name__ == '__main__':
    unittest.main()
