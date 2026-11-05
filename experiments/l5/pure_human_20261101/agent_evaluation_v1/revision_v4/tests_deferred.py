"""Synthetic only: skip errors, score ready pairs, defer dependencies, bounded repairs."""
import unittest,tempfile,json,copy,os,contextlib,io
from pathlib import Path
from unittest.mock import patch
import runner
from transport import Client,write,TechnicalFailure,sha
from contracts import validate_reference
from tests_contracts import SOURCE,REF,RECORD,PRODUCT
from tests_offline import Response

class FakeClient:
 trace=[]
 def __init__(self,root,config,prompts):self.root=root;self.config=config
 def call(self,role,unit,data,validator,path):
  if path.exists():return json.loads(path.read_text())
  sweep=self.config['repair_sweep'];self.trace.append((role,unit,sweep))
  if role=='reference_adjudicator' and unit=='T_BAD' and sweep==0:raise TechnicalFailure('Synthetic missing candidate disposition')
  if role=='score_primary' and unit=='P001' and sweep==0:raise TechnicalFailure('Synthetic JSON failure')
  if role.startswith('reference_'):
   if role=='reference_review':out={'scope_review':'Source audited','items':[{'candidate_id':'RB001','verdict':'CONFIRM','reason':'Source supports','evidence':[]}],'omissions':[]}
   else:
    out=copy.deepcopy(REF);prefix={'reference_builder':'RB','reference_independent':'RI','reference_adjudicator_blind':'RC','reference_adjudicator':'R'}[role];out['obligations'][0]['id']=prefix+'001';out['read_parts']=['S1'];out['obligations'][0]['lineage']=[]
    if prefix=='R':out['obligations'][0]['lineage']=['RB001','RI001','RC001'];out['decisions']=[]
  elif role=='alignment':out={'groups':[{'id':'G001','left_ids':['C001'],'right_ids':['C001'],'relation':'equivalent','reason':'Same save behavior'}]}
  else:
   out=copy.deepcopy(RECORD)
   if role=='score_adjudicator':out.update({'decisions':[{'kind':'coverage','id':'R001','action':'retained','reason':'Supported'},{'kind':'unit_group','id':'G001','action':'retained','reason':'Supported'}],'whole_artifact_review_completed':True,'suspected_reference_issues':[]})
  result=validator(out);result['_agent']={'role':role,'provider':'SYNTHETIC','returned_model':'SYNTHETIC','provenance':'agent_generated'};write(path,result);return result

