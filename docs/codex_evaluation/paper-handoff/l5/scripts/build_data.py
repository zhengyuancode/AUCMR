"""Export final L5 author handoff. Read-only toward experimental artifacts; no API calls."""
from pathlib import Path
import json,csv,hashlib,collections,statistics,datetime,shutil
HERE=Path(__file__).resolve().parents[1]
REPO=HERE.parents[3]
EXP=REPO/'experiments/l5/pure_human_20261101'
FINAL=EXP/'agent_evaluation_v1/scope_adjudication_v1/continuation_v2'
MERGED=FINAL/'merged'
ROLES=('score_primary','score_secondary','score_adjudicated')
SOURCES={}
def rel(p):return Path(p).resolve().relative_to(REPO).as_posix()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def register(p,purpose='final_evidence',access='author'):
 p=Path(p);assert p.is_file(),p
 SOURCES[rel(p)]={'path':rel(p),'sha256':sha(p),'bytes':p.stat().st_size,'purpose':purpose,'access':access}
 return rel(p)
def read(p,purpose='final_evidence'):
 register(p,purpose);return json.loads(Path(p).read_text())
def portable(x):
 if isinstance(x,dict):return {k:portable(v) for k,v in x.items()}
 if isinstance(x,list):return [portable(v) for v in x]
 if isinstance(x,str):return x.replace(str(REPO)+'/', '').replace(str(REPO.parent)+'/', '')
 return x
def write(name,obj):
 p=HERE/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(portable(obj),ensure_ascii=False,indent=2)+'\n')
def table(name,rows):
 p=HERE/name;p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader()
  for row in rows:w.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
def copy_text(p,name):
 register(p,'unaltered_material');dst=HERE/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dst)
def actor(meta):return {k:meta.get(k) for k in ('role','provider','requested_model','returned_model','at','request_sha256')}

