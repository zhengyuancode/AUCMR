import json,sys,tempfile,unittest,hashlib,ast
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from runtime.inputs import parse_case,load_case
from runtime.pipeline import execute,render_result,forbidden_retrieval
from runtime.core import agents
from runtime.core.schemas import Claim,SlotContent,CockburnSlot

SYN={'id':'SYN','original_id':'SYN','title':'Read catalog','primary_actor':'Reader','requirements':[{'source_part_id':'SRC-001','pdf_page':1,'text':'The catalog is available to a reader. When a reader selects Open, the system retrieves and displays catalog entries. The reader can select a category to display its entries instead. A failed retrieval is reported as an error.'}]}
def obj(prompt,label):return json.JSONDecoder().raw_decode(prompt.split(label,1)[1].lstrip())[0]
class Mock(agents.ModelProvider):
 def __init__(self,bad=False,empty=False):
  self.stage='';self.records=[];self.bad=bad;self.empty=empty;self.lateral=0;self.patches=0
  super().__init__(self.reply,lambda *a:(_ for _ in ()).throw(AssertionError('image access')))
 def reply(self,prompt,images,assistant,is_overlap):
  self.records.append({'stage':self.stage,'prompt':prompt})
  if self.stage.endswith(':text_evidence_plan'):return json.dumps({'selected_card_ids':['EC-REQ-001'],'covered_aspect_ids':['source'],'missing_aspects':[],'reason':'offline synthetic'})
  if 'internal semantic evidence verifier' in prompt:
   claims=obj(prompt,'Candidate claims:');return json.dumps({'claims':[{'index':c['index'],'decision':'drop' if 'UNSUPPORTED' in c['content'] else 'keep','content':c['content'],'evidence_refs':c['evidence_refs'],'missing_aspects':[]} for c in claims]})
  if 'Focus: VERTICAL' in prompt:return '[]'
  if 'Focus: LATERAL' in prompt:
   self.lateral+=1
   if self.bad and self.lateral==1:return json.dumps([{'rule_violated':'altflow_diverge_points','root_slot':'alternative_flow','target_claim_index':0,'root_cause':'Step 99 absent','fix_intent':'Attach category variation to step 2','evidence_hint':None}])
   return '[]'
  if 'For modify:' in prompt and 'Issue #1' in prompt:
   self.patches+=1
   return json.dumps({'action':'modify','target_claim_index':0,'new_claim':{'content':'At step 2, the reader selects a category and the system displays entries in that category.','evidence_refs':['EC-REQ-001'],'step_number':1}})
  if self.stage.endswith(':author'):
   slot=self.stage.split(':')[0]
   if self.empty:return json.dumps({'status':'no_evidence','claims':[],'missing_aspects':['source_absent']})
   texts={'goal':['Read catalog entries.'],'preconditions':['The catalog is available.'],'trigger':['The reader selects Open.'],'main_flow':['The reader selects Open.','The system retrieves and displays catalog entries.'],'alternative_flow':[f'At step {99 if self.bad else 2}, the reader selects a category and the system displays entries in that category.'],'exception_flow':['At step 2, if retrieval fails, the system reports an error.'],'postconditions':['Catalog entries are displayed.']}
   claims=[{'content':s,'evidence_refs':['EC-REQ-001'],**({'step_number':i+1} if slot.endswith('flow') else {})} for i,s in enumerate(texts[slot])]
   return json.dumps({'claims':claims,'evidence_refs':['EC-REQ-001'],'covered_aspect_ids':[slot],'missing_aspects':[],'scope_contract':{'goal_statement':'Read catalog entries.'}})
  raise AssertionError('Unexpected stage '+self.stage+' '+prompt[:100])

