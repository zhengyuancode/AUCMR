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
