"""Offline compatibility and immutable continuation checks; no API calls."""
import unittest,tempfile,json,copy
from pathlib import Path
from unittest.mock import patch
from transport import Client,write,sha
from inheritance import data_sha,info
from isolation import guard_payload
from citations import catalog
from contracts import validate_reference,validate_evidence,source_parts
from tests_contracts import SOURCE,REF
class V3(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.root=self.base/'revision_v3';self.parent=self.base/'revision_v2';self.root.mkdir();self.parent.mkdir()
  (self.root/'metric_rules.txt').write_text('Synthetic only')
  self.model={'provider':'SYNTHETIC','model':'synthetic','base_url':'https://never.invalid','key_env':'AUCMR_JUNXI_SYNTHETIC_API_KEY','extra':{}}
  self.cfg={'bindings':{'reference_builder':'mock'},'models':{'mock':self.model},'max_tokens':100,'max_attempts':4,'timeout_seconds':1,'retry_backoff_seconds':0}
  self.prompts={'common_rules':'Synthetic rules','reference_builder':'Reference only'}
  self.record=copy.deepcopy(REF);self.record['_agent']={'role':'reference_builder','provider':'SYNTHETIC','requested_model':'synthetic','returned_model':'synthetic','at':'original-v2-time','provenance':'agent_generated'}
  self.rel='records/reference_builder/T_SYN.json';write(self.parent/self.rel,self.record);write(self.root/self.rel,self.record)
  self.manifest={'parent_files':{},'successful':{'reference_builder/T_SYN':{'data_sha256':data_sha({'source':SOURCE}),'binding':self.model,'parent_record':self.rel,'record_path':self.rel,'record_sha256':sha(self.parent/self.rel)}},'failed':{}}
  write(self.root/'inheritance_manifest.json',self.manifest)
 def tearDown(self):self.tmp.cleanup()
 def test_inherit_without_api_or_forged_model_identity(self):
  c=Client(self.root,self.cfg,self.prompts)
  with patch('transport.requests.post',side_effect=AssertionError('NO NETWORK')) as network:
   out=c.call('reference_builder','T_SYN',{'source':SOURCE},lambda x:validate_reference(x,SOURCE),self.root/self.rel)
   self.assertEqual(out['_agent']['at'],'original-v2-time');self.assertEqual(network.call_count,0)
  job=json.loads((self.root/'calls/reference_builder/T_SYN/job.json').read_text());self.assertEqual(job['execution'],'INHERITED_V2_NO_NEW_MODEL_CALL')
  self.assertFalse(list((self.root/'calls').glob('**/attempt-*')))
 def test_changed_context_cannot_overwrite_frozen_inheritance(self):
  source=copy.deepcopy(SOURCE);source['title']='Different target';before=sha(self.root/self.rel)
  with self.assertRaises(ValueError):info(self.root,'reference_builder','T_SYN',{'source':source})
  self.assertEqual(before,sha(self.root/self.rel))
 def test_tampered_parent_rejected(self):
  (self.parent/self.rel).write_text('{}')
  with self.assertRaises(AssertionError):Client(self.root,self.cfg,self.prompts).call('reference_builder','T_SYN',{'source':SOURCE},lambda x:x,self.root/self.rel)
 def test_catalog_cannot_include_hidden_text(self):
  good={'source':SOURCE,'citation_catalog':catalog(SOURCE)};guard_payload('reference_builder',good)
  bad=copy.deepcopy(good);bad['citation_catalog'][0]['lines'][0]['text']='hidden control answer'
  with self.assertRaises(AssertionError):guard_payload('reference_builder',bad)
 def test_line_evidence_is_exact_location_not_semantic_grant(self):
  source=copy.deepcopy(SOURCE);source['requirements'][0]['text']='The system must NOT delete records.\nKeep audit history.'
  e=validate_evidence([{'part_id':'S1','line_start':1,'line_end':1,'quote':'The system deletes records.'}],source_parts(source),'synthetic',True)[0]
  self.assertIn('NOT',e['quote']);self.assertEqual(e['reported_quote'],'The system deletes records.')
  self.assertEqual(e['citation_validation'],'LINE_RANGE_LOCATION_ONLY')
 def test_cross_part_evidence_stays_separate(self):
  source=copy.deepcopy(SOURCE);source['requirements'].append({'source_part_id':'S2','pdf_page':4,'text':'Preserve the resulting identifier.'})
  out=validate_evidence([{'part_id':'S1','line_start':1},{'part_id':'S2','line_start':1}],source_parts(source),'synthetic',True)
  self.assertEqual(len(out),2);self.assertEqual([x['pdf_page'] for x in out],[3,4])
 def test_failed_parent_context_not_leaked_to_other_task(self):
  self.manifest['failed']={'reference_builder/T_OTHER':{'data_sha256':data_sha({'source':SOURCE})}}
  write(self.root/'inheritance_manifest.json',self.manifest)
  self.assertEqual(info(self.root,'reference_independent','T_OTHER',{'source':SOURCE}),(None,None))
if __name__=='__main__':unittest.main()
