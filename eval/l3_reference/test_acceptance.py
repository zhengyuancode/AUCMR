import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from eval.agent_evaluation.common import file_hash,write_json,read_json
from eval.agent_evaluation.budget import Budget
from eval.agent_evaluation.transport import deny_network
from . import acceptance as run
from . import acceptance_contract as contract

E={'pdf_page_1based':1,'quote':'Evidence permits operation.','visual_region_description':''}

class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory();cls.base=Path(cls.tmp.name)
        import fitz
        pdf=fitz.open();page=pdf.new_page();page.insert_text((50,50),'Evidence permits operation.');pdf.save(cls.base/'source.pdf');pdf.close()
        sha=file_hash(cls.base/'source.pdf');cls.refs={};cls.contexts={}
        for tid in run.PILOTS:
            cls.refs[tid]={'target_id':tid,'version':1,'protocol_version':'TEST','content_sha256':'old','source_sha256':sha,'source_version':'SYNTHETIC','scope':{'actor':'User','evidence':[E]},'requirements':[{'requirement_id':tid+f'-R{i:04d}','text':f'unchanged {i}','conditions':None,'status':'CONFIRMED','evidence':[E],'source_sha256':sha} for i in (1,2)],'unresolved_issues':[]}
            cls.contexts[tid]={'bundle':cls.base,'target':{'target_id':tid,'project_id':'synthetic'},'source':{'project_id':'synthetic','source_id':'SYNTHETIC','pdf':'source.pdf','pdf_sha256':sha,'pages':1,'xml':None,'source_version':'SYNTHETIC'},'pages':{1:'Evidence permits operation.'}}
        cls.tid=run.PILOTS[0];cls.ref=cls.refs[cls.tid];cls.ctx=cls.contexts[cls.tid]
        cls.entries=[{'operation':'page','source_id':'SYNTHETIC','source_sha256':sha,'pdf_page':1,'image_sha256':'synthetic'}]
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def audit(self,ref=None):
        ref=ref or self.ref
        return {'target_id':ref['target_id'],'items':[{'requirement_id':r['requirement_id'],'rules':{f'R{i}':'PASS' for i in range(1,6)},'finding_ids':[],'rationale':'Source supports the unchanged obligation.'} for r in ref['requirements']],'findings':[],'coverage':[{'section':'1','disposition':'MAPPED','requirement_ids':[r['requirement_id'] for r in ref['requirements']],'finding_ids':[],'rationale':'Complete synthetic section.'}],'checked_text_pages':[1],'checked_image_pages':[1],'missing_checks':[],'source_limitations':[],'status_recommendation':'ACCEPTED'}
    def revision(self,tid=None):return {'target_id':tid or self.tid,'scope_preserved':True,'decisions':[],'changes':[],'remaining_issues':[],'unit_consistency':'One independently testable action per item in both cases.','status_recommendation':'ACCEPTED'}
    def recheck(self,tid=None):return {'target_id':tid or self.tid,'change_checks':[],'finding_checks':[],'related_item_checks':[],'new_findings':[],'remaining_issues':[],'missing_checks':[],'scope_and_denominator_preserved_or_versioned':True,'status_recommendation':'ACCEPTED'}
    def defect(self,status='DEFECT',rule='R2'):
        return {'finding_id':'F01','status':status,'kind':'REFERENCE_ERROR','requirement_ids':[self.ref['requirements'][0]['requirement_id']],'rule_id':rule,'source_locations':[E],'source_evidence':'The source permits rather than requires.','observed_difference':'Mandate substituted for permission.','effect_on_coverage_judgment_or_denominator':'Would falsely reject an optional behavior.','minimal_correction':'Restore permission wording.'}
    def changed(self,op='REPLACE'):
        f=self.defect();findings={'A:F01':f};rev=self.revision();rev['decisions']=[{'finding_id':'A:F01','decision':'CONFIRM_DEFECT','reason':'Verified semantic difference.','source_locations':[E]}]
        item={'text':'permitted operation','conditions':None,'status':'CONFIRMED','evidence':[E]}
        rev['changes']=[{'change_id':'CH1','finding_ids':['A:F01'],'operation':op,'old_ids':[self.ref['requirements'][0]['requirement_id']],'new_items':[item] if op=='REPLACE' else [item,{**item,'text':'other independent action'}],'rationale':'Minimal repair','effect_on_coverage_judgment_or_denominator':'Meaning fixed or independent units exposed.'}]
        return rev,findings
    def test_pass_without_defect_quota(self):contract.validate_audit(self.audit(),self.ref,self.ctx,self.entries)
    def test_full_audit_missing_item_rejected(self):
        a=self.audit();a['items'].pop();self.assertRaises(ValueError,contract.validate_audit,a,self.ref,self.ctx,self.entries)
    def test_unsubstantiated_item_defect_rejected(self):
        a=self.audit();a['items'][0]['rules']['R1']='DEFECT';self.assertRaises(ValueError,contract.validate_audit,a,self.ref,self.ctx,self.entries)
    def test_shared_uncertainty_retains_all_rule_associations(self):
        a=self.audit();f=self.defect('UNRESOLVED');f['kind']='SOURCE_UNCERTAINTY';a['findings']=[f];a['items'][0]['finding_ids']=['F01'];a['items'][0]['rules'].update(R2='UNRESOLVED',R3='UNRESOLVED')
        contract.validate_audit(a,self.ref,self.ctx,self.entries)
        self.assertEqual({x['rule_id'] for x in contract.flatten({'B':a})['B:F01']['item_rule_associations']},{'R2','R3'})
        a['items'][0]['rules']['R3']='DEFECT';self.assertRaises(ValueError,contract.validate_audit,a,self.ref,self.ctx,self.entries)
    def test_r4_requires_independent_example(self):
        self.assertRaises(ValueError,contract.finding,self.defect(rule='R4'),{r['requirement_id'] for r in self.ref['requirements']},self.ctx,self.entries)
    def test_unknown_quote_rejected(self):
        f=self.defect();f['source_locations']=[{**E,'quote':'invented'}];self.assertRaises(ValueError,contract.finding,f,{r['requirement_id'] for r in self.ref['requirements']},self.ctx,self.entries)
    def test_cannot_modify_suggestion(self):
        rev,findings=self.changed();findings['A:F01']['status']='SUGGESTION';self.assertRaises(ValueError,contract.apply_revision,self.ref,rev,findings,self.ctx,self.entries)
    def test_minimal_replace_preserves_other_item_and_old_id(self):
        rev,findings=self.changed();out=contract.apply_revision(self.ref,rev,findings,self.ctx,self.entries)
        self.assertEqual(out['requirements'][1],self.ref['requirements'][1]);self.assertEqual(out['requirements'][0]['requirement_id'],self.ref['requirements'][0]['requirement_id']);self.assertEqual(out['after_count'],2)
    def test_split_allocates_new_ids_and_preserves_lineage(self):
        rev,findings=self.changed('SPLIT');out=contract.apply_revision(self.ref,rev,findings,self.ctx,self.entries)
        self.assertEqual(out['after_count'],3);self.assertEqual(out['lineage'][self.ref['requirements'][0]['requirement_id']],[self.tid+'-R0003',self.tid+'-R0004'])
    def test_scope_change_rejected(self):
        rev=self.revision();rev['scope_preserved']=False;self.assertRaises(ValueError,contract.apply_revision,self.ref,rev,{},self.ctx,self.entries)
    def test_overlapping_patches_rejected(self):
        rev,f=self.changed();rev['changes'].append({**rev['changes'][0],'change_id':'CH2'});self.assertRaises(ValueError,contract.apply_revision,self.ref,rev,f,self.ctx,self.entries)
    def test_add_requires_r6(self):
        rev,f=self.changed();rev['changes'][0].update(operation='ADD',old_ids=[]);self.assertRaises(ValueError,contract.apply_revision,self.ref,rev,f,self.ctx,self.entries)
    def test_unresolved_finding_must_remain_visible(self):
        rev=self.revision();rev['decisions']=[{'finding_id':'A:F01','decision':'UNRESOLVED','reason':'Scope unclear','source_locations':[E]}];self.assertRaises(ValueError,contract.apply_revision,self.ref,rev,{'A:F01':self.defect('UNRESOLVED')},self.ctx,self.entries)
    def test_status_is_not_forced_by_recommendation(self):
        rev=self.revision();candidate=contract.apply_revision(self.ref,rev,{},self.ctx,self.entries);r=self.recheck();audits={a:self.audit() for a in 'ABC'}
        self.assertEqual(contract.final_status(audits,rev,candidate,r,[1],1),'ACCEPTED')
        r['related_item_checks']=[{'status':'DEFECT'}];self.assertEqual(contract.final_status(audits,rev,candidate,r,[1],1),'NEEDS_REVISION')
        r['related_item_checks']=[];r['remaining_issues']=[{'kind':'SCOPE_UNCERTAINTY'}];self.assertEqual(contract.final_status(audits,rev,candidate,r,[1],1),'NEEDS_SCOPE_DECISION')
        r['remaining_issues']=[];audits['A']['checked_image_pages']=[];self.assertEqual(contract.final_status(audits,rev,candidate,r,[1],1),'INCOMPLETE')
    def test_no_changes_attestation_is_not_a_fabricated_patch(self):
        rev=self.revision();candidate=contract.apply_revision(self.ref,rev,{},self.ctx,self.entries);r=self.recheck();r['change_checks']=[{'change_id':'NO-CHANGES','status':'PASS','rationale':'Unchanged baseline','source_locations':[E]}]
        contract.validate_recheck(r,self.ref,candidate,{},self.ctx,self.entries)
        r['change_checks'][0]['change_id']='NO-CHANGES-001'
        contract.validate_recheck(r,self.ref,candidate,{},self.ctx,self.entries)
        r['change_checks'][0]['change_id']='CH01';self.assertRaises(ValueError,contract.validate_recheck,r,self.ref,candidate,{},self.ctx,self.entries)
    def test_confirmed_row_cannot_silently_be_scored_unresolved(self):
        rev=self.revision();candidate=contract.apply_revision(self.ref,rev,{},self.ctx,self.entries);r=self.recheck();r['remaining_issues']=[{'kind':'SOURCE_UNCERTAINTY','requirement_ids':[self.ref['requirements'][0]['requirement_id']],'scoring_treatment':'FIXED_DENOMINATOR_UNRESOLVED_FOR_ALL_METHODS'}]
        self.assertEqual(contract.final_status({a:self.audit() for a in 'ABC'},rev,candidate,r,[1],1),'NEEDS_REVISION')
    def test_no_authorization_zero_transport(self):
        with patch('sys.argv',['acceptance','--run-dir',str(self.base/'unauthorized')]),patch.object(run,'AcceptanceTransport') as live:
            self.assertRaises(ValueError,run.main);live.assert_not_called()
    def test_strict_parser_preserves_and_rejects_conflicts(self):
        body,ops=run.parse_result('{"target_id":"T"}','audit');self.assertEqual(body,{'audit':{'target_id':'T'}})
        self.assertRaises(ValueError,run.parse_result,'{"audit":{},"audit":{}}','audit')
    def test_offline_full_cycle_rules_every_role_resume_no_calls(self):
        owner=self
        class Fake:
            mode='mock'
            def __init__(self):self.calls=[]
            def request(self,binding,messages,task,attempt):
                self.calls.append(task['task_id']);assert 'COMPLETE TEST RULES' in messages[0]['content']
                data=json.loads(messages[1]['content']);tid=data['target']['target_id'];stage=task['role']
                if stage.endswith('AUDIT'):body={'audit':owner.audit(owner.refs[tid])}
                elif stage.endswith('REVISION'):body={'revision':owner.revision(tid)}
                else:body={'recheck':owner.recheck(tid)}
                return {'body':json.dumps(body),'finish_reason':'stop','response_model':'MOCK','raw_usage':{'prompt_tokens':1,'completion_tokens':1},'cost_cny':0,'cost_basis':'SYNTHETIC'}
        with tempfile.TemporaryDirectory() as tmp,deny_network(),contextlib.redirect_stdout(io.StringIO()):
            root=Path(tmp);(root/'rules.snapshot.md').write_text('COMPLETE TEST RULES');(root/'protocol.snapshot.md').write_text('FIXED DENOMINATOR')
            for tid,ref in self.refs.items():write_json(root/'baseline'/(tid+'.json'),ref)
            config={'models':{r:{'provider':'MOCK','parameters':{}} for r in 'ABC'},'image_pages':{t:[1] for t in run.PILOTS},'source_bundle_sha256':'synthetic','limits':{'requests_per_task':6,'format_repairs':2,'total_requests':60}}
            t=Fake();b=Budget(root/'ledger.sqlite',provider_managed=True);run.run(root,config,self.contexts,self.refs,t,b);self.assertEqual(len(t.calls),10)
            run.run(root,config,self.contexts,self.refs,t,b);self.assertEqual(len(t.calls),10)
            summary=read_json(root/'acceptance-summary.json');self.assertTrue(all(x['status']=='ACCEPTED' for x in summary['outcomes']))
            for tid in run.PILOTS:
                new=read_json(root/'references'/(tid+'.json'));self.assertEqual(new['version'],2);self.assertEqual(new['requirements'],self.refs[tid]['requirements'])

if __name__=='__main__':unittest.main()
