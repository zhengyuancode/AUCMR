"""Frozen unattended evaluation pipeline. No candidate generation, no human scoring claims."""
from pathlib import Path
import json,sys,os,concurrent.futures,traceback,hashlib,fcntl,collections,subprocess,re
from transport import Client,write,sha,now,event,TechnicalFailure
from contracts import validate_reference,validate_score,validate_alignment
from isolation import clean
ROOT=Path(__file__).resolve().parent

def read(p):return json.loads(Path(p).read_text())
def check_freeze(root=None):
 root=ROOT if root is None else root
 freeze=read(root/'freeze.json')
 for name,h in freeze['files'].items():
  if sha(root/name)!=h:raise RuntimeError('Frozen evaluation file modified: '+name)
 for name,h in read(root/'upstream_manifest.json').items():
  if sha(root.parent/name)!=h:raise RuntimeError('Frozen upstream file modified: '+name)
 return freeze

def check_ref_freeze(root=None):
 root=ROOT if root is None else root
 record=read(root/'reference_freeze.json')
 for name,h in record['files'].items():
  if sha(root/name)!=h:raise RuntimeError('Frozen reference modified: '+name)
 return record

def reference_validator(record,source,prefix=None):
 out=validate_reference(record,source)
 if prefix:
  assert all(re.fullmatch(re.escape(prefix)+r'\d+',o['id']) for o in out['obligations']),'Use required stable role prefix '+prefix
 expected={p['source_part_id'] for p in source['requirements']}
 if set(out.get('read_parts',[]))!=expected:raise ValueError('read_parts must enumerate all supplied source part IDs, with no invented IDs')
 return out

def review_validator(record,source,builder):
 assert isinstance(record,dict) and isinstance(record.get('items'),list)
 ids=[x['candidate_id'] for x in record['items']];expected={x['id'] for x in builder['obligations']};assert len(ids)==len(set(ids)) and set(ids)==expected,'Review each builder candidate exactly once'
 parts={x['source_part_id']:x for x in source['requirements']}
 for x in record['items']:
  assert x['verdict'] in ['CONFIRM','REVISE','EXCLUDE','UNRESOLVED'] and x['reason'].strip()
  for e in x.get('evidence',[]):assert e['part_id'] in parts and e['quote'] and e['quote'] in parts[e['part_id']]['text']
 assert isinstance(record.get('omissions'),list) and record.get('scope_review')
 validated=validate_reference({'scope':str(record['scope_review']),'obligations':record['omissions'],'unresolved':[]},source)
 assert all(re.fullmatch(r'RR\d+',o['id']) for o in validated['obligations']),'Review omission IDs require RR prefix'
 record['omissions']=validated['obligations']
 return record

def final_reference_validator(record,source,drafts):
 out=reference_validator(record,source,'R')
 proposed={x['id'] for d in drafts for x in d['obligations']}
 traced={x for o in out['obligations'] for x in o['lineage']}
 assert proposed<=traced,'Every draft candidate must remain in lineage of a confirmed, unresolved or excluded final item: missing '+str(sorted(proposed-traced))
 assert isinstance(out.get('decisions'),list),'Final reference decisions required'
 parts={p['source_part_id']:p for p in source['requirements']}
 for d in out['decisions']:
  assert d.get('reason','').strip(),'Reference decision reason required'
  for e in d.get('evidence',[]):assert e['part_id'] in parts and e['quote'] and e['quote'] in parts[e['part_id']]['text'],'Decision quote must match source'
 return out

def ref_one(task,client):
 source=read(ROOT/'bundles/source'/f'{task}.json')
 def call(role,data,validator):return client.call(role,task,clean(data),validator,ROOT/'records'/role/f'{task}.json')
 builder=call('reference_builder',{'source':source},lambda x:reference_validator(x,source,'RB'))
 independent=call('reference_independent',{'source':source},lambda x:reference_validator(x,source,'RI'))
 # Both independent drafts are saved before exposure to one another.
 blind=call('reference_adjudicator_blind',{'source':source},lambda x:reference_validator(x,source,'RC'))
 review=call('reference_review',{'source':source,'independent_draft':independent,'builder_draft':builder},lambda x:review_validator(x,source,builder))
 result=client.call('reference_adjudicator',task,clean({'source':source,'blind_draft':blind,'builder_draft':builder,'independent_draft':independent,'review':review}),lambda x:final_reference_validator(x,source,[builder,independent,blind,{'obligations':review['omissions']}]),ROOT/'references'/f'{task}.json')
 return result

