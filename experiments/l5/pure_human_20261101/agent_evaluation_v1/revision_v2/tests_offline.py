"""Synthetic executor tests. No formal judgments and zero real network requests."""
import unittest,tempfile,json,os,random,copy
from pathlib import Path
from unittest.mock import patch
import runner
from transport import Client,write,TechnicalFailure,sha
from isolation import guard_payload
from tests_contracts import SOURCE,REF,RECORD,PRODUCT
ROLES=['reference_builder','reference_independent','reference_adjudicator_blind','reference_review','reference_adjudicator','score_primary','score_secondary','alignment','score_adjudicator']
class Response:
 status_code=200
 def __init__(self,out):self.text=json.dumps({'model':'synthetic-only','id':'SYNTHETIC','choices':[{'finish_reason':'stop','message':{'content':json.dumps(out)}}],'usage':{'total_tokens':1}})
class Offline(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.oldroot=runner.ROOT;runner.ROOT=self.root
  (self.root/'metric_rules.txt').write_text('Synthetic only')
  self.config={'bindings':{k:'mock' for k in ROLES},'models':{'mock':{'provider':'SYNTHETIC','model':'SYNTHETIC','key_env':'AUCMR_JUNXI_SYNTHETIC_API_KEY','base_url':'https://never-send.invalid','extra':{}}},'max_tokens':1000,'max_attempts':3,'timeout_seconds':1,'retry_backoff_seconds':0,'queues':{'agreement_audit':['P_SYN']}}
  self.prompts={'common_rules':'SYNTHETIC_ONLY',**{x:x for x in ROLES}};self.client=Client(self.root,self.config,self.prompts)
  self.env=patch.dict(os.environ,{'AUCMR_JUNXI_SYNTHETIC_API_KEY':'dummy-offline-value'});self.env.start();self.calls=[]
  write(self.root/'bundles/source/T_SYN.json',SOURCE);write(self.root/'bundles/products/P_SYN.json',{'artifact_code':'P_SYN','task_id':'T_SYN','text':PRODUCT})
 def tearDown(self):self.env.stop();runner.ROOT=self.oldroot;self.tmp.cleanup()
 def fake(self,url,json,headers,timeout):
  assert url=='https://never-send.invalid/chat/completions';assert 'tools' not in json and 'tool_choice' not in json
  role=json['messages'][0]['content'].split('\n')[-1];data=__import__('json').loads(json['messages'][1]['content']);self.calls.append((role,data))
  if role.startswith('reference_'):
   if role=='reference_review':return Response({'scope_review':'Synthetic source reviewed','items':[{'candidate_id':'RB001','verdict':'CONFIRM','reason':'Source supports','evidence':[]}],'omissions':[]})
   out=copy.deepcopy(REF);out['read_parts']=['S1'];prefix={'reference_builder':'RB','reference_independent':'RI','reference_adjudicator_blind':'RC','reference_adjudicator':'R'}[role]
   out['obligations'][0]['id']=prefix+'001';out['obligations'][0]['lineage']=[] if prefix!='R' else ['RB001','RI001','RC001']
   if prefix=='R':out['decisions']=[]
   return Response(out)
  if role=='alignment':return Response({'groups':[{'id':'G001','left_ids':['C001'],'right_ids':['C001'],'relation':'equivalent','reason':'Exact saving behavior'}]})
  out=copy.deepcopy(RECORD)
  if role=='score_adjudicator':out.update({'decisions':[{'kind':'coverage','id':'R001','action':'retained','reason':'Explicit save'},{'kind':'unit_group','id':'G001','action':'retained','reason':'Exact source support'}],'whole_artifact_review_completed':True,'suspected_reference_issues':[]})
  return Response(out)
 def test_isolation_blocked_keys(self):
  guard_payload('reference_builder',{'source':SOURCE})
  for extra in [{'product':{'text':'hidden'}},{'mapping':[]},{'tools':[]}]:
   with self.assertRaises(AssertionError):guard_payload('reference_builder',{'source':SOURCE,**extra})
  poisoned=copy.deepcopy(SOURCE);poisoned['case_id']='secret'
  with self.assertRaises(AssertionError):guard_payload('reference_builder',{'source':poisoned})
  with self.assertRaises(AssertionError):guard_payload('alignment',{'source':SOURCE,'product':{'artifact_code':'P_SYN','task_id':'T_SYN','text':PRODUCT},'left_units':[{'id':'C1','judgment':'S'}],'right_units':[]})
 def test_global_reference_gate(self):
  with self.assertRaises(FileNotFoundError):runner.product_context('P_SYN')
 def test_pipeline_and_immutable_resume(self):
  with patch('transport.requests.post',side_effect=self.fake):
   runner.ref_one('T_SYN',self.client);self.assertEqual([r for r,d in self.calls],ROLES[:5])
   for role,data in self.calls[:3]:self.assertEqual(set(data),{'source'})
   runner.freeze_references(['T_SYN']);before=sha(self.root/'reference_freeze.json')
   for role in ['score_primary','score_secondary']:runner.score_one('P_SYN',role,self.client)
   runner.adjudicate_one('P_SYN',self.client,self.config);self.assertEqual(len(self.calls),9)
   runner.score_one('P_SYN','score_primary',self.client);self.assertEqual(len(self.calls),9)
   self.assertEqual(before,sha(self.root/'reference_freeze.json'))
   alignment=next(d for role,d in self.calls if role=='alignment');self.assertNotIn('judgment',alignment['left_units'][0]);self.assertNotIn('duplicate_of',alignment['left_units'][0])
   job=self.root/'calls/score_primary/P_SYN/job.json';info=json.loads(job.read_text());info['status']='QUEUED';write(job,info);(self.root/'records/score_primary/P_SYN.json').unlink()
   runner.score_one('P_SYN','score_primary',self.client);self.assertEqual(len(self.calls),9)
 def test_technical_failure_not_zero_and_attempt_cap(self):
  data={'source':SOURCE};record=self.root/'records/failure.json'
  with patch('transport.requests.post',return_value=Response({'bad_schema':True})) as mock:
   for _ in range(2):
    with self.assertRaises(TechnicalFailure):self.client.call('reference_builder','bad',data,lambda x:runner.reference_validator(x,SOURCE),record)
   self.assertEqual(mock.call_count,3)
  self.assertFalse(record.exists());self.assertEqual(len(list((self.root/'calls/reference_builder/bad').glob('attempt-*'))),3)
 def test_repair_contains_prior_response(self):
  bad=Response({'bad_schema':True})
  good=copy.deepcopy(REF);good['read_parts']=['S1']
  with patch('transport.requests.post',side_effect=[bad,Response(good)]) as mock:
   self.client.call('reference_builder','repair',{'source':SOURCE},lambda x:runner.reference_validator(x,SOURCE),self.root/'records/repaired.json')
   messages=mock.call_args_list[1].kwargs['json']['messages']
   self.assertEqual(messages[-2]['role'],'assistant')
   self.assertEqual(json.loads(messages[-2]['content']),{'bad_schema':True})
   self.assertIn('Repair',messages[-1]['content'])
 def test_review_omission_and_lineage(self):
  builder=copy.deepcopy(REF);builder['obligations'][0]['id']='RB001'
  review={'scope_review':'scope','items':[{'candidate_id':'RB001','verdict':'CONFIRM','reason':'reason','evidence':[]}],'omissions':[copy.deepcopy(REF['obligations'][0])]};review['omissions'][0]['evidence'][0]['quote']='invented'
  with self.assertRaises(ValueError):runner.review_validator(review,SOURCE,builder)
  final=copy.deepcopy(REF);final['read_parts']=['S1'];final['decisions']=[];final['obligations'][0]['lineage']=[]
  with self.assertRaises(AssertionError):runner.final_reference_validator(final,SOURCE,[builder])
 def test_fixed_queue(self):
  a=list(range(58));b=list(range(58));random.Random(20261101).shuffle(a);random.Random(20261101).shuffle(b)
  self.assertEqual(a,b);self.assertEqual(len(set(a)),58)
if __name__=='__main__':unittest.main()
