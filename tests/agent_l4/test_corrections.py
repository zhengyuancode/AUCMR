import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from eval.l4_agent.corrections import (plan_correction, merge_correction, discovery_guidance,
                                      correction_reads, acquire_correction_evidence, correction_schema)
from eval.l4_agent.staged import validate_stage, correction_request
from eval.l4_agent.protocol import REPO, network_blocked
from eval.l4_agent.engine import _judge_one
from eval.l4_agent.inputs import collect, validate_config
from eval.l4_agent.fixtures import make_fixtures
from eval.l4_agent.records import validate_record, seal
from eval.l4_agent.client import model_messages
from eval.l4_agent.contract import CONTRACT_VERSION, shape_errors, schema, evidence_catalog
from tests.agent_l4.test_staged import answer


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.stage='preconditions'
        self.artifact={'slots':{self.stage:{'claims':[{'claim_id':f'preconditions:{i}'} for i in range(3)]}}}
        self.accesses=[
            {'access_id':'L4-ACCESS-3','operation':'source_search','arguments':{'source_id':'s','query':'track'},'result':{'status':'OK','hits':[{'pdf_page':113}]}},
            {'access_id':'L4-ACCESS-5','operation':'source_page','arguments':{'source_id':'s','pdf_page':25},'result':{'status':'OK','text':'Page 25 source text.'}},
        ]
        self.base={'score':None,'status':'UNRESOLVED','reason':'Insufficient evidence','unresolved':['No evidence for first claim'],
                   'checks':[{'claim_id':f'preconditions:{i}','finding':'UNRESOLVED' if i==0 else 'SUPPORTED',
                              'rationale':'Model-supplied original rationale',
                              'evidence':[] if i==0 else [{'evidence_id':'L4-ACCESS-3','image_description':None}]} for i in range(3)]}
        self.raw=json.dumps(self.base)
        self.plan=plan_correction(self.raw,self.stage,self.artifact,self.accesses)
        self.patch={'replacements':{f'preconditions:{i}':{'finding':'UNRESOLVED','rationale':'Model states page 113 must be read', 'evidence':[]} for i in [1,2]},
                    'assessment':{'score':None,'status':'UNRESOLVED','reason':'Still insufficient evidence','unresolved':['Missing page 113 evidence']}}

    def test_fixed_positions_preserve_untouched_check_and_require_exact_ids(self):
        self.assertEqual(self.plan['target_claim_ids'],['preconditions:1','preconditions:2'])
        merged=merge_correction(json.dumps(self.patch),self.plan,self.stage,self.artifact,self.accesses)
        self.assertEqual(merged['checks'][0],self.base['checks'][0])
        self.assertEqual([c['claim_id'] for c in merged['checks']],[f'preconditions:{i}' for i in range(3)])
        validate_stage(json.dumps(merged),self.stage,self.artifact,self.accesses)
        for mutate in [lambda p:p['replacements'].pop('preconditions:2'),
                       lambda p:p['replacements'].update({'preconditions:0':deepcopy(p['replacements']['preconditions:1'])}),
                       lambda p:p.update(checks=[]),
                       lambda p:p['replacements'].update({'preconditions:3':deepcopy(p['replacements']['preconditions:1'])})]:
            patch=deepcopy(self.patch);mutate(patch)
            with self.assertRaises(ValueError):merge_correction(json.dumps(patch),self.plan,self.stage,self.artifact,self.accesses)
        with self.assertRaises(ValueError):merge_correction(self.raw,self.plan,self.stage,self.artifact,self.accesses)
        raw=json.dumps(self.patch).replace('"preconditions:1":', '"preconditions:1": {}, "preconditions:1":',1)
        with self.assertRaises(ValueError):merge_correction(raw,self.plan,self.stage,self.artifact,self.accesses)

    def test_discovery_is_not_evidence_and_page_read_does_not_replace_judgment(self):
        guide=discovery_guidance(self.accesses)
        self.assertEqual(guide[0]['required_read']['arguments'],{'source_id':'s','pdf_page':113,'image':False})
        patch=deepcopy(self.patch)
        patch['replacements']['preconditions:1']['evidence']=[{'evidence_id':'L4-ACCESS-3','image_description':None}]
        with self.assertRaises(ValueError):merge_correction(json.dumps(patch),self.plan,self.stage,self.artifact,self.accesses)
        after=self.accesses+[{'access_id':'L4-ACCESS-6','operation':'source_page','arguments':{'source_id':'s','pdf_page':113},'result':{'status':'OK','text':'Page 113 actual original content.'}}]
        patch['replacements']['preconditions:1']['evidence']=[{'evidence_id':'L4-ACCESS-6:T0','image_description':None}]
        merged=merge_correction(json.dumps(patch),self.plan,self.stage,self.artifact,after)
        value=validate_stage(json.dumps(merged),self.stage,self.artifact,after)
        self.assertEqual(value['checks'][1]['source_refs'][0]['pdf_page'],113)
        self.assertEqual(value['checks'][1]['finding'],'UNRESOLVED')
        self.assertEqual(discovery_guidance(after),[])

    def test_duplicate_base_is_not_silently_deduped(self):
        self.base['checks'].append(deepcopy(self.base['checks'][1]))
        self.assertIsNone(plan_correction(json.dumps(self.base),self.stage,self.artifact,self.accesses))

    def test_assessment_only_never_requests_or_accepts_check_replacements(self):
        base=deepcopy(self.base)
        for check in base['checks']:check.update(finding='UNRESOLVED',evidence=[])
        base.update(score=2,status='SCORED',unresolved=[])
        plan=plan_correction(json.dumps(base),self.stage,self.artifact,self.accesses)
        self.assertEqual(plan['target_claim_ids'],[])
        assessment={'score':None,'status':'UNRESOLVED','reason':'Model assessment','unresolved':['Retained checks lack evidence']}
        request=correction_request({'required_claim_ids':['preconditions:0'],'legal_output_modes':{}},plan,self.stage,self.artifact,self.accesses)
        self.assertEqual(request['correction_mode'],'ASSESSMENT_ONLY')
        self.assertEqual(request['required_claim_ids'],[])
        self.assertNotIn('original_stage_response',request)
        self.assertNotIn('original_assessment',request)
        self.assertEqual(set(request['output_schema']['properties']),set(assessment))
        merged=merge_correction(json.dumps(assessment),plan,self.stage,self.artifact,self.accesses)
        self.assertEqual(merged['checks'],base['checks'])
        validate_stage(json.dumps(merged),self.stage,self.artifact,self.accesses)
        for extra in [{'replacements':{}},{'checks':base['checks']},{'replacements':{'preconditions:0':base['checks'][0]}}]:
            with self.assertRaises(ValueError):merge_correction(json.dumps({**assessment,**extra}),plan,self.stage,self.artifact,self.accesses)
        with self.assertRaises(ValueError):merge_correction(json.dumps({'assessment':assessment}),plan,self.stage,self.artifact,self.accesses)
        with self.assertRaises(ValueError):merge_correction(json.dumps({**assessment,'score':2,'status':'SCORED'}),plan,self.stage,self.artifact,self.accesses)

    def test_patch_assessment_obeys_retained_and_replaced_findings(self):
        # Compare complete judgment and patch validation over low scores and
        # unresolved combinations: no program-filled scores, no weaker patch.
        for retained in ['UNRESOLVED','SUPPORTED','CONTRADICTED','WRONG_SLOT']:
            for replacement in ['UNRESOLVED','SUPPORTED','CONTRADICTED','WRONG_SLOT']:
                for status,score,reasons in [('UNRESOLVED',None,['Evidence missing']),
                                             ('UNRESOLVED',3,['Evidence missing']),
                                             ('UNRESOLVED',None,[]),('SCORED',None,[]),
                                             ('SCORED',0,[]),('SCORED',2,[]),('SCORED',3,[])]:
                    with self.subTest(retained=retained,replacement=replacement,status=status,score=score,reasons=reasons):
                        base=deepcopy(self.base)
                        for i,c in enumerate(base['checks']):
                            c['finding']=retained if i==0 else replacement
                            c['evidence']=[{'evidence_id':'L4-ACCESS-5:T0','image_description':None}]
                        plan={**self.plan,'base_response':json.dumps(base)}
                        patch={'replacements':{c['claim_id']:{k:v for k,v in c.items() if k!='claim_id'} for c in base['checks'][1:]},
                               'assessment':{'status':status,'score':score,'unresolved':reasons,'reason':'Model judgment'}}
                        full={**patch['assessment'],'checks':base['checks']}
                        full_errors=shape_errors(full,schema(self.stage,self.artifact,evidence_catalog(self.accesses)))
                        patch_errors=shape_errors(patch,correction_schema(plan,self.stage,self.artifact,self.accesses))
                        self.assertEqual(bool(patch_errors),bool(full_errors))
                        if retained=='UNRESOLVED' and status=='SCORED':self.assertTrue(patch_errors)

    def test_only_explicit_search_hit_pages_are_actually_read_once(self):
        self.base['checks'][1]['rationale']='Search shows page 113; page 999 is unavailable.'
        self.base['checks'][2]['rationale']='第 113 页。'
        plan=plan_correction(json.dumps(self.base),self.stage,self.artifact,self.accesses)
        expected={'name':'source_page','arguments':{'source_id':'s','pdf_page':113,'image':False}}
        self.assertEqual(correction_reads(plan),[expected])
        class Tools:
            policy={'tools_per_round':2}
            calls=[]
            def invoke(inner,name,arguments):
                inner.calls.append({'name':name,'arguments':arguments})
                return {'status':'OK','text':'Actually read content','access_id':'L4-ACCESS-6'}
        tools=Tools();results=acquire_correction_evidence(plan,tools)
        self.assertEqual(tools.calls,[expected]);self.assertEqual(results[0]['result']['status'],'OK')
        self.assertEqual(plan['base_response'],json.dumps(self.base))  # no invented judgment
        self.assertEqual(correction_reads(self.plan),[])  # no explicit page, no guess
        plan['base_accesses'].append({'access_id':'other','operation':'source_search',
            'arguments':{'source_id':'other-source'},'result':{'status':'OK','hits':[{'pdf_page':113}]}})
        base=json.loads(plan['base_response'])
        for c in base['checks'][1:]:c['evidence'].append({'evidence_id':'other','image_description':None})
        plan['base_response']=json.dumps(base)
        self.assertEqual(correction_reads(plan),[])  # ambiguous source identity

    def test_patch_messages_do_not_simulate_tool_dialogue_or_allow_tool_execution(self):
        with tempfile.TemporaryDirectory(dir=REPO/'outputs/agent_l4') as tmp, network_blocked():
            root=Path(tmp);config=make_fixtures(root);config['request_organization']=CONTRACT_VERSION
            manifest=collect(config);artifact=manifest['artifacts'][0];task=config['targets'][0]
            class Client:
                calls=0
                def respond(inner,request,history):
                    inner.calls+=1
                    if inner.calls==1:
                        value=answer(request);value['checks'][0]['evidence'][0]['evidence_id']='FAKE'
                        return json.dumps(value)
                    self.assertEqual(request['tools'],{})
                    self.assertEqual(request['discovery_only_pages'],[])
                    messages=model_messages(request,history)
                    self.assertFalse(any(m['role']=='assistant' for m in messages))
                    self.assertFalse(any('"tool_results":' in json.dumps(m) or '"tool_calls":' in json.dumps(m) for m in messages))
                    return json.dumps({'tool_calls':[{'name':'source_page','arguments':{'source_id':'fake','pdf_page':999}}]})
            client=Client();record=_judge_one(artifact,task,manifest['sources'],config,manifest,root,client)
            self.assertEqual(client.calls,2)
            self.assertEqual(record['processing_status'],'TECHNICAL_FAILURE')
            self.assertIn('PATCH_ONLY_NO_TOOL_CALLS',str(record['validation_errors']))
            self.assertFalse(any(e['arguments'].get('pdf_page')==999 for e in record['source_access']))

    def test_search_chunk_typo_only_locates_recorded_explicit_page(self):
        base=deepcopy(self.base)
        base['checks'][1].update(rationale='Page 113 is described in the search.',
            evidence=[{'evidence_id':'L4-ACCESS-3:T0','image_description':None}])
        base['checks'][2].update(finding='UNRESOLVED',evidence=[])
        plan=plan_correction(json.dumps(base),self.stage,self.artifact,self.accesses)
        self.assertEqual(plan['target_claim_ids'],['preconditions:1'])
        self.assertEqual(correction_reads(plan),[{'name':'source_page','arguments':{'source_id':'s','pdf_page':113,'image':False}}])
        with self.assertRaises(ValueError):validate_stage(json.dumps(base),self.stage,self.artifact,self.accesses)
        for identifier in ['L4-ACCESS-30:T0','L4-ACCESS-3:T00','L4-ACCESS-3:T0junk','L4-ACCESS-3:I0',' L4-ACCESS-3:T0','L4-ACCESS-5:T0']:
            value=deepcopy(base);value['checks'][1]['evidence'][0]['evidence_id']=identifier
            candidate={**plan,'base_response':json.dumps(value)}
            self.assertEqual(correction_reads(candidate),[],identifier)
        value=deepcopy(base);value['checks'][1]['rationale']='Page 112 is not a recorded hit'
        self.assertEqual(correction_reads({**plan,'base_response':json.dumps(value)}),[])

    def test_access_page_locator_requires_matching_explicit_recorded_page(self):
        base=deepcopy(self.base)
        base['checks'][1].update(rationale='Page 113 contains the requested section.',
            evidence=[{'evidence_id':'L4-ACCESS-3:113','image_description':None}])
        base['checks'][2].update(finding='UNRESOLVED',evidence=[])
        plan=plan_correction(json.dumps(base),self.stage,self.artifact,self.accesses)
        self.assertEqual(correction_reads(plan),[{'name':'source_page','arguments':{'source_id':'s','pdf_page':113,'image':False}}])
        with self.assertRaises(ValueError):validate_stage(json.dumps(base),self.stage,self.artifact,self.accesses)
        for identifier,rationale in [('L4-ACCESS-3:112','Page 113'),('L4-ACCESS-3:113','No explicit page'),('L4-ACCESS-3:999','Page 999'),('L4-ACCESS-5:113','Page 113')]:
            value=deepcopy(base);value['checks'][1]['evidence'][0]['evidence_id']=identifier;value['checks'][1]['rationale']=rationale
            self.assertEqual(correction_reads({**plan,'base_response':json.dumps(value)}),[])

    def test_full_component_patch_audit_and_resume(self):
        with tempfile.TemporaryDirectory(dir=REPO/'outputs/agent_l4') as tmp, network_blocked():
            root=Path(tmp);config=make_fixtures(root);config['request_organization']=CONTRACT_VERSION
            manifest=collect(config);artifact=manifest['artifacts'][0];task=config['targets'][0]
            class Client:
                calls=0
                def respond(inner,request,history):
                    inner.calls+=1
                    value=answer(request)
                    if inner.calls==1:
                        value['checks'][0]['evidence'][0]['evidence_id']='L4-ACCESS-SEARCH'
                    if request.get('correction_mode'):
                        self.assertEqual(inner.calls,2)
                        self.assertIn('Replace existing checks',model_messages(request,history)[-1]['content'])
                        return json.dumps({'replacements':{c['claim_id']:{k:v for k,v in c.items() if k!='claim_id'} for c in value['checks']},
                                           'assessment':{k:v for k,v in value.items() if k!='checks'}})
                    return json.dumps(value)
            client=Client();record=_judge_one(artifact,task,manifest['sources'],config,manifest,root,client)
            self.assertEqual(record['processing_status'],'COMPLETED');validate_record(record,artifact,manifest,config)
            part=record['components'][0]
            self.assertIn('replacements',json.loads(part['raw_response']))
            self.assertIn('checks',part['merged_response'])
            class NoCalls:
                def respond(inner,*args):self.fail('Resent completed paid judgment')
            replay=_judge_one(artifact,task,manifest['sources'],config,manifest,root,NoCalls())
            self.assertEqual(replay['raw_response'],record['raw_response']);validate_record(replay,artifact,manifest,config)
            config['request_organization']='L4_SLOT_EVIDENCE_2.0.0'
            with self.assertRaises(ValueError):validate_config(config)