def product_context(code):
 check_ref_freeze();product=read(ROOT/'bundles/products'/f'{code}.json');task=product['task_id']
 return {'source':read(ROOT/'bundles/source'/f'{task}.json'),'reference':clean(read(ROOT/'references'/f'{task}.json')),'product':product}

def score_one(code,role,client,stability=False):
 data=product_context(code);path=ROOT/'records'/('score_stability' if stability else role)/f'{code}.json'
 out=client.call(role,('stability-' if stability else '')+code,data,lambda x:validate_score(x,data['source'],data['reference'],data['product']['text']),path)
 return out

def bare_units(record):
 result=[]
 for u in record['units']:
  x={k:u[k] for k in ['id','text','quote','occurrence','category']}
  if 'product_span' in u:x.update({'start':u['product_span']['start'],'end':u['product_span']['end']})
  result.append(x)
 return result

def difference_plan(left,right,alignment,sampled):
 l={x['obligation_id']:x for x in left['coverage']};r={x['obligation_id']:x for x in right['coverage']}
 different_coverage=[k for k in l if l[k]['value']!=r[k]['value']]
 different_units=[];same_units=[]
 lu={u['id']:u for u in left['units']};ru={u['id']:u for u in right['units']}
 for group in alignment['groups']:
  a=group['left_ids'];b=group['right_ids']
  if group['relation']!='equivalent' or len(a)!=1 or len(b)!=1:different_units.append(group['id']);continue
  x=lu[a[0]];y=ru[b[0]]
  sig=lambda u:(u['judgment'],bool(u['duplicate_of']),bool(u['same_assertion_as']))
  (same_units if sig(x)==sig(y) else different_units).append(group['id'])
 return {'all_disagreements_required':True,'coverage_disagreements':different_coverage,'unit_group_disagreements':different_units,'agreed_coverage':[k for k in l if k not in different_coverage],'agreed_unit_groups':same_units,'project_stratified_agreement_sample':sampled,'whole_artifact_review':True,'instruction':'Adjudicate every disagreement, examine exact product and source for segmentation omissions; audit agreement items as well. If unresolved keep null plus metric-specific reason. No method identities provided.'}

def adjudicated_validator(record,data,plan,alignment):
 out=validate_score(record,data['source'],data['reference'],data['product']['text'],allow_unresolved=True)
 decisions=out.get('decisions');assert isinstance(decisions,list),'Adjudication decisions required'
 expected={('coverage',x['obligation_id']) for x in out['coverage']}|{('unit_group',g['id']) for g in alignment['groups']}
 actual=[]
 for d in decisions:
  assert d.get('kind') in ['coverage','unit_group'] and d.get('action') in ['retained','changed','unresolved'] and d.get('reason','').strip(),'Invalid adjudication decision'
  actual.append((d['kind'],d['id']))
 assert len(actual)==len(set(actual)) and set(actual)==expected,'Adjudication must document ALL coverage and alignment groups (including agreements) exactly once'
 assert out.get('whole_artifact_review_completed') is True,'Whole anonymous artifact review required'
 assert isinstance(out.get('suspected_reference_issues'),list),'Reference concerns must be recorded separately'
 if any(d['action']=='unresolved' for d in decisions):assert out['unresolved'],'Unresolved decisions require metric-specific unresolved records'
 return out

def adjudicate_one(code,client,config):
 data=product_context(code);left=read(ROOT/'records/score_primary'/f'{code}.json');right=read(ROOT/'records/score_secondary'/f'{code}.json')
 alignment=client.call('alignment',code,{'source':data['source'],'product':data['product'],'left_units':bare_units(left),'right_units':bare_units(right)},lambda x:validate_alignment(x,left,right),ROOT/'records/alignment'/f'{code}.json')
 plan=difference_plan(left,right,alignment,code in config['queues']['agreement_audit']);write(ROOT/'review_plans'/f'{code}.json',plan)
 payload={**data,'primary':clean(left),'secondary':clean(right),'alignment':clean(alignment),'review_plan':plan}
 return client.call('score_adjudicator',code,payload,lambda x:adjudicated_validator(x,data,plan,alignment),ROOT/'records/score_adjudicated'/f'{code}.json')

