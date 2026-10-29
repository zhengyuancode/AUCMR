import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eval.agent_evaluation.budget import Budget
from eval.agent_evaluation.transport import deny_network, binding_blockers
from eval.evaluation_runtime import EvaluationRuntime, RuntimeBlocked, RuntimeResponseError
from eval.evaluation_request_budget import check_request
from eval.evaluation_billing import normalize_usage
from tests.test_evaluation_runtime import binding, response, MESSAGES


def configured():
    b = binding()
    b.update(cost_control='PROVIDER_MANAGED',binding_status='NOT_BOUND',
             interface_observation={'accepted_by_controller':True,'text_and_image_success_observed':True},
             cost_estimate={'input_cny_per_million':2,'output_cny_per_million':6,'source':'official-test-fixture'})
    b['billing']={'complete':False,'verified_upper_bound':False}
    b.pop('per_request_upper_bound')
    b['request_limits']={'verified':False,'text_tokens_per_utf8_byte':None}
    return b


class ProviderManagedTests(unittest.TestCase):
    def test_ledger_has_no_local_ceiling_or_charge_reservation(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger=Budget(Path(temp)/'budget.sqlite',cap=1,provider_managed=True)
            ledger.reserve('a','provider',None)
            ledger.settle('a',1000)
            ledger.reserve('b','provider',None)
            ledger.settle('b',None)
            ledger.reserve('c','provider',None)
            with ledger.connect() as db:
                self.assertEqual(db.execute('SELECT reserved,actual,state FROM reservations WHERE id="b"').fetchone(),(0,None,'UNPRICED'))
            self.assertIsNone(ledger.summary()['cap_cny'])
            self.assertEqual(ledger.summary()['accounted_cny'],1000)
            with self.assertRaises(ValueError):ledger.reserve('a','provider',None)

    def test_historical_unknown_hold_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger=Budget(Path(temp)/'budget.sqlite',provider_managed=True)
            with ledger.connect() as db:
                db.execute('INSERT INTO reservations VALUES (?,?,?,?,?,?)',('old','provider',500000,None,'UNKNOWN','historical'))
            ledger.reserve('new','provider',None)
            with ledger.connect() as db:
                self.assertEqual(db.execute('SELECT reserved,actual,state FROM reservations WHERE id="old"').fetchone(),(500000,None,'UNKNOWN'))
            self.assertEqual(ledger.summary()['accounted_cny'],.5)

    def test_request_checks_without_token_proof(self):
        b=configured();self.assertEqual(check_request(b,MESSAGES)['basis'],'PROVIDER_MANAGED_NO_TOKEN_BOUND')
        b['parameters']['max_tokens']=0
        with self.assertRaises(ValueError):check_request(b,MESSAGES)
        b=configured();b['request_limits']['max_request_bytes']=1
        with self.assertRaises(ValueError):check_request(b,MESSAGES)

    def test_estimate_and_unknown_usage_honest(self):
        b=configured();r=normalize_usage(response(),b)
        self.assertAlmostEqual(r['cost_cny'],.0008)
        self.assertEqual(r['cost_basis'],'UNCACHED_TOKEN_ESTIMATE_NOT_INVOICE')
        self.assertIsNone(r['invoice_cost_cny'])
        r=normalize_usage({'usage':{}},b)
        self.assertIsNone(r['cost_cny']);self.assertEqual(r['cost_basis'],'UNKNOWN')

    def test_live_runtime_authorization_receipt_and_no_resend(self):
        with tempfile.TemporaryDirectory() as temp, deny_network(), patch.dict('os.environ',{'AUCMR_RUNTIME_TEST_KEY':'fixture-key'}):
            root=Path(temp);runtime=EvaluationRuntime(configured(),root/'run','L2',root/'ledger.sqlite',live_authorized=True)
            with patch.object(runtime,'_http',return_value=response()) as http:
                a=runtime.request(MESSAGES,task_id='one');b=runtime.request(MESSAGES,task_id='one')
                self.assertEqual(http.call_count,1);self.assertTrue(b['cached'])
                self.assertEqual(a['raw_usage'],response()['usage'])
            with patch.object(runtime,'_http') as http:
                runtime.live_authorized=False
                with self.assertRaises(RuntimeBlocked):runtime.request(MESSAGES,task_id='unauthorized')
                http.assert_not_called()

    def test_missing_usage_recorded_without_fabricated_zero(self):
        with tempfile.TemporaryDirectory() as temp, deny_network(), patch.dict('os.environ',{'AUCMR_RUNTIME_TEST_KEY':'fixture-key'}):
            root=Path(temp);runtime=EvaluationRuntime(configured(),root/'run','L4',root/'ledger.sqlite',live_authorized=True)
            with patch.object(runtime,'_http',return_value=response(usage={})) as http:
                a=runtime.request(MESSAGES,task_id='one');b=runtime.request(MESSAGES,task_id='one')
                self.assertIsNone(a['cost_cny']);self.assertEqual(http.call_count,1)
                self.assertEqual(runtime.budget.summary()['states'],['UNPRICED'])

    def test_output_and_format_failures_still_rejected(self):
        with tempfile.TemporaryDirectory() as temp, deny_network(), patch.dict('os.environ',{'AUCMR_RUNTIME_TEST_KEY':'fixture-key'}):
            root=Path(temp);runtime=EvaluationRuntime(configured(),root/'run','L2',root/'ledger.sqlite',live_authorized=True)
            for i,raw in enumerate([response('bad json'),response(usage={'prompt_tokens':20,'completion_tokens':257})]):
                with patch.object(runtime,'_http',return_value=raw):
                    with self.assertRaises(RuntimeResponseError):runtime.request(MESSAGES,task_id=str(i))

    def test_l3_binding_checks_do_not_certify_billing(self):
        models={k:configured() for k in 'ABC'};models['B']['api_model_id']='fixture-model-B'
        c={'cost_control':'PROVIDER_MANAGED','budgets':{'live_authorized':True},'models':models}
        with patch.dict('os.environ',{'AUCMR_RUNTIME_TEST_KEY':'fixture-key'}):
            self.assertEqual(binding_blockers(c),[])
            c['budgets']['live_authorized']=False
            self.assertIn('LIVE_NOT_AUTHORIZED',binding_blockers(c))

    def test_l3_pipeline_uses_accounting_only_and_valid_records(self):
        from eval.agent_evaluation.common import DESIGN, read_json, read_jsonl
        from eval.agent_evaluation.fixtures import FixtureScript, fixture_manifest
        from eval.agent_evaluation.orchestrator import Orchestrator
        from eval.agent_evaluation.transport import MockTransport
        with tempfile.TemporaryDirectory() as temp, deny_network():
            root=Path(temp)/'fixtures/accounting-only'
            manifest,specs=fixture_manifest(root,ids=['C01'],methods=['AUCMR'])
            c=read_json(DESIGN/'config.json');c['cost_control']='PROVIDER_MANAGED'
            c['budgets']['campaign_hard_cap']=None
            transport=MockTransport(FixtureScript(specs,manifest['sources']))
            runner=Orchestrator(root,c,manifest,transport)
            result=runner.run(['C01']);calls=len(transport.calls)
            self.assertEqual(Orchestrator(root,c,manifest,transport).run(['C01']),result)
            self.assertEqual(len(transport.calls),calls)
            self.assertIsNone(runner.budget.summary()['cap_cny'])
            self.assertTrue(read_jsonl(root/'call_ledger.jsonl'))
            self.assertTrue(all(r['payload']['reserved_cny']==0 for r in read_jsonl(root/'call_ledger.jsonl')))


if __name__=='__main__':unittest.main()
