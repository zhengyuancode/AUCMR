import unittest,copy
from audit import excluded_only,validate,issue_key
from aggregate import interpret
from isolation import guard_payload

class Tests(unittest.TestCase):
 def setUp(self):
  self.ref={'obligations':[{'id':'R1','status':'CONFIRMED','category':'main'},{'id':'R2','status':'UNRESOLVED','category':'main'}]}
  self.rec={'coverage':[{'obligation_id':'R1','value':.5}], 'units':[{'id':'C1','category':'main','judgment':'S','duplicate_of':None,'same_assertion_as':None}],'empty_behavior':False,'unresolved':[]}
 def test_excluded(self):
  self.rec['unresolved']=[{'metric':'C','item_id':'R2','reason':'source ambiguous'}]
  original=copy.deepcopy(self.rec);out=interpret(self.rec,self.ref)
  self.assertEqual(out['C'],.5);self.assertEqual(self.rec,original);self.assertEqual(len(out['annotation_dispositions']),1)
 def test_no_auto_dismissal(self):
  for issue in [{'metric':'C','item_id':'scope'},{'metric':'C','item_id':'R1'},{'metric':'ALL','item_id':'R2'},{'metric':'C','item_id':'unknown'}]:
   self.rec['unresolved']=[issue];self.assertIsNone(interpret(self.rec,self.ref)['C'])
 def test_diagnostic_only_numeric(self):
  issue={'metric':'C','item_id':'R1','reason':'partial explanation'};self.rec['unresolved']=[issue]
  self.assertEqual(interpret(self.rec,self.ref,[issue_key(issue)])['C'],.5)
  self.rec['coverage'][0]['value']=None;self.assertIsNone(interpret(self.rec,self.ref,[issue_key(issue)])['C'])
 def test_scope_retained_both(self):
  self.assertIsNone(interpret(self.rec,self.ref,scope_concern=True)['C'])
 def test_k_r_unchanged(self):
  before=interpret(self.rec,self.ref);self.rec['unresolved']=[{'metric':'C','item_id':'R2'}];after=interpret(self.rec,self.ref)
  for m in ('K','R'):self.assertEqual(before[m],after[m])
 def test_validate_and_isolation(self):
  data={'source':{'task_id':'SYNTH','title':'Synthetic','primary_actor':'User','requirements':[{'source_part_id':'S1','pdf_page':1,'text':'The system saves files.'}]},'reference':self.ref,'product':{'artifact_code':'SYNTH','task_id':'SYNTH','text':'Saves files.'},'annotation_cases':[{'annotation_id':'x'}],'existing_coverage':{}}
  guard_payload('annotation_review_one',data)
  response={'artifact_code':'SYNTH','decisions':[{'annotation_id':'x','disposition':'DIAGNOSTIC','reason':'explains omission','evidence':[{'part_id':'S1','quote':'saves files'}]}],'paired_scope_concern':None}
  self.assertTrue(validate(response,data)['decisions'][0]['evidence'][0]['verified_source_span'])
  response['decisions'][0]['evidence'][0]['quote']='Fabricated evidence'
  with self.assertRaises(ValueError):validate(response,data)
  data['method_identity']='hidden'
  with self.assertRaises(AssertionError):guard_payload('annotation_review_one',data)

if __name__=='__main__':unittest.main()
