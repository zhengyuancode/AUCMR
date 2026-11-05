"""Read existing L4 evidence into this handoff directory. Never sends requests."""
from pathlib import Path
import sys, json, csv, hashlib, shutil, sqlite3
from collections import Counter
from decimal import Decimal

PACKAGE = Path(__file__).resolve().parents[1]
REPO = PACKAGE.parents[3]
sys.path.insert(0, str(REPO))
from eval.l4_agent.inputs import content
from eval.l4_agent.protocol import SLOTS, RUBRIC, implementation
from eval.l4_agent.contract import INSTRUCTIONS
from eval.l4_agent.mcp_tools import MODEL_TOOLS
import fitz
D = PACKAGE/'data'
D.mkdir(exist_ok=True)
FINAL = Path('outputs/evaluation_preparation/l4-final-v414-20261030')
def read(p): return json.loads((REPO/p).read_text())
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
 return h.hexdigest()
def portable(x):
 if isinstance(x,str): return x.replace(str(REPO)+'/', '')
 if isinstance(x,list): return [portable(v) for v in x]
 if isinstance(x,dict): return {k:portable(v) for k,v in x.items()}
 return x
def write(n,x): (D/n).write_text(json.dumps(portable(x),ensure_ascii=False,indent=2)+'\n')
def lines(n,x): (D/n).write_text(''.join(json.dumps(portable(v),ensure_ascii=False)+'\n' for v in x))
def table(n,rows):
 with (D/n).open('w',newline='',encoding='utf-8-sig') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
catalog=read('config/l4_panel/unified93-v41/catalog.json');allocation=read(FINAL/'allocation.json');export=read(FINAL/'combined-results/export.json');prov=read(FINAL/'combined-results/provenance.json');cfg=read(FINAL/'config.json')
write('catalog.json',catalog);write('allocation.json',allocation);write('export.json',export);write('provenance.json',prov)
shutil.copyfile(REPO/FINAL/'combined-results/ratings.csv',D/'ratings.csv')
write('protocol_snapshot.json',{'version':cfg['version'],'rubric':RUBRIC,'slots':SLOTS,'system_instruction':INSTRUCTIONS,'model_tools':MODEL_TOOLS})
write('implementation_manifest.json',{'captured_date':'2026-11-04','files':implementation(),'frozen_final_files':cfg['implementation'],'note':'Source is in repository; current hashes are not a substitute for each retained record snapshot.'})
write('execution_settings.json',{k:cfg[k] for k in ['version','models','tool_rounds','format_corrections','max_requests_per_evaluation','material_limits','source_backend','source_access_design','cost_policy','campaign_ledger']})
sources=[]
for s in catalog['sources']:
 assert sha(REPO/s['path'])==s['expected_sha256'], s['path']
 with fitz.open(REPO/s['path']) as pdf: pages=len(pdf)
 sources.append({**s,'pdf_pages':pages,'case_count':sum(t['project_id']==s['project_id'] for t in catalog['targets'])})
write('sources.json',sources);table('sources.csv',sources);table('cases.csv',catalog['targets'])
views=[];targets={t['task_id']:t for t in catalog['targets']}
for b in catalog['input_bindings']:
 assert sha(REPO/b['path'])==b['sha256']
 views.append({**b,**content(b,targets[b['task_id']])})
lines('evaluated_inputs.jsonl',views)
table('agents.csv',[{**{k:v for k,v in a.items() if k!='task_ids'},'case_count':len(a['task_ids']),'task_ids':'|'.join(a['task_ids'])} for a in allocation['agents']])
table('assignment.csv',[{**{k:v for k,v in e.items() if k!='method_order'},'project_id':targets[e['task_id']]['project_id'],**{chr(65+i):m for i,m in enumerate(e['method_order'])}} for e in allocation['evaluations']])
shutil.copyfile(REPO/'outputs/l4-panel-v4-20261029/L4-93cases-42agents.xlsx',D/'L4-93cases-42agents.xlsx')
records=[];access=[];responses=[];adopted=set();prompts={};runs=set();artifacts=[];initial_contracts={};tool_requests=[]
for eid,p in prov.items():
 rp=Path(p['path']); assert sha(REPO/rp)==p['sha256'],eid
 rec=read(rp);assert rec['status']=='COMPLETE'
 runs.add(str(rp.parent.parent))
 records.append({k:v for k,v in rec.items() if k not in ('source_access','sha256')})
 # Keep exact hashes of original sealed files; this is explicitly an extracted copy.
 audit=[]
 for x in rec['source_access']:
  y=dict(x)
  if 'text' in y:
   txt=y.pop('text');y['delivered_text_chars']=len(txt);y['delivered_text_sha256']=hashlib.sha256(txt.encode()).hexdigest()
  if 'search_hits' in y: y['search_hits']=[{k:v for k,v in hit.items() if k!='excerpt'} for hit in y['search_hits']]
  audit.append(y)
 access.append({'evaluation_id':eid,'task_id':rec['task_id'],'model_key':rec['model_key'],'events':audit})
 for att in rec['attempts']:
  a=read(att['path']);r=a.get('receipt') or {};rid=r.get('request_id',att.get('request_id'));adopted.add(rid)
  ep=r.get('evidence_path');ep=portable(ep) if ep else str(rp.parent.parent/'sessions'/eid/'runtime_requests'/f'{rid}.json')
  rr=read(ep);payload=rr.get('payload',{});messages=payload.get('messages',[])
  if messages:
   st=messages[0]['content'];h=hashlib.sha256(st.encode()).hexdigest();prompts.setdefault(h,{'text':st,'evaluations':set(),'versions':set()});prompts[h]['evaluations'].add(eid);prompts[h]['versions'].add(rec['version'])
  for m in messages[1:2]:
   cont=m.get('content');texts=[cont] if isinstance(cont,str) else [part.get('text','') for part in (cont or []) if part.get('type')=='text']
   for txt in texts:
    try: ip=json.loads(txt)
    except (ValueError,TypeError): continue
    if isinstance(ip,dict) and 'rating_contract' in ip:
     rc=ip['rating_contract'];key=hashlib.sha256(json.dumps(rc,sort_keys=True,ensure_ascii=False).encode()).hexdigest();initial_contracts.setdefault(key,{'rating_contract':rc,'evaluation_ids':set()});initial_contracts[key]['evaluation_ids'].add(eid)
  body=r.get('normalized_body') or r.get('body') or ''
  try:
   obj=json.loads(body)
   for call in obj.get('tool_calls',[]): tool_requests.append({'evaluation_id':eid,'request_id':rid,'round':att['round'],'name':call.get('name'),'arguments':call.get('arguments')})
  except (ValueError,AttributeError): pass
  # Retain only the final accepted scoring response, not abandoned/failed attempts.
  if att==rec['attempts'][-1]:
   responses.append({'evaluation_id':eid,'record_version':rec['version'],'runtime_path':ep,'runtime_sha256':sha(REPO/ep),'attempt_path':att['path'],'attempt_sha256':sha(REPO/att['path']),**{k:r.get(k) for k in ['request_id','body','finish_reason','response_model','provider_request_id','raw_usage','usage','cost_cny_decimal','cost_basis','estimate_source']}})
