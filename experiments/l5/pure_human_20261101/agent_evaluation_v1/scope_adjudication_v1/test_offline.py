import unittest,copy
from run import validate_scope,validate_coverage,citations
from isolation import guard_payload
class Tests(unittest.TestCase):
 def setUp(self):
  self.source={'task_id':'SYN','title':'Save file','primary_actor':'User','requirements':[{'source_part_id':'S1','pdf_page':1,'text':'The user may save a file.\nOnly authorized users may save files.'}]}
  self.old={'obligations':[{'id':'R1','text':'Save files','status':'CONFIRMED'},{'id':'R2','text':'All users may edit','status':'CONFIRMED'}]}
  self.prod={'task_id':'SYN','artifact_code':'PX','text':'The authorized user saves a file.'}
  self.ref={'obligations':[{'id':'R1','status':'CONFIRMED'},{'id':'R2','status':'EXCLUDED'}]}
 def test_source_isolation(self):
  data={'source':self.source,'prior_reference':self.old};guard_payload('scope_reader_one',data)
  with self.assertRaises(AssertionError):guard_payload('scope_reader_one',{**data,'product':self.prod})
 def test_score_identity_isolation(self):
  data={'source':self.source,'reference':self.ref,'product':self.prod};guard_payload('coverage_primary',data)
  data['reference']['method_identity']='hidden'
  with self.assertRaises(AssertionError):guard_payload('coverage_primary',data)
 def test_line_and_quote_locations(self):
  a=citations([{'part_id':'S1','line_start':1,'line_end':1}],self.source)[0]
  self.assertEqual(a['exact_quote'],'The user may save a file.')
  b=citations([{'quote':'authorized user'}],self.source,self.prod)[0];self.assertEqual(b['domain'],'product')
 def test_fabrication_rejected(self):
  with self.assertRaises(ValueError):citations([{'part_id':'S1','quote':'The system charges a fee'}],self.source)
 def test_source_reference_complete_disposition(self):
  out={'task_id':'SYN','scope':'save file only','interpretation_rules':['authorization applies'],'alternatives_and_dissent':['No need for all editing actions'],'obligations':[{'id':k,'text':txt,'category':'main','status':st,'scope':'save file','atomicity':'one fact','reason':'source based','evidence':[{'part_id':'S1','line_start':1,'line_end':2}]} for k,txt,st in [('R1','May save a file','CONFIRMED'),('R2','All users may edit','EXCLUDED')]]}
  self.assertEqual(validate_scope(out,{'source':self.source,'prior_reference':self.old})['obligations'][0]['prior_text'],'Save files')
  out['obligations'].pop()
  with self.assertRaises(AssertionError):validate_scope(out,{'source':self.source,'prior_reference':self.old})
 def test_coverage_cannot_use_excluded_or_null(self):
  data={'source':self.source,'reference':self.ref,'product':self.prod}
  out={'artifact_code':'PX','coverage':[{'obligation_id':'R1','value':1,'reason':'stated','evidence':[{'part_id':'S1','line_start':1,'line_end':1}],'product_evidence':[{'line_start':1,'line_end':1}]}],'diagnostics':['Ordinary uncertainty is described, not whole-metric NA']}
  self.assertEqual(validate_coverage(copy.deepcopy(out),data)['coverage'][0]['value'],1)
  out['coverage'][0]['value']=None
  with self.assertRaises(AssertionError):validate_coverage(out,data)
  out['coverage'][0]['value']=1;out['coverage'][0]['obligation_id']='R2'
  with self.assertRaises(AssertionError):validate_coverage(out,data)
if __name__=='__main__':unittest.main()
