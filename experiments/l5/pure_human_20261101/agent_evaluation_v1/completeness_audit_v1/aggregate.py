"""Versioned annotation interpretation; item-level scores remain byte-identical."""
from pathlib import Path
import sys,copy,hashlib,json,shutil,os
from audit import ROOT,PARENT,BASE,read,write,sha,now,canonical,issue_key,excluded_only,verify
import metrics as core
ORIGINAL=core.score_record

def interpret(record,reference,diagnostic_ids=(),scope_concern=False):
 out=copy.deepcopy(record);retained=[];moved=[]
 confirmed={o['id'] for o in reference['obligations'] if o['status']=='CONFIRMED'}
 cov={x['obligation_id']:x.get('value') for x in record['coverage']}
 for issue in out.get('unresolved',[]):
  rule=None
  if excluded_only(issue,reference):rule='OUTSIDE_CONFIRMED_C_DENOMINATOR'
  elif (isinstance(issue,dict) and issue.get('metric')=='C' and issue_key(issue) in diagnostic_ids
        and issue.get('item_id') in confirmed and cov.get(issue['item_id']) in (0,.5,1)):
   rule='TWO_BLIND_REVIEWS_CLASSIFY_EXISTING_NUMERIC_JUDGMENT_NOTE_AS_DIAGNOSTIC'
  if rule:moved.append({'issue':issue,'rule':rule})
  else:retained.append(issue)
 if scope_concern:retained.append({'metric':'C','item_id':'scope','reason':'Supplementary blind annotation review retains a task-scope/reference concern; applies equally to both anonymous products. See decision_register.json. No reference was changed.'})
 out['unresolved']=retained
 result=ORIGINAL(out,reference)
 result['raw_unresolved_annotations']=record.get('unresolved',[])
 result['annotation_dispositions']=moved
 result['supplementary_pair_scope_concern']=scope_concern
 return result

def decisions():
 scan=read(ROOT/'scan.json');register=[];accepted={};concerns={}
 for code in scan['paired_review_artifacts']:
  responses=[]
  for role in ('annotation_review_one','annotation_review_two'):
   p=ROOT/f'reviews/{role}/{code}.json';responses.append(read(p) if p.exists() else None)
  data=read(ROOT/f'bundles/{code}.json');task=data['source']['task_id']
  for role,response in zip(('annotation_review_one','annotation_review_two'),responses):
   if response and response.get('paired_scope_concern'):
    concerns.setdefault(task,[]).append({'artifact_code':code,'reviewer':role,'concern':response['paired_scope_concern']})
  for item in data['annotation_cases']:
   found=[next((x for x in response['decisions'] if x['annotation_id']==item['annotation_id']),None) if response else None for response in responses]
   accept=all(x and x['disposition']=='DIAGNOSTIC' for x in found)
   register.append({'artifact_code':code,'task_id':task,**item,'reviews':found,'resolution':'DIAGNOSTIC' if accept else 'RETAIN_UNRESOLVED'})
   if accept:accepted.setdefault(code,set()).add(item['annotation_id'])
 for item in scan['items']:
  if item['classification']=='OUTSIDE_C_DENOMINATOR':register.append({**item,'resolution':'RETAIN_SOURCE_ISSUE_WITHOUT_INVALIDATING_CONFIRMED_C'})
 write(ROOT/'decision_register.json',{'at':now(),'items':register,'paired_scope_concerns':concerns,'item_scores_changed':False})
 return accepted,concerns