lines('final_records.jsonl',records);lines('source_access.jsonl',access);lines('accepted_responses.jsonl',responses);write('tool_requests.json',tool_requests)
write('observed_system_prompts.json',[{'sha256':h,'text':v['text'],'evaluations':sorted(v['evaluations']),'versions':sorted(v['versions'])} for h,v in prompts.items()])
write('observed_rating_contracts.json',[{'sha256':h,'rating_contract':v['rating_contract'],'evaluation_ids':sorted(v['evaluation_ids'])} for h,v in initial_contracts.items()])
# These run roots together are the original panel batch plus authorized recovery.
requests={};duplicate_paths=0
for run in sorted(runs):
 for f in sorted((REPO/run).glob('sessions/*/runtime_requests/*.json')):
  rr=json.loads(f.read_text());rid=rr['request_id'];r=rr.get('result') or {}
  if rid in requests:
   duplicate_paths+=1;continue
  requests[rid]={'request_id':rid,'evaluation_id':rr.get('run_id'),'path':str(f.relative_to(REPO)),'sha256':sha(f),'state':rr.get('state'),'model':rr.get('payload',{}).get('model'),'created_at':rr.get('created_at'),'received_at':rr.get('received_at'),'selected_final_session':rid in adopted,'cost_cny':r.get('cost_cny_decimal',r.get('cost_cny')),'cost_basis':r.get('cost_basis'),'usage':r.get('usage'),'raw_usage':r.get('raw_usage'),'finish_reason':r.get('finish_reason'),'response_model':r.get('response_model')}
ledger=sqlite3.connect(f'file:{REPO}/outputs/evaluation_campaign/budget.sqlite?mode=ro',uri=True)
for rid,row in requests.items():
 v=ledger.execute('select provider,reserved,actual,state,detail from reservations where id=?',(rid,)).fetchone();assert v is not None,rid
 row['ledger']={'provider':v[0],'reserved_internal_units':v[1],'actual_internal_units':v[2],'state':v[3]}
ledger.close()
lines('request_accounting.jsonl',list(requests.values()))
def costs(rows):
 return {'requests':len(rows),'known_estimated_cny':str(sum((Decimal(str(r['cost_cny'])) for r in rows if r['cost_cny'] is not None),Decimal(0))),'unknown_ended':sum(r['ledger']['state'] in ['UNPRICED','BILLING_UNKNOWN'] for r in rows),'pending':sum(r['ledger']['state'] in ['PENDING','RESERVED'] for r in rows),'states':dict(Counter(r['ledger']['state'] for r in rows))}
cs={'all_campaign':costs(list(requests.values())),'selected_final_sessions':costs([r for r in requests.values() if r['selected_final_session']]),'by_model':{m:costs([r for r in requests.values() if r['model']==m]) for m in sorted({r['model'] for r in requests.values()})},'deduplicated_saved_copies':duplicate_paths,'basis':'RECORDED_TOKEN_ESTIMATE_NOT_INVOICE','history_unknown_not_zero':True}
assert cs['all_campaign']['requests']==492,cs
assert Decimal(cs['all_campaign']['known_estimated_cny'])==Decimal('122.467136'),cs
write('cost_summary.json',cs)
for p in ['config/l4_panel/unified93-v41/catalog.json',str(FINAL/'allocation.json'),str(FINAL/'config.json'),str(FINAL/'combined-results/export.json'),str(FINAL/'combined-results/provenance.json'),'outputs/l4-panel-v4-20261029/L4-93cases-42agents.xlsx']:
 artifacts.append({'path':p,'sha256':sha(REPO/p),'bytes':(REPO/p).stat().st_size})
write('source_artifacts.json',artifacts)
print(json.dumps({'records':len(records),'inputs':len(views),'sources':sources,'costs':cs,'prompt_variants':len(prompts),'contract_variants':len(initial_contracts),'tool_requests':len(tool_requests)},ensure_ascii=False))
