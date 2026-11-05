"""Frozen unattended evaluation pipeline. No candidate generation, no human scoring claims."""
from pathlib import Path
import json,sys,os,concurrent.futures,traceback,hashlib,fcntl,collections,subprocess,re
from transport import Client,write,sha,now,event,TechnicalFailure
from contracts import validate_reference,validate_score,validate_alignment,validate_evidence,source_parts
from isolation import clean
from normalizers import materialize_explicit_unresolved
ROOT=Path(__file__).resolve().parent

def read(p):return json.loads(Path(p).read_text())
def check_freeze(root=None):
 root=ROOT if root is None else root
 freeze=read(root/'freeze.json')
 for name,h in freeze['files'].items():
  if sha(root/name)!=h:raise RuntimeError('Frozen evaluation file modified: '+name)
 for name,h in read(root/'upstream_manifest.json').items():
  if sha(root.parents[1]/name)!=h:raise RuntimeError('Frozen upstream file modified: '+name)
 from inheritance import verify_parent
 verify_parent(root)
 return freeze

class DependencyPending(RuntimeError):
 pass

def check_target_freeze(task,root=None):
 root=ROOT if root is None else root
 p=root/'reference_freezes'/f'{task}.json'
 if not p.exists():raise DependencyPending('Target reference not ready/frozen: '+task)
 record=read(p)
 for name,h in record['files'].items():
  if sha(root/name)!=h:raise RuntimeError('Frozen target reference modified: '+task+'/'+name)
 return record

def check_ref_freeze(root=None):
 root=ROOT if root is None else root
 record=read(root/'reference_freeze.json')
 for name,h in record['files'].items():
  if sha(root/name)!=h:raise RuntimeError('Initial reference freeze changed: '+name)
 for p in (root/'reference_freezes').glob('*.json'):check_target_freeze(p.stem,root)
 return record

def reference_validator(record,source,prefix=None):
 out=validate_reference(record,source)
 supplied=[p['source_part_id'] for p in source['requirements']]
 reported=out.get('read_parts',[])
 if reported is None:reported=[]
 if isinstance(reported,str):reported=[reported]
 assert isinstance(reported,list),'read_parts must be a list if supplied'
 out['reported_read_parts']=reported
 out['executor_supplied_parts']=supplied
 out['unrecognized_reported_parts']=[x for x in reported if x not in supplied]
 out['reading_scope_note']='All listed executor parts were supplied; self-report is not proof every part was semantically reviewed. Independent review checks source coverage.'
 if prefix:out['agent_role_id_namespace']=prefix
 return out

def review_validator(record,source,builder,independent=None):
 assert isinstance(record,dict) and isinstance(record.get('items'),list)
 ids=[x['candidate_id'] for x in record['items']]
 expected={x['id'] for x in builder['obligations']}
 allowed=expected|({x['id'] for x in independent['obligations']} if independent else set())
 assert expected<=set(ids),'Missing required builder review IDs: '+str(sorted(expected-set(ids)))
 assert len(ids)==len(set(ids)),'Conflicting/duplicate review opinions require repair'
 assert set(ids)<=allowed,'Review IDs must refer to supplied builder/independent candidates'
 parts=source_parts(source)
 for x in record['items']:
  assert x['verdict'] in ['CONFIRM','REVISE','EXCLUDE','UNRESOLVED'] and x['reason'].strip()
  x['evidence']=validate_evidence(x.get('evidence',[]),parts,'review '+x['candidate_id'])
 if record.get('omissions') is None:record['omissions']=[]
 assert isinstance(record.get('omissions'),list) and record.get('scope_review')
 validated=validate_reference({'scope':str(record['scope_review']),'obligations':record['omissions'],'unresolved':[]},source)
 record['omissions']=validated['obligations'];record['required_builder_ids_reviewed']=sorted(expected)
 record['additional_independent_opinions']=[x for x in ids if x not in expected]
 return record

