"""Recompute descriptive L4 tables from bundled data; standard library, no network.

Default is read-only verification. --write updates only this package's derived tables.
No inference of human independence, no p-values, no discarded low ratings.
"""
from pathlib import Path
from collections import Counter, defaultdict
from statistics import mean, median, stdev
import argparse, csv, json, math
P=Path(__file__).resolve().parents[1]; D=P/'data'
SLOTS=('goal','preconditions','trigger','main_flow','alternative_flow','exception_flow','postconditions')
def read(n): return json.loads((D/n).read_text())
def jsonl(n): return [json.loads(s) for s in (D/n).read_text().splitlines() if s]
def csvtext(rows):
 import io
 out=io.StringIO(newline='');w=csv.DictWriter(out,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows);return out.getvalue()
def describe(values):
 v=[x for x in values if x is not None]
 return {'n':len(v),'mean':mean(v) if v else None,'median':median(v) if v else None,'sd':stdev(v) if len(v)>1 else None,'min':min(v) if v else None,'max':max(v) if v else None}
def summary_rows(rows,groups,metrics):
 buckets=defaultdict(list)
 for r in rows: buckets[tuple(r[g] for g in groups)].append(r)
 out=[]
 for group,items in sorted(buckets.items()):
  for metric in metrics:
   out.append({**dict(zip(groups,group)),'metric':metric,**describe([r[metric] for r in items])})
 return out