def main():
 assert REPO.name=='AUCMR' and EXP.is_dir()
 pointer=read(EXP/'agent_evaluation_v1/CURRENT_RESULT.json','result_selection')
 pointer_path=str(pointer['current_results']).replace('\\','/')
 assert pointer_path==rel(MERGED/'results') or pointer_path.endswith('/'+rel(MERGED/'results')),'Authoritative result changed; review before rebuilding'
 selected=read(EXP/'inputs/manifest.json');candidates=read(EXP/'screening/candidates.json');boundaries=read(EXP/'screening/source_boundaries.json');official=read(EXP/'evidence/source_manifest.json')
 results=read(MERGED/'results/all_results.json');mapping=read(MERGED/'organizer_private/mapping.json','deblinded_pair_identity')
 bycase={x['case_id']:x for x in results['per_case']};byinput={x['case']:x for x in selected};bycandidate={x['id']:x for x in candidates};byartifact={x['artifact_code']:x for x in mapping}
 docs={x['document_id']:x for x in official['documents']};projects=[]
 for doc,bound in boundaries.items():
  group=[x for x in candidates if x['document']==doc];counts=collections.Counter(x['decision'] for x in group);d=docs[doc]
  projects.append({'project_id':doc,'material_type':bound['material_type'],'pdf_pages':d['pages'],'original_extracted_words':d['extracted_words'],'shared_input_words':bound['words'],'input_pdf_pages':bound['pdf_pages'],'body_occurrences':len(group),'eligible':counts['INCLUDE'],'excluded':counts['EXCLUDE'],'pending_boundary_not_selected':counts['PENDING_BOUNDARY'],'duplicate_occurrences':counts['DUPLICATE'],'selected_targets':sum(bool(x.get('selected')) for x in group),'hidden_scope':bound['hidden'],'parallel_copy_boundary':bound['parallel_audit'],'source_pdf':register(EXP/d['source_path'],'official_PURE_pdf'),'source_pdf_sha256':d['sha256']})
 table('tables/projects.csv',projects)
 table('tables/candidates.csv',[{'candidate_id':x['id'],'document':x['document'],'original_id':x.get('original_id'),'title':x['title'],'primary_actor':x.get('primary_actor'),'eligibility':x['decision'],'selected':bool(x.get('selected')),'reason':x.get('reason'),'B_pdf_pages':x.get('B_span',{}).get('pages',[]),'selection_hash':x.get('selection_hash',''),'completeness_note':x.get('completeness',''),'independent_requirement_locations':x.get('independent_requirement_locations','')} for x in candidates])
 case_rows=[];input_rows=[];reference_rows=[];obligation_rows=[];index=[];needed_agents=[]
 for case,inputmeta in byinput.items():
  row=bycase[case];t=row['task_id'];cand=bycandidate[case];inp=read(EXP/inputmeta['path']);assert sha(EXP/inputmeta['path'])==inputmeta['sha256']
  write(f'materials/inputs/{case}.json',inp)
  ap=EXP/f'runs/formal-v2-credential-recovery/{case}/A.txt';bp=EXP/f'private_controls/{case}.txt';raw=EXP/f'private_controls/{case}.raw.txt'
  copy_text(ap,f'materials/generated/{case}.txt');copy_text(bp,f'materials/controls/{case}.txt');copy_text(raw,f'materials/controls/{case}.raw.txt')
  for p in ap.parent.rglob('*'):
   if p.is_file():register(p,'formal_generation_trajectory')
  register(EXP/f'agent_evaluation_v1/revision_v5/bundles/source/{t}.json','shared_evaluation_source')
  assert sha(bp)==cand['B_display_sha256'] and sha(raw)==cand['B_raw_sha256']
  reference=read(MERGED/f'references/{t}.json');needed_agents.append(('final_reference',t,reference['_agent']))
  clean_ref={'task_id':t,'case_id':case,'title':inp['title'],'primary_actor':inp['primary_actor'],'scope':reference['scope'],'interpretation_rules':reference.get('interpretation_rules',[]),'alternatives_and_dissent':reference.get('alternatives_and_dissent',[]),'obligations':[],'source_record':rel(MERGED/f'references/{t}.json')}
  rc=collections.Counter(o['status'] for o in reference['obligations'])
  for o in reference['obligations']:
   evid=[{'part_id':e['part_id'],'pdf_page':e.get('pdf_page'),'quote':e.get('exact_quote',e.get('quote')),'source_span':e.get('source_span')} for e in o['evidence']]
   clean_ref['obligations'].append({**{k:o.get(k) for k in ('id','text','category','status','scope','atomicity','reason')},'evidence':evid})
   obligation_rows.append({'task_id':t,'case_id':case,'obligation_id':o['id'],'status':o['status'],'category':o['category'],'text':o['text'],'scope':o.get('scope',''),'atomicity':o.get('atomicity',''),'reason':o.get('reason',''),'source_parts':[e['part_id'] for e in evid],'pdf_pages':[e['pdf_page'] for e in evid],'verbatim_evidence':[e['quote'] for e in evid]})
  write(f'materials/references/{t}.json',clean_ref)
  reference_rows.append({'task_id':t,'case_id':case,'document':inp['document'],'confirmed':rc['CONFIRMED'],'source_unresolved':rc['UNRESOLVED'],'excluded_in_final_obligation_array':rc['EXCLUDED'],'confirmed_by_category':dict(collections.Counter(o['category'] for o in reference['obligations'] if o['status']=='CONFIRMED')),'final_reference_path':rel(MERGED/f'references/{t}.json')})
  case_rows.append({'case_id':case,'task_id':t,'document':inp['document'],'original_id':inp['original_id'],'title':inp['title'],'given_primary_actor':inp['primary_actor'],'input_words':inputmeta['input_words'],'source_pdf_pages':inputmeta['source_pages'],'source_parts':len(inp['requirements']),'B_pdf_pages':cand['B_span']['pages'],'B_start_line_1based':cand['B_span']['start_line'],'B_end_line_exclusive_1based':cand['B_span']['end_line_exclusive'],'input_path':rel(EXP/inputmeta['path']),'A_path':rel(ap),'B_display_path':rel(bp),'B_raw_path':rel(raw),'A_anonymous_code':row['artifact_codes']['A'],'B_anonymous_code':row['artifact_codes']['B'],'A_words_whitespace_split':len(ap.read_text().split()),'B_words_whitespace_split':len(bp.read_text().split()),'A_sha256':sha(ap),'B_display_sha256':sha(bp),'B_raw_sha256':sha(raw),'reference_confirmed':rc['CONFIRMED'],'reference_unresolved':rc['UNRESOLVED'],'input_sufficiency_basis':cand.get('independent_requirement_locations',''),'original_completeness_flags':cand.get('B_completeness_flags',{})})
  for part in inp['requirements']:
   input_rows.append({'case_id':case,'task_id':t,'part_id':part['source_part_id'],'pdf_page_1based':part['pdf_page'],'normalized_lines_1based_inclusive':part['normalized_lines'],'words':len(part['text'].split()),'input_path':rel(EXP/inputmeta['path']),'text_sha256':hashlib.sha256(part['text'].encode()).hexdigest()})
  for method,code in row['artifact_codes'].items():
   register(EXP/f'agent_evaluation_v1/revision_v5/bundles/products/{code}.json','anonymous_evaluated_display')
   for role in ROLES:
    path=MERGED/f'records/{role}/{code}.json';record=read(path);c_agent=record['_agent'];kr_agent=record.get('composition_provenance',{}).get('inherited_units_agent',c_agent)
    needed_agents += [(role+'_C',code,c_agent),(role+'_KR',code,kr_agent)]
    # Copy final judgments, not intermediate annotations or obsolete control flow.
    current={'artifact_code':code,'task_id':t,'case_id':case,'method':method,'evaluation_role':role,'coverage':record['coverage'],'units':record['units'],'empty_behavior':record['empty_behavior'],'final_metric_values':next(x for x in results['per_artifact'] if x['artifact_code']==code)['scores'][role], 'agent_for_C':actor(c_agent),'agent_for_K_R':actor(kr_agent),'source_record':rel(path),'source_record_sha256':sha(path)}
    current['final_boundary_decisions']=current['final_metric_values'].get('inherited_annotation_dispositions',[])
    current['final_metric_values']={k:current['final_metric_values'][k] for k in ('C','K','R','denominators','counts','by_category')}
    write(f'materials/ratings/{role}/{code}.json',current)
    index.append({'artifact_code':code,'task_id':t,'case_id':case,'method':method,'role':role,'record_path':rel(path),'sha256':sha(path),'handoff_record':f'materials/ratings/{role}/{code}.json'})
   aligned=read(MERGED/f'records/alignment/{code}.json','blind_unit_alignment');needed_agents.append(('blind_alignment',code,aligned['_agent']))
 table('tables/cases.csv',case_rows);table('tables/input_boundaries.csv',input_rows);table('tables/reference_inventory.csv',reference_rows);table('tables/obligations.csv',obligation_rows);write('materials/record_index.json',index)
 # Resolve accepted-record request fingerprints to actual saved API requests.
 for role in ('scope_reader_one','scope_reader_two'):
  for p in (FINAL/f'records/{role}').glob('*.json'):
   rec=read(p);needed_agents.append((role,p.stem,rec['_agent']))
 for role in ('reference_builder','reference_independent','reference_adjudicator_blind','reference_review'):
  for p in (MERGED/f'records/{role}').glob('*.json'):
   if p.stem in ('T003','T009'):continue
   rec=read(p);needed_agents.append((role,p.stem,rec['_agent']))
 for p in (MERGED/'records/score_stability').glob('*.json'):
  rec=read(p);needed_agents.append(('stability_secondary',p.stem,rec['_agent']))
 wanted={a.get('request_sha256') for _,_,a in needed_agents};request_lookup={}
 files=sorted((EXP/'agent_evaluation_v1').glob('**/calls/*/*/attempt-*/request.json'),key=lambda p:(len(p.parts),str(p)))
 for p in files:
  d=json.loads(p.read_text());finger=d.get('input_sha256')
  if finger in wanted and finger not in request_lookup:
   request_lookup[finger]=(p,d)
 profiles=[];call_index=[]
 for role,unit,meta in needed_agents:
  fingerprint=meta.get('request_sha256');found=request_lookup.get(fingerprint)
  assert found,('Missing accepted request provenance',role,unit,fingerprint)
  p,d=found
  exact_attempt=p.parent.parent/f"attempt-{int(meta.get('attempt',1)):02d}"
  if (exact_attempt/'request.json').exists():
   candidate=json.loads((exact_attempt/'request.json').read_text())
   if candidate.get('input_sha256')==fingerprint:p,d=exact_attempt/'request.json',candidate
  payload=d['payload'];status=p.parent/'status.json';register(p,'accepted_model_request');register(status,'accepted_model_response_metadata')
  content=p.parent/'content.txt'
  assert content.exists(),('Missing actual response content',role,unit)
  register(content,'actual_model_response_content')
  # A recovered request's final answer may be an earlier attempt with same fingerprint.
  profiles.append({'role_or_metric_component':role,'unit':unit,'provider':meta['provider'],'requested_model':meta['requested_model'],'returned_model':meta['returned_model'],'temperature':payload.get('temperature'),'max_tokens':payload.get('max_tokens'),'thinking_setting':payload.get('extra',payload.get('thinking',{'enable_thinking':payload.get('enable_thinking')})),'tools_provided':d.get('tools_provided',False),'request_path':rel(p),'request_fingerprint':fingerprint,'accepted_record_time':meta.get('at')})
  call_index.append({'role':role,'unit':unit,'request_fingerprint':fingerprint,'request_path':rel(p),'response_content_path':rel(content),'response_metadata_path':rel(status),'response_model_from_accepted_record':meta['returned_model'],'accepted_record_time':meta.get('at'),'lookup_note':'Request fingerprint plus accepted-record attempt locates real API response; mechanical validation may be separate. Final rating record is authoritative for adopted judgments.'})
 profiles.insert(0,{'role_or_metric_component':'generation','unit':'29 targets','provider':'DeepSeek','requested_model':'deepseek-v4-flash','returned_model':'deepseek-flash','temperature':None,'max_tokens':None,'thinking_setting':'service default / chat_template_kwargs in requests','tools_provided':False,'request_path':rel(EXP/'runs/formal-v2-credential-recovery'),'request_fingerprint':'per call in logs','accepted_record_time':'see per-target status.json'})
 table('tables/model_and_role_config.csv',profiles);write('materials/call_provenance_index.json',call_index)
 for name in ('overall','per_case','per_project','agreement','completion'):
  write(f'results/{name}.json',read(MERGED/f'results/{name}.json'))
 stability=read(MERGED/'results/stability.json')
 clean_stability={k:v for k,v in stability.items() if k!='items'}
 clean_stability['items']=[{**{k:v for k,v in x.items() if k not in ('first','repeat')},'first':{k:x['first'][k] for k in ('C','K','R')},'repeat':{k:x['repeat'][k] for k in ('C','K','R')}} for x in stability['items']]
 write('results/agreement_and_stability.json',{'agreement':read(MERGED/'results/agreement.json'),'stability':clean_stability})
 percase=[];perproject=[];overall=[]
 for x in results['per_case']:
  for mode in ('independent_mean','adjudicated'):
   for metric in ('C','K','R'):percase.append({'case_id':x['case_id'],'task_id':x['task_id'],'project':x['document'],'mode':mode,'metric':metric,**x[mode][metric]})
 for x in results['per_project']:
  for mode in ('independent_mean','adjudicated'):
   for metric in ('C','K','R'):perproject.append({'project':x['document'],'planned_targets':x['planned_targets'],'mode':mode,'metric':metric,**x[mode][metric]})
 for mode in ('independent_mean','adjudicated'):
  for metric in ('C','K','R'):overall.append({'mode':mode,'metric':metric,**results['overall'][mode][metric]})
 table('tables/results_per_case.csv',percase);table('tables/results_per_project.csv',perproject);table('tables/results_overall.csv',overall)
 diagnostics={}
 for role in ROLES:
  diagnostics[role]={}
  for method in ('A','B'):
   rows=[x['scores'][role] for x in results['per_artifact'] if x['kind']==method];sums=collections.Counter()
   for x in rows:sums.update(x['counts'])
   diagnostics[role][method]={'pooled_counts_descriptive_not_main_aggregate':dict(sums),'empty_behavior_artifacts':sum(x['empty_behavior'] for x in rows),'C_denominator_range':[min(x['denominators']['C_N'] for x in rows),max(x['denominators']['C_N'] for x in rows)],'R_M_range':[min(x['denominators']['R_M'] for x in rows),max(x['denominators']['R_M'] for x in rows)]}
 direction={}
 for mode in ('independent_mean','adjudicated'):
  direction[mode]={}
  for metric in ('C','K','R'):
   sign=lambda v:'A_higher' if v>1e-12 else 'A_lower' if v< -1e-12 else 'equal'
   direction[mode][metric]={'targets':dict(collections.Counter(sign(x[mode][metric]['A_minus_B']) for x in results['per_case'])),'projects':dict(collections.Counter(sign(x[mode][metric]['A_minus_B']) for x in results['per_project']))}
 lengths={m:{'count':29,'mean':statistics.fmean(x[f'{m}_words_whitespace_split'] for x in case_rows),'median':statistics.median(x[f'{m}_words_whitespace_split'] for x in case_rows),'min':min(x[f'{m}_words_whitespace_split'] for x in case_rows),'max':max(x[f'{m}_words_whitespace_split'] for x in case_rows)} for m in ('A','B')}
 write('results/diagnostics.json',{'reference_counts':dict(collections.Counter(o['status'] for o in obligation_rows)),'by_role_and_method':diagnostics,'direction_counts':direction,'display_word_counts':lengths,'interpretation':'Counts pooled only for diagnostics; main estimates are project-weighted. Words include presentation labels, not behavior-unit counts.'})
 generation=read(EXP/'runs/formal-v2-credential-recovery/delivery_audit.json');write('results/generation_summary.json',{k:generation[k] for k in ('A_completed','planned','request_count','http_status_counts','response_model_counts','usage','all_frozen_files_verified','A_text_matches_untouched_result','source_control_input_hashes_verified','presentation_semantics_verified')})
 paths=['protocol/EVALUATION_v1.md','protocol/SCREENING_RULES_v1.md','protocol/SCREENING_RULES_v2.md','protocol/INPUT_AND_CONTROL_FREEZE.json','ADAPTATION.md','config/run.json','prompts/text_plan.txt','prompts/task_contract.txt','evidence/core_lineage.json','evidence/core_adaptation.diff','evidence/overlap_audit.json','validation/final_status.json','validation/review_package_integrity.json','validation/MATERIAL_AUDIT.md','agent_evaluation_v1/PROTOCOL.md','agent_evaluation_v1/freeze.json','agent_evaluation_v1/revision_v5/config.json','agent_evaluation_v1/revision_v5/metrics.py','agent_evaluation_v1/revision_v5/isolation.py','agent_evaluation_v1/scope_adjudication_v1/PROTOCOL.md','agent_evaluation_v1/scope_adjudication_v1/summarize.py']
 for p in paths:register(EXP/p,'method_or_verification')
 register(EXP/'agent_evaluation_v1/completeness_audit_v1/compatibility_v2/decision_register.json','adopted_boundary_decision_provenance')
 for name in ('runner.py','transport.py','contracts.py','contexts.py','normalizers.py','locator.py','citations.py','product_citations.py','prompts.json','permissions.json','metric_rules.txt','reference_freeze.final.json','validation/offline.json','validation/model_binding_check.json'):
  register(EXP/'agent_evaluation_v1/revision_v5'/name,'effective_agent_execution_specification')
 for p in (EXP/'runtime').rglob('*.py'):register(p,'generation_implementation')
 for p in (EXP/'prompts').glob('*'):
  if p.is_file():register(p,'generation_prompt')
 for p in ('config.json','prompts.json','isolation.py','freeze.json','reference_freeze.json','reference_changes.json','composition_provenance.json','integrity.json','evaluation_freeze.json'):register(FINAL/p,'final_evaluation_specification')
 write('artifact_manifest.json',{'format':'repository-relative-paths','repository_root_marker':'AUCMR','package_role':'deblinded author handoff; never send this directory as blind scoring input','files':list(SOURCES.values()),'final_results_root':rel(MERGED/'results'),'generation_root':rel(EXP/'runs/formal-v2-credential-recovery')})
 write('package_identity.json',{'experiment':'L5','purpose':'Agent evaluation of AUCMR vs existing human textual use cases under selected PURE shared evidence','projects':10,'targets':29,'artifacts':58,'rating_records':174,'confirmed_obligations':295,'source_unresolved_obligations':4,'human_review_completed':False,'current_quality_results_have_NA':False,'source_results':rel(MERGED/'results'),'path_base':'AUCMR repository root','generated_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
 print(json.dumps({'projects':len(projects),'cases':len(case_rows),'rating_records':len(index),'source_manifest_files':len(SOURCES),'accepted_request_profiles':len(profiles),'reference_counts':dict(collections.Counter(o['status'] for o in obligation_rows))}))

if __name__=='__main__':main()
