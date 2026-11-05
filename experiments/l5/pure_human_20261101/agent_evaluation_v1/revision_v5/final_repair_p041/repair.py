"""One blinded alignment repair and adjudication; all other actual records retained."""
from pathlib import Path
import sys,json,copy,hashlib,shutil,os
R=Path(__file__).resolve().parent;P=R.parent;sys.path.insert(0,str(P))
from transport import Client,write,sha,now
from contracts import validate_alignment
from runner import bare_units,difference_plan,adjudicated_validator,check_freeze,check_target_freeze
from isolation import clean

def load(p):return json.loads(Path(p).read_text())
def namespace(score,prefix):
 out=copy.deepcopy(score);inverse={}
 for u in out['units']:
  original=u['id'];u['id']=prefix+original;inverse[u['id']]=original
 return out,inverse

def project_alignment(record,left,right,li,ri):
 out=validate_alignment(record,left,right)
 original=copy.deepcopy(out['groups'])
 for g in out['groups']:
  g['left_ids']=[li[x] for x in g['left_ids']];g['right_ids']=[ri[x] for x in g['right_ids']]
 out['administrative_id_projection']={'rule':'Exact lookup from provided LEFT_/RIGHT_ namespace; no semantic matching inferred','original_namespaced_groups':original}
 return out

def verify():
 m=load(R/'freeze.json')
 for p,h in m['files'].items():assert sha(R/p)==h,p
 check_freeze(P);check_target_freeze('T015',P)
 for p,h in m['parent_inputs'].items():assert sha(P/p)==h,p

def prepare():
 assert not (R/'freeze.json').exists()
 product=load(P/'bundles/products/P041.json');task=product['task_id'];assert task=='T015'
 bundle={'source':load(P/f'bundles/source/{task}.json'),'reference':load(P/f'references/{task}.json'),'product':product,'primary':load(P/'records/score_primary/P041.json'),'secondary':load(P/'records/score_secondary/P041.json')}
 write(R/'bundle.json',bundle)
 cfg=load(P/'config.json');cfg.update({'version':'L5_P041_NAMESPACE_REPAIR_1','phase_attempt_limits':[4,0,0],'max_total_attempts':4,'repair_sweep':0})
 write(R/'config.json',cfg)
 prompts=load(P/'prompts.json');prompts['alignment']+='\nREPAIR ID SAFETY: LEFT units have IDs LEFT_Cxxx, RIGHT units RIGHT_Cxxx. Copy each ID verbatim from its OWN supplied list. Do not copy a RIGHT ID to the left or renumber by shared ID values. Align original behavior/quote/spans. IDs across sides are deliberately distinct; only semantic equality can establish an equivalent group.'
 write(R/'prompts.json',prompts);shutil.copyfile(P/'metric_rules.txt',R/'metric_rules.txt')
 files=[R/x for x in ['repair.py','test_repair.py','AMENDMENT.md','bundle.json','config.json','prompts.json','metric_rules.txt']]
 write(R/'freeze.json',{'frozen_at':now(),'scope':'P041 only: fix failed alignment indexing, then actual third-role adjudication; successful first judgments and all other outputs unchanged','files':{str(p.relative_to(R)):sha(p) for p in files},'parent_inputs':{p:sha(P/p) for p in ['freeze.json','reference_freezes/T015.json','bundles/products/P041.json','bundles/source/T015.json','references/T015.json','records/score_primary/P041.json','records/score_secondary/P041.json']},'actual_model_calls_before_this_freeze':0})
 print('Targeted repair frozen.')

def execute():
 verify();cfg=load(R/'config.json');b=load(R/'bundle.json');client=Client(R,cfg,load(R/'prompts.json'))
 write(R/'state.json',{'status':'RUNNING','stage':'alignment_P041','at':now()})
 left,li=namespace(b['primary'],'LEFT_');right,ri=namespace(b['secondary'],'RIGHT_')
 data={'source':b['source'],'product':b['product'],'left_units':bare_units(left),'right_units':bare_units(right)}
 alignment=client.call('alignment','P041_NAMESPACE',data,lambda x:project_alignment(x,left,right,li,ri),R/'records/alignment/P041.json')
 # Validate projected IDs against the original two independent inventories.
 validate_alignment(copy.deepcopy(alignment),b['primary'],b['secondary'])
 plan=difference_plan(b['primary'],b['secondary'],alignment,'P041' in cfg['queues']['agreement_audit'])
 payload=clean({**b,'alignment':alignment,'review_plan':plan})
 write(R/'state.json',{'status':'RUNNING','stage':'adjudicate_P041','at':now()})
 client.call('score_adjudicator','P041_TARGETED',payload,lambda x:adjudicated_validator(x,b,plan,alignment),R/'records/score_adjudicated/P041.json')
 verify()
 merged=R/'merged';merged.mkdir(exist_ok=True)
 for folder in ['references','records','organizer_private']:
  dest=merged/folder
  if dest.exists():shutil.rmtree(dest)
  shutil.copytree(P/folder,dest)
 os.chmod(merged/'organizer_private',0o700)
 for p in (merged/'organizer_private').glob('*'):os.chmod(p,0o600)
 for role in ['alignment','score_adjudicated']:shutil.copyfile(R/'records'/role/'P041.json',merged/'records'/role/'P041.json')
 from metrics import save_aggregate
 result=save_aggregate(merged)
 for name in ['stability.json','reference_inventory.json','reference_issue_register.json','length_diagnostics.json']:
  if (P/'results'/name).exists():shutil.copyfile(P/'results'/name,merged/'results'/name)
 original=load(P/'results/overall.json');assert result['overall']['independent_mean']==original['independent_mean'],'Independent first ratings must remain exactly unchanged'
 counts={role:len(list((merged/'records'/role).glob('*.json'))) for role in ['score_primary','score_secondary','alignment','score_adjudicated','score_stability']}
 assert all(counts[k]==58 for k in ['score_primary','score_secondary','alignment','score_adjudicated']) and counts['score_stability']==10
 write(merged/'results/completion.json',{'at':now(),'technical_records_complete':True,'counts':counts,'references':29,'parent_successes_unchanged':True,'only_new_judgments':'P041 blinded namespace alignment + third-role adjudication','primary_metrics_NA_retained_under_existing_rules':True,'human_evaluation':False,'scope':'Selected PURE and shared source Agent evaluation only'})
 write(R/'output_manifest.json',{'at':now(),'files':{str(p.relative_to(R)):sha(p) for folder in ['records','calls','merged/results'] for p in (R/folder).rglob('*') if p.is_file()}})
 write(R/'state.json',{'status':'COMPLETED','at':now(),'technical_pending':0,'counts':counts,'report':'merged/results/REPORT.md','independent_first_ratings_unchanged':True})
 print(json.dumps({'status':'COMPLETED','counts':counts,'report':str(merged/'results/REPORT.md')},ensure_ascii=False),flush=True)
if __name__=='__main__':
 try:
  if '--prepare' in sys.argv:prepare()
  else:execute()
 except Exception as e:
  write(R/'state.json',{'status':'FAILED','at':now(),'error':str(e)});raise