def calculate():
 exp=read('export.json');cat=read('catalog.json');alloc=read('allocation.json');prov=read('provenance.json')
 rows=exp['ratings'];targets={t['task_id']:t for t in cat['targets']};methods=cat['methods']
 assert len(rows)==1953 and len(prov)==279
 assert len({(r['evaluation_id'],r['method']) for r in rows})==1953
 bycm=defaultdict(list)
 inputs={(v['task_id'],v['method']):v for v in jsonl('evaluated_inputs.jsonl')}
 for r in rows:
  assert r['status'] in ('SCORED','NO_EVALUABLE_CONTENT')
  assert all(type(r['pcs_'+s]) is int and 0<=r['pcs_'+s]<=3 for s in SLOTS)
  assert math.isclose(r['pcs_mean'],sum(r['pcs_'+s] for s in SLOTS)/7,abs_tol=1e-12)
  assert (type(r['prc']) is int and 1<=r['prc']<=5) if r['status']=='SCORED' else r['prc'] is None and r['pcs_mean']==0
  v=inputs[(r['task_id'],r['method'])];assert v['no_content']==(r['status']=='NO_EVALUABLE_CONTENT')
  assert all(r['pcs_'+s]==0 for s in v['empty_slots'])
  bycm[r['task_id'],r['method']].append(r)
 cms=[]
 for (task,method),rr in bycm.items():
  assert len(rr)==3 and {r['model_key'] for r in rr}=={'glm','doubao','qwen'}
  cms.append({'task_id':task,'project_id':targets[task]['project_id'],'method':method,'pcs_mean':mean(r['pcs_mean'] for r in rr),'prc_mean':mean(r['prc'] for r in rr) if all(r['prc'] is not None for r in rr) else None,**{'pcs_'+s:mean(r['pcs_'+s] for r in rr) for s in SLOTS}})
 sourcecm={(r['task_id'],r['method']):r for r in exp['case_method_means']}
 for r in cms:
  for key in ('pcs_mean','prc_mean'):
   old=sourcecm[r['task_id'],r['method']][key]
   assert (old is None and r[key] is None) or (old is not None and r[key] is not None and math.isclose(old,r[key],abs_tol=1e-12))
 common={t for t in targets if all(sourcecm[t,m]['prc_mean'] is not None for m in methods)}
 method=summary_rows(cms,['method'],['pcs_mean','prc_mean'])
 common_rows=summary_rows([r for r in cms if r['task_id'] in common],['method'],['pcs_mean','prc_mean'])
 bymodel=summary_rows(rows,['model_key','method'],['pcs_mean','prc'])
 byproject=summary_rows(cms,['project_id','method'],['pcs_mean','prc_mean'])
 slots=summary_rows(cms,['method'],['pcs_'+s for s in SLOTS])
 pair=[]
 for m in methods:
  if m=='AUCMR': continue
  for metric in ('pcs_mean','prc_mean'):
   pairs=[(sourcecm[t,'AUCMR'][metric],sourcecm[t,m][metric]) for t in targets if sourcecm[t,'AUCMR'][metric] is not None and sourcecm[t,m][metric] is not None]
   delta=[a-b for a,b in pairs]
   pair.append({'baseline':m,'metric':metric,'paired_cases':len(delta),'aucmr_mean':mean(a for a,b in pairs),'baseline_mean':mean(b for a,b in pairs),'mean_delta':mean(delta),'wins':sum(x>1e-10 for x in delta),'ties':sum(abs(x)<=1e-10 for x in delta),'losses':sum(x< -1e-10 for x in delta)})
 # Agreement on identical task-method units; descriptive, not independent subjects.
 agreement=[]
 for ma,mb in [('glm','doubao'),('glm','qwen'),('doubao','qwen')]:
  for metric in ('pcs_mean','prc'):
   pairs=[]
   for rr in bycm.values():
    r={x['model_key']:x for x in rr};a,b=r[ma][metric],r[mb][metric]
    if a is not None and b is not None:pairs.append((a,b))
   agreement.append({'model_a':ma,'model_b':mb,'metric':metric,'pairs':len(pairs),'mean_signed_a_minus_b':mean(a-b for a,b in pairs),'mean_absolute_difference':mean(abs(a-b) for a,b in pairs),'exact_match_fraction':mean(abs(a-b)<1e-10 for a,b in pairs)})
 access=jsonl('source_access.jsonl');toolreq=read('tool_requests.json');toolby=defaultdict(Counter)
 for t in toolreq: toolby[t['evaluation_id']][t['name']]+=1
 ac=[]
 for a in access:
  events=a['events'];initial=[x for x in events if x.get('operation')=='initial_retrieval'];idx=next((i for i,x in enumerate(events) if x.get('operation')=='initial_retrieval'),len(events))
  pages=[x for x in events if 'pdf_page' in x and 'delivered_text_chars' in x]
  later=[x for x in events[idx+1:] if 'pdf_page' in x and 'delivered_text_chars' in x]
  tc=toolby[a['evaluation_id']]
  ac.append({'evaluation_id':a['evaluation_id'],'task_id':a['task_id'],'model_key':a['model_key'],'project_id':targets[a['task_id']]['project_id'],'full_text_delivered_initially':all(x.get('full_text_delivered',False) for x in initial) and bool(initial),'initial_pages':len({(x['source_id'],p) for x in initial for p in x.get('pages',[])}),'initial_image_pages':len({(x['source_id'],p) for x in initial for p in x.get('image_pages',[])}),'unique_delivered_pages':len({(x['source_id'],x['pdf_page']) for x in pages}),'unique_image_pages':len({(x['source_id'],x['pdf_page']) for x in pages if x.get('image_attached')}),'additional_page_read_events':len(later),'delivered_text_chars_including_repeats':sum(x['delivered_text_chars'] for x in pages),'parsed_tool_calls':sum(tc.values()),**{k:tc[k] for k in ('source_search','source_page','source_section','case_outputs')}})
 positions=[{'method':m,**{chr(65+i):sum(e['method_order'][i]==m for e in alloc['evaluations']) for i in range(7)}} for m in methods]
 no_content=[{'task_id':v['task_id'],'project_id':targets[v['task_id']]['project_id'],'name':targets[v['task_id']]['name'],'method':v['method'],'input_path':v['path'],'input_sha256':v['sha256']} for v in inputs.values() if v['no_content']]
 summary={'cases':len(targets),'agents':len(alloc['agents']),'evaluations':len(prov),'method_records':len(rows),'rating_states':dict(Counter(r['status'] for r in rows)),'case_method_pairs':len(cms),'common_prc_cases':len(common),'projects':dict(Counter(t['project_id'] for t in targets.values())),'versions':dict(Counter(x['version'] for x in prov.values())),'agent_loads':dict(Counter(len(a['task_ids']) for a in alloc['agents'])),'source_access':{'initial_full_text_evaluations':sum(r['full_text_delivered_initially'] for r in ac),'initial_images_evaluations':sum(r['initial_image_pages']>0 for r in ac),'any_images_evaluations':sum(r['unique_image_pages']>0 for r in ac),'additional_tools_evaluations':sum(r['parsed_tool_calls']>0 for r in ac),'additional_page_read_evaluations':sum(r['additional_page_read_events']>0 for r in ac),'parsed_tool_calls':dict(Counter(t['name'] for t in toolreq))},'costs':read('cost_summary.json')}
 tables={'case_method_means.csv':cms,'method_summary.csv':method,'common_support_summary.csv':common_rows,'model_method_summary.csv':bymodel,'project_method_summary.csv':byproject,'slot_summary.csv':slots,'paired_comparison.csv':pair,'model_agreement.csv':agreement,'source_access_summary.csv':ac,'method_positions.csv':positions,'no_content.csv':no_content}
 # JSON object keys are strings, including the 6/7-case load histogram.
 return json.loads(json.dumps(summary)),tables
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--write',action='store_true');args=p.parse_args()
 summary,tables=calculate()
 if args.write:
  (D/'analysis_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
  for n,rows in tables.items():(D/n).write_text(csvtext(rows),encoding='utf-8-sig',newline='')
 else:
  assert summary==read('analysis_summary.json'),'SUMMARY_DIFFERS'
  for n,rows in tables.items():assert (D/n).read_text(encoding='utf-8-sig')==csvtext(rows).replace('\r\n','\n'),n
 print(json.dumps({'verification':'PASS','summary':summary},ensure_ascii=False))