def final_reference_validator(record,source,drafts):
 record=materialize_explicit_unresolved(record,drafts)
 out=reference_validator(record,source,'R')
 proposed={x['id'] for d in drafts for x in d['obligations']}
 traced={x for o in out['obligations'] for x in o['lineage']}
 assert isinstance(out.get('decisions'),list),'Final reference decisions required'
 final_ids={x['id'] for x in out['obligations']};disposed=set();parts=source_parts(source)
 for d in out['decisions']:
  assert d.get('reason','').strip(),'Reference decision reason required'
  assert isinstance(d.get('candidate_ids'),list) and isinstance(d.get('final_ids'),list),'Decision source/final IDs must be lists'
  assert set(d['candidate_ids'])<=proposed and set(d['final_ids'])<=final_ids,'Decision IDs must refer to supplied candidates/final obligations'
  assert d.get('decision') in ['confirm','merge','split','exclude','unresolved'],'Unknown disposition'
  d['evidence']=validate_evidence(d.get('evidence',[]),parts,'reference decision')
  # An explicitly justified exclusion is an auditable tombstone and does NOT
  # require inventing a denominator obligation. Other outcomes need final IDs.
  if d['decision']=='exclude' or d['final_ids']:disposed.update(d['candidate_ids'])
 missing=proposed-(traced|disposed)
 assert not missing,'Draft candidates lack lineage/disposition. Supply explicit justified exclude/merge/split/unresolved final ID without dropping them. MISSING_CANDIDATES='+json.dumps([o for d in drafts for o in d['obligations'] if o['id'] in missing],ensure_ascii=False)
 out['candidate_disposition_audit']={'proposed':sorted(proposed),'lineage':sorted(traced),'explicit_decisions':sorted(disposed),'undisposed':[]}
 return out

def ref_one(task,client):
 source=read(ROOT/'bundles/source'/f'{task}.json')
 def call(role,data,validator):return client.call(role,task,clean(data),validator,ROOT/'records'/role/f'{task}.json')
 builder=call('reference_builder',{'source':source},lambda x:reference_validator(x,source,'RB'))
 independent=call('reference_independent',{'source':source},lambda x:reference_validator(x,source,'RI'))
 # Both independent drafts are saved before exposure to one another.
 blind=call('reference_adjudicator_blind',{'source':source},lambda x:reference_validator(x,source,'RC'))
 review=call('reference_review',{'source':source,'independent_draft':independent,'builder_draft':builder},lambda x:review_validator(x,source,builder,independent))
 result=client.call('reference_adjudicator',task,clean({'source':source,'blind_draft':blind,'builder_draft':builder,'independent_draft':independent,'review':review}),lambda x:final_reference_validator(x,source,[builder,independent,blind,{'obligations':review['omissions']}]),ROOT/'references'/f'{task}.json')
 return result

def product_context(code):
 product=read(ROOT/'bundles/products'/f'{code}.json');task=product['task_id'];check_target_freeze(task)
 return {'source':read(ROOT/'bundles/source'/f'{task}.json'),'reference':clean(read(ROOT/'references'/f'{task}.json')),'product':product}

def score_one(code,role,client,stability=False):
 data=product_context(code);path=ROOT/'records'/('score_stability' if stability else role)/f'{code}.json'
 out=client.call(role,('stability-' if stability else '')+code,data,lambda x:validate_score(x,data['source'],data['reference'],data['product']['text'],allow_unresolved=True),path)
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
 data=product_context(code)
 for role in ['score_primary','score_secondary']:
  if not (ROOT/'records'/role/f'{code}.json').exists():raise DependencyPending('Independent first judgment pending: '+role+'/'+code)
 left=read(ROOT/'records/score_primary'/f'{code}.json');right=read(ROOT/'records/score_secondary'/f'{code}.json')
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
    failure={'stage':name,'item':item,'type':type(exc).__name__,'classification':'DEPENDENCY_PENDING' if isinstance(exc,DependencyPending) else 'TECHNICAL_ERROR','error':str(exc),'at':now()};failures.append(failure);event(ROOT,{'status':'STAGE_ITEM_FAILED',**failure})
    p=ROOT/'failures'/name/f'{item}.json'
    if not p.exists():write(p,{**failure,'traceback':traceback.format_exc()})
    print(json.dumps(failure),flush=True)
 write(ROOT/'stage_status'/f'{name}.json',{'stage':name,'planned':len(items),'completed':len(items)-len(failures),'failures':failures,'at':now()})
 return failures

