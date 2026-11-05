"""Integration tests for mild administrative tolerance, synthetic only."""
from pathlib import Path
import unittest,copy,tempfile,json
from contracts import validate_score,validate_alignment
from runner import final_reference_validator,adjudicated_validator
from metrics import score_record
from tests_contracts import SOURCE,REF,RECORD,PRODUCT
from recover_prior import recover
from inheritance import data_sha
from transport import write
class V5(unittest.TestCase):
 def test_explicit_unresolved_preserves_confirmed_denominator(self):
  draft=copy.deepcopy(REF);draft['obligations'][0]['id']='RB001'
  extra=copy.deepcopy(draft['obligations'][0]);extra.update({'id':'RB015','status':'UNRESOLVED','text':'Ambiguous target scope'});draft['obligations'].append(extra)
  out=copy.deepcopy(REF);out['obligations'][0]['lineage']=['RB001'];out['decisions']=[{'candidate_ids':['RB015'],'final_ids':[],'decision':'unresolved','reason':'Target scope genuinely unclear in the provided source','evidence':copy.deepcopy(extra['evidence'])}]
  result=final_reference_validator(out,SOURCE,[draft]);self.assertEqual(sum(x['status']=='CONFIRMED' for x in result['obligations']),1)
  self.assertEqual(result['obligations'][-1]['status'],'UNRESOLVED');self.assertEqual(out['decisions'][0]['final_ids'],[])
 def test_optional_ratings_retained_without_primary_citation_claim(self):
  ref=copy.deepcopy(REF);extra=copy.deepcopy(ref['obligations'][0]);extra.update({'id':'R999','status':'UNRESOLVED'});ref['obligations'].append(extra)
  record=copy.deepcopy(RECORD);record['coverage'].append({'obligation_id':'R999','value':0,'reason':'Optional diagnosis','evidence':[],'product_quotes':['Not in product']})
  out=validate_score(record,SOURCE,ref,PRODUCT,True)
  self.assertEqual(len(out['coverage']),1);self.assertEqual(out['supplementary_coverage'][0]['citation_validation'],'RAW_SUPPLEMENTARY_NOT_PRIMARY_VALIDATED')
  self.assertEqual(score_record(out,ref)['C'],1)
 def test_duplicate_conflict_not_select_highest(self):
  r=copy.deepcopy(RECORD);other=copy.deepcopy(r['coverage'][0]);other.update({'value':0,'reason':'Conflicting initial mark','product_quotes':[]});r['coverage'].append(other)
  out=validate_score(r,SOURCE,REF,PRODUCT,True);self.assertIsNone(out['coverage'][0]['value']);self.assertIsNone(score_record(out,REF)['C'])
  self.assertEqual(len(out['coverage_conflicts'][0]['original_ratings']),2)
 def test_product_line_anchor_not_fake_quote(self):
  r=copy.deepcopy(RECORD);r['coverage'][0].update({'product_quotes':['Paraphrase not literal'],'product_lines':[{'line_start':1}]})
  r['units'][0].update({'quote':'Paraphrase not literal','line_start':1,'line_end':1})
  out=validate_score(r,SOURCE,REF,PRODUCT,True)
  self.assertEqual(out['units'][0]['quote'],PRODUCT);self.assertEqual(out['units'][0]['reported_quote'],'Paraphrase not literal')
  self.assertFalse(out['mechanical_validation']['judgment_accuracy_verified'])
 def test_admin_alignment_is_unreviewed_and_requires_real_adjudication_record(self):
  a=validate_alignment({'groups':[]},RECORD,RECORD)
  self.assertEqual(len(a['groups']),2);self.assertTrue(all(x['relation']=='unmatched' and x['audit_status']=='NOT_REVIEWED' for x in a['groups']))
  out=copy.deepcopy(RECORD);out.update({'decisions':[{'kind':'coverage','id':'R001','action':'retained','reason':'Source supports'}],'whole_artifact_review_completed':True,'suspected_reference_issues':[]})
  data={'source':SOURCE,'reference':REF,'product':{'text':PRODUCT}}
  with self.assertRaises(AssertionError):adjudicated_validator(out,data,{},a)
 def test_chronological_recovery_does_not_select_higher_score(self):
  with tempfile.TemporaryDirectory() as td:
   base=Path(td);root=base/'revision_v5';parent=base/'revision_v4';root.mkdir();parent.mkdir()
   ref=copy.deepcopy(REF);extra=copy.deepcopy(ref['obligations'][0]);extra.update({'id':'R999','status':'UNRESOLVED'});ref['obligations'].append(extra)
   product={'artifact_code':'P_SYN','task_id':'T_SYN','text':PRODUCT};data={'source':SOURCE,'reference':ref,'product':product}
   write(root/'bundles/source/T_SYN.json',SOURCE);write(root/'bundles/products/P_SYN.json',product);write(root/'references/T_SYN.json',ref)
   model={'provider':'SYNTHETIC','model':'SYNTHETIC','key_env':'AUCMR_JUNXI_SYNTHETIC_API_KEY'}
   write(root/'config.json',{'bindings':{'score_primary':'m'},'models':{'m':model}})
   write(root/'inheritance_manifest.json',{'parent':'../revision_v4','parent_files':{},'successful':{},'failed':{'score_primary/P_SYN':{'data_sha256':data_sha(data)}}})
   for n,value in [(1,0),(2,1)]:
    folder=parent/'calls/score_primary/P_SYN'/f'attempt-{n:02d}';folder.mkdir(parents=True)
    record=copy.deepcopy(RECORD);record['coverage'][0]['value']=value
    if value==0:record['coverage'][0]['product_quotes']=[]
    record['coverage'].append({'obligation_id':'R999','value':0,'reason':'Extra rating','evidence':[],'product_quotes':[]})
    (folder/'content.txt').write_text(json.dumps(record));write(folder/'status.json',{'http_status':200,'finish_reason':'stop','attempt':n,'at':'SYNTHETIC','returned_model':'SYNTHETIC'})
    write(folder/'request.json',{'binding':model,'input_sha256':'SYNTHETIC','payload':{'messages':[{'role':'system','content':'Synthetic'},{'role':'user','content':json.dumps(data)}]}})
   summary=recover(root);self.assertEqual(summary['recovered_count'],1,summary);self.assertEqual(summary['recovered'][0]['parent_attempt'],1)
   adopted=json.loads((root/'records/score_primary/P_SYN.json').read_text());self.assertEqual(adopted['coverage'][0]['value'],0)
if __name__=='__main__':unittest.main()
