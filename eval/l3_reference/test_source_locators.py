"""Exact page re-location and explicit section extraction, without model calls."""
import copy
import unittest
from .source_locators import propose
from .validation import check_evidence


class SourceLocatorTests(unittest.TestCase):
    def setUp(self):
        self.ctx={'source':{'source_id':'S','pdf_sha256':'h','pages':3},'pages':{
            1:'3.2.1.1Description\nFirst exact obligation.\n3.2.1.2Input\nA menu event.\n',
            2:'3.2.1.3Processing\nThe system does not delete data.\n3.2.1.4Output\nA new view.\n',
            3:'Different source text.\n'}}
        self.entries=[{'operation':'page','source_id':'S','source_sha256':'h','pdf_page':p} for p in (1,2,3)]

    def draft(self,page,quote):
        return {'scope':{'goal':'Preserve this goal','evidence':[{'pdf_page_1based':page,'quote':quote,'visual_region_description':''}]},
                'requirements':[{'local_id':'D001','text':'Never rewrite this claim','conditions':'Keep condition','status':'PROPOSED','evidence':[{'pdf_page_1based':1,'quote':'First exact obligation.','visual_region_description':''}]}],'unresolved_issues':[]}

    def fix(self,draft,entries=None):
        original=copy.deepcopy(draft);fixed,patches=propose(draft,self.ctx,self.entries if entries is None else entries)
        self.assertEqual(draft,original)
        self.assertEqual(fixed['requirements'][0]['text'],original['requirements'][0]['text'])
        self.assertEqual(fixed['scope']['goal'],original['scope']['goal'])
        return fixed,patches

    def test_unique_exact_quote_moves_to_actual_page(self):
        fixed,patches=self.fix(self.draft(3,'First exact obligation.'))
        self.assertEqual(fixed['scope']['evidence'][0]['pdf_page_1based'],1)
        self.assertIn('UNIQUE_EXACT',patches[0]['reason']);check_evidence(fixed['scope']['evidence'],self.ctx,self.entries)

    def test_exact_cross_page_quote_splits_into_source_fragments(self):
        self.ctx['pages'][1]='A cross page source fragment '
        self.ctx['pages'][2]='continues without a new requirement.'
        fixed,patches=self.fix(self.draft(1,'A cross page source fragment continues without a new requirement.'))
        self.assertEqual([e['pdf_page_1based'] for e in fixed['scope']['evidence']],[1,2])
        check_evidence(fixed['scope']['evidence'],self.ctx,self.entries)

    def test_unique_section_ids_extract_original_blocks_without_claim_rewrite(self):
        d=self.draft(1,'3.2.1.1Description Paraphrased description. 3.2.1.3Processing Damaged transcription.')
        fixed,patches=self.fix(d)
        self.assertEqual([e['pdf_page_1based'] for e in fixed['scope']['evidence']],[1,2])
        self.assertIn('NOT_SEMANTIC_VALIDATION',patches[0]['reason'])
        self.assertIn('does not delete',fixed['scope']['evidence'][1]['quote'])
        self.assertNotIn('A menu event',fixed['scope']['evidence'][0]['quote'])
        check_evidence(fixed['scope']['evidence'],self.ctx,self.entries)

    def test_no_fuzzy_matching_without_explicit_section_anchor(self):
        d=self.draft(3,'The system does delete data.')
        fixed,patches=self.fix(d);self.assertEqual(fixed,d);self.assertEqual(patches,[])

    def test_repeated_quote_remains_ambiguous(self):
        self.ctx['pages'][2]+='First exact obligation.'
        d=self.draft(3,'First exact obligation.');fixed,patches=self.fix(d)
        self.assertEqual(fixed,d);self.assertEqual(patches,[])

    def test_duplicate_section_ids_not_guessed(self):
        self.ctx['pages'][3]='3.2.1.3Processing\nConflicting section.\n'
        d=self.draft(1,'3.2.1.3Processing A different transcription.')
        fixed,patches=self.fix(d);self.assertEqual(fixed,d);self.assertEqual(patches,[])

    def test_section_title_mismatch_not_guessed(self):
        d=self.draft(3,'3.2.1.1Processing Different title.')
        fixed,patches=self.fix(d);self.assertEqual(fixed,d);self.assertEqual(patches,[])

    def test_unread_pages_and_out_of_bounds_stay_rejected(self):
        d=self.draft(1,'The system does not delete data.')
        fixed,patches=self.fix(d,[self.entries[0]]);self.assertEqual(fixed,d);self.assertEqual(patches,[])
        d=self.draft(999,'First exact obligation.')
        fixed,patches=self.fix(d);self.assertEqual(fixed,d);self.assertEqual(patches,[])

    def test_missing_scope_index_labeled_as_derived_not_agent_justification(self):
        d=self.draft(1,'First exact obligation.');d['scope'].pop('evidence')
        fixed,patches=self.fix(d)
        self.assertEqual(fixed['scope']['evidence'],d['requirements'][0]['evidence'])
        self.assertIn('NOT_AN_AGENT_SCOPE_JUSTIFICATION',patches[0]['reason'])
        self.assertFalse(patches[0]['before_present'])

    def test_missing_evidence_cannot_invent_a_source_index(self):
        d=self.draft(1,'First exact obligation.');d['scope'].pop('evidence');d['requirements'][0]['evidence']=[]
        fixed,patches=self.fix(d);self.assertEqual(fixed,d);self.assertEqual(patches,[])

    def test_partial_locator_repair_retains_unresolved_quote_verbatim(self):
        d=self.draft(3,'First exact obligation.')
        unresolved={'pdf_page_1based':1,'quote':'Unmatched original quote.','visual_region_description':''}
        d['scope']['evidence'].append(unresolved)
        fixed,patches=self.fix(d)
        self.assertEqual(fixed['scope']['evidence'][0]['pdf_page_1based'],1)
        self.assertEqual(fixed['scope']['evidence'][1],unresolved)
        self.assertTrue(patches)

    def test_already_valid_citations_are_left_untouched(self):
        d=self.draft(1,'First exact obligation.')
        fixed,patches=self.fix(d);self.assertEqual(fixed,d);self.assertEqual(patches,[])

    def test_advisory_quote_is_unverified_never_an_exact_match(self):
        e={'pdf_page_1based':1,'quote':'The system deletes all data.','visual_region_description':''}
        self.assertRaisesRegex(ValueError,'quote not found',check_evidence,[e],self.ctx,self.entries)
        ctx={**self.ctx,'quote_locator_policy':'ADVISORY_AGENT_QUOTE'}
        checked=check_evidence([e],ctx,self.entries)
        self.assertEqual(checked[0]['mode'],'UNVERIFIED_AGENT_QUOTE')
        self.assertTrue(checked[0]['warning']);self.assertIsNone(checked[0]['image_sha256'])
        self.assertRaisesRegex(ValueError,'actually read',check_evidence,[e],ctx,[])

    def test_advisory_visual_region_without_image_is_not_claimed_as_seen(self):
        ctx={**self.ctx,'quote_locator_policy':'ADVISORY_AGENT_QUOTE'}
        e={'pdf_page_1based':1,'quote':'','visual_region_description':'Unseen diagram region'}
        checked=check_evidence([e],ctx,self.entries)
        self.assertEqual(checked[0]['mode'],'UNVERIFIED_VISUAL_LOCATOR')
        self.assertIsNone(checked[0]['image_sha256']);self.assertTrue(checked[0]['warning'])

    def test_mixed_locator_array_retains_valid_original_with_optional_visual_field_absent(self):
        from .locator_repairs import apply,SCHEMA
        d=self.draft(3,'First exact obligation.')
        d['scope']['evidence'].append({'pdf_page_1based':2,'quote':'A new view.'})
        fixed,patches=self.fix(d)
        record={'schema_version':SCHEMA,'authorization':'Synthetic receipt-bound locator repair','input_sha256':'i','receipt_sha256':'r','source_sha256':'h','patches':patches}
        applied=apply(d,record,input_sha256='i',receipt_sha256='r',source_sha256='h')
        self.assertEqual(applied,fixed)
        self.assertNotIn('visual_region_description',applied['scope']['evidence'][1])
        check_evidence(applied['scope']['evidence'],self.ctx,self.entries)

    def test_batch_runtime_extracts_once_and_proves_actual_receipt_without_extra_call(self):
        import contextlib,io,json,tempfile
        from pathlib import Path
        from eval.agent_evaluation.common import write_json,read_json,file_hash
        from eval.agent_evaluation.transport import deny_network
        from eval.agent_evaluation.budget import Budget
        from . import runner
        from .validation import source_context,validate_frozen
        from .test_runtime import Fake
        with tempfile.TemporaryDirectory() as tmp,deny_network(),contextlib.redirect_stdout(io.StringIO()):
            root=Path(tmp)/'run';root.mkdir();bundle=Path(tmp)/'sources';bundle.mkdir()
            import fitz
            pdf=fitz.open()
            for i in range(15):
                page=pdf.new_page();page.insert_text((50,50),'3.2.1.1Description\nSynthetic source evidence.\n3.2.1.2Input\nMenu event.' if i==0 else 'Background page '+str(i+1))
            pdf.save(bundle/'s.pdf');pdf.close();tid=runner.PILOTS[0]
            write_json(bundle/'targets.json',{'targets':[{'target_id':tid,'project_id':'qheadache','actor':'Player','name':'Synthetic'}],
                       'sources':[{'source_id':'S','project_id':'qheadache','pdf':'s.pdf','pdf_sha256':file_hash(bundle/'s.pdf'),'pages':15,'xml':None,'source_version':'SYNTHETIC'}]})
            write_json(bundle/'allowlist.json',{'files':{p.name:file_hash(p) for p in bundle.iterdir()}})
            ctx=source_context(bundle,tid);budget=Budget(root/'ledger.sqlite',provider_managed=True)
            config={'source_only':True,'policy':'Adopt final Agent references; metadata source extraction allowed.',
                    'models':{r:{'provider':'MOCK','api_model_id':'MOCK-'+r,'parameters':{'max_tokens':8192}} for r in 'ABC'},
                    'source_bundle_sha256':'s','reference_limits':{'max_requests_per_task':8,'max_format_repairs':2,'max_total_requests':40}}
            class BadLocator(Fake):
                def request(self,*args):
                    result=super().request(*args);body=json.loads(result['body'])
                    if args[2]['role']=='A_BUILD':
                        body['draft']['scope']['evidence']=[{'pdf_page_1based':9,'quote':'3.2.1.1Description Inexact transcription.','visual_region_description':''}]
                    result['body']=json.dumps(body);return result
            transport=BadLocator(1);results={}
            for stage in runner.STAGES:
                materials={} if stage not in ('B_COMPARE','C_FINAL') else {'A':results['A_BUILD']['draft'],'B':results['B_BLIND']['draft']}
                if stage=='C_FINAL':materials.update(B_review=results['B_COMPARE']['draft'],C=results['C_BLIND']['draft'])
                results[stage]=runner.task(root,ctx,stage,config,transport,budget,materials)
            reference=runner.freeze(root,ctx,results);self.assertEqual(len(validate_frozen(reference,bundle,root)),1)
            self.assertEqual(len(transport.calls),5)
            record=results['A_BUILD'];self.assertIn('locator_repair_path',record)
            repair=root/record['locator_repair_path'];saved=read_json(repair)
            self.assertEqual(saved['patches'][0]['before'][0]['pdf_page_1based'],9)
            self.assertEqual(saved['patches'][0]['after'][0]['pdf_page_1based'],1)
            saved['patches'][0]['reason']='tampered';write_json(repair,saved)
            self.assertRaisesRegex(ValueError,'locator repair hash mismatch',validate_frozen,reference,bundle,root)


if __name__=='__main__':unittest.main()