def main():
 verify();freeze=read(ROOT/'review_freeze.json')
 for p,h in freeze['files'].items():assert sha(ROOT/p)==h,p
 accepted,concerns=decisions()
 merged=ROOT/'merged';assert not merged.exists(),'No overwrite of prior aggregation; use a new version'
 merged.mkdir()
 for folder in ('records','references','organizer_private'):shutil.copytree(BASE/folder,merged/folder)
 os.chmod(merged/'organizer_private',0o700)
 for f in (merged/'organizer_private').glob('*'):os.chmod(f,0o600)
 context={}
 for f in (merged/'records').glob('score_*/*.json'):
  product=read(PARENT/f'bundles/products/{f.stem}.json')
  context[canonical(read(f))]=(f.stem,product['task_id'])
 def adjusted(record,ref):
  code,task=context[canonical(record)]
  return interpret(record,ref,accepted.get(code,set()),task in concerns)
 core.score_record=adjusted
 result=core.save_aggregate(merged)
 old=read(BASE/'results/all_results.json')
 for mode in ('independent_mean','adjudicated'):
  for m in ('K','R'):assert result['overall'][mode][m]==old['overall'][mode][m],(mode,m)
 for new,prior in zip(result['per_artifact'],old['per_artifact']):
  assert new['artifact_code']==prior['artifact_code']
  for role in core.ROLES:
   for m in ('K','R'):assert new['scores'][role][m]==prior['scores'][role][m]
 # Reuse stability only after proving no interpretation changed any sampled score.
 stability=read(BASE/'results/stability.json')
 for item in stability['items']:
  code=item['artifact_code'];task=read(PARENT/f'bundles/products/{code}.json')['task_id'];ref=read(merged/f'references/{task}.json')
  for role in ('score_secondary','score_stability'):
   rec=read(merged/f'records/{role}/{code}.json');oldscore=ORIGINAL(rec,ref);newscore=adjusted(rec,ref)
   assert all(oldscore[m]==newscore[m] for m in core.METRICS),'Stability requires recomputation'
 for name in ('stability.json','reference_inventory.json','reference_issue_register.json','length_diagnostics.json'):
  shutil.copyfile(BASE/'results'/name,merged/'results'/name)
 changes=[]
 for new,prior in zip(result['per_case'],old['per_case']):
  if any(new[mode]['C']!=prior[mode]['C'] for mode in ('independent_mean','adjudicated')):
   changes.append({'task_id':new['task_id'],'case_id':new['case_id'],'before':{m:prior[m]['C'] for m in ('independent_mean','adjudicated')},'after':{m:new[m]['C'] for m in ('independent_mean','adjudicated')}})
 remaining=[{'task_id':x['task_id'],'case_id':x['case_id'],'modes':[m for m in ('independent_mean','adjudicated') if not x[m]['C']['paired']]} for x in result['per_case'] if any(not x[m]['C']['paired'] for m in ('independent_mean','adjudicated'))]
 write(merged/'results/change_audit.json',{'at':now(),'changes':changes,'remaining_C_NA_targets':remaining,'before_overall':old['overall'],'after_overall':result['overall'],'K_R_unchanged':True,'raw_first_and_adjudicated_item_judgments_unchanged':True})
 integrity={str(f.relative_to(BASE)):sha(f) for folder in ('records','references') for f in (BASE/folder).rglob('*') if f.is_file()}
 for p,h in integrity.items():assert sha(merged/p)==h,p
 verify()
 write(ROOT/'integrity.json',{'at':now(),'all_upstream_historical_hashes_verified':True,'copied_records_references_byte_identical':len(integrity),'files':integrity,'original_A_B_and_frozen_inputs_untouched':True})
 write(merged/'results/annotation_provenance.json',{'at':now(),'original_metric_script_sha256':sha(PARENT/'metrics.py'),'annotation_adapter_sha256':sha(ROOT/'aggregate.py'),'decision_register_sha256':sha(ROOT/'decision_register.json'),'freeze_sha256':sha(ROOT/'freeze.json'),'review_freeze_sha256':sha(ROOT/'review_freeze.json'),'post_scoring_audit':True,'no_new_item_scores':True})
 with (merged/'results/REPORT.md').open('a') as f:
  f.write('\n\n本版本为评分完成后的未决注释复核：原逐项评分与冻结参照不变，来源分母外注释不再使 C 全项 NA；补充双模型复核仅对明确的诊断注释调整其汇总作用。原始首判和旧汇总保留，不能称为预注册修正。详见 ../../PROTOCOL.md、../../decision_register.json、change_audit.json。\n')
 write(ROOT/'state.json',{'at':now(),'status':'COMPLETED','technical_pending':0,'annotation_review_failures':read(ROOT/'review_completion.json')['failed'],'remaining_C_NA_targets':remaining,'paired_C':{m:result['overall'][m]['C']['paired_targets'] for m in ('independent_mean','adjudicated')},'report':str(merged/'results/REPORT.md')})
 print(json.dumps(read(ROOT/'state.json'),ensure_ascii=False))

if __name__=='__main__':main()