class Tests(unittest.TestCase):
 def test_input_freeze_and_hashes(self):
  f=json.loads((ROOT/'protocol/INPUT_AND_CONTROL_FREEZE.json').read_text())
  for name,digest in f['files'].items():self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),digest,name)
  m=json.loads((ROOT/'inputs/manifest.json').read_text());self.assertEqual(len(m),29)
  for item in m:
   c=load_case(ROOT,item['case']);self.assertTrue(c.actors)
   for slot in ['goal','trigger','preconditions','main_flow','alternative_flow','exception_flow','postconditions']:self.assertTrue(c.seed(slot).is_empty())
   data=json.loads((ROOT/item['path']).read_text());parts={p['source_part_id']:p['text'] for p in data['requirements']}
   for s in c.spans:self.assertEqual(parts[s['part_id']][s['start_char']:s['end_char']],s['quote'])
 def test_source_control_intervals_disjoint(self):
  cat=json.loads((ROOT/'screening/candidates.json').read_text());rec=json.loads((ROOT/'screening/source_boundaries.json').read_text())
  for x in cat:
   a=x['B_span']['start_line'];b=x['B_span']['end_line_exclusive']
   for c in rec[x['document']]['chunks']:
    s=c['span'];self.assertFalse(max(a,s['start_line'])<min(b,s['end_line_exclusive']),(x['id'],s))
 def test_whitelist_and_disabled_access(self):
  with self.assertRaises(ValueError):load_case(ROOT,'../private_controls/agentmom-UC4')
  for fun in [forbidden_retrieval,agents.query_designer,agents.evidence_collector,agents.per_slot_dcrrm_loop]:
   with self.assertRaises(RuntimeError):fun()
 def test_full_pipeline_dra(self):
  c=parse_case(SYN);p=Mock(bad=True);config=json.loads((ROOT/'config/run.json').read_text())
  with tempfile.TemporaryDirectory(dir=ROOT/'validation') as tmp,patch('requests.post',side_effect=AssertionError('offline network')):
   out=execute(c,p,config,Path(tmp),ROOT)
   self.assertEqual(p.patches,1);self.assertEqual(p.lateral,2)
   self.assertNotIn('step 99',render_result(out));self.assertTrue(out['runtime_stats']['dra_executed'])
   self.assertTrue(any(x['action']=='consistency_revision' for x in out['revision_history']))
   (ROOT/'validation/synthetic_trace.json').write_text(json.dumps(p.records,indent=2))
   (ROOT/'validation/synthetic_result.json').write_text(json.dumps(out,indent=2))
 def test_empty_output_is_not_technical_failure(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'validation') as tmp:
   out=execute(parse_case(SYN),Mock(empty=True),json.loads((ROOT/'config/run.json').read_text()),Path(tmp),ROOT)
   self.assertFalse(out['slots']['main_flow'].get('claims',[]));self.assertTrue(out['quality_flags'])
 def test_unsupported_claim_dropped(self):
  slot=SlotContent(slot='postconditions',claims=[Claim(content='UNSUPPORTED business fact.',evidence_refs=['EC-REQ-001'])],evidence_card_ids=['EC-REQ-001'])
  out=agents.semantic_grounding_verifier(CockburnSlot.POSTCONDITIONS,slot,parse_case(SYN).cards,Mock(),fail_closed=True,use_source_images=False)
  self.assertTrue(out.is_empty())
 def test_given_actor_and_name_are_not_generated(self):
  c=parse_case(SYN);self.assertEqual(c.seed('primary_actor').claims[0].content,'Reader');self.assertEqual(c.seed('primary_actor').claims[0].evidence_refs,['TASK-META'])
 def test_original_consistency_and_prompts(self):
  original=ROOT.parents[2]/'crest';self.assertEqual((original/'schemas.py').read_bytes(),(ROOT/'runtime/core/schemas.py').read_bytes());self.assertEqual((original/'prompts/__init__.py').read_bytes(),(ROOT/'runtime/core/prompts/__init__.py').read_bytes())
  def fn(p):return next(ast.dump(n,include_attributes=False) for n in ast.parse(p.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='cross_slot_revision')
  self.assertEqual(fn(original/'agents.py'),fn(ROOT/'runtime/core/agents.py'))
if __name__=='__main__':
 suite=unittest.defaultTestLoader.loadTestsFromTestCase(Tests);res=unittest.TextTestRunner(verbosity=2).run(suite)
 report={'passed':res.wasSuccessful(),'tests':res.testsRun,'failures':len(res.failures),'errors':len(res.errors),'real_model_calls':0,'scope':'input isolation, lossless spans, empty output, semantic rejection, original DRA repair, immutable input/control protocol'}
 if (ROOT/'validation/offline.json').exists():
  with (ROOT/'validation/offline_history.jsonl').open('a') as f:f.write((ROOT/'validation/offline.json').read_text().replace('\n',' ')+'\n')
 (ROOT/'validation/offline.json').write_text(json.dumps(report,indent=2)+'\n');sys.exit(0 if res.wasSuccessful() else 1)
