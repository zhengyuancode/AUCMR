"""User-authorized credential-only recovery after all initial requests were rejected."""
from pathlib import Path
import sys,os,json,hashlib,concurrent.futures,traceback
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R));sys.path.insert(0,str(R/'tools'))
import run as original
from runtime.transport import LoggedProvider,utcnow
from runtime.pipeline import execute,render_result
from runtime.inputs import load_case
V1=R/'runs/formal-v1';R2=R/'runs/formal-v2-credential-recovery'
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def check():
 original.verify_freeze(V1)
 m=json.loads((R2/'freeze.json').read_text())
 for f,h in m['files'].items():
  if digest(R/f)!=h:raise RuntimeError('Recovery frozen file changed: '+f)
 if digest(R2/'config.snapshot.json')!=m['config_sha256']:raise RuntimeError('Recovery configuration changed')
def one(case,config):
 check();out=R2/case;out.mkdir(exist_ok=False)
 write(out/'status.json',{'case':case,'status':'RUNNING','started_at':utcnow()});provider=LoggedProvider(R,out,config)
 try:
  result=execute(load_case(R,case),provider,config,out,R);(out/'A.txt').write_text(render_result(result))
  status={'case':case,'status':'COMPLETED','completed_at':utcnow(),'request_count':provider.counter,'output_sha256':digest(out/'A.txt'),'result_sha256':digest(out/'result.json'),'quality_flags':result['quality_flags']}
 except Exception as e:
  (out/'traceback.txt').write_text(traceback.format_exc());status={'case':case,'status':'FAILED','finished_at':utcnow(),'request_count':provider.counter,'error_type':type(e).__name__,'error':str(e)}
 write(out/'status.json',status);print(json.dumps({k:status.get(k) for k in ['case','status','request_count','error_type']}),flush=True);return status
if __name__=='__main__':
 original.verify_freeze(V1)
 old=json.loads((V1/'summary.json').read_text())
 assert len(old['cases'])==29 and all(x['status']=='FAILED' and x['request_count']==1 for x in old['cases'])
 for x in old['cases']:
  st=json.loads((V1/x['case']/'requests/0001/status.json').read_text());assert st['http_status']==402
  assert not (V1/x['case']/'A.txt').exists()
 key=os.environ.get('AUCMR_JUNXI_DEEPSEEK_API_KEY')
 if not key:raise SystemExit('Authorized Junxi project credential missing')
 os.environ['AUCMR_L5_RUN_KEY']=key
 config=json.loads((V1/'config.snapshot.json').read_text());config['model']['credential_source']='existing_project_env_AUCMR_JUNXI_DEEPSEEK_API_KEY'
 R2.mkdir(exist_ok=False);write(R2/'config.snapshot.json',config)
 files=dict(json.loads((V1/'freeze.json').read_text())['files']);files[str(Path(__file__).resolve().relative_to(R))]=digest(Path(__file__))
 freeze={'frozen_at':utcnow(),'files':files,'config_sha256':digest(R2/'config.snapshot.json'),'parent_freeze_sha256':digest(V1/'freeze.json'),'reason':'all original first requests rejected HTTP402 insufficient balance; zero model content','only_change':'user-authorized Junxi credential source; model/base_url/payload/prompts/input/B/selection unchanged','authorization':'User: 展示所有的key，都使用带HuJunxi的','quality_based_selection':False,'all_29_targets_retained':True,'real_content_seen_before_recovery':False}
 write(R2/'freeze.json',freeze)
 first=one(config['cases'][0],config);statuses=[first];write(R2/'progress.json',statuses)
 calls=[json.loads(p.read_text()) for p in (R2/config['cases'][0]/'requests').glob('*/status.json')]
 if not any(x.get('http_status')==200 for x in calls):
  for c in config['cases'][1:]:statuses.append({'case':c,'status':'NOT_STARTED','reason':'provider availability gate failed'})
 else:
  with concurrent.futures.ThreadPoolExecutor(max_workers=config['workers']) as pool:
   futures=[pool.submit(one,c,config) for c in config['cases'][1:]]
   for f in concurrent.futures.as_completed(futures):statuses.append(f.result());write(R2/'progress.json',statuses)
 check();write(R2/'summary.json',{'status':'COMPLETED' if all(x['status']=='COMPLETED' for x in statuses) else 'INCOMPLETE','cases':statuses,'finished_at':utcnow()})
