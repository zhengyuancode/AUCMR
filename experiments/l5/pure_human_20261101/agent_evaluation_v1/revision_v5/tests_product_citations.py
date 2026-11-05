"""Synthetic tests only; no formal A/B data or model calls."""
import unittest
from copy import deepcopy
from product_citations import product_catalog, locate_product_lines, prepare_score_citations
TEXT = '  Start\r\n\r\nSystem must NOT delete 5 records.\nSave café.\nSave café.\n'
class ProductCitations(unittest.TestCase):
 def test_catalog_keeps_line_indices_and_space(self):
  self.assertEqual(product_catalog({'artifact_code':'P_SYN','text':TEXT})['lines'],[
   {'line':1,'text':'  Start'},{'line':2,'text':''},{'line':3,'text':'System must NOT delete 5 records.'},{'line':4,'text':'Save café.'},{'line':5,'text':'Save café.'}])
  self.assertEqual(product_catalog({'artifact_code':'P_SYN','text':''})['lines'],[])
 def test_catalog_input_types(self):
  for p in [None,{}, {'artifact_code':'','text':'x'}, {'artifact_code':'P','text':None}]:
   with self.assertRaises(ValueError):product_catalog(p)
 def test_original_quote_and_unicode_byte_offsets(self):
  s=locate_product_lines(TEXT,{'line_start':4});self.assertEqual(TEXT[s['start']:s['end']],'Save café.')
  self.assertEqual(TEXT.encode()[s['utf8_byte_start']:s['utf8_byte_end']].decode(),s['quote'])
  self.assertEqual(s['citation_validation'],'LINE_RANGE_LOCATION_ONLY')
 def test_occurrence_uses_actual_selected_line(self):
  s=locate_product_lines(TEXT,{'line_start':5});self.assertEqual(s['occurrence'],1);self.assertEqual(s['occurrence_count'],2)
 def test_multiline_quote_keeps_internal_breaks(self):
  self.assertEqual(locate_product_lines(TEXT,{'line_start':3,'line_end':4})['quote'],'System must NOT delete 5 records.\nSave café.')
 def test_invalid_ranges_and_blank_evidence(self):
  for s in [{'line_start':0},{'line_start':-1},{'line_start':True},{'line_start':1.0},{'line_start':'1'},{'line_start':6},{'line_start':4,'line_end':3},{'line_start':1,'line_end':False},{'line_start':1,'line_end':9},{'line_end':1},{'line_start':2}]:
   with self.subTest(s=s):
    with self.assertRaises(ValueError):locate_product_lines(TEXT,s)
 def test_coverage_paraphrase_is_caption_not_verified_quote(self):
  r={'coverage':[{'obligation_id':'R001','value':1,'reason':'Model judgment','product_quotes':['System deletes all records.'],'product_lines':[{'line_start':3}]}],'units':[]};before=deepcopy(r);out=prepare_score_citations(r,TEXT);row=out['coverage'][0]
  self.assertEqual(row['product_quotes'],['System must NOT delete 5 records.']);self.assertEqual(row['reported_product_quotes'],['System deletes all records.']);self.assertEqual(row['value'],1);self.assertEqual(r,before)
  self.assertFalse(out['product_citation_normalizations'][0]['semantic_judgment_changed'])
 def test_unit_anchor_maps_quote_without_changing_judgment(self):
  r={'units':[{'id':'C001','text':'Atom','judgment':'U','quote':'Paraphrase','occurrence':99,'line_start':5}]};u=prepare_score_citations(r,TEXT)['units'][0]
  self.assertEqual(u['quote'],'Save café.');self.assertEqual(u['occurrence'],1);self.assertEqual(u['reported_quote'],'Paraphrase');self.assertEqual(u['reported_occurrence'],99);self.assertEqual(u['judgment'],'U')
 def test_shared_line_multiple_atoms_allowed(self):
  r={'units':[{'id':'C001','text':'Check','line_start':1},{'id':'C002','text':'Save','line_start':1}]};out=prepare_score_citations(r,'Check and save.')
  self.assertEqual(out['units'][0]['product_line_span'],out['units'][1]['product_line_span']);self.assertNotIn('judgment',out['units'][0])
 def test_legacy_quotes_preserved_when_no_anchors(self):
  r={'coverage':[{'product_quotes':['Paraphrase, still invalid elsewhere']}],'units':[{'quote':'Paraphrase','occurrence':0}]};self.assertEqual(prepare_score_citations(r,TEXT),r)
 def test_no_units_or_judgments_fabricated(self):
  self.assertEqual(prepare_score_citations({},TEXT),{})
  r={'coverage':[{'obligation_id':'R001','product_lines':[{'line_start':3}]}],'units':[]};out=prepare_score_citations(r,TEXT);self.assertNotIn('value',out['coverage'][0]);self.assertEqual(out['units'],[])
 def test_normalization_is_idempotent(self):
  r={'coverage':[{'obligation_id':'R001','product_quotes':['Caption'],'product_lines':[{'line_start':3},{'line_start':3}]}],'units':[{'id':'C001','line_start':4,'quote':'Caption','occurrence':20}]};once=prepare_score_citations(r,TEXT)
  self.assertEqual(prepare_score_citations(once,TEXT),once);self.assertEqual(len(once['coverage'][0]['product_quotes']),1)
 def test_bad_container_and_specs_not_silently_accepted(self):
  for r in [{'coverage':{}},{'units':None},{'coverage':[None]},{'coverage':[{'product_lines':'3'}]},{'units':[{'line_end':1}]},{'product_citation_normalizations':{}}]:
   with self.subTest(r=r):
    with self.assertRaises(ValueError):prepare_score_citations(r,TEXT)
if __name__=='__main__':unittest.main()
