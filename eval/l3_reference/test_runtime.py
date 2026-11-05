"""Source-backed synthetic offline regression; never live network."""
import contextlib
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from eval.agent_evaluation.common import digest, file_hash, write_json, read_json
from eval.agent_evaluation.transport import deny_network
from eval.agent_evaluation.budget import Budget
from . import runner
from .validation import source_context, validate_draft, validate_frozen, confined, quote_in_page
from .response import decode

class Fake:
    mode='mock'
    def __init__(self,n=6,pending=False):self.calls=[];self.n=n;self.pending=pending
    def request(self,binding,messages,task,attempt):
        self.calls.append(task['role']);stage=task['role'];materials=json.loads(messages[1]['content'])
        e={'pdf_page_1based':1,'quote':'Synthetic source evidence.','visual_region_description':''}
        final=stage in ('B_COMPARE','C_FINAL');ids=materials['candidate_ids']
        rows=[{'local_id':f'D{i+1:03d}','text':f'Synthetic requirement {i+1}','conditions':None,'status':('REFERENCE_UNRESOLVED' if self.pending and i==self.n-1 else 'CONFIRMED' if final else 'PROPOSED'),'evidence':[e]} for i in range(self.n)]
        if final:
            for r in rows:r['origin_ids']=[x for x in ids if x.endswith(':'+r['local_id'])]
        draft={'scope':{'goal':'Synthetic goal','actor':'Player','start_boundary':'Synthetic start','success_boundary':'Synthetic end','in_scope':['Synthetic action'],'out_of_scope':[],'evidence':[e]},'requirements':rows,'unresolved_issues':[]}
        if final:draft['candidate_decisions']=[{'candidate_id':x,'decision':'UNRESOLVED' if self.pending and x.endswith(f'D{self.n:03d}') else 'INCLUDED','final_ids':[x.split(':')[1]],'reason':'Synthetic source check','evidence':[e]} for x in ids]
        body=json.dumps({'draft':draft})
        return {'body':body,'raw_response':{'choices':[{'message':{'content':body},'finish_reason':'stop'}]},'finish_reason':'stop','response_model':'MOCK_NOT_RESEARCH','request_id':'mock-'+str(len(self.calls)),'usage':{'input_tokens':1,'output_tokens':1},'raw_usage':{'prompt_tokens':1,'completion_tokens':1},'cost_cny':0,'cost_basis':'SYNTHETIC'}

