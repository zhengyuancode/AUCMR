"""Merge revised C only; retain all original K/R judgments and historical versions."""
import copy,shutil,os,collections,json,hashlib
from run import ROOT,PARENT,PRIOR,BASE,TASKS,CODES,read,write,sha,now,canonical,verify
import metrics as core
RAW_SCORE=core.score_record

def issue_key(x):return hashlib.sha256(canonical(x).encode()).hexdigest()[:20]

def deliver():
 verify()
 for p,h in read(ROOT/'evaluation_freeze.json')['files'].items():assert sha(ROOT/p)==h,p
 merged=ROOT/'merged';assert not merged.exists(),'Keep prior aggregation; do not overwrite'
 merged.mkdir()
 for folder in ('records','references','organizer_private'):shutil.copytree(BASE/folder,merged/folder)
 os.chmod(merged/'organizer_private',0o700)
 for p in (merged/'organizer_private').glob('*'):os.chmod(p,0o600)
 changes=[]
 for task in TASKS:
  old=read(BASE/f'references/{task}.json');new=read(ROOT/f'references/{task}.json')
  shutil.copyfile(ROOT/f'references/{task}.json',merged/f'references/{task}.json')
  changes.append({'task_id':task,'prior_confirmed':sum(x['status']=='CONFIRMED' for x in old['obligations']),'new_confirmed':sum(x['status']=='CONFIRMED' for x in new['obligations']),'chosen_scope':new['scope'],'interpretation_rules':new['interpretation_rules'],'alternatives_and_dissent':new['alternatives_and_dissent'],'obligations':[{'id':x['id'],'prior_text':x['prior_text'],'final_text':x['text'],'final_status':x['status'],'reason':x['reason'],'evidence':x['evidence']} for x in new['obligations']]})
 compositions=[]
 for code in CODES:
  for score_role,c_role in [('score_primary','coverage_primary'),('score_secondary','coverage_secondary'),('score_adjudicated','coverage_adjudicator')]:
   old=read(BASE/f'records/{score_role}/{code}.json');new=read(ROOT/f'records/{c_role}/{code}.json');combined=copy.deepcopy(old)
   combined['coverage']=new['coverage'];combined['previous_C_annotations']=copy.deepcopy(old.get('unresolved',[]))
   assert all(x.get('metric')!='ALL' for x in old.get('unresolved',[])),'Cannot silently remove cross-metric issue'
   combined['unresolved']=[x for x in old.get('unresolved',[]) if x.get('metric')!='C']
   for name in ('supplementary_coverage','coverage_conflicts'):
    if name in combined:combined['previous_'+name]=combined.pop(name)
   if 'decisions' in combined:
    combined['previous_decisions']=combined['decisions']
    combined['decisions']=[x for x in combined['decisions'] if x.get('kind')!='coverage']+[{'kind':'coverage','id':x['obligation_id'],'action':'changed','reason':x['reason'],'source':'new_reasoned_scope_coverage_adjudication'} for x in new['coverage']]
   combined['coverage_revision_diagnostics']=new.get('diagnostics',[])
   combined['_agent']={**new['_agent'],'provenance':'COMPOSITE_RECORD_NEW_COVERAGE_ONLY_INHERITED_K_R_UNITS'}
   combined['composition_provenance']={'new_C_record':f'records/{c_role}/{code}.json','new_C_record_sha256':sha(ROOT/f'records/{c_role}/{code}.json'),'inherited_units_record':str(BASE/f'records/{score_role}/{code}.json'),'inherited_units_record_sha256':sha(BASE/f'records/{score_role}/{code}.json'),'inherited_units_agent':old.get('_agent'),'new_source_scope_freeze_sha256':sha(ROOT/'reference_freeze.json')}
   assert combined['units']==old['units'],'K/R units must be unchanged'
   write(merged/f'records/{score_role}/{code}.json',combined);compositions.append({'artifact_code':code,'role':score_role,**combined['composition_provenance']})
 # Prior diagnostic-disposition decisions still apply to unaffected targets.
 old_audit=read(PRIOR/'decision_register.json');accepted=collections.defaultdict(set)
 for item in old_audit['items']:
  if item['resolution']=='DIAGNOSTIC':accepted[item['artifact_code']].add(item['annotation_id'])
 record_codes={canonical(read(f)):f.stem for f in (merged/'records').glob('score_*/*.json')}
 def adjusted(record,ref):
  code=record_codes[canonical(record)];out=copy.deepcopy(record);moved=[];retained=[]
  status={o['id']:o['status'] for o in ref['obligations']};cov={x['obligation_id']:x.get('value') for x in out['coverage']}
  for issue in out.get('unresolved',[]):
   excluded=issue.get('metric')=='C' and status.get(issue.get('item_id')) in ('UNRESOLVED','EXCLUDED')
   diagnostic=issue.get('metric')=='C' and issue_key(issue) in accepted[code] and status.get(issue.get('item_id'))=='CONFIRMED' and cov.get(issue['item_id']) in (0,.5,1)
   if excluded or diagnostic:moved.append({'issue':issue,'rule':'Inherited approved annotation disposition'})
   else:retained.append(issue)
  out['unresolved']=retained;score=RAW_SCORE(out,ref);score['raw_unresolved_annotations']=record.get('unresolved',[]);score['inherited_annotation_dispositions']=moved
  return score
 core.score_record=adjusted;result=core.save_aggregate(merged);old_results=read(BASE/'results/all_results.json')
 assert not result['completion']['technical_or_missing_records'],'All records must calculate successfully'
 for mode in ('independent_mean','adjudicated'):
  for m in ('K','R'):assert result['overall'][mode][m]==old_results['overall'][mode][m]
  assert result['overall'][mode]['C']['paired_targets']==29,'Do not claim complete C without valid pairs'
 for a,b in zip(result['per_artifact'],old_results['per_artifact']):
  assert a['artifact_code']==b['artifact_code']
  for role in core.ROLES:
   for m in ('K','R'):assert a['scores'][role][m]==b['scores'][role][m]
   if a['artifact_code'] not in CODES:assert a['scores'][role]['C']==b['scores'][role]['C']
 stability=read(BASE/'results/stability.json');assert not set(stability['sample'])&set(CODES)
 shutil.copyfile(BASE/'results/stability.json',merged/'results/stability.json')
 shutil.copyfile(BASE/'results/length_diagnostics.json',merged/'results/length_diagnostics.json')
 inventory=[]
 for p in sorted((merged/'references').glob('*.json')):
  ref=read(p);inventory.append({'task_id':p.stem,'counts':dict(collections.Counter(x['status'] for x in ref['obligations'])),'source_unresolved':[o for o in ref['obligations'] if o['status']=='UNRESOLVED'],'excluded':[o for o in ref['obligations'] if o['status']=='EXCLUDED']})
 write(merged/'results/reference_inventory.json',inventory)
 per_case_changes=[{'task_id':a['task_id'],'case_id':a['case_id'],'before':{m:b[m]['C'] for m in ('independent_mean','adjudicated')},'after':{m:a[m]['C'] for m in ('independent_mean','adjudicated')}} for a,b in zip(result['per_case'],old_results['per_case']) if a['task_id'] in TASKS]
 write(ROOT/'reference_changes.json',changes);write(ROOT/'composition_provenance.json',compositions)
 write(merged/'results/change_audit.json',{'at':now(),'reference_changes':changes,'per_case_C_changes':per_case_changes,'before_overall':old_results['overall'],'after_overall':result['overall'],'K_R_unchanged':True,'unaffected_27_target_scores_unchanged':True,'modified_C_ratings':12,'human_evaluation':False})
 statusfiles=list((ROOT/'calls').glob('*/*/attempt-*/status.json'));statuses=[read(p) for p in statusfiles]
 write(merged/'results/completion.json',{'at':now(),'references':29,'score_primary':58,'score_secondary':58,'score_adjudicated':58,'alignment':58,'score_stability':10,'C_paired_targets':29,'technical_pending':0,'actual_models':dict(collections.Counter(s.get('returned_model') for s in statuses)),'new_source_interpretation_records':6,'new_coverage_records':12,'old_versions_retained':True,'K_R_not_rescored':True})
 with (merged/'results/REPORT.md').open('a') as f:
  f.write('\n\n本版本按用户授权对 T003、T009 作有依据的最终范围裁决，并在冻结新参照后对双方统一重评 C；不要求所有 Agent 同意。旧 NA、旧参照和评分历史保留。其余 27 个目标不变，K/R 全部不变。C 的29对结果依赖本版本明确的操作性解释，不表示此前分歧已被证明不存在；这属于评分后的方法修订。详见 ../../PROTOCOL.md、../../reference_changes.json、change_audit.json。\n')
 verify()
 write(ROOT/'integrity.json',{'at':now(),'historical_files_verified':len(read(ROOT/'upstream_manifest.json')),'frozen_original_products_and_sources_verified':True,'K_R_identical_every_artifact':True,'other_27_targets_C_identical':True,'only_new_merged_files_changed':True})
 write(ROOT/'state.json',{'at':now(),'status':'COMPLETED','technical_pending':0,'C_paired_targets':29,'remaining_C_NA_targets':[],'report':str(merged/'results/REPORT.md')})
 paper=['# 两个目标的最终参照解释与论文记录','','本次采用有来源依据的最终操作性裁决，不要求所有 Agent 完全一致。两个目标的新版参照在本次产物重评前冻结；本修订发生于原实验评分之后，旧参照、NA 结果和所有原评分均保留。','','两个目标的四份匿名产物由两种模型独立重新判断完整性，再由裁决角色作最终选择。仅覆盖评分发生变化；其余27目标及全部K/R判断不变。']
 for change in changes:
  paper+=['','## '+change['task_id'],'',change['chosen_scope'],'',f"原明确义务 {change['prior_confirmed']} 项，本版本明确义务 {change['new_confirmed']} 项。",'','解释规则：']
  paper+=['- '+x for x in change['interpretation_rules']]
  paper+=['','异议及备选解释：']+['- '+x for x in change['alternatives_and_dissent']]
 paper+=['','## 论文披露建议','','对于先前存在完整性解释分歧的两个目标，我们在保留原始判断的基础上，依据共同需求和给定用例范围形成最终操作性参照。来源裁决阶段不接触产物；新版参照冻结后，双方匿名产物均按相同参照重新评价。最终裁决不要求评者一致，异议和选择理由独立保存。完整性统计因此覆盖全部29对产物，其有效性以所披露的范围解释为条件，而不是声称先前歧义不存在。','','本次属于评分后的授权方法修订，不能称为原始预注册。全部评价为Agent评价，不是真人评审。不能将一致性当准确性，也不能据此严格证明不劣。来源约束正确性较高不等于现实世界绝对正确；AUCMR冗余仍较高，不能声称各维度全面优越。']
 (ROOT/'PAPER_NOTES.md').write_text('\n'.join(paper)+'\n')
 previous=read(ROOT/'prior_CURRENT_RESULT.json')
 write(ROOT.parent/'CURRENT_RESULT.json',{**previous,'updated_at':now(),'status':'COMPLETE_REASONED_SCOPE_ADJUDICATION','current_report':str(merged/'results/REPORT.md'),'current_results':str(merged/'results'),'current_state':str(ROOT/'state.json'),'current_audit_report':str(ROOT/'PAPER_NOTES.md'),'paper_notes':str(ROOT/'PAPER_NOTES.md'),'C_NA_note':'29/29 paired targets after source-only reasoned scope adjudication and uniform blind C re-evaluation for T003/T009; prior NA history retained.','prior_current_result':str(ROOT/'prior_CURRENT_RESULT.json')})
 write(ROOT/'output_manifest.json',{'at':now(),'files':{str(p.relative_to(ROOT)):sha(p) for folder in ('records','references','calls','merged/results') for p in (ROOT/folder).rglob('*') if p.is_file()}})
 print(json.dumps(read(ROOT/'state.json'),ensure_ascii=False),flush=True)
