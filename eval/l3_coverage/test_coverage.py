"""Synthetic source-backed coverage tests. All network access is denied."""
import contextlib
import copy
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from eval.agent_evaluation.common import digest,file_hash,read_json,write_json
from eval.agent_evaluation.budget import Budget
from eval.agent_evaluation.transport import deny_network
from eval.l3_reference.validation import source_context
from .contract import aggregate,decode,validate,sample,reconcile,deterministic_rows,quote_span
from .runner import Experiment,main
from .tasks import task,GlobalStop,ReviewSource
from .inputs import SLOTS
from .technical_recovery import decode_citations,DECODER,POLICY

class Quiet:
    def __init__(self):self.stop=threading.Event()
    def event(self,*args,**kwargs):pass

class Fake:
    mode='mock'
    def __init__(self):self.calls=[]
    def request(self,binding,messages,info,attempt):
        self.calls.append({'binding':binding,'messages':copy.deepcopy(messages),'info':info})
        data=json.loads(messages[1]['content']);artifact=data['complete_generated_output'];text=artifact['slots']['main_flow']['claims'][0]['content']
        label='FULL'
        if artifact['artifact_id'].startswith('CAL-'):
            label={'CAL-EXACT':'FULL','CAL-MISSING_ORDER':'PARTIAL','CAL-UNRELATED':'NOT_COVERED','CAL-REVERSED_ORDER':'CONTRADICTED'}[artifact['artifact_id']]
        elif info['role']=='B':label='PARTIAL'
        rows=[]
        for r in data['reference']['requirements']:
            rows.append({'requirement_id':r['requirement_id'],'label':label,'technical_status':'OK','reviewed_complete_output':True,'rationale':'Synthetic semantic response, never real evaluation.',
                         'matches':[] if label=='NOT_COVERED' else [{'slot':'main_flow','claim_id':artifact['slots']['main_flow']['claims'][0]['claim_id'],'quote':text}]})
        body=json.dumps({'judgments':rows,'reference_issues':[]})
        return {'body':body,'finish_reason':'stop','response_model':'MOCK','cost_cny':0,'cost_basis':'OFFLINE','raw_usage':{'prompt_tokens':1,'completion_tokens':1},'raw_response':{'choices':[{'message':{'content':body},'finish_reason':'stop'}]}}