def run_stage(name,items,fn,workers):
 write(ROOT/'state.json',{'stage':name,'status':'RUNNING','planned':len(items),'at':now(),'pid':os.getpid()})
 failures=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
  futures={pool.submit(fn,x):x for x in items}
  for f in concurrent.futures.as_completed(futures):
   item=futures[f]
   try:f.result();print(json.dumps({'stage':name,'item':item,'status':'COMPLETED'}),flush=True)
   except Exception as exc:
    failure={'stage':name,'item':item,'type':type(exc).__name__,'error':str(exc),'at':now()};failures.append(failure);event(ROOT,{'status':'STAGE_ITEM_FAILED',**failure})
    p=ROOT/'failures'/name/f'{item}.json'
    if not p.exists():write(p,{**failure,'traceback':traceback.format_exc()})
    print(json.dumps(failure),flush=True)
 write(ROOT/'stage_status'/f'{name}.json',{'stage':name,'planned':len(items),'completed':len(items)-len(failures),'failures':failures,'at':now()})
 return failures

def freeze_references(tasks):
 if (ROOT/'reference_freeze.json').exists():return check_ref_freeze()
 paths=[]
 for task in tasks:
  source=read(ROOT/'bundles/source'/f'{task}.json')
  r=ROOT/'references'/f'{task}.json';reference_validator(read(r),source);paths.append(r)
  for role in ['reference_builder','reference_independent','reference_adjudicator_blind','reference_review']:
   paths.append(ROOT/'records'/role/f'{task}.json')
 for p in (ROOT/'calls').glob('reference*/**/*'):
  if p.is_file():paths.append(p)
 write(ROOT/'reference_freeze.json',{'frozen_at':now(),'all_targets':tasks,'no_scores_before_freeze':not any((ROOT/'records/score_primary').glob('*.json')),'files':{str(p.relative_to(ROOT)):sha(p) for p in paths},'reference_kind':'source_constrained_agent_reference','unresolved_denominator':'L5: UNRESOLVED excluded from C denominator and reported; L3 formula not used'})
 return check_ref_freeze()