class Deferred(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.oldroot=runner.ROOT;runner.ROOT=self.root
 def tearDown(self):runner.ROOT=self.oldroot;self.tmp.cleanup()
 def stage(self):
  for task in ['T_GOOD','T_BAD']:
   src=copy.deepcopy(SOURCE);src['task_id']=task;write(self.root/'bundles/source'/f'{task}.json',src)
  for code,task in [('P001','T_GOOD'),('P002','T_GOOD'),('P003','T_BAD'),('P004','T_BAD')]:write(self.root/'bundles/products'/f'{code}.json',{'artifact_code':code,'task_id':task,'text':PRODUCT})
  cfg={'workers':2,'deferred_repair_sweeps':2,'repair_sweep':0,'queues':{'reference':['T_BAD','T_GOOD'],'score_primary':['P001','P002','P003','P004'],'score_secondary':['P001','P002','P003','P004'],'adjudication':['P001','P002','P003','P004'],'agreement_audit':['P002'],'stability':['P002']}}
  write(self.root/'config.json',cfg);write(self.root/'prompts.json',{});return cfg
 def test_end_to_end_continues_then_repairs_without_rerunning_success(self):
  cfg=self.stage();FakeClient.trace=[]
  with patch('runner.check_freeze'),patch('runner.Client',FakeClient),patch('runner.deliver') as deliver,contextlib.redirect_stdout(io.StringIO()):code=runner.main()
  self.assertEqual(code,0);self.assertEqual(json.loads((self.root/'state.json').read_text())['status'],'COMPLETED')
  deliver.assert_called_once();self.assertEqual(deliver.call_args.args[1],[])
  self.assertTrue((self.root/'reference_freeze.final.json').exists())
  for role in ['score_primary','score_secondary','alignment','score_adjudicated']:self.assertEqual(len(list((self.root/'records'/role).glob('*.json'))),4)
  # Ready GOOD pair starts in initial phase even though BAD reference failed.
  self.assertIn(('score_secondary','P002',0),FakeClient.trace)
  self.assertNotIn(('score_secondary','P003',0),FakeClient.trace)
  self.assertIn(('score_secondary','P003',1),FakeClient.trace)
  self.assertEqual(sum(r=='score_secondary' and c=='P002' for r,c,s in FakeClient.trace),1)
  self.assertEqual(sum(r=='score_primary' and c=='P001' for r,c,s in FakeClient.trace),2)
  self.assertGreater(len(json.loads((self.root/'failure_history.json').read_text())['items']),0)
  self.assertEqual(json.loads((self.root/'pending_errors.json').read_text())['count'],0)
 def test_unrepaired_batch_still_finishes_and_reports_pending(self):
  cfg=self.stage()
  class AlwaysBad(FakeClient):
   def call(self,role,unit,data,validator,path):
    if role=='reference_adjudicator' and unit=='T_BAD':raise TechnicalFailure('Unrepaired synthetic source')
    return super().call(role,unit,data,validator,path)
  with patch('runner.check_freeze'),patch('runner.Client',AlwaysBad),patch('runner.deliver'),contextlib.redirect_stdout(io.StringIO()):runner.main()
  st=json.loads((self.root/'state.json').read_text());self.assertEqual(st['status'],'FINISHED_WITH_PENDING_ERRORS');self.assertTrue(st['all_queues_attempted'])
  self.assertTrue((self.root/'records/score_primary/P002.json').exists());self.assertFalse((self.root/'records/score_primary/P003.json').exists())
 def test_attempt_budget_1_then_4_then_4_never_zero_fills(self):
  (self.root/'metric_rules.txt').write_text('Synthetic only')
  model={'provider':'SYNTHETIC','model':'SYNTHETIC','base_url':'https://never.invalid','key_env':'AUCMR_JUNXI_SYNTHETIC_API_KEY','extra':{}}
  cfg={'models':{'m':model},'bindings':{'reference_builder':'m'},'phase_attempt_limits':[1,4,4],'max_total_attempts':9,'repair_sweep':0,'max_tokens':100,'timeout_seconds':1,'retry_backoff_seconds':0}
  client=Client(self.root,cfg,{'common_rules':'Synthetic','reference_builder':'Reference'});record=self.root/'records/bad.json'
  with patch.dict(os.environ,{'AUCMR_JUNXI_SYNTHETIC_API_KEY':'offline'}),patch('transport.requests.post',return_value=Response({'bad':True})) as api:
   for phase,total in [(0,1),(1,5),(2,9)]:
    cfg['repair_sweep']=phase
    for repeat in range(2):
     with self.assertRaises(TechnicalFailure):client.call('reference_builder','BAD',{'source':SOURCE},lambda x:validate_reference(x,SOURCE),record)
     self.assertEqual(api.call_count,total)
  self.assertFalse(record.exists());self.assertEqual(len(list((self.root/'calls').glob('**/attempt-*'))),9)
 def test_aggregation_error_retained_in_final_unified_pending(self):
  self.stage()
  with patch('runner.check_freeze'),patch('runner.Client',FakeClient),patch('runner.deliver',side_effect=RuntimeError('Synthetic aggregate error')),contextlib.redirect_stdout(io.StringIO()):runner.main()
  pending=json.loads((self.root/'pending_errors.json').read_text());self.assertTrue(any(x['stage']=='aggregation' for x in pending['items']))
if __name__=='__main__':unittest.main()
