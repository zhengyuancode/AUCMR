"""V2 semantic safeguards and diagnosed false-positive regressions; synthetic only."""
import unittest,copy
from contracts import validate_reference,validate_evidence,source_parts,validate_score
from runner import review_validator,final_reference_validator,reference_validator
from tests_contracts import SOURCE,REF,RECORD,PRODUCT
class Revision(unittest.TestCase):
 def test_source_layout_spans(self):
  source=copy.deepcopy(SOURCE);source['requirements'][0]['text']='The system checks\n  and saves the item.'
  ref=copy.deepcopy(REF);ref['obligations'][0]['evidence'][0]['quote']='checks and saves the item.'
  out=validate_reference(ref,source);span=out['obligations'][0]['evidence'][0]['source_span']
  self.assertEqual(source['requirements'][0]['text'][span['start']:span['end']],span['exact_quote'])
  self.assertEqual(span['match_mode'],'layout_nfkc_typography')
 def test_part_correction_unique_and_ambiguous(self):
  source=copy.deepcopy(SOURCE);source['requirements'].append({'source_part_id':'S2','pdf_page':4,'text':'No related evidence here.'});parts=source_parts(source)
  out=validate_evidence([{'part_id':'S2','quote':'saves the item.'}],parts,'test',True)[0]
  self.assertEqual(out['part_id'],'S1');self.assertEqual(out['reported_part_id'],'S2');self.assertTrue(out['part_id_corrected'])
  source['requirements'].append({'source_part_id':'S3','pdf_page':5,'text':'saves  the item.'})
  with self.assertRaises(ValueError):validate_evidence([{'part_id':'S2','quote':'saves the item.'}],source_parts(source),'test',True)
 def test_no_fact_or_negation_rewrite(self):
  for quote in ['does not save the item.','deletes the item.','saves 5 items.']:
   with self.assertRaises(ValueError):validate_evidence([{'part_id':'S1','quote':quote}],source_parts(SOURCE),'test',True)
 def test_administrative_defaults_recorded(self):
  ref=copy.deepcopy(REF);del ref['unresolved'];del ref['obligations'][0]['lineage']
  out=validate_reference(ref,SOURCE);self.assertEqual(out['unresolved'],[]);self.assertEqual(out['obligations'][0]['lineage'],[]);self.assertEqual(len(out['administrative_normalizations']),2)
 def test_read_scope_not_fabricated(self):
  ref=copy.deepcopy(REF);ref['read_parts']=[]
  out=reference_validator(ref,SOURCE,'RB');self.assertEqual(out['reported_read_parts'],[]);self.assertEqual(out['executor_supplied_parts'],['S1'])
 def test_known_additional_review_allowed_missing_rejected(self):
  builder=copy.deepcopy(REF);builder['obligations'][0]['id']='RB001';ind=copy.deepcopy(REF);ind['obligations'][0]['id']='RI001'
  review={'scope_review':'Save only','items':[{'candidate_id':x,'verdict':'CONFIRM','reason':'Supported','evidence':[]} for x in ['RB001','RI001']],'omissions':[]}
  self.assertEqual(review_validator(review,SOURCE,builder,ind)['additional_independent_opinions'],['RI001'])
  review['items']=review['items'][1:]
  with self.assertRaises(AssertionError):review_validator(review,SOURCE,builder,ind)
 def test_explicit_exclude_is_valid_tombstone(self):
  draft=copy.deepcopy(REF);draft['obligations'][0]['id']='RB001'
  out={'scope':'Save only','obligations':[],'unresolved':[],'decisions':[{'candidate_ids':['RB001'],'final_ids':[],'decision':'exclude','reason':'Candidate outside given scope; independent reviewer explained restriction','evidence':[]} ]}
  self.assertEqual(final_reference_validator(out,SOURCE,[draft])['candidate_disposition_audit']['undisposed'],[])
  out['decisions']=[]
  with self.assertRaises(AssertionError):final_reference_validator(out,SOURCE,[draft])
 def test_real_uncertainty_not_api_failure_or_forced_label(self):
  record=copy.deepcopy(RECORD);record['coverage'][0]['value']=None;record['unresolved']=[{'metric':'C','item_id':'R001','reason':'Cannot resolve the relation from supplied data'}]
  out=validate_score(record,SOURCE,REF,PRODUCT,True);self.assertIsNone(out['coverage'][0]['value'])
 def test_missing_judgment_not_filled(self):
  record=copy.deepcopy(RECORD);del record['units'][0]['judgment']
  with self.assertRaises(ValueError):validate_score(record,SOURCE,REF,PRODUCT,True)
if __name__=='__main__':unittest.main()