def deliver(config,failures):
 check_freeze();check_ref_freeze()
 subprocess.run([sys.executable,'-B',str(ROOT/'metrics.py'),'--root',str(ROOT)],check=True)
 from metrics import score_record,mean,weighted_kappa
 stability=[]
 for code in config['queues']['stability']:
  p=ROOT/'records/score_stability'/f'{code}.json';initial=ROOT/'records/score_secondary'/f'{code}.json'
  if not p.exists() or not initial.exists():stability.append({'artifact_code':code,'status':'TECHNICAL_MISSING'});continue
  task=read(ROOT/'bundles/products'/f'{code}.json')['task_id'];ref=read(ROOT/'references'/f'{task}.json')
  first=score_record(read(initial),ref);second=score_record(read(p),ref)
  first_record=read(initial);repeat_record=read(p)
  c1={x['obligation_id']:x['value'] for x in first_record['coverage']};c2={x['obligation_id']:x['value'] for x in repeat_record['coverage']}
  pairs=[(c1[k],c2[k]) for k in c1]
  stability.append({'artifact_code':code,'status':'COMPLETED','first':first,'repeat':second,'absolute_difference':{m:abs(first[m]-second[m]) if first[m] is not None and second[m] is not None else None for m in ['C','K','R']},'coverage_weighted_kappa':weighted_kappa(pairs),'coverage_exact_agreement':mean([float(a==b) for a,b in pairs])})
 write(ROOT/'results/stability.json',{'sample':config['queues']['stability'],'same_model_fresh_sessions':True,'not_independent_project_samples':True,'items':stability,'mean_absolute_difference':{m:mean([x['absolute_difference'][m] for x in stability if x['status']=='COMPLETED']) for m in ['C','K','R']},'assertion_kappa':'NOT_COMPUTED_FOR_STABILITY: repeated assertion units have not been independently aligned'})
 reference_inventory=[]
 for p in sorted((ROOT/'references').glob('*.json')):
  ref=read(p);reference_inventory.append({'task_id':p.stem,'counts':dict(collections.Counter(x['status'] for x in ref['obligations'])),'unresolved':[o for o in ref['obligations'] if o['status']=='UNRESOLVED'],'scope_issues':ref['unresolved'],'C_denominator_rule':'Only CONFIRMED; UNRESOLVED excluded per original L5, not L3'})
 write(ROOT/'results/reference_inventory.json',reference_inventory)
 concerns=[]
 for p in (ROOT/'records/score_adjudicated').glob('*.json'):
  for issue in read(p).get('suspected_reference_issues',[]):concerns.append({'artifact_code':p.stem,'issue':issue,'status':'PENDING_SOURCE_REVIEW_NOT_AUTOMATICALLY_CONFIRMED','policy':'If substantiated, create new version and re-evaluate both products/all judges for affected target; do not patch frozen reference'})
 write(ROOT/'results/reference_issue_register.json',concerns)
 words=[{'artifact_code':p.stem,'words':len(read(p)['text'].split()),'characters':len(read(p)['text'])} for p in (ROOT/'bundles/products').glob('*.json')]
 write(ROOT/'results/length_diagnostics.json',{'not_a_quality_score':True,'artifacts':words})
 statuses=[read(p) for p in (ROOT/'calls').glob('*/**/attempt-*/status.json')]
 returned=dict(collections.Counter(x.get('returned_model') for x in statuses if x.get('returned_model')))
 counts={role:len(list((ROOT/'records'/role).glob('*.json'))) for role in ['score_primary','score_secondary','score_adjudicated','alignment','score_stability']}
 write(ROOT/'results/completion.json',{'at':now(),'references':len(list((ROOT/'references').glob('*.json'))),'counts':counts,'planned_artifacts':58,'failed_stage_items':failures,'actual_returned_models':returned,'http_status_counts':dict(collections.Counter(str(x.get('http_status')) for x in statuses)),'attempts':len(statuses),'human_evaluation':False,'claim_scope':'在选定 PURE 材料及共同证据范围内，Agent 对 AUCMR 生成稿与既有人工文本用例的质量评价。','no_noninferiority_claim':True,'suspected_reference_issue_count':len(concerns)})
 manifest={str(p.relative_to(ROOT)):sha(p) for folder in ['records','references','calls','results'] for p in (ROOT/folder).rglob('*') if p.is_file()}
 write(ROOT/'evaluation_output_manifest.json',{'at':now(),'files':manifest,'semantic_quality_not_guaranteed_by_hash':True})

def main():
 check_freeze();config=read(ROOT/'config.json');client=Client(ROOT,config,read(ROOT/'prompts.json'));q=config['queues'];failures=[]
 if not (ROOT/'reference_freeze.json').exists():
  fs=run_stage('reference',q['reference'],lambda t:ref_one(t,client),config['workers']);failures+=fs
  if fs:
   write(ROOT/'state.json',{'stage':'reference','status':'BLOCKED_REFERENCE_TECHNICAL_FAILURE','failures':fs,'at':now(),'scoring_started':False});return 2
  freeze_references(q['reference'])
 else:check_ref_freeze()
 for role in ['score_primary','score_secondary']:
  failures+=run_stage(role,q[role],lambda code:score_one(code,role,client),config['workers'])
 available=[c for c in q['adjudication'] if all((ROOT/'records'/role/f'{c}.json').exists() for role in ['score_primary','score_secondary'])]
 failures+=run_stage('score_adjudicated',available,lambda c:adjudicate_one(c,client,config),config['workers'])
 sampled=[c for c in q['stability'] if (ROOT/'records/score_secondary'/f'{c}.json').exists()]
 failures+=run_stage('score_stability',sampled,lambda c:score_one(c,'score_secondary',client,True),config['workers'])
 deliver(config,failures)
 status='COMPLETED' if not failures and len(available)==58 else 'INCOMPLETE'
 write(ROOT/'state.json',{'stage':'FINISHED','status':status,'at':now(),'failures':failures,'results':'results/REPORT.md'});return 0 if status=='COMPLETED' else 2
if __name__=='__main__':
 lock=(ROOT/'run.lock').open('a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise SystemExit('Another runner is active; use progress.py')
 try:raise SystemExit(main())
 except Exception as exc:
  if isinstance(exc,SystemExit):raise
  write(ROOT/'state.json',{'status':'FAILED','error':str(exc),'traceback':traceback.format_exc(),'at':now()});raise
