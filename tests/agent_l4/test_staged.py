import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from eval.l4_agent.contract import evidence_catalog, shape_errors, schema, expand, CONTRACT_VERSION
from eval.l4_agent.staged import validate_stage, stage_request
from eval.l4_agent.engine import _judge_one, validate_run
from eval.l4_agent.fixtures import make_fixtures
from eval.l4_agent.inputs import collect, freeze
from eval.l4_agent.protocol import REPO, network_blocked, read_json, L4Error
from eval.l4_agent.records import validate_record, seal
from eval.l4_agent.runner import run_evaluation
from eval.evaluation_runtime import RuntimeDeferred, RuntimeResponseError
from tests.agent_l4.test_runtime import fixture_binding


def answer(request, *, unresolved=False):
    common = {"score": None if unresolved else 3, "status": "UNRESOLVED" if unresolved else "SCORED",
              "reason": "Constructed test judgment", "unresolved": ["Constructed missing evidence"] if unresolved else []}
    evidence = [] if unresolved else [{"evidence_id": next(iter(request['evidence_catalog'])), "image_description": None}]
    if request['stage_id'] == 'prc':
        common['score'] = None if unresolved else 5
        common['modifications'] = [{'kind': 'NONE', 'location': 'ARTIFACT', 'need': 'Constructed inspected finding', 'evidence': evidence}]
    else:
        common['checks'] = [{'claim_id': cid, 'finding': 'UNRESOLVED' if unresolved else 'SUPPORTED',
                             'rationale': 'Constructed test rationale', 'evidence': evidence}
                            for cid in request['required_claim_ids']]
    return common


class StagedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=REPO/'outputs/agent_l4')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.blocker = network_blocked(); self.blocker.__enter__()
        self.addCleanup(self.blocker.__exit__, None, None, None)
        self.config = make_fixtures(self.root)
        self.config['request_organization'] = CONTRACT_VERSION
        self.manifest = collect(self.config)
        self.a = self.manifest['artifacts'][0]; self.t = self.config['targets'][0]
        self.sources = self.manifest['sources']

    def run_judge(self, client):
        return _judge_one(self.a, self.t, self.sources, self.config, self.manifest, self.root, client)

    def test_judgment_after_two_searches_reserves_a_correction_request(self):
        seen=[]
        class Client:
            def respond(inner,request,history):
                if request['stage_id']=='goal':
                    n=request['stage_limits']['current_request'];seen.append(n)
                    self.assertEqual(request['stage_limits']['requests_remaining_after_this'],4-n)
                    if n<3:return json.dumps({'tool_calls':[{'name':'source_search','arguments':{'source_id':self.sources[0]['source_id'],'query':'not-in-fixture-'+str(n)}}]})
                    self.assertTrue(request['judgment_only']);self.assertEqual(request['tools'],{})
                    self.assertNotIn('tool_request_example',request)
                    self.assertNotIn('tool_response_schema',request)
                return json.dumps(answer(request,unresolved=True))
        result=self.run_judge(Client());validate_record(result,self.a,self.manifest,self.config)
        self.assertEqual(seen,[1,2,3]);self.assertEqual(result['processing_status'],'UNRESOLVED')
        self.assertIsNone(result['pcs']['goal']['score'])

    def test_final_round_tool_call_rejected_without_execution_or_fifth_call(self):
        class Client:
            calls=0
            def respond(inner,request,history):
                inner.calls+=1
                return json.dumps({'tool_calls':[{'name':'source_search','arguments':{'source_id':self.sources[0]['source_id'],'query':'search-'+str(inner.calls)}}]})
        client=Client();result=self.run_judge(client)
        self.assertEqual(client.calls,4);self.assertEqual(result['processing_status'],'TECHNICAL_FAILURE')
        self.assertIn('JUDGMENT_PHASE_REQUIRES_RESULT',str(result['validation_errors']))
        self.assertFalse(any(e['arguments'].get('query')=='search-3' for e in result['source_access']))
        self.assertFalse(any(e['arguments'].get('query')=='search-4' for e in result['source_access']))

    def test_third_request_invalid_judgment_corrected_on_fourth_without_redraw(self):
        seen=[]
        class Client:
            def respond(inner,request,history):
                n=request['stage_limits']['current_request']
                if request['stage_id']!='goal':return json.dumps(answer(request))
                seen.append(n)
                if n<3:return json.dumps({'tool_calls':[{'name':'source_search','arguments':{'source_id':self.sources[0]['source_id'],'query':'missing-'+str(n)}}]})
                value=answer(request,unresolved=True)
                if n==3:
                    value['checks'][0]['evidence']=[{'evidence_id':'L4-ACCESS-999:T0','image_description':None}]
                    return json.dumps(value)
                self.assertEqual(request['correction_mode'],'REPLACE_NAMED_CHECKS')
                self.assertEqual(request['tools'],{})
                target=request['target_claim_ids']
                return json.dumps({'replacements':{c['claim_id']:{k:v for k,v in c.items() if k!='claim_id'} for c in value['checks'] if c['claim_id'] in target},'assessment':{k:v for k,v in value.items() if k!='checks'}})
        result=self.run_judge(Client());validate_record(result,self.a,self.manifest,self.config)
        self.assertEqual(seen,[1,2,3,4]);self.assertEqual(result['processing_status'],'UNRESOLVED')
        class NoCalls:
            def respond(inner,*args):self.fail('Paid judgment resent')
        replay=self.run_judge(NoCalls());self.assertEqual(replay['raw_response'],result['raw_response'])

    def length_then_assessment(self, bad_final=False):
        seen=[]
        class Client:
            last_receipt=None
            def respond(inner,request,history):
                inner.last_receipt=None
                if request['stage_id']!='goal':return json.dumps(answer(request))
                n=request['stage_limits']['current_request'];seen.append(n)
                if n==1:
                    inner.last_receipt={'body':'','finish_reason':'length','cost_cny':0.01,'offline':True}
                    raise RuntimeResponseError('INCOMPLETE_FINISH_REASON',receipt=inner.last_receipt)
                if n==2:
                    from eval.l4_agent.client import model_messages
                    messages=model_messages(request,history)
                    self.assertIn('finish_reason=length',messages[-1]['content'])
                    self.assertFalse(any(m['role']=='assistant' for m in messages))
                    return json.dumps({'tool_calls':[{'name':'source_search','arguments':{'source_id':self.sources[0]['source_id'],'query':'missing'}}]})
                value=answer(request,unresolved=True)
                if n==3:
                    value.update(score=2,status='SCORED',unresolved=[])
                    return json.dumps(value)
                self.assertEqual(n,4)
                self.assertEqual(request['correction_mode'],'ASSESSMENT_ONLY')
                self.assertEqual(request['target_claim_ids'],[])
                assessment={k:v for k,v in value.items() if k!='checks'}
                if bad_final:assessment.update(score=2,status='SCORED',unresolved=[])
                return json.dumps(assessment)
        result=self.run_judge(Client());self.assertEqual(seen,[1,2,3,4])
        self.assertEqual(result['components'][0]['recovery_counters'],{'incomplete_recoveries':1,'validation_corrections':1})
        return result

    def test_length_recovery_preserves_assessment_correction_and_resume(self):
        result=self.length_then_assessment()
        self.assertEqual(result['processing_status'],'UNRESOLVED')
        part=result['components'][0]
        self.assertEqual(json.loads(part['correction_plan']['base_response'])['checks'],part['merged_response']['checks'])
        validate_record(result,self.a,self.manifest,self.config)
        class NoCalls:
            def respond(inner,*args):self.fail('Paid response resent')
        replay=self.run_judge(NoCalls());self.assertEqual(replay['raw_response'],result['raw_response'])

    def test_length_then_failed_fourth_correction_stops_without_fifth(self):
        result=self.length_then_assessment(bad_final=True)
        self.assertEqual(result['processing_status'],'TECHNICAL_FAILURE')
        self.assertIn('UNRESOLVED',str(result['validation_errors']))

    def test_second_truncation_stops_even_if_visible_json_parses(self):
        class Client:
            calls=0
            def respond(inner,request,history):
                inner.calls+=1
                inner.last_receipt={'body':json.dumps(answer(request)),'finish_reason':'length','cost_cny':0.01,'offline':True}
                raise RuntimeResponseError('INCOMPLETE_FINISH_REASON',receipt=inner.last_receipt)
        client=Client();result=self.run_judge(client)
        self.assertEqual(client.calls,2);self.assertEqual(result['processing_status'],'TECHNICAL_FAILURE')
        self.assertEqual(result['components'][0]['recovery_counters'],{'incomplete_recoveries':1,'validation_corrections':0})

    def test_unpriced_truncated_response_stops_without_recovery(self):
        class Client:
            calls=0
            def respond(inner,request,history):
                inner.calls+=1
                inner.last_receipt={'body':'','finish_reason':'length','cost_cny':None,'offline':True}
                raise RuntimeResponseError('INCOMPLETE_FINISH_REASON',receipt=inner.last_receipt)
        client=Client();result=self.run_judge(client)
        self.assertEqual(client.calls,1);self.assertTrue(result['stop_run'])
        self.assertIn('UNKNOWN_COST_STOP',str(result['validation_errors']))

    def test_full_context_independent_prc_empty_slots_and_checkpoint_reuse(self):
        class Client:
            calls = []
            def respond(inner, request, history):
                inner.calls.append(deepcopy(request))
                self.assertEqual(request['artifact']['slots'], self.a['slots'])
                self.assertNotIn('pcs', request)
                return json.dumps(answer(request))
        client = Client(); result = self.run_judge(client)
        validate_record(result, self.a, self.manifest, self.config)
        self.assertEqual(result['processing_status'], 'COMPLETED')
        self.assertEqual(len(client.calls), 8 - len(self.a['empty_slots']))
        self.assertTrue(all(r['stage_id'] not in self.a['empty_slots'] for r in client.calls))
        self.assertEqual(client.calls[-1]['stage_id'], 'prc')
        class NoCalls:
            def respond(inner,*args): self.fail('Paid completed stage resent')
        replay = self.run_judge(NoCalls())
        self.assertEqual(replay['raw_response'], result['raw_response'])
        validate_record(replay, self.a, self.manifest, self.config)
        broken = deepcopy(replay); broken['raw_response'] = broken['raw_response'].replace('Constructed test judgment','Invented judgment',1)
        broken.pop('record_sha256'); broken = seal(broken)
        with self.assertRaises(L4Error): validate_record(broken,self.a,self.manifest,self.config)

    def test_semantic_unresolved_is_valid_and_not_zero(self):
        class Client:
            def respond(inner, request, history): return json.dumps(answer(request, unresolved=True))
        result = self.run_judge(Client())
        validate_record(result, self.a, self.manifest, self.config)
        self.assertEqual(result['processing_status'], 'UNRESOLVED')
        self.assertIsNone(result['pcs_mean']); self.assertIsNone(result['prc']['score'])

    def test_actual_empty_slots_never_requested_and_low_score_stays_low(self):
        self.a=next(a for a in self.manifest['artifacts'] if a['state']=='PRESENT' and a['empty_slots'])
        self.t=next(t for t in self.config['targets'] if t['task_id']==self.a['task_id'])
        seen=[]
        class Client:
            def respond(inner,request,history):
                seen.append(request['stage_id']);v=answer(request)
                if request['stage_id']!='prc':
                    v['score']=0
                    for c in v['checks']:c['finding']='CONTRADICTED'
                return json.dumps(v)
        result=self.run_judge(Client());validate_record(result,self.a,self.manifest,self.config)
        self.assertTrue(set(self.a['empty_slots']).isdisjoint(seen))
        self.assertEqual(result['pcs_mean'],0)
        for s in self.a['empty_slots']:self.assertEqual(result['pcs'][s]['status'],'EMPTY_SLOT')

    def test_new_organization_requires_live_authorization_before_client(self):
        path=self.root/'unauthorized.json';path.write_text(json.dumps(self.config))
        with patch('eval.l4_agent.client.RuntimeJudgeClient.respond') as call,self.assertRaises(L4Error):
            run_evaluation(path,run_dir=self.root,explicit_live=False)
        call.assert_not_called()

    def test_malformed_json_bounded_feedback_and_no_score_repair(self):
        class Client:
            calls = 0
            def respond(inner,request,history):
                inner.calls += 1
                return '{"score":3'
        client=Client();result=self.run_judge(client)
        self.assertEqual(client.calls,2)
        self.assertEqual(result['processing_status'],'TECHNICAL_FAILURE')
        self.assertIsNone(result['prc']['score']);validate_record(result,self.a,self.manifest,self.config)

    def test_missing_ids_explicit_feedback_and_single_correction(self):
        class Client:
            calls = 0
            def respond(inner,request,history):
                inner.calls += 1
                value=answer(request)
                if inner.calls==1:value['checks']=[]
                elif inner.calls==2:
                    self.assertIn('missing_claim_ids',history[-1]['validation_feedback']['errors'][0])
                return json.dumps(value)
        result=self.run_judge(Client())
        self.assertEqual(result['processing_status'],'COMPLETED')

    def test_stage_schema_and_evidence_reject_wrong_fields_ids_and_uncited_scores(self):
        class Capture:
            request=None
            def respond(inner,request,history):
                inner.request=request
                raise RuntimeDeferred('test')
        client=Capture()
        with self.assertRaises(RuntimeDeferred):self.run_judge(client)
        r=client.request; accesses=json.loads(json.dumps([]))
        # Real source access audit is reproduced with the same bootstrap helper.
        from eval.l4_agent.sources import SourceTools
        from eval.l4_agent.engine import bootstrap_sources
        tools=SourceTools(self.sources,self.a['project_id'],self.config['source_policy'],self.root/'audit.jsonl','test')
        bootstrap_sources(tools,self.t,self.sources); accesses=tools.entries
        good=answer(r);validate_stage(json.dumps(good),r['stage_id'],self.a,accesses)
        for mutate in (
            lambda v:v['checks'][0].pop('evidence'),
            lambda v:v['checks'][0].update(evidence=[]),
            lambda v:v['checks'][0]['evidence'][0].update(evidence_id='OTHER_PAGE'),
            lambda v:v['checks'][0].update(claim_id='made-up:0'),
            lambda v:v['checks'].append(deepcopy(v['checks'][0])),
            lambda v:v.update(status='UNRESOLVED',unresolved=['unknown']),
            lambda v:v['checks'][0].update(finding='UNRESOLVED'),
        ):
            value=deepcopy(good);mutate(value)
            with self.assertRaises(ValueError):validate_stage(json.dumps(value),r['stage_id'],self.a,accesses)
        pr=stage_request('prc',self.a,self.t,self.sources,self.config['source_policy'],accesses)
        bad=answer(pr);bad['score']=4;bad['modifications'][0].update(kind='FACTUAL',location='ARTIFACT')
        with self.assertRaises(ValueError):validate_stage(json.dumps(bad),'prc',self.a,accesses)

    def test_real_runtime_resume_does_not_resend_paid_partial_stage(self):
        c=deepcopy(self.config);c['targets']=[self.t];c['methods']=[self.a['method']]
        c['input_bindings']=[b for b in c['input_bindings'] if b['task_id']==self.a['task_id'] and b['method']==self.a['method']]
        c['model']=fixture_binding();c['runtime']={'campaign_ledger':str((self.root/'fixtures/budget.sqlite').relative_to(REPO)),'budget_cap_cny':500}
        path=self.root/'config.requested.json';path.write_text(json.dumps(c))
        class Transport:
            mode='mock';calls=0;interrupt=True
            def request(inner,binding,messages,task,attempt):
                inner.calls+=1
                request=json.loads(messages[1]['content'])
                return {'model':'fixture-model','choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer(request))}}],
                        'usage':{'prompt_tokens':100,'completion_tokens':50}}
        transport=Transport()
        from eval.l4_agent.client import RuntimeJudgeClient
        original=RuntimeJudgeClient.respond;count=0
        def interrupt(client,request,history):
            nonlocal count
            count+=1
            raw=original(client,request,history)
            if count==2:raise RuntimeDeferred('Interruption after receipt persisted, before component saved')
            return raw
        with patch.object(RuntimeJudgeClient,'respond',interrupt),self.assertRaises(RuntimeDeferred):
            run_evaluation(path,run_dir=self.root,transport=transport)
        paid=transport.calls
        report=run_evaluation(resume=self.root,transport=transport)
        self.assertEqual(paid,2);self.assertEqual(transport.calls,8-len(self.a['empty_slots']))
        self.assertEqual(report['technical_failures'],0)
        validate_run(self.root)
