"""Locator-only repair and mixed-response receipt provenance tests; no network."""
import base64
import copy
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from eval.agent_evaluation.common import digest, file_hash, read_json, write_json
from eval.agent_evaluation.budget import Budget
from eval.agent_evaluation.transport import deny_network
from . import runner
from .response import decode, tools_already_delivered
from .locator_repairs import SCHEMA, apply
from .validation import validate_draft, validate_frozen, proof, source_context
from .test_runtime import Fake


E={'pdf_page_1based':1,'quote':'Synthetic source evidence.','visual_region_description':''}


class LocatorTests(unittest.TestCase):
    def setUp(self):
        self.draft={'scope':{'evidence':[dict(E)]},'requirements':[{'text':'Do not change this requirement.','evidence':[dict(E)]}]}
        self.record={'schema_version':SCHEMA,'input_sha256':'i','receipt_sha256':'r','source_sha256':'s','authorization':'Explicit user locator repair',
                     'patches':[{'path':['requirements',0,'evidence'],'before':[dict(E)],'after':[{**E,'pdf_page_1based':2}],'reason':'Correct page'}]}

    def repair(self,record=None):
        return apply(self.draft,record or self.record,input_sha256='i',receipt_sha256='r',source_sha256='s')

    def test_only_evidence_changes_original_and_text_preserved(self):
        before=copy.deepcopy(self.draft); result=self.repair()
        self.assertEqual(self.draft,before); self.assertEqual(result['requirements'][0]['text'],before['requirements'][0]['text'])
        self.assertEqual(result['requirements'][0]['evidence'][0]['pdf_page_1based'],2)

    def test_refuses_text_status_scope_or_origin_edits(self):
        for path in (['requirements',0,'text'],['requirements',0,'status'],['scope','goal'],['requirements',0,'origin_ids'],['unresolved_issues']):
            r=copy.deepcopy(self.record);r['patches'][0]['path']=path
            self.assertRaisesRegex(ValueError,'only replace evidence',self.repair,r)

    def test_binding_mismatch_rejected(self):
        for key in ('receipt_sha256','input_sha256','source_sha256'):
            r=copy.deepcopy(self.record);r[key]='altered';self.assertRaisesRegex(ValueError,'binding mismatch',self.repair,r)

    def test_mismatched_before_and_duplicate_patch_rejected(self):
        r=copy.deepcopy(self.record);r['patches'][0]['before']=[];self.assertRaisesRegex(ValueError,'original',self.repair,r)
        r=copy.deepcopy(self.record);r['patches']*=2;self.assertRaisesRegex(ValueError,'duplicate',self.repair,r)

    def test_missing_evidence_addition_requires_explicit_absence_marker(self):
        self.draft['scope'].pop('evidence')
        r=copy.deepcopy(self.record);r['patches']=[{'path':['scope','evidence'],'before':None,'after':[dict(E)],'reason':'Add source-reviewed scope locator'}]
        self.assertRaisesRegex(ValueError,'presence',self.repair,r)
        r['patches'][0]['before_present']=False
        result=self.repair(r);self.assertEqual(result['scope']['evidence'],[E]);self.assertNotIn('evidence',self.draft['scope'])

    def test_concatenated_objects_preserve_both_tools_and_bare_draft(self):
        tools={'tool_calls':[{'name':'source_page','arguments':{'source_id':'S','pdf_page':1}}]}
        draft={'scope':{'goal':'unchanged'},'requirements':[{'text':'Do not change'}],'unresolved_issues':[]}
        result,ops=decode(json.dumps(tools)+'\n'+json.dumps(draft))
        self.assertEqual(result,{**tools,'draft':draft});self.assertIn('JOIN_DISJOINT_JSON_OBJECTS',ops)
        for value in ('{}\n{}\n{}','{"draft":{}}\n{"draft":{}}','{}\nnot json','{"draft":{}}\n{"unexpected":true}'):
            self.assertRaises(ValueError,decode,value)

    def test_malformed_page_key_expands_only_literal_evidence_coordinate(self):
        raw='{"draft":{"scope":{"evidence":[{"pdf_page_30,"quote":"Actual quoted text"}]},"requirements":[{"text":"Unchanged requirement","evidence":[{"pdf_page_31,"quote":"Another quote"}]}],"unresolved_issues":[]}}'
        result,ops=decode(raw)
        self.assertEqual(result['draft']['scope']['evidence'][0],{'pdf_page_1based':30,'quote':'Actual quoted text'})
        self.assertEqual(result['draft']['requirements'][0]['text'],'Unchanged requirement')
        self.assertEqual(result['draft']['requirements'][0]['evidence'][0]['pdf_page_1based'],31)
        self.assertEqual(ops,['EXPAND_MALFORMED_PDF_PAGE_KEY:30','EXPAND_MALFORMED_PDF_PAGE_KEY:31'])

    def test_page_key_repair_does_not_touch_strings_or_valid_unrelated_keys(self):
        from .response import expand_malformed_page_keys
        raw=json.dumps({'text':'Example: {"pdf_page_30,"quote":"not an actual locator"}',
                        'array':['pdf_page_30,"quote":'], 'pdf_page_30':'a valid unrelated key'})
        fixed,ops=expand_malformed_page_keys(raw)
        self.assertEqual(fixed,raw);self.assertEqual(ops,[])
        for bad in ('{"draft":{"evidence":[{"pdf_page_,"quote":"missing number"}]}}',
                    '{"draft":{"evidence":[{"pdf_page_30,"unknown":"not locator syntax"}]}}'):
            self.assertRaises(ValueError,decode,bad)

    def test_structured_conditions_round_trip_without_logic_or_text_rewrite(self):
        condition=[{'condition':'When A','requirement_relation':'or','if':True},{'condition':'When B','then':['Keep all','values'],'limit':0.05}]
        data={'draft':{'scope':{},'requirements':[{'local_id':'D001','text':'Exact original claim','conditions':condition}],'unresolved_issues':[]}}
        original=copy.deepcopy(data);result,ops=decode(data)
        self.assertEqual(data,original)
        self.assertEqual(result['draft']['requirements'][0]['text'],'Exact original claim')
        self.assertEqual(json.loads(result['draft']['requirements'][0]['conditions']),condition)
        self.assertIn('ENCODE_STRUCTURED_CONDITIONS_JSON:D001',ops)

    def test_bare_scope_with_nested_draft_fields_is_lifted_without_loss(self):
        data={'scope':{'goal':'Unchanged','actor':'User','requirements':[{'text':'Exact claim','conditions':None}],'unresolved_issues':[]}}
        result,ops=decode(json.dumps(data))
        self.assertEqual(result['draft'],{'scope':{'goal':'Unchanged','actor':'User'},'requirements':data['scope']['requirements'],'unresolved_issues':[]})
        self.assertIn('WRAP_SCOPE_WITH_NESTED_DRAFT_FIELDS',ops)

    def test_bare_local_id_is_quoted_but_string_contents_are_untouched(self):
        raw='{"draft":{"scope":{},"requirements":[{"local_id":D009,"text":"D009 must remain exact","conditions":null}],"unresolved_issues":[]}}'
        result,ops=decode(raw)
        self.assertEqual(result['draft']['requirements'][0]['local_id'],'D009')
        self.assertEqual(result['draft']['requirements'][0]['text'],'D009 must remain exact')
        self.assertIn('QUOTE_BARE_LOCAL_ID:D009',ops)

    def test_nonstandard_page_field_keeps_its_value_not_digits_from_key(self):
        data={'draft':{'scope':{'evidence':[{'pdf_page_2based':26,'quote':'Original excerpt'}]},'requirements':[],'unresolved_issues':[]}}
        original=copy.deepcopy(data);result,ops=decode(data)
        self.assertEqual(data,original);self.assertEqual(result['draft']['scope']['evidence'][0],{'pdf_page_1based':26,'quote':'Original excerpt'})
        self.assertIn('NORMALIZE_PDF_PAGE_FIELD_NAME:pdf_page_2based',ops)
        data['draft']['scope']['evidence'][0]['pdf_page_1based']=27
        self.assertRaisesRegex(ValueError,'conflicting page',decode,data)

    def test_mixed_envelope_preserves_tools_and_draft_without_conflicts(self):
        draft={'scope':{'goal':'Unchanged'},'requirements':[{'text':'Unchanged'}],'unresolved_issues':[]}
        calls=[{'name':'source_page','arguments':{'source_id':'S','pdf_page':1}}]
        data={'tool_calls':calls,'draft':{'scope':draft['scope']},'requirements':draft['requirements'],'unresolved_issues':[]}
        result,ops=decode(json.dumps(data));self.assertEqual(result,{'tool_calls':calls,'draft':draft});self.assertTrue(ops)
        data['draft']['requirements']=[];self.assertRaisesRegex(ValueError,'conflicting',decode,json.dumps(data))

    def test_pending_images_cannot_be_claimed_as_delivered(self):
        calls=[{'name':'source_page','arguments':{'source_id':'S','pdf_page':1,'image':True,'dpi':144}}]
        self.assertFalse(tools_already_delivered(calls,[{'role':'user','content':json.dumps({'source_id':'S','pdf_pages':[{'pdf_page_1based':1,'text':'Only text'}]})}]))
        self.assertFalse(tools_already_delivered(calls,[]))
        import hashlib
        raw=b'SYNTHETIC_IMAGE';h=hashlib.sha256(raw).hexdigest()
        messages=[{'role':'user','content':[{'type':'text','text':json.dumps({'source_id':'S','pdf_page':1,'access_id':'A'})},
                 {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(raw).decode()}}]}]
        entry={'access_id':'A','image_sha256':h,'dpi':144,'bbox':None}
        self.assertTrue(tools_already_delivered(calls,messages,[entry]))
        self.assertFalse(tools_already_delivered(calls,messages,[{**entry,'bbox':[1,2,3,4]}]))

    def test_patched_quote_still_needs_exact_source_validation(self):
        from .validation import check_evidence
        ctx={'source':{'pages':2,'source_id':'S','pdf_sha256':'s'},'pages':{1:'Synthetic source evidence.',2:'Different text'}}
        entries=[{'operation':'page','source_id':'S','source_sha256':'s','pdf_page':2}]
        self.assertRaisesRegex(ValueError,'quote not found',check_evidence,self.repair()['requirements'][0]['evidence'],ctx,entries)

    def test_synthetic_mixed_response_full_freeze_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp,deny_network(),contextlib.redirect_stdout(io.StringIO()):
            root=Path(tmp)/'run';root.mkdir();bundle=Path(tmp)/'source';bundle.mkdir()
            import fitz
            pdf=fitz.open()
            for _ in range(15):pdf.new_page().insert_text((50,50),'Synthetic source evidence.')
            pdf.save(bundle/'s.pdf');pdf.close();tid=runner.PILOTS[0]
            write_json(bundle/'targets.json',{'targets':[{'target_id':tid,'project_id':'qheadache','actor':'Player','name':'Synthetic'}],
                       'sources':[{'source_id':'S','project_id':'qheadache','pdf':'s.pdf','pdf_sha256':file_hash(bundle/'s.pdf'),'pages':15,'xml':None,'source_version':'SYNTHETIC'}]})
            write_json(bundle/'allowlist.json',{'files':{p.name:file_hash(p) for p in bundle.iterdir()}})
            ctx=source_context(bundle,tid);budget=Budget(root/'ledger.sqlite',provider_managed=True)
            config={'models':{r:{'provider':'MOCK','api_model_id':'MOCK-'+r,'parameters':{'max_tokens':8192}} for r in 'ABC'},'source_bundle_sha256':'s','reference_limits':{'max_requests_per_task':8,'max_format_repairs':2,'max_total_requests':40}}
            class Mixed(Fake):
                def request(self,*args):
                    response=super().request(*args);body=json.loads(response['body'])
                    if args[2]['role']=='A_BUILD':body['tool_calls']=[{'name':'source_page','arguments':{'source_id':'S','pdf_page':12,'image':True,'dpi':144}}]
                    response['body']=json.dumps(body);return response
            transport=Mixed(1);results={}
            for stage in runner.STAGES:
                materials={} if stage not in ('B_COMPARE','C_FINAL') else {'A':results['A_BUILD']['draft'],'B':results['B_BLIND']['draft']}
                if stage=='C_FINAL':materials.update(B_review=results['B_COMPARE']['draft'],C=results['C_BLIND']['draft'])
                results[stage]=runner.task(root,ctx,stage,config,transport,budget,materials)
            reference=runner.freeze(root,ctx,results);self.assertEqual(len(validate_frozen(reference,bundle,root)),1)
            self.assertEqual(len(transport.calls),5)
            runner.task(root,ctx,'A_BUILD',config,transport,budget,{});self.assertEqual(len(transport.calls),5)


if __name__=='__main__':unittest.main()
