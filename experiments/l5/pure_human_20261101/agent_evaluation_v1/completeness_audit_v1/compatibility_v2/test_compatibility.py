import unittest
from recover import resolve,normalize
class Tests(unittest.TestCase):
 def setUp(self):
  self.data={'source':{'requirements':[{'source_part_id':'S','pdf_page':2,'text':'Save file.\nCheck name.\nReturn status.'}]},'product':{'artifact_code':'P','text':'Save.\nReturn.'},'annotation_cases':[]}
 def test_source_lines(self):
  e=resolve({'part_id':'S','line_start':2,'line_end':2},self.data);self.assertEqual(e['quote'],'Check name.')
 def test_product(self):
  self.assertEqual(resolve({'part_id':'P','quote':'Save.'},self.data)['citation_domain'],'product')
 def test_ellipsis(self):
  e=resolve({'part_id':'S','quote':'Save file.\n...\nReturn status.'},self.data);self.assertTrue(e['discontinuous']);self.assertEqual(len(e['verified_segments']),2)
 def test_wrong_order_rejected(self):
  with self.assertRaises(AssertionError):resolve({'part_id':'S','quote':'Return status.\n...\nSave file.'},self.data)
 def test_no_invented_annotations(self):
  r=normalize({'artifact_code':'P','decisions':[{'annotation_id':'invented'}],'paired_scope_concern':None},self.data)
  self.assertEqual(r['decisions'],[]);self.assertEqual(len(r['unsolicited_decisions']),1)
 def test_product_cannot_be_source(self):
  self.data['annotation_cases']=[{'annotation_id':'x'}]
  with self.assertRaises(AssertionError):normalize({'artifact_code':'P','decisions':[{'annotation_id':'x','disposition':'DIAGNOSTIC','reason':'test','evidence':[{'part_id':'P','quote':'Save.'}]}]},self.data)
if __name__=='__main__':unittest.main()