class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)/'run';self.root.mkdir();bundle=Path(self.temp.name)/'bundle';bundle.mkdir()
        import fitz
        pdf=fitz.open()
        for _ in range(3):pdf.new_page().insert_text((30,30),'Synthetic menu source.')
        pdf.save(bundle/'source.pdf');pdf.close()
        targets=[{'target_id':'T1','project_id':'P1','actor':'Player','name':'Synthetic target'},{'target_id':'T2','project_id':'P1','actor':'Player','name':'Second target'}]
        source={'source_id':'S1','project_id':'P1','pdf':'source.pdf','pdf_sha256':file_hash(bundle/'source.pdf'),'pages':3,'xml':None,'source_version':'TEST'}
        write_json(bundle/'targets.json',{'targets':targets,'sources':[source]});write_json(bundle/'allowlist.json',{'files':{p.name:file_hash(p) for p in bundle.iterdir()}})
        self.contexts={t['target_id']:source_context(bundle,t['target_id']) for t in targets}
        evidence=[{'pdf_page_1based':1,'quote':'Synthetic menu source.'}]
        self.ref={'target_id':'T1','version':1,'reference_sha256':'a'*64,'scope':{'goal':'Synthetic','evidence':evidence},'requirements':[
            {'requirement_id':'R1','text':'The Game menu shall contain Open game, listed before Save and Exit.','conditions':None,'status':'CONFIRMED','evidence':evidence},
            {'requirement_id':'R2','text':'Unresolved source condition','conditions':None,'status':'REFERENCE_UNRESOLVED','evidence':evidence}]}
        slots={s:{'claims':[]} for s in SLOTS};slots['main_flow']['claims']=[{'claim_id':'CL1','content':'The Game menu contains Open game.'}]
        self.artifact={'artifact_id':'ART1','artifact_sha256':'b'*64,'slots':slots,'no_content':False,'explicit_refusal':False}
        self.config={'models':{r:{'provider':'MOCK-'+r,'api_model_id':'MOCK-'+r,'parameters':{'max_tokens':8192}} for r in 'ABC'},'chunk_size':20,'max_requests_per_task':6,'format_repairs':1}
        self.case={'case_id':'K1','target_id':'T1','project_id':'P1','method':'M1','binding':{'sha256':'b'*64},'artifact':self.artifact,'input_error':None}
        self.plan={'references':{'T1':self.ref},'cases':{'K1':self.case},'methods':['M1'],'pilot_targets':['T1'],'order_A':['K1'],'order_B':['K1']}
        self.budget=Budget(self.root/'test-ledger.sqlite',provider_managed=True);self.fake=Fake();self.progress=Quiet();self.exp=Experiment(self.root,self.config,self.plan,self.contexts,self.fake,self.budget,self.progress)
    def call(self,**options):
        with deny_network():return task(self.root,self.config,self.contexts['T1'],self.ref,self.artifact,[self.ref['requirements'][0]],options.get('phase','INITIAL'),options.get('role','A'),self.budget,options.get('transport',self.fake),self.progress)
    def test_placeholder_locator_requires_unique_id_and_exact_quote(self):
        body={'judgments':[{'requirement_id':'R1','matches':[{'slot':'exact slot','claim_id':'CL1','quote':'The Game menu contains Open game.'}]}]}
        fixed,ops=decode(json.dumps(body),self.artifact)
        self.assertEqual(fixed['judgments'][0]['matches'][0]['slot'],'main_flow');self.assertEqual(len(ops),1)
        body['judgments'][0]['matches'][0]['quote']='invented'
        fixed,ops=decode(json.dumps(body),self.artifact)
        self.assertEqual(fixed['judgments'][0]['matches'][0]['slot'],'exact slot');self.assertEqual(ops,[])

    def test_quote_typography_recovery_cannot_change_words_or_numbers(self):
        self.assertEqual(quote_span("select 'Extract.'", "select 'Extract'."), "select 'Extract'.")
        self.assertEqual(quote_span('select "Extract".', 'select “Extract”.'), 'select “Extract”.')
        self.assertEqual(quote_span('select "Extract."', 'select "Extract".'), 'select "Extract".')
        self.assertIsNone(quote_span('allow extraction', 'do not allow archive extraction'))
        self.assertIsNone(quote_span('at least 2 files', 'at least 3 files'))
        self.assertIsNone(quote_span('Open', 'Open then Open'))

    def test_unknown_claim_id_recovery_is_unique_literal_quote_only(self):
        body={'judgments':[{'requirement_id':'R1','label':'PARTIAL','matches':[{'slot':'text','claim_id':'TYPO','quote':'The Game menu contains Open game.'}]}]}
        fixed,ops=decode(json.dumps(body),self.artifact)
        self.assertEqual(fixed['judgments'][0]['matches'][0]['claim_id'],'CL1');self.assertEqual(fixed['judgments'][0]['label'],'PARTIAL')
        duplicate=copy.deepcopy(self.artifact);duplicate['slots']['goal']['claims']=[{'claim_id':'CL2','content':'The Game menu contains Open game.'}]
        fixed,ops=decode(json.dumps(body),duplicate);self.assertEqual(fixed['judgments'][0]['matches'][0]['claim_id'],'TYPO');self.assertEqual(ops,[])

    def test_repeated_quote_requires_exact_slot_and_unique_single_omission_id(self):
        artifact=copy.deepcopy(self.artifact)
        artifact['slots']['main_flow']['claims']=[{'claim_id':'CL-1234','content':'A literal repeated quotation.'}]
        artifact['slots']['goal']['claims']=[{'claim_id':'CL-5678','content':'A literal repeated quotation.'}]
        body={'judgments':[{'requirement_id':'R1','label':'PARTIAL','matches':[{'slot':'main_flow','claim_id':'CL-124','quote':'literal repeated quotation'}]}]}
        fixed,ops=decode(json.dumps(body),artifact)
        self.assertEqual(fixed['judgments'][0]['matches'][0]['claim_id'],'CL-1234');self.assertEqual(fixed['judgments'][0]['label'],'PARTIAL');self.assertTrue(ops)
        for slot,cid in [('text','CL-124'),('goal','CL-124'),('main_flow','TYPO')]:
            bad=copy.deepcopy(body);bad['judgments'][0]['matches'][0].update(slot=slot,claim_id=cid)
            fixed,ops=decode(json.dumps(bad),artifact);self.assertEqual(ops,[])

    def test_redundant_brace_between_rows_preserves_both_rows_and_strings(self):
        raw='{"judgments":[{"requirement_id":"R1","rationale":"literal },},{ remains"},},{"requirement_id":"R2"}]}'
        body,ops=decode(raw)
        self.assertEqual([r['requirement_id'] for r in body['judgments']],['R1','R2'])
        self.assertEqual(body['judgments'][0]['rationale'],'literal },},{ remains');self.assertEqual(len(ops),1)

    def test_target_prefix_typo_requires_complete_unique_suffix_set(self):
        reqs=[{'requirement_id':'UC-0b113737d3ff758c-R0001'},{'requirement_id':'UC-0b113737d3ff758c-R0002'}]
        body={'judgments':[{'requirement_id':'UC-0b1137d3ff758c-R0001','label':'PARTIAL'},{'requirement_id':'UC-0b1137d3ff758c-R0002','label':'FULL'}]}
        fixed,ops=decode(json.dumps(body),requirements=reqs)
        self.assertEqual([x['requirement_id'] for x in fixed['judgments']],[x['requirement_id'] for x in reqs]);self.assertEqual(fixed['judgments'][0]['label'],'PARTIAL')
        body['judgments'][1]['requirement_id']='UC-0b1137d3ff758c-R0001'
        fixed,ops=decode(json.dumps(body),requirements=reqs);self.assertEqual(ops,[])
        body['judgments'][1]['requirement_id']='UC-ffffffffffffffff-R0002'
        fixed,ops=decode(json.dumps(body),requirements=reqs);self.assertEqual(ops,[])

    def test_real_request_requires_explicit_mode(self):
        with patch('sys.argv',['coverage']),patch('eval.l3_coverage.runner.AcceptanceTransport') as live,contextlib.redirect_stderr(io.StringIO()):
            self.assertRaises(SystemExit,main);live.assert_not_called()
    def test_receipt_reuse_never_resends(self):
        self.call();self.call();self.assertEqual(len(self.fake.calls),1)
        before=[file_hash(p) for p in (self.root/'task_progress').glob('*/request-*.json')]
        self.call();self.assertEqual(before,[file_hash(p) for p in (self.root/'task_progress').glob('*/request-*.json')])
    def test_cached_semantic_result_tamper_rejected(self):
        self.call();path=next((self.root/'tasks').glob('*/result.json'));data=read_json(path);data['response']['judgments'][0]['label']='NOT_COVERED';write_json(path,data)
        self.assertRaisesRegex(ValueError,'differ',self.call);self.assertEqual(len(self.fake.calls),1)
    def test_source_tools_have_real_receipt_and_full_output_after_roundtrip(self):
        class Tools(Fake):
            def request(self,*args):
                v=super().request(*args)
                if len(self.calls)==1:v['body']=json.dumps({'tool_calls':[{'name':'source_page','arguments':{'source_id':'S1','pdf_page':2}}]})
                return v
        t=Tools();self.call(transport=t);self.assertEqual(len(t.calls),2)
        self.assertEqual(json.loads(t.calls[0]['messages'][1]['content'])['complete_generated_output'],json.loads(t.calls[1]['messages'][1]['content'])['complete_generated_output'])
        self.call(transport=t);self.assertEqual(len(t.calls),2)
    def test_missing_quote_gets_one_bounded_correction(self):
        class Bad(Fake):
            def request(self,*args):
                v=super().request(*args);body=json.loads(v['body']);body['judgments'][0]['matches'][0]['quote']='INVENTED';v['body']=json.dumps(body);return v
        t=Bad()
        for _ in range(2):self.assertRaisesRegex(ValueError,'verbatim',self.call,transport=t)
        self.assertEqual(len(t.calls),2)
    def test_one_explicit_targeted_repair_reuses_two_failed_receipts(self):
        class InitiallyBad(Fake):
            def request(self,*args):
                v=super().request(*args)
                if len(self.calls)<=2:
                    b=json.loads(v['body']);b['judgments'][0]['matches'][0]['quote']='invented';v['body']=json.dumps(b)
                return v
        t=InitiallyBad();self.assertRaises(ValueError,self.call,transport=t);self.assertEqual(len(t.calls),2)
        folder=next((self.root/'tasks').iterdir());receipt=self.root/'task_progress'/digest('coverage/'+folder.name)/'request-2.json'
        item={'input_sha256':file_hash(folder/'input.json'),'receipt_path':str(receipt.relative_to(self.root)),'receipt_sha256':file_hash(receipt),'extra_format_repairs':1,'reason':'Offline targeted literal-citation repair.'}
        write_json(self.root/'repair-overrides.json',{'schema':'L3_COVERAGE_ONE_TARGETED_FORMAT_REPAIR_V1','tasks':{folder.name:item}})
        self.call(transport=t);self.call(transport=t);self.assertEqual(len(t.calls),3)
        self.assertIn('CONTIGUOUS',t.calls[-1]['messages'][-1]['content'])

    def test_stage_repair_registers_only_failed_task_and_reuses_paid_calls(self):
        class InitiallyBad(Fake):
            def request(self,*args):
                v=super().request(*args)
                if len(self.calls)<=2:
                    b=json.loads(v['body']);b['judgments'][0]['matches'][0]['quote']='invented';v['body']=json.dumps(b)
                return v
        t=InitiallyBad();self.exp.transport=t
        with deny_network():
            self.exp.evaluate(self.case,'INITIAL','A')
            self.assertEqual(len(t.calls),2)
            self.exp.targeted_repair({'K1'},[('INITIAL','A'),('INITIAL','B')])
            self.exp.targeted_repair({'K1'},[('INITIAL','A'),('INITIAL','B')])
        self.assertEqual(len(t.calls),3);self.assertEqual(len(read_json(self.root/'repair-overrides.json')['tasks']),1)
        self.assertEqual(read_json(self.exp.view_path('INITIAL','A','K1'))['errors'],[])

    def test_targeted_repair_remains_bounded_when_model_keeps_invalid_quote(self):
        class AlwaysBad(Fake):
            def request(self,*args):
                v=super().request(*args);b=json.loads(v['body']);b['judgments'][0]['matches'][0]['quote']='invented';v['body']=json.dumps(b);return v
        t=AlwaysBad();self.exp.transport=t
        with deny_network():
            self.exp.evaluate(self.case,'INITIAL','A')
            for _ in range(3):self.exp.targeted_repair({'K1'},[('INITIAL','A')])
        self.assertEqual(len(t.calls),3)
        self.assertTrue(read_json(self.exp.view_path('INITIAL','A','K1'))['errors'])

    def test_transport_failure_unknown_cost_no_retry(self):
        class Broken(Fake):
            # Exercise production unknown-charge handling in an isolated ledger;
            # no HTTP client exists and deny_network remains active.
            mode='synthetic_production_accounting'
            def request(self,*args):self.calls.append('SENT');raise OSError('Do not leak exception details')
        t=Broken()
        for _ in range(2):self.assertRaises(GlobalStop,self.call,transport=t)
        self.assertEqual(len(t.calls),1)
        with self.budget.connect() as db:self.assertEqual(db.execute('SELECT state,actual FROM reservations').fetchone(),('UNPRICED',None))

    def approved_fourth_request_fixture(self,mode='success'):
        class Four(Fake):
            def request(self,*args):
                v=super().request(*args)
                if len(self.calls)<=3 or mode=='invalid':
                    b=json.loads(v['body']);b['judgments'][0]['matches'][0]['quote']='invented';v['body']=json.dumps(b)
                elif mode=='tool':
                    v['body']=json.dumps({'tool_calls':[{'name':'source_page','arguments':{'source_id':'S1','pdf_page':2}}]})
                return v
        t=Four();self.exp.transport=t
        with deny_network():
            self.exp.evaluate(self.case,'INITIAL','A')
            self.exp.targeted_repair({'K1'},[('INITIAL','A')])
        self.assertEqual(len(t.calls),3)
        folder=next((self.root/'tasks').iterdir());receipt=self.root/'task_progress'/digest('coverage/'+folder.name)/'request-3.json'
        item={'task':folder.name,'input_sha256':file_hash(folder/'input.json'),'receipt_path':str(receipt.relative_to(self.root)),
              'receipt_sha256':file_hash(receipt),'current_requests':3,'proposed_next_attempt':4,'proposed_extra_requests':1}
        proposal=self.root/'remaining-repair-proposal-20261105-v7.json';write_json(proposal,{'tasks':[item]})
        write_json(self.root/'user-repair-authorization-20261105.json',{'schema':'L3_USER_APPROVED_REPAIR_V1','status':'USER_AUTHORIZED',
                   'authorization':'Synthetic explicit user approval','proposal_sha256':file_hash(proposal),'tasks':{folder.name:item}})
        return t,receipt

    def test_approved_fourth_request_reuses_three_paid_receipts(self):
        t,receipt=self.approved_fourth_request_fixture();before=file_hash(receipt)
        self.call(transport=t);self.call(transport=t)
        self.assertEqual(len(t.calls),4);self.assertEqual(file_hash(receipt),before)

    def test_approved_fourth_request_still_invalid_never_gets_fifth(self):
        t,_=self.approved_fourth_request_fixture('invalid')
        for _ in range(2):self.assertRaises(ValueError,self.call,transport=t)
        self.assertEqual(len(t.calls),4)

    def test_approved_fourth_request_tool_response_cannot_extend_permission(self):
        t,_=self.approved_fourth_request_fixture('tool')
        for _ in range(2):self.assertRaisesRegex(ValueError,'bounded',self.call,transport=t)
        self.assertEqual(len(t.calls),4)

    def test_approved_fourth_request_rejects_changed_proposal_before_call(self):
        t,_=self.approved_fourth_request_fixture()
        write_json(self.root/'remaining-repair-proposal-20261105-v7.json',{'tasks':[]})
        self.assertRaisesRegex(GlobalStop,'proposal hash mismatch',self.call,transport=t)
        self.assertEqual(len(t.calls),3)

    def technical_policy_fixture(self,t):
        folder=next((self.root/'tasks').iterdir());receipt=self.root/'task_progress'/digest('coverage/'+folder.name)/'request-4.json'
        write_json(self.root/POLICY,{'schema':DECODER,'status':'USER_AUTHORIZED','tasks':{folder.name:{
            'input_sha256':file_hash(folder/'input.json'),'receipt_path':str(receipt.relative_to(self.root)),
            'receipt_sha256':file_hash(receipt),'maximum_attempt':6}}})
        return receipt

    def test_claim_citation_relaxation_preserves_label_and_original_locator(self):
        body={'judgments':[{'requirement_id':'R1','label':'PARTIAL','technical_status':'OK','reviewed_complete_output':True,
              'rationale':'Only one aspect expressed.','matches':[{'claim_id':'CL1','slot':'text','quote':'paraphrase'}]}]}
        fixed,ops=decode_citations(json.dumps(body),self.artifact,[self.ref['requirements'][0]])
        self.assertEqual(fixed['judgments'][0]['label'],'PARTIAL')
        self.assertEqual(fixed['judgments'][0]['matches'][0]['quote'],self.artifact['slots']['main_flow']['claims'][0]['content'])
        self.assertEqual(ops[-1]['original_locator']['quote'],'paraphrase')
        body['judgments'][0]['matches'][0]['claim_id']='NONEXISTENT'
        self.assertRaises(ValueError,decode_citations,json.dumps(body),self.artifact,[self.ref['requirements'][0]])

    def test_authorized_claim_citations_recover_without_new_call(self):
        t,_=self.approved_fourth_request_fixture('invalid');self.assertRaises(ValueError,self.call,transport=t)
        receipt=self.technical_policy_fixture(t);before=file_hash(receipt)
        self.call(transport=t);self.call(transport=t)
        self.assertEqual(len(t.calls),4);self.assertEqual(file_hash(receipt),before)

    def test_compact_recovery_uses_fresh_messages_and_never_resends(self):
        t,_=self.approved_fourth_request_fixture('tool');self.assertRaises(ValueError,self.call,transport=t);self.technical_policy_fixture(t)
        class Compact(Fake):
            def request(self,*args):
                v=super().request(*args);b=json.loads(v['body'])
                for row in b['judgments']:
                    row['claim_ids']=[m['claim_id'] for m in row.pop('matches')];row.pop('technical_status');row.pop('reviewed_complete_output')
                v['body']=json.dumps(b);return v
        c=Compact();self.call(transport=c);self.call(transport=c)
        self.assertEqual(len(c.calls),1);self.assertEqual(len(c.calls[0]['messages']),2)
        self.assertEqual(json.loads(c.calls[0]['messages'][1]['content'])['complete_generated_output'],self.artifact)

    def test_compact_recovery_is_bounded_at_six_requests(self):
        t,_=self.approved_fourth_request_fixture('tool');self.assertRaises(ValueError,self.call,transport=t);self.technical_policy_fixture(t)
        c=Fake()
        for _ in range(2):self.assertRaises(ValueError,self.call,transport=c)
        self.assertEqual(len(c.calls),2)
    def test_provider_rejection_not_format_retry(self):
        class Rejected(Fake):
            def request(self,*args):
                v=super().request(*args);v.update(body=None,cost_cny=None,finish_reason=None,raw_response={'http_status':400,'error_response_text':'{"error":{"code":"Arrearage"}}'});return v
        t=Rejected();self.assertRaisesRegex(GlobalStop,'Arrearage',self.call,transport=t);self.assertEqual(len(t.calls),1)
    def account_permit(self):
        folder=next((self.root/'tasks').iterdir());receipt=next((self.root/'task_progress').glob('*/request-1.json'))
        write_json(self.root/'failed-stage-recovery.json',{'schema_version':'AUCMR_L3_FAILED_STAGE_RECOVERY_1.0',
            'authorization':'Synthetic explicit user account restoration','tasks':{str(folder.relative_to(self.root)):{
            'input_sha256':file_hash(folder/'input.json'),'failed_attempt':1,'kind':'ACCOUNT_RESTORED',
            'receipt_path':str(receipt.relative_to(self.root)),'receipt_sha256':file_hash(receipt)}}})
        return receipt
    def test_explicit_account_recovery_preserves_unknown_and_never_resends_cached_calls(self):
        class Restored(Fake):
            def request(self,*args):
                v=super().request(*args)
                if len(self.calls)==1:v.update(body=None,cost_cny=None,finish_reason=None,raw_response={'http_status':400,'error_response_text':'{"error":{"code":"Arrearage"}}'})
                return v
        t=Restored();self.assertRaises(GlobalStop,self.call,transport=t)
        receipt=self.account_permit();old=file_hash(receipt)
        self.call(transport=t);self.call(transport=t)
        self.assertEqual(len(t.calls),2);self.assertEqual(file_hash(receipt),old)
        self.assertEqual(t.calls[0]['messages'],t.calls[1]['messages'])
        with self.budget.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM reservations WHERE state='UNPRICED' AND actual IS NULL").fetchone()[0],1)
    def test_explicit_account_recovery_cannot_retry_second_rejection(self):
        class Rejected(Fake):
            def request(self,*args):
                v=super().request(*args);v.update(body=None,cost_cny=None,finish_reason=None,raw_response={'http_status':400,'error_response_text':'{"error":{"code":"Arrearage"}}'});return v
        t=Rejected();self.assertRaises(GlobalStop,self.call,transport=t);self.account_permit()
        for _ in range(2):self.assertRaises(GlobalStop,self.call,transport=t)
        self.assertEqual(len(t.calls),2)
    def test_explicit_account_recovery_rejects_tampered_receipt(self):
        class Rejected(Fake):
            def request(self,*args):
                v=super().request(*args);v.update(body=None,cost_cny=None,finish_reason=None,raw_response={'http_status':400,'error_response_text':'{"error":{"code":"Arrearage"}}'});return v
        t=Rejected();self.assertRaises(GlobalStop,self.call,transport=t);receipt=self.account_permit()
        receipt.write_text(receipt.read_text()+' ')
        self.assertRaisesRegex(ValueError,'receipt hash mismatch',self.call,transport=t);self.assertEqual(len(t.calls),1)
    def test_fixed_denominator_and_unresolved_not_promoted(self):
        row=self.call()['judgments'][0];score=aggregate(self.ref,{'R1':row})
        self.assertEqual(score['N'],2);self.assertEqual(score['RC_full'],.5);self.assertEqual(score['counts']['UNRESOLVED'],1)
        self.assertEqual(aggregate(self.ref,{})['counts']['TECHNICAL_FAILURE'],1)
        bad=copy.deepcopy(row);bad['requirement_id']='R2';self.assertRaises(ValueError,aggregate,self.ref,{'R1':row,'R2':bad})
    def test_full_partial_and_conflict_require_real_quotes(self):
        body=self.call()
        for label in ['FULL','PARTIAL','CONTRADICTED']:
            b=copy.deepcopy(body);b['judgments'][0]['label']=label;b['judgments'][0]['matches']=[]
            self.assertRaises(ValueError,validate,b,self.ref['requirements'][:1],self.artifact)
    def test_duplicate_and_unknown_requirement_rejected(self):
        b=self.call();b['judgments']*=2;self.assertRaises(ValueError,validate,b,self.ref['requirements'][:1],self.artifact)
        b['judgments']=b['judgments'][:1];b['judgments'][0]['requirement_id']='OTHER';self.assertRaises(ValueError,validate,b,self.ref['requirements'][:1],self.artifact)
    def test_empty_output_automatic_uncovered_and_unresolved(self):
        a=copy.deepcopy(self.artifact);a['no_content']=True
        rows=deterministic_rows(self.ref,a);self.assertEqual(rows['R1']['label'],'NOT_COVERED');self.assertEqual(rows['R2']['label'],'UNRESOLVED')
    def test_independent_and_blind_inputs_and_stability_do_not_replace(self):
        with deny_network():
            self.exp.initial({'K1'});groups=self.exp.review({'K1'},pilot=True);self.exp.stability({'K1'});result=self.exp.summarize({'K1'},groups)
        calls=len(self.fake.calls);self.assertGreater(calls,4)
        for c in self.fake.calls:
            data=json.loads(c['messages'][1]['content'])
            if 'one adjudication' not in c['messages'][0]['content']:self.assertNotIn('anonymous_opinions',data)
            self.assertNotIn('method',data['complete_generated_output'])
        self.assertEqual(result['rows'][0]['scores']['A_FIRST']['RC_full'],.5)
        self.assertEqual(result['rows'][0]['scores']['B_FIRST']['RC_full'],0)
        self.assertEqual(result['rows'][0]['scores']['RECONCILED']['RC_full'],.5)
        with deny_network():self.exp.initial({'K1'});self.exp.review({'K1'},pilot=True);self.exp.stability({'K1'})
        self.assertEqual(len(self.fake.calls),calls)
    def test_missing_required_c_review_stays_technical_failure(self):
        row=self.call()['judgments'][0];a={'R1':row};merged=reconcile(self.ref['requirements'],a,a,{}, {'R1'})
        self.assertNotIn('R1',merged);self.assertEqual(aggregate(self.ref,merged)['status'],'INCOMPLETE')
    def test_chunks_always_include_complete_output(self):
        ref=self.plan['references']['T1'];template=ref['requirements'][0]
        ref['requirements']=[{**template,'requirement_id':f'R{i}'} for i in range(43)]
        with deny_network():self.exp.evaluate(self.case,'INITIAL','A')
        self.assertEqual(len(self.fake.calls),3)
        self.assertEqual([len(json.loads(c['messages'][1]['content'])['reference']['requirements']) for c in self.fake.calls],[20,20,3])
        self.assertTrue(all(json.loads(c['messages'][1]['content'])['complete_generated_output']==self.artifact for c in self.fake.calls))
    def test_sampling_stable_and_document_stratified(self):
        rows=[{'id':str(i),'project_id':'P'+str(i//11)} for i in range(22)]
        a=sample(rows,'TEST');self.assertEqual(a,sample(list(reversed(rows)),'TEST'));self.assertEqual(len(a),4)
        self.assertEqual(Counter(x['project_id'] for x in a),Counter({'P0':2,'P1':2}))
    def test_c_source_budget_shared_across_blind_and_final(self):
        quota=self.root/'quota.json';args=(self.contexts['T1'],'C',self.root/'access.jsonl','session')
        a=ReviewSource(*args,quota_path=quota,initial_pages=[1]);b=ReviewSource(*args,quota_path=quota,initial_pages=[1])
        for _ in range(6):a.invoke('source_search',{'source_id':'S1','query':'Synthetic'})
        self.assertRaisesRegex(ValueError,'shared C',b.invoke,'source_search',{'source_id':'S1','query':'Synthetic'})
    def test_calibration_exact_controlled_labels(self):
        with deny_network():self.exp.calibration()
        self.assertEqual(len(self.fake.calls),12);self.assertEqual(read_json(self.root/'calibration.json')['status'],'PASS')
        with deny_network():self.exp.calibration()
        self.assertEqual(len(self.fake.calls),12)
    def test_conflicting_json_and_partial_strings_never_invented(self):
        for raw in ['{"judgments":[],"judgments":[]}','{"judgments":[{"rationale":"unfinished']:
            self.assertRaises(ValueError,decode,raw)

from collections import Counter
if __name__=='__main__':unittest.main()
