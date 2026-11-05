"""Synthetic-only helper tests; no formal data or network."""
import unittest
from copy import deepcopy
from normalizers import materialize_explicit_unresolved, normalize_coverage, complete_alignment

def candidate(ident='RB015', text='Display processing status'):
 return {'id':ident,'text':text,'category':'result','status':'UNRESOLVED','scope':'Window scope unclear','atomicity':'One display state','reason':'Draft ambiguity','lineage':[],'evidence':[{'part_id':'S1','quote':'Each window displays status.'}]}
def decision(ids=None):
 return {'candidate_ids':ids or ['RB015'],'final_ids':[],'decision':'unresolved','reason':'Each window is not explicitly enumerated.','evidence':[{'part_id':'S1','line_start':2,'line_end':2}]}
def reference():
 return {'scope':'History','obligations':[{'id':'R014','status':'CONFIRMED','text':'Show history','lineage':['RB001']}],'decisions':[decision()],'unresolved':[]}
def rating(ident='R1',value=1,quote='Save item'):
 return {'obligation_id':ident,'value':value,'reason':'Explicit save action','evidence':[{'part_id':'S1','quote':'Save the item.'}],'product_quotes':[quote] if quote else []}
class Normalizers(unittest.TestCase):
 def test_materialize_explicit_decision_only(self):
  src=reference();drafts=[{'obligations':[candidate()]}];before=deepcopy(src);out=materialize_explicit_unresolved(src,drafts);item=out['obligations'][-1]
  self.assertEqual(src,before);self.assertEqual(item['id'],'R015');self.assertEqual(item['status'],'UNRESOLVED');self.assertEqual(item['text'],drafts[0]['obligations'][0]['text']);self.assertEqual(item['reason'],src['decisions'][0]['reason']);self.assertEqual(item['evidence'],src['decisions'][0]['evidence']);self.assertEqual(out['decisions'][0]['final_ids'],['R015']);self.assertEqual(sum(o['status']=='CONFIRMED' for o in out['obligations']),1)
 def test_no_implicit_unresolved(self):
  src=reference();src['decisions']=[];self.assertEqual(materialize_explicit_unresolved(src,[{'obligations':[candidate()]}]),src)
 def test_no_materialization_does_not_reject_unrelated_legacy_duplicates(self):
  src=reference();src['decisions']=[]
  drafts=[{'obligations':[candidate('RI001','Old wording')]},{'obligations':[candidate('RI001','Reviewed wording')]}]
  self.assertEqual(materialize_explicit_unresolved(src,drafts),src)
 def test_materialization_checks_only_targeted_duplicate_ids(self):
  drafts=[{'obligations':[candidate(),candidate('RI001','Old wording')]},{'obligations':[candidate('RI001','Reviewed wording')]}]
  out=materialize_explicit_unresolved(reference(),drafts);self.assertEqual(out['obligations'][-1]['lineage'],['RB015'])
  drafts.append({'obligations':[candidate('RB015','Conflicting target wording')]})
  with self.assertRaises(ValueError):materialize_explicit_unresolved(reference(),drafts)
 def test_unknown_candidate_rejected(self):
  with self.assertRaises(ValueError):materialize_explicit_unresolved(reference(),[{'obligations':[]}])
 def test_multiple_candidates_separate(self):
  src=reference();src['decisions']=[decision(['RB015','RI009'])];out=materialize_explicit_unresolved(src,[{'obligations':[candidate(),candidate('RI009','Show date')]}]);self.assertEqual([o['id'] for o in out['obligations'][-2:]],['R015','R016']);self.assertEqual([o['lineage'] for o in out['obligations'][-2:]],[['RB015'],['RI009']])
 def test_existing_unresolved_reused_and_conflict_rejected(self):
  src=reference();item=candidate('R020');item['lineage']=['RB015'];src['obligations'].append(item);out=materialize_explicit_unresolved(src,[{'obligations':[candidate()]}]);self.assertEqual(len(out['obligations']),2);self.assertEqual(out['decisions'][0]['final_ids'],['R020']);src['obligations'][-1]['status']='CONFIRMED'
  with self.assertRaises(ValueError):materialize_explicit_unresolved(src,[{'obligations':[candidate()]}])
 def test_materialization_idempotent(self):
  drafts=[{'obligations':[candidate()]}];first=materialize_explicit_unresolved(reference(),drafts);self.assertEqual(materialize_explicit_unresolved(first,drafts),first)
 def test_supplementary_preserved(self):
  ref={'obligations':[{'id':'R1','status':'CONFIRMED'},{'id':'R2','status':'UNRESOLVED'},{'id':'R3','status':'EXCLUDED'}]};record={'coverage':[rating(),rating('R2',0),rating('R3',1)],'unresolved':[]};before=deepcopy(record);out=normalize_coverage(record,ref);self.assertEqual(record,before);self.assertEqual([x['obligation_id'] for x in out['coverage']],['R1']);self.assertEqual(len(out['supplementary_coverage']),2);self.assertFalse(any(x['primary_metric_included'] for x in out['supplementary_coverage']));self.assertTrue(all(x['citation_validation']=='RAW_SUPPLEMENTARY_NOT_PRIMARY_VALIDATED' for x in out['supplementary_coverage']))
 def test_unknown_reference_not_guessed(self):
  with self.assertRaises(ValueError):normalize_coverage({'coverage':[rating('R9')]},{'obligations':[{'id':'R1','status':'CONFIRMED'}]})
 def test_same_value_duplicates_coalesced(self):
  out=normalize_coverage({'coverage':[rating(),rating(quote='Save now')],'unresolved':[]},{'obligations':[{'id':'R1','status':'CONFIRMED'}]});self.assertEqual(len(out['coverage']),1);self.assertEqual(out['coverage'][0]['value'],1);self.assertEqual(len(out['coverage'][0]['duplicate_ratings']),2);self.assertEqual(out['coverage'][0]['product_quotes'],['Save item','Save now']);self.assertEqual(out['unresolved'],[])
 def test_conflicting_duplicates_uncertain(self):
  out=normalize_coverage({'coverage':[rating(value=1),rating(value=0)],'unresolved':[]},{'obligations':[{'id':'R1','status':'CONFIRMED'}]});self.assertIsNone(out['coverage'][0]['value']);self.assertEqual(out['unresolved'][0]['metric'],'C');self.assertEqual([r['value'] for r in out['coverage_conflicts'][0]['original_ratings']],[1,0])
 def test_missing_not_filled(self):
  out=normalize_coverage({'coverage':[rating()]},{'obligations':[{'id':'R1','status':'CONFIRMED'},{'id':'R2','status':'CONFIRMED'}]});self.assertEqual([x['obligation_id'] for x in out['coverage']],['R1'])
 def test_illegal_bool_not_numeric(self):
  with self.assertRaises(ValueError):normalize_coverage({'coverage':[rating(value=True),rating(value=1)]},{'obligations':[{'id':'R1','status':'CONFIRMED'}]})
 def test_coverage_idempotent(self):
  ref={'obligations':[{'id':'R1','status':'CONFIRMED'},{'id':'R2','status':'UNRESOLVED'}]};first=normalize_coverage({'coverage':[rating(),rating('R2',0)]},ref);self.assertEqual(normalize_coverage(first,ref),first)
 def test_alignment_omission_not_fake_match(self):
  left={'units':[{'id':'C1'},{'id':'C2'}]};right=deepcopy(left);record={'groups':[{'id':'G1','left_ids':['C1'],'right_ids':['C1'],'relation':'equivalent','reason':'Same action'}]};before=deepcopy(record);out=complete_alignment(record,left,right);self.assertEqual(record,before);new=out['groups'][1:];self.assertEqual(len(new),2);self.assertEqual(out['alignment_unreviewed_units'],2);self.assertTrue(all(g['relation']=='unmatched' and g['audit_status']=='NOT_REVIEWED' for g in new));self.assertTrue(all(not(g['left_ids'] and g['right_ids']) for g in new))
 def test_alignment_invalid_original_not_repaired(self):
  record={'groups':[{'id':'G1','left_ids':['UNKNOWN'],'right_ids':['C1'],'relation':'equivalent','reason':'Invalid'}]};out=complete_alignment(record,{'units':[{'id':'C1'}]},{'units':[{'id':'C1'}]});self.assertEqual(out['groups'][0]['left_ids'],['UNKNOWN'])
 def test_alignment_idempotent(self):
  left={'units':[{'id':'C1'}]};first=complete_alignment({'groups':[]},left,left);self.assertEqual(complete_alignment(first,left,left),first)
if __name__=='__main__':unittest.main()