def freeze_references(tasks):
 # Append independent immutable target freezes. The initial batch manifest is
 # never replaced when a deferred target is completed later.
 ready=[];pending=[]
 for task in tasks:
  dest=ROOT/'reference_freezes'/f'{task}.json'
  if dest.exists():check_target_freeze(task);ready.append(task);continue
  paths=[ROOT/'references'/f'{task}.json',ROOT/'bundles/source'/f'{task}.json']
  paths += [ROOT/'records'/role/f'{task}.json' for role in ['reference_builder','reference_independent','reference_adjudicator_blind','reference_review']]
  if not all(p.exists() for p in paths):pending.append(task);continue
  source=read(ROOT/'bundles/source'/f'{task}.json');reference_validator(read(paths[0]),source)
  before=not any((ROOT/'records'/role/f'{code}.json').exists() for role in ['score_primary','score_secondary'] for code in read(ROOT/'config.json')['queues'][role] if read(ROOT/'bundles/products'/f'{code}.json')['task_id']==task)
  assert before,'Cannot freeze a new reference after scoring its product: '+task
  write(dest,{'task_id':task,'frozen_at':now(),'before_either_product_score':before,'files':{str(p.relative_to(ROOT)):sha(p) for p in paths},'reference_kind':'source_constrained_agent_reference_v5_per_target'})
  ready.append(task)
 if not (ROOT/'reference_freeze.json').exists():
  write(ROOT/'reference_freeze.json',{'frozen_at':now(),'initial_ready_targets':ready,'initial_pending_targets':pending,'files':{str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'reference_freezes').glob('*.json')},'policy':'User-authorized target-level paired freeze; pending target blocks only its own pair, not the batch'})
 write(ROOT/'reference_readiness.json',{'ready':ready,'pending':pending,'at':now()})
 if not pending and not (ROOT/'reference_freeze.final.json').exists():
  write(ROOT/'reference_freeze.final.json',{'frozen_at':now(),'all_targets':tasks,'files':{str(p.relative_to(ROOT)):sha(p) for p in (ROOT/'reference_freezes').glob('*.json')}})
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
  pairs=[(c1[k],c2[k]) for k in c1 if c1[k] is not None and c2[k] is not None]
  stability.append({'artifact_code':code,'status':'COMPLETED','first':first,'repeat':second,'absolute_difference':{m:abs(first[m]-second[m]) if first[m] is not None and second[m] is not None else None for m in ['C','K','R']},'coverage_evaluable_items':len(pairs),'coverage_unresolved_or_missing':len(c1)-len(pairs),'coverage_weighted_kappa':weighted_kappa(pairs),'coverage_exact_agreement':mean([float(a==b) for a,b in pairs])})
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
 inheritance=read(ROOT/'inheritance_manifest.json')
 inherited_counts=dict(collections.Counter(k.split('/')[0] for k in inheritance['successful']))
 inherited_models=dict(collections.Counter(read(ROOT/v['record_path'])['_agent']['returned_model'] for v in inheritance['successful'].values()))
 counts={role:len(list((ROOT/'records'/role).glob('*.json'))) for role in ['score_primary','score_secondary','score_adjudicated','alignment','score_stability']}
 write(ROOT/'results/completion.json',{'at':now(),'references':len(list((ROOT/'references').glob('*.json'))),'counts':counts,'planned_artifacts':58,'failed_stage_items':failures,'new_v5_actual_returned_models':returned,'inherited_prior_actual_models':inherited_models,'inherited_roles_all_stages':len(inheritance['successful']),'inherited_source_roles':sum(k.startswith('reference') for k in inheritance['successful']),'inherited_by_role':inherited_counts,'inherited_final_references':sum(k.startswith('reference_adjudicator/') for k in inheritance['successful']),'new_calls_only_in_http_counts':True,'administratively_recovered_prior_responses':sum(bool(v.get('recovered_response')) for v in inheritance['successful'].values()),'alignment_admin_unreviewed_units':sum(read(p).get('alignment_unreviewed_units',0) for p in (ROOT/'records/alignment').glob('*.json')),'http_status_counts':dict(collections.Counter(str(x.get('http_status')) for x in statuses)),'attempts':len(statuses),'human_evaluation':False,'claim_scope':'在选定 PURE 材料及共同证据范围内，Agent 对 AUCMR 生成稿与既有人工文本用例的质量评价。','no_noninferiority_claim':True,'suspected_reference_issue_count':len(concerns),'all_queue_processing_finished':True,'remaining_pending':failures,'historical_errors_file':'failure_history.json','per_target_reference_freeze':True})
 diagnostics=[]
 for folder in ['score_primary','score_secondary','score_adjudicated']:
  for p in (ROOT/'records'/folder).glob('*.json'):
   rec=read(p);diagnostics.append({'role':folder,'artifact_code':p.stem,'supplementary_ratings':len(rec.get('supplementary_coverage',[])),'conflicting_duplicate_items':len(rec.get('coverage_conflicts',[])),'product_line_citations':len(rec.get('product_citation_normalizations',[]))})
 align_audits=[]
 for p in (ROOT/'records/alignment').glob('*.json'):
  a=read(p);added=[g['id'] for g in a['groups'] if g.get('audit_status')=='NOT_REVIEWED']
  adjud=ROOT/'records/score_adjudicated'/p.name
  decisions=read(adjud).get('decisions',[]) if adjud.exists() else []
  documented={d['id'] for d in decisions if d['kind']=='unit_group'}
  if added:align_audits.append({'artifact_code':p.stem,'admin_not_reviewed_groups':added,'third_role_decisions_present':sorted(set(added)&documented),'third_role_still_missing':sorted(set(added)-documented),'not_semantic_equivalence_claim':True})
 write(ROOT/'results/normalization_diagnostics.json',{'per_score':diagnostics,'conservative_alignment_additions':align_audits,'administrative_normalization_not_semantic_validation':True})
 manifest={str(p.relative_to(ROOT)):sha(p) for folder in ['records','references','calls','results'] for p in (ROOT/folder).rglob('*') if p.is_file()}
 write(ROOT/'evaluation_output_manifest.json',{'at':now(),'files':manifest,'semantic_quality_not_guaranteed_by_hash':True})

