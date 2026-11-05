"""Audited continuation of successful v2 records, without claiming new model calls."""
from pathlib import Path
import json,hashlib,shutil,os
from transport import canonical,sha,write,now

def data_sha(data):return hashlib.sha256(canonical(data).encode()).hexdigest()
def prepare(root):
 parent=root.parent/'revision_v4';manifest={'parent':'../revision_v4','prepared_at':now(),'parent_files':{},'successful':{},'failed':{},'prior_scoring_exists':True}
 oldfreeze=json.loads((parent/'freeze.json').read_text())
 for name,h in oldfreeze['files'].items():assert sha(parent/name)==h,'Parent frozen input changed: '+name
 manifest['prior_score_counts']={role:len(list((parent/'records'/role).glob('*.json'))) for role in ['score_primary','score_secondary','score_adjudicated']}
 localcfg=json.loads((root/'config.json').read_text());oldcfg=json.loads((parent/'config.json').read_text())
 assert localcfg['models']==oldcfg['models'] and localcfg['bindings']==oldcfg['bindings'],'Cannot silently change inherited model bindings'
 for name in ['freeze.json','config.json','state.json']:
  manifest['parent_files'][name]=sha(parent/name)
 for jobpath in sorted((parent/'calls').glob('*/*/job.json')):
  job=json.loads(jobpath.read_text());role=job['role'];unit=job['unit'];key=role+'/'+unit
  attempts=sorted(jobpath.parent.glob('attempt-*'))
  if job['status']=='COMPLETED':
   record=parent/job['record'];assert sha(record)==job['record_sha256']
   agent=json.loads(record.read_text())['_agent']
   if job.get('execution','').startswith('INHERITED'):
    pm=json.loads((parent/'inheritance_manifest.json').read_text());origin=(parent/pm['parent']).resolve();pe=pm['successful'][key];request=origin/pe['parent_request']
   else:request=jobpath.parent/f"attempt-{agent['attempt']:02d}"/'request.json'
   req=json.loads(request.read_text());data=json.loads(req['payload']['messages'][1]['content']);data.pop('citation_catalog',None);data.pop('product_catalog',None)
   target=root/job['record'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(record,target)
   info={'parent_record':str(record.relative_to(parent)),'record_path':job['record'],'record_sha256':sha(record),'parent_job':str(jobpath.relative_to(parent)),'parent_job_sha256':sha(jobpath),'parent_request':os.path.relpath(request,parent),'parent_request_sha256':sha(request),'parent_request_fingerprint':job['request_sha256'],'data_sha256':data_sha(data),'binding':req['binding'],'provenance':'INHERITED_REAL_PRIOR_RESPONSE_NOT_NEW_V5_MODEL_CALL'}
   manifest['successful'][key]=info
   for p in [record,jobpath,request,request.parent/'response.txt',request.parent/'status.json']:
    if p.exists():manifest['parent_files'][os.path.relpath(p,parent)]=sha(p)
  elif job['status']=='FAILED' and attempts:
   attempt=attempts[-1];request=attempt/'request.json';content=attempt/'content.txt'
   if not content.exists():continue
   req=json.loads(request.read_text());data=json.loads(req['payload']['messages'][1]['content']);data.pop('citation_catalog',None);data.pop('product_catalog',None)
   manifest['failed'][key]={'data_sha256':data_sha(data),'parent_content':str(content.relative_to(parent)),'content_sha256':sha(content),'error':job['error'],'parent_attempts':len(attempts)}
   for p in [jobpath,request,content]:manifest['parent_files'][os.path.relpath(p,parent)]=sha(p)
 for plan in (parent/'review_plans').glob('*.json'):
  dest=root/'review_plans'/plan.name;dest.parent.mkdir(exist_ok=True);shutil.copyfile(plan,dest);manifest['parent_files'][os.path.relpath(plan,parent)]=sha(plan)
 write(root/'inheritance_manifest.json',manifest)
 return manifest

def info(root,role,unit,data):
 path=root/'inheritance_manifest.json'
 if not path.exists():return None,None
 manifest=json.loads(path.read_text());key=role+'/'+unit
 success=manifest['successful'].get(key);failure=manifest['failed'].get(key)
 if success and success['data_sha256']!=data_sha(data):raise ValueError('Inherited record input changed; cannot overwrite frozen source response: '+key)
 if failure and failure['data_sha256']!=data_sha(data):failure=None
 return success,failure

def verify_parent(root):
 path=root/'inheritance_manifest.json'
 if not path.exists():return
 manifest=json.loads(path.read_text());parent=(root/manifest['parent']).resolve()
 for name,h in manifest['parent_files'].items():assert sha(parent/name)==h,'Inherited parent evidence changed: '+name

if __name__=='__main__':
 r=Path(__file__).resolve().parent
 assert not (r/'freeze.json').exists(),'Cannot alter a frozen continuation'
 m=prepare(r);print(json.dumps({'inherited_successful_roles':len(m['successful']),'parent_failed_roles':len(m['failed']),'inherited_final_references':sum(k.startswith('reference_adjudicator/') for k in m['successful'])}))

def parent_root(root):
 return (root/json.loads((root/'inheritance_manifest.json').read_text())['parent']).resolve()
