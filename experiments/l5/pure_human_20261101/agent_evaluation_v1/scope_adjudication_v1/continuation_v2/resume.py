from pathlib import Path
import sys,copy,shutil,json,os,subprocess,datetime,fcntl
HERE=Path(__file__).resolve().parent;BASE=HERE.parent
sys.path.insert(0,str(BASE))
import run
from transport import write,sha,now
read=run.read
ORIGINAL_VALIDATOR=run.validate_scope

def validate(out,data):
 out=copy.deepcopy(out);expected={x['id'] for x in data['prior_reference']['obligations']}
 allowed={i for d in data['prior_reference'].get('decisions',[]) if d.get('decision')=='exclude' for i in d.get('candidate_ids',[])}
 extra=[x for x in out['obligations'] if x['id'] not in expected]
 if extra:
  assert len({x['id'] for x in extra})==len(extra),'Duplicate supplementary exclusion'
  for x in extra:
   assert x['id'] in allowed and x['status']=='EXCLUDED','Only explicitly excluded historical draft IDs may be supplementary'
   assert x.get('reason','').strip() and x.get('evidence'),'Supplementary exclusion requires reason and evidence'
   x['evidence']=run.citations(x['evidence'],data['source'])
  out['obligations']=[x for x in out['obligations'] if x['id'] in expected]
  out['supplementary_exclusions']=extra
  out['administrative_normalization']='Historically excluded draft IDs retained separately; no required obligation or substantive decision removed'
 return ORIGINAL_VALIDATOR(out,data)

def prepare():
 assert not (HERE/'freeze.json').exists()
 run.verify()
 for name in read(BASE/'freeze.json')['files']:
  dest=HERE/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(BASE/name,dest)
 for folder in ('calls','records'):shutil.copytree(BASE/folder,HERE/folder)
 recovered=[]
 for task in run.TASKS:
  role='scope_reader_one';data=run.source_payload(task)
  for f in sorted((BASE/f'calls/{role}/{task}').glob('attempt-*/content.txt')):
   try:out=validate(read(f),data)
   except Exception:continue
   req=read(f.parent/'request.json');status=read(f.parent/'status.json');job=read(f.parent.parent/'job.json')
   assert status['http_status']==200 and status['finish_reason']=='stop'
   out['_agent']={'role':role,'provider':req['binding']['provider'],'requested_model':req['binding']['model'],'returned_model':status['returned_model'],'request_sha256':job['request_sha256'],'attempt':status['attempt'],'at':status['finished_at'],'provenance':'RECOVERED_FIRST_ACTUAL_RESPONSE_EXCLUDED_DRAFT_IDS_SEPARATED','original_content_sha256':sha(f),'original_content_path':str(f)}
   dest=HERE/f'records/{role}/{task}.json';write(dest,out)
   oldjob=HERE/f'calls/{role}/{task}/job.json';write(oldjob.with_name('prior_failed_job.json'),read(oldjob))
   write(oldjob,{'status':'COMPLETED','role':role,'unit':task,'request_sha256':job['request_sha256'],'record_sha256':sha(dest),'record':str(dest.relative_to(HERE)),'execution':'RECOVERED_EXISTING_MODEL_RESPONSE_NO_NEW_CALL','finished_at':now()})
   recovered.append({'role':role,'task':task,'original_content':str(f),'original_content_sha256':sha(f),'supplementary_ids':[x['id'] for x in out.get('supplementary_exclusions',[])]});break
  else:raise RuntimeError('No recoverable response for '+task)
 write(HERE/'recovery.json',{'at':now(),'items':recovered,'semantic_decisions_unchanged':True,'new_model_calls':0})
 # Shared baseline files remain frozen; new generated records are bound before continuation.
 oldfreeze=read(BASE/'freeze.json')
 files={str(p.relative_to(HERE)):sha(p) for p in HERE.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name!='freeze.json'}
 write(HERE/'freeze.json',{'at':now(),'files':files,'shared_dependencies':oldfreeze['shared_dependencies'],'parent_freeze_sha256':sha(BASE/'freeze.json'),'continuation_after_administrative_recovery':True})
 print(json.dumps({'recovered':recovered,'new_calls':0}))

def execute():
 # Validate historical parent separately, then execute frozen continuation.
 run.verify();run.ROOT=HERE;run.validate_scope=validate;run.execute()

def start():
 run.verify();saved=run.ROOT;run.ROOT=HERE;run.verify();run.ROOT=saved
 lock=(HERE/'run.lock').open('a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise SystemExit('Continuation already running')
 fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
 stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ');log=HERE/f'console-{stamp}.log'
 with log.open('ab') as out:
  p=subprocess.Popen([sys.executable,'-B',str(HERE/'resume.py')],cwd=HERE,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
 write(HERE/'process.json',{'pid':p.pid,'at':now(),'log':log.name});print('Started continuation',p.pid,'log',log.name)

if __name__=='__main__':
 if '--prepare' in sys.argv:prepare()
 elif '--start' in sys.argv:start()
 else:execute()