def pending_items(config):
 q=config['queues'];out=[]
 for task in q['reference']:
  if not (ROOT/'reference_freezes'/f'{task}.json').exists():out.append({'stage':'reference','item':task,'classification':'REFERENCE_INCOMPLETE'})
 for role in ['score_primary','score_secondary','score_adjudicated','alignment','score_stability']:
  queue=q['stability'] if role=='score_stability' else q['score_primary']
  for code in queue:
   if not (ROOT/'records'/role/f'{code}.json').exists():
    task=read(ROOT/'bundles/products'/f'{code}.json')['task_id']
    out.append({'stage':role,'item':code,'task_id':task,'classification':'DEPENDENCY_PENDING' if not (ROOT/'reference_freezes'/f'{task}.json').exists() else 'TECHNICAL_PENDING'})
 return out

def execute_phase(config,client,sweep,history):
 q=config['queues'];client.config['repair_sweep']=sweep
 label='initial' if sweep==0 else f'deferred_repair_{sweep}'
 tasks=q['reference'] if sweep==0 else [t for t in q['reference'] if not (ROOT/'reference_freezes'/f'{t}.json').exists()]
 history+=run_stage(label+'_reference',tasks,lambda t:ref_one(t,client),config['workers'])
 freeze_references(q['reference'])
 for role in ['score_primary','score_secondary']:
  queue=q[role] if sweep==0 else [c for c in q[role] if not (ROOT/'records'/role/f'{c}.json').exists()]
  history+=run_stage(label+'_'+role,queue,lambda c:score_one(c,role,client),config['workers'])
 queue=q['adjudication'] if sweep==0 else [c for c in q['adjudication'] if not (ROOT/'records/score_adjudicated'/f'{c}.json').exists()]
 history+=run_stage(label+'_score_adjudicated',queue,lambda c:adjudicate_one(c,client,config),config['workers'])
 queue=q['stability'] if sweep==0 else [c for c in q['stability'] if not (ROOT/'records/score_stability'/f'{c}.json').exists()]
 history+=run_stage(label+'_score_stability',queue,lambda c:score_one(c,'score_secondary',client,True),config['workers'])
 pending=pending_items(config)
 write(ROOT/'pending_errors.json',{'phase':label,'at':now(),'count':len(pending),'items':pending,'technical_failure_not_zero':True})
 write(ROOT/'failure_history.json',{'at':now(),'items':history,'initial_judgments_preserved':True,'includes_later_recovered_errors':True})
 return pending

def main():
 check_freeze();config=read(ROOT/'config.json');client=Client(ROOT,config,read(ROOT/'prompts.json'));history=read(ROOT/'failure_history.json')['items'] if (ROOT/'failure_history.json').exists() else []
 pending=execute_phase(config,client,0,history)
 for sweep in range(1,config['deferred_repair_sweeps']+1):
  if not pending:break
  pending=execute_phase(config,client,sweep,history)
 try:
  deliver(config,pending)
 except Exception as exc:
  # An aggregation error does not discard the completed judgment queue.
  pending.append({'stage':'aggregation','classification':'POSTPROCESSING_ERROR','error':str(exc)})
  write(ROOT/'aggregation_error.json',{'at':now(),'error':str(exc),'traceback':traceback.format_exc()})
 write(ROOT/'pending_errors.json',{'phase':'finished','at':now(),'count':len(pending),'items':pending,'technical_failure_not_zero':True})
 status='COMPLETED' if not pending else 'FINISHED_WITH_PENDING_ERRORS'
 write(ROOT/'state.json',{'stage':'FINISHED','status':status,'at':now(),'pending':pending,'historical_errors':len(history),'results':'results/REPORT.md','all_queues_attempted':True,'deferred_sweeps_limit':config['deferred_repair_sweeps']})
 return 0
if __name__=='__main__':
 lock=(ROOT/'run.lock').open('a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise SystemExit('Another runner is active; use progress.py')
 try:raise SystemExit(main())
 except Exception as exc:
  if isinstance(exc,SystemExit):raise
  write(ROOT/'state.json',{'status':'FAILED','error':str(exc),'traceback':traceback.format_exc(),'at':now()});raise
