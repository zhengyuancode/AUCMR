"""No-tool model endpoint. Only validated in-memory JSON is sent; never exposes files."""
from pathlib import Path
import json,os,time,datetime,hashlib,requests,threading,sys
if sys.flags.optimize:raise RuntimeError('Optimized Python disables validation; use python -B without -O')
LOCK=threading.Lock()
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def write(p,x):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
 tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n');os.replace(tmp,p)
def event(root,x):
 with LOCK:
  with (root/'events.jsonl').open('a') as f:f.write(json.dumps({'at':now(),**x},ensure_ascii=False)+'\n')
class TechnicalFailure(RuntimeError):pass
class Client:
 def __init__(self,root,config,prompts):self.root=root;self.config=config;self.prompts=prompts
 def call(self,role,unit,data,validator,record_path):
  from isolation import guard_payload
  from inheritance import info,parent_root
  inherited,parent_failure=info(self.root,role,unit,data)
  original_data=data
  if 'source' in data:
   from citations import catalog
   data={**data,'citation_catalog':catalog(data['source'])}
  guard_payload(role,data)
  model=self.config['models'][self.config['bindings'][role]]
  system=self.prompts['common_rules']+'\n'+(self.root/'metric_rules.txt').read_text()+'\n'+self.prompts[role]
  request={'model':model['model'],'messages':[{'role':'system','content':system},{'role':'user','content':canonical(data)}], 'temperature':0,'max_tokens':self.config['max_tokens'],'response_format':{'type':'json_object'},'stream':False,**model['extra']}
  fingerprint=hashlib.sha256(canonical(request).encode()).hexdigest()
  directory=self.root/'calls'/role/unit;directory.mkdir(parents=True,exist_ok=True)
  job=directory/'job.json'
  if job.exists():
   old=json.loads(job.read_text());assert old['request_sha256']==fingerprint,'Successful input cannot be replaced'
   if old['status']=='COMPLETED':
    out=json.loads(record_path.read_text());assert sha(record_path)==old['record_sha256'];return out
  else:write(job,{'status':'QUEUED','role':role,'unit':unit,'request_sha256':fingerprint,'started_at':now()})
  for completed in sorted(directory.glob('attempt-*/validated.json')):
   recovered=json.loads(completed.read_text());assert recovered['_agent']['request_sha256']==fingerprint
   validator(recovered);write(record_path,recovered)
   write(job,{'status':'COMPLETED','role':role,'unit':unit,'request_sha256':fingerprint,'record_sha256':sha(record_path),'record':str(record_path.relative_to(self.root)),'recovered_from_validated':str(completed.relative_to(self.root)),'finished_at':now()})
   event(self.root,{'role':role,'unit':unit,'status':'RECOVERED_VALIDATED'});return recovered
  if inherited:
   parent=parent_root(self.root)
   assert inherited['binding']==model,'Inherited model binding differs'
   assert sha(parent/inherited['parent_record'])==inherited['record_sha256']
   assert sha(record_path)==inherited['record_sha256']
   record=json.loads(record_path.read_text())
   validator(json.loads(canonical(record)))  # Validate a copy; preserve original labels, spans and identity.
   write(job,{'status':'COMPLETED','role':role,'unit':unit,'request_sha256':fingerprint,'record_sha256':sha(record_path),'record':str(record_path.relative_to(self.root)),'execution':'INHERITED_PRIOR_NO_NEW_MODEL_CALL','parent_evidence':inherited,'validated_at':now()})
   event(self.root,{'role':role,'unit':unit,'status':'INHERITED_VALIDATED'});return record
  key=os.environ.get(model['key_env'])
  if not key:raise TechnicalFailure('Required authorized credential missing: '+model['key_env'])
  # A crashed/incomplete attempt remains on disk and counts toward the fixed cap.
  existing=sorted(directory.glob('attempt-*'));last_error=None;last_content=None
  if existing:
   st=existing[-1]/'status.json'
   if st.exists():last_error=json.loads(st.read_text()).get('error')
   previous=existing[-1]/'content.txt'
   if previous.exists():last_content=previous.read_text()
  if not existing and parent_failure:
   parent=parent_root(self.root);prior=parent/parent_failure['parent_content']
   assert sha(prior)==parent_failure['content_sha256']
   last_content=prior.read_text();last_error=parent_failure['error']
  sweep=self.config.get('repair_sweep',0)
  used_in_sweep=sum(json.loads((p/'status.json').read_text()).get('repair_sweep',0)==sweep for p in existing if (p/'status.json').exists())
  attempt_ceiling=min(len(existing)+max(0,self.config.get('phase_attempt_limits',[self.config.get('max_attempts',4)]*3)[sweep]-used_in_sweep),self.config.get('max_total_attempts',9))
  for attempt in range(len(existing)+1,attempt_ceiling+1):
   p=directory/f'attempt-{attempt:02d}';p.mkdir(exist_ok=False)
   current=json.loads(canonical(request))
   if last_error:
    if last_content:current['messages'].append({'role':'assistant','content':last_content})
    current['messages'].append({'role':'user','content':'Repair the supplied previous response for the specific mechanical/schema/citation error below. Return its complete corrected JSON. Preserve all other substantive judgments; do not restart the assessment, seek a preferred score, or invent evidence. Missing substantive items must be examined using only the original source. If a quotation was reconstructed across a table or source parts, replace it with one or more SOURCE LINE CITATIONS {part_id,line_start,line_end} using citation_catalog. The executor extracts exact raw text from those lines. Never put a paraphrase into the quote field or guess missing evidence. Preserve each source segment separately. Technical error: '+last_error[:2500]})
   write(p/'request.json',{'role':role,'binding':model,'payload':current,'input_sha256':fingerprint,'tools_provided':False,'filesystem_access':False,'read_scope':list(data),'created_at':now(),'repair_sweep':sweep,'attempt_ceiling':attempt_ceiling})
   status={'status':'STARTED','attempt':attempt,'repair_sweep':sweep,'at':now()};write(p/'status.json',status)
   event(self.root,{'role':role,'unit':unit,'attempt':attempt,'status':'STARTED'})
   start=time.monotonic()
   try:
    response=requests.post(model['base_url'].rstrip('/')+'/chat/completions',json=current,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},timeout=self.config['timeout_seconds'])
    raw=response.text.replace(key,'[REDACTED]');(p/'response.txt').write_text(raw);status['http_status']=response.status_code
    if response.status_code!=200:raise TechnicalFailure('HTTP '+str(response.status_code))
    body=json.loads(raw);choice=body['choices'][0];status.update({'returned_model':body.get('model'),'response_id':body.get('id'),'usage':body.get('usage'),'finish_reason':choice.get('finish_reason')})
    content=choice['message'].get('content')
    if isinstance(content,str):(p/'content.txt').write_text(content);last_content=content
    if choice.get('finish_reason')!='stop':raise TechnicalFailure('Incomplete finish: '+str(choice.get('finish_reason')))
    assert isinstance(content,str) and content.strip(),'Missing message content'
    # Only strip an enclosing Markdown JSON fence. Never repair semantic content.
    clean=content.strip()
    if clean.startswith('```') and clean.endswith('```'):clean=clean.split('\n',1)[1].rsplit('```',1)[0].strip()
    result=validator(json.loads(clean));result['_agent']={'role':role,'provider':model['provider'],'requested_model':model['model'],'returned_model':body.get('model'),'request_sha256':fingerprint,'attempt':attempt,'at':now(),'provenance':'agent_generated'}
    write(p/'validated.json',result);write(record_path,result);status['status']='COMPLETED';write(p/'status.json',{**status,'elapsed_seconds':time.monotonic()-start,'finished_at':now()})
    write(job,{'status':'COMPLETED','role':role,'unit':unit,'request_sha256':fingerprint,'record_sha256':sha(record_path),'record':str(record_path.relative_to(self.root)),'finished_at':now()})
    event(self.root,{'role':role,'unit':unit,'status':'COMPLETED','attempt':attempt});return result
   except Exception as e:
    last_error=(type(e).__name__+': '+str(e)).replace(key,'[REDACTED]');status.update({'status':'TECHNICAL_FAILURE','error':last_error,'elapsed_seconds':time.monotonic()-start,'finished_at':now()});write(p/'status.json',status)
    event(self.root,{'role':role,'unit':unit,'attempt':attempt,'status':'TECHNICAL_FAILURE','error':last_error})
    # 401/402/403 are authorization/account failures, not quality. No account fallback.
    if status.get('http_status') in [401,402,403]:break
    if attempt<attempt_ceiling:time.sleep(self.config['retry_backoff_seconds'])
  write(job,{'status':'FAILED','role':role,'unit':unit,'request_sha256':fingerprint,'error':last_error or 'Attempt budget exhausted after interruption','finished_at':now()})
  raise TechnicalFailure(f'{role}/{unit}: '+(last_error or 'attempt budget exhausted'))