class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.base=Path(cls.temp.name);cls.bundle=cls.base/'source-only';cls.bundle.mkdir()
        import fitz
        pdf=fitz.open()
        for _ in range(15):p=pdf.new_page();p.insert_text((50,50),'Synthetic source evidence.')
        pdf.save(cls.bundle/'source.pdf');pdf.close()
        cls.tid=runner.PILOTS[0];s={'project_id':'qheadache','source_id':'SYNTHETIC','pdf':'source.pdf','pdf_sha256':file_hash(cls.bundle/'source.pdf'),'pages':15,'xml':None,'source_version':'SYNTHETIC_ONLY'}
        write_json(cls.bundle/'targets.json',{'targets':[{'target_id':cls.tid,'project_id':'qheadache','actor':'Player','name':'Synthetic target'}],'sources':[s]})
        write_json(cls.bundle/'allowlist.json',{'files':{p.name:file_hash(p) for p in cls.bundle.iterdir() if p.is_file()}})
        cls.ctx=source_context(cls.bundle,cls.tid);cls.config={'models':{x:{'provider':'MOCK','api_model_id':'MOCK-'+x,'parameters':{'max_tokens':8192}} for x in 'ABC'},'source_bundle_sha256':'a'*64,'reference_limits':{'max_requests_per_task':8,'max_format_repairs':2,'max_total_requests':80}}
        cls.cache={};spec=importlib.util.spec_from_file_location('coverage_contract',runner.PREPARATION/'tools/contract.py');cls.contract=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.contract)
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def fixture(self,n=6,pending=False):
        key=(n,pending)
        if key not in self.cache:
            root=self.base/f'run-{n}-{pending}';root.mkdir();t=Fake(n,pending);b=Budget(root/'ledger.sqlite',provider_managed=True);rs={}
            with deny_network(),contextlib.redirect_stdout(io.StringIO()):
                for stage in runner.STAGES:
                    m={} if stage not in ('B_COMPARE','C_FINAL') else {'A':rs['A_BUILD']['draft'],'B':rs['B_BLIND']['draft']}
                    if stage=='C_FINAL':m.update(B_review=rs['B_COMPARE']['draft'],C=rs['C_BLIND']['draft'])
                    rs[stage]=runner.task(root,self.ctx,stage,self.config,t,b,m)
                ref=runner.freeze(root,self.ctx,rs)
            self.cache[key]=(root,t,b,rs,ref)
        return self.cache[key]
    def coverage(self,r,labels):
        return {'protocol_version':runner.VERSION,'target_id':r['target_id'],'reference_version':1,'reference_sha256':r['content_sha256'],'artifact_id':'anon','artifact_sha256':'b'*64,'judgments':[{'requirement_id':x['requirement_id'],'label':label,'technical_status':'FAILED' if label is None else 'OK','rationale':'synthetic','reviewed_complete_output':True,'matches':[{'slot':'main_flow','claim_id':'C1','quote':'synthetic'}]} for x,label in zip(r['requirements'],labels)]}
    def score(self,r,j,root):return self.contract.score(r,j,'anon','b'*64,source_bundle=self.bundle,run_root=root)
    def entries(self,root,stage):return [json.loads(x) for x in (root/'tasks'/self.tid/stage/'access.jsonl').read_text().splitlines()]
    def test_unauthorized_zero_calls(self):
        with patch('sys.argv',['l3ref','--run-dir',str(self.base/'unauthorized')]),patch.object(runner,'LiveTransport') as live:self.assertRaisesRegex(ValueError,'authorization',runner.main);live.assert_not_called()
    def test_independent_freeze_resume_no_calls(self):
        root,t,b,rs,r=self.fixture();self.assertEqual(len(t.calls),5);self.assertEqual(len(validate_frozen(r,self.bundle,root)),6)
        for stage in ('A_BUILD','B_BLIND','C_BLIND'):self.assertEqual(read_json(root/rs[stage]['input_path'])['candidate_materials'],{})
        with deny_network():runner.task(root,self.ctx,'A_BUILD',self.config,t,b,{})
        self.assertEqual(len(t.calls),5)
    def test_unresolved_not_promoted(self):
        root,t,b,rs,r=self.fixture(2,True);body=copy.deepcopy(rs['C_FINAL']['draft']);body['requirements'][-1]['status']='CONFIRMED';ids={d['candidate_id']:{} for d in body['candidate_decisions']};self.assertRaisesRegex(ValueError,'promoted',validate_draft,body,self.ctx,self.entries(root,'C_FINAL'),ids)

    def test_intermediate_lineage_warning_preserves_conflict_and_final_is_strict(self):
        root,_,_,rs,_=self.fixture(2);body=copy.deepcopy(rs['B_COMPARE']['draft']);body['requirements'][0]['origin_ids']=[]
        candidates={p+':'+x['local_id']:x for p,s in [('A','A_BUILD'),('B','B_BLIND')] for x in rs[s]['draft']['requirements']}
        original=copy.deepcopy(body);ctx={**self.ctx,'quote_locator_policy':'ADVISORY_AGENT_QUOTE','review_stage':'B_COMPARE'}
        checks=validate_draft(body,ctx,self.entries(root,'B_COMPARE'),candidates)
        self.assertEqual(body,original);self.assertTrue(checks['lineage'])
        self.assertRaises(ValueError,validate_draft,body,{**ctx,'review_stage':'C_FINAL'},self.entries(root,'B_COMPARE'),candidates)

    def test_uncited_blind_proposal_and_missing_issue_metadata_are_explicit_warnings(self):
        root,_,_,rs,_=self.fixture(2);body=copy.deepcopy(rs['A_BUILD']['draft']);body['requirements'][0]['evidence']=[];body.pop('unresolved_issues')
        ctx={**self.ctx,'quote_locator_policy':'ADVISORY_AGENT_QUOTE','review_stage':'A_BUILD'}
        original=copy.deepcopy(body);checks=validate_draft(body,ctx,self.entries(root,'A_BUILD'))
        self.assertEqual(body,original)
        self.assertEqual(checks['requirements']['D001'][0]['mode'],'UNCITED_PROPOSED_REQUIREMENT')
        self.assertEqual(checks['metadata'][0]['mode'],'AGENT_ISSUE_LIST_NOT_PROVIDED')
        self.assertRaises(ValueError,validate_draft,body,self.ctx,self.entries(root,'A_BUILD'))
    def test_page_out_of_range(self):
        root,t,b,rs,r=self.fixture();body=copy.deepcopy(rs['A_BUILD']['draft']);body['scope']['evidence'][0]['pdf_page_1based']=999999;self.assertRaisesRegex(ValueError,'bounds',validate_draft,body,self.ctx,[])
    def test_unread_page_and_fake_quote(self):
        root,t,b,rs,r=self.fixture();body=copy.deepcopy(rs['A_BUILD']['draft']);self.assertRaisesRegex(ValueError,'actually read',validate_draft,body,self.ctx,[]);body['scope']['evidence'][0]['quote']='invented quote';self.assertRaisesRegex(ValueError,'not found',validate_draft,body,self.ctx,self.entries(root,'A_BUILD'))
    def test_pdf_discretionary_hyphen_equivalence(self):
        self.assertTrue(quote_in_page('is re-draw according', 'is\xa0re\xaddraw according'))
        self.assertTrue(quote_in_page('is redraw according', 'is\xa0re\xaddraw according'))
        self.assertFalse(quote_in_page('is not re-draw according', 'is\xa0re\xaddraw according'))
        self.assertFalse(quote_in_page('nonfunctional', 'non-functional'))
        self.assertTrue(quote_in_page("users' desires", 'users’ desires'))
        self.assertTrue(quote_in_page('press "Extract"', 'press “Extract”'))
        self.assertFalse(quote_in_page('do allow', 'do not allow'))
    def test_stale_software_diagnostic_preserves_sent_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            old={'status':'FORMAT_OR_LOCATOR_FAILED','error':'old software error'}
            write_json(folder/'validation-2.json',old);write_json(folder/'messages-3.json',[{'role':'user','content':'Validation error (data): '+json.dumps(old)+'. Return corrected JSON.'}])
            feedback,relevant=runner.repair_feedback(folder,2,ValueError('actual error'))
            self.assertEqual(feedback,old);self.assertFalse(relevant)
            write_json(folder/'validation-3.json',old)
            feedback,relevant=runner.repair_feedback(folder,3,ValueError('actual error'))
            self.assertTrue(relevant);self.assertEqual(feedback['error'],'actual error')
            self.assertEqual(read_json(folder/'validation-3.json'),old)
            self.assertEqual(read_json(folder/'validation-recheck-3.json'),feedback)
            write_json(folder/'messages-4.json',[{'role':'user','content':'Validation error (data): '+json.dumps(feedback)+'. Return corrected JSON.'}])
            restored,relevant=runner.repair_feedback(folder,3,ValueError('actual error'))
            self.assertEqual(restored,feedback);self.assertTrue(relevant)
    def test_lossless_draft_envelopes(self):
        draft={'scope':{'goal':'exact source text'},'requirements':[{'text':'Do not change'}],'unresolved_issues':[]}
        for raw in (draft,{'draft':{'scope':draft['scope']},'requirements':draft['requirements'],'unresolved_issues':[]}):
            body,operations=decode(json.dumps(raw));self.assertEqual(body,{'draft':draft});self.assertTrue(operations)
        self.assertEqual(decode(json.dumps({'draft':draft})),({'draft':draft},[]))
    def test_envelope_conflicts_and_invalid_json_rejected(self):
        for text in ('{"draft":{},"requirements":[],"unknown":true}', '{"draft":{"requirements":[]},"requirements":[]}', '{"draft":{},"draft":{}}', '{"draft":{}}}', '{"draft":NaN}'):
            self.assertRaises(ValueError,decode,text)
        for text in ('{"draft":{"scope":"unfinished', '{"draft":', '{"draft":{"requirements":[1,'):
            self.assertRaises(ValueError,decode,text)
    def test_unambiguous_eof_and_nested_fields_preserve_content(self):
        draft={'scope':{'goal':'source'},'requirements':[{'text':'must not change'}],'unresolved_issues':['unknown']}
        malformed=json.dumps({'draft':{'scope':{**draft['scope'],'requirements':draft['requirements'],'unresolved_issues':draft['unresolved_issues']}}})[:-1]
        body,operations=decode(malformed);self.assertEqual(body,{'draft':draft});self.assertEqual(len(operations),2)
        self.assertRaises(ValueError,decode,json.dumps({'draft':{'scope':{'requirements':[]},'requirements':[]}}))
    def test_normalized_envelope_still_requires_source_validation(self):
        root,t,b,rs,r=self.fixture();draft=copy.deepcopy(rs['A_BUILD']['draft']);draft['requirements'][0]['evidence'][0]['quote']='Invented requirement evidence'
        body,_=decode(json.dumps(draft));self.assertRaisesRegex(ValueError,'not found',validate_draft,body['draft'],self.ctx,self.entries(root,'A_BUILD'))
    def test_image_transcription_is_not_claimed_as_text_match(self):
        from .validation import check_evidence
        root,_,_,_,_=self.fixture();entries=self.entries(root,'A_BUILD')
        evidence=[{'pdf_page_1based':12,'quote':'Visual label','visual_region_description':'Top checkbox labeled Visual label'}]
        checks=check_evidence(evidence,self.ctx,entries)
        self.assertEqual(checks[0]['mode'],'IMAGE_TRANSCRIPTION_REQUIRES_SEMANTIC_REVIEW');self.assertTrue(checks[0]['image_sha256'])
        evidence[0]['pdf_page_1based']=1;self.assertRaises(ValueError,check_evidence,evidence,self.ctx,entries)
        evidence[0]['pdf_page_1based']=12;evidence[0]['visual_region_description']='Unrelated caption';self.assertRaises(ValueError,check_evidence,evidence,self.ctx,entries)
    def test_repair_and_total_request_limits_survive_resume(self):
        class InvalidQuote(Fake):
            def __init__(self,vary):super().__init__();self.vary=vary
            def request(self,*args):
                result=super().request(*args);body=json.loads(result['body'])
                body['draft']['scope']['evidence'][0]['quote']='Invented quote '+str(len(self.calls) if self.vary else 0)
                result['body']=json.dumps(body);return result
        for vary,expected in ((False,3),(True,8)):
            with tempfile.TemporaryDirectory() as tmp,deny_network():
                root=Path(tmp);transport=InvalidQuote(vary);budget=Budget(root/'ledger.sqlite',provider_managed=True)
                for _ in range(2):
                    self.assertRaises(ValueError,runner.task,root,self.ctx,'A_BUILD',self.config,transport,budget,{})
                    self.assertEqual(len(transport.calls),expected)
    def test_output_revision_preserves_prior_call_and_inputs(self):
        root0,t0,b0,rs,_=self.fixture();materials={'A':rs['A_BUILD']['draft'],'B':rs['B_BLIND']['draft']}
        with tempfile.TemporaryDirectory() as tmp,deny_network(),contextlib.redirect_stdout(io.StringIO()):
            root=Path(tmp);base=root/'tasks'/self.tid/'B_COMPARE';base.mkdir(parents=True)
            write_json(base/'call-1.json',{'status':'OK','finish_reason':'length','cost_cny':0})
            policy={'task_overrides':{self.tid+'/B_COMPARE':{'revision':'output-32768','max_output_tokens':32768}}};write_json(root/'execution-adjustments.json',policy)
            t=Fake();b=Budget(root/'ledger.sqlite',provider_managed=True)
            result=runner.task(root,self.ctx,'B_COMPARE',self.config,t,b,materials)
            self.assertIn('output-32768',result['input_path']);self.assertEqual(read_json(root/result['input_path'])['binding']['parameters']['max_tokens'],32768)
            self.assertEqual(read_json(base/'call-1.json')['finish_reason'],'length');self.assertEqual(runner.summary(root)['calls'],2)
            runner.task(root,self.ctx,'B_COMPARE',self.config,t,b,materials);self.assertEqual(len(t.calls),1)
    def test_output_revision_does_not_reset_stage_limit(self):
        with tempfile.TemporaryDirectory() as tmp,deny_network():
            root=Path(tmp);base=root/'tasks'/self.tid/'B_COMPARE';base.mkdir(parents=True)
            for i in range(8):write_json(base/f'call-{i+1}.json',{'status':'OK'})
            write_json(root/'execution-adjustments.json',{'task_overrides':{self.tid+'/B_COMPARE':{'revision':'output-32768','max_output_tokens':32768}}})
            t=Fake();b=Budget(root/'ledger.sqlite',provider_managed=True)
            self.assertRaisesRegex(ValueError,'including previous revisions',runner.task,root,self.ctx,'B_COMPARE',self.config,t,b,{})
            self.assertEqual(len(t.calls),0)
    def test_visual_requires_image(self):
        root,t,b,rs,r=self.fixture();body=copy.deepcopy(rs['A_BUILD']['draft']);body['scope']['evidence'][0].update(quote='',visual_region_description='imagined image');self.assertRaisesRegex(ValueError,'image',validate_draft,body,self.ctx,self.entries(root,'A_BUILD'))
    def test_frozen_scope_or_log_tamper(self):
        root,t,b,rs,r=self.fixture()
        for kind in ('scope','log'):
            bad=copy.deepcopy(r)
            if kind=='scope':bad['scope']['goal']='changed'
            else:bad['provenance']['B_BLIND']['access_log_sha256']='f'*64
            bad.pop('content_sha256');bad['content_sha256']=digest(bad);self.assertRaises(ValueError,validate_frozen,bad,self.bundle,root)
    def test_actual_proof_context_required(self):self.assertRaisesRegex(ValueError,'actual source',self.contract.validate_reference,self.fixture()[-1])
    def test_tool_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            src=runner.Source(self.ctx,'TEST',Path(tmp)/'access.jsonl','test');self.assertRaises(ValueError,src.invoke,'shell',{'command':'cat outputs/result'});self.assertRaises(ValueError,src.invoke,'source_page',{'source_id':'../outputs','pdf_page':1});self.assertRaises(ValueError,confined,self.bundle,'../outside')
    def test_changed_materials_refuse_resume(self):
        root,t,b,rs,r=self.fixture();self.assertRaisesRegex(ValueError,'changed',runner.task,root,self.ctx,'A_BUILD',self.config,t,b,{'leak':'proposal'})
    def test_uncertain_sent_request_not_resent(self):
        from eval.agent_evaluation.agents.recovery import TaskRecovery
        root,t,b,rs,r=self.fixture();recovery=TaskRecovery(root,'SYNTHETIC_INTERRUPTED','hash',b,t);b.reserve('interrupted','MOCK',None);n=len(t.calls);self.assertRaises(ValueError,recovery.request,'interrupted',{'provider':'MOCK'},[],{},1,None);self.assertEqual(len(t.calls),n)

    def test_defer_mode_still_reuses_previously_paid_format_corrections(self):
        class FirstTwoInvalid(Fake):
            def request(self,*args):
                result=super().request(*args)
                if len(self.calls)<=2:result['body']='INVALID_JSON_ALREADY_RECORDED'
                return result
        with tempfile.TemporaryDirectory() as tmp,deny_network(),contextlib.redirect_stdout(io.StringIO()):
            root=Path(tmp);transport=FirstTwoInvalid(1);budget=Budget(root/'ledger.sqlite',provider_managed=True)
            result=runner.task(root,self.ctx,'A_BUILD',self.config,transport,budget,{})
            self.assertEqual(len(transport.calls),3)
            (root/result['input_path']).with_name('result.json').unlink()  # Synthetic test fixture only.
            before={str(p):file_hash(p) for p in root.glob('task_progress/*/request-*.json')}
            runner.task(root,self.ctx,'A_BUILD',self.config,transport,budget,{},defer_paid_repairs=True)
            self.assertEqual(len(transport.calls),3)
            self.assertTrue(all(file_hash(Path(p))==h for p,h in before.items()))
    def test_redaction(self):
        from eval.evaluation_runtime import _redact
        self.assertNotIn('synthetic-test-secret',json.dumps(_redact({'error':'synthetic-test-secret'},'synthetic-test-secret')))
    def test_five_labels_failure_fixed_denominator(self):
        root,_,_,_,r=self.fixture();s=self.score(r,self.coverage(r,['FULL','PARTIAL','NOT_COVERED','CONTRADICTED','UNRESOLVED',None]),root);self.assertEqual(s['N'],6);self.assertEqual(s['RC_full'],1/6);self.assertEqual(s['status'],'INCOMPLETE')
    def test_missing_rows_and_method(self):
        root,_,_,_,r=self.fixture();s=self.score(r,self.coverage(r,['FULL']),root);self.assertEqual(s['counts']['TECHNICAL_FAILURE'],5);self.assertEqual(s['N'],6);self.assertEqual(self.score(r,None,root)['N'],6)
    def test_partial_no_half(self):
        root,_,_,_,r=self.fixture();self.assertEqual(self.score(r,self.coverage(r,['PARTIAL']*6),root)['RC_full'],0)
    def test_reference_unresolved_keeps_denominator(self):
        root,_,_,_,r=self.fixture(2,True);self.assertEqual(self.score(r,self.coverage(r,['FULL','UNRESOLVED']),root)['RC_full'],.5);self.assertRaises(ValueError,self.score,r,self.coverage(r,['FULL','FULL']),root)
    def test_empty_reference(self):
        root,_,_,_,r=self.fixture(0);self.assertIsNone(self.score(r,None,root)['RC_full'])
    def test_duplicate_rows_and_wrong_input(self):
        root,_,_,_,r=self.fixture();j=self.coverage(r,['FULL']);j['judgments']*=2;self.assertRaises(ValueError,self.score,r,j,root);j=self.coverage(r,['FULL']);j['artifact_sha256']='wrong';self.assertRaises(ValueError,self.score,r,j,root)
    def test_stale_version_all_seven(self):
        root,_,_,_,r=self.fixture();j=self.coverage(r,['FULL']*6);j['reference_version']=0
        for _ in range(7):self.assertRaises(ValueError,self.score,r,j,root)
    def test_output_evidence_and_context(self):
        root,_,_,_,r=self.fixture()
        for key,value in [('matches',[]),('reviewed_complete_output',False)]:
            j=self.coverage(r,['FULL']);j['judgments'][0][key]=value;self.assertRaises(ValueError,self.score,r,j,root)

if __name__=='__main__':unittest.main()
