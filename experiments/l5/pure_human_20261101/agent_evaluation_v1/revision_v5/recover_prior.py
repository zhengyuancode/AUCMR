"""Recover the first chronological admissible prior raw response. Never selects by score."""
from pathlib import Path
import json,copy,os,hashlib
from contexts import context,validate
from inheritance import data_sha
from transport import sha,write,now
ROOT=Path(__file__).resolve().parent

def recover(root=ROOT):
 manifest=json.loads((root/'inheritance_manifest.json').read_text());parent=(root/manifest['parent']).resolve();recovered=[];unrecovered=[]
 # References first so their paired products, if absent in the parent, can be newly evaluated.
 keys=sorted(manifest['failed'],key=lambda k:(not k.startswith('reference'),k))
 for key in keys:
  if key in manifest['successful']:continue
  role,unit=key.split('/');fail=manifest['failed'][key];errors=[]
  try:data=context(root,role,unit)
  except Exception as e:unrecovered.append({'key':key,'reason':'dependency unavailable','error':str(e)});continue
  if data_sha(data)!=fail['data_sha256']:unrecovered.append({'key':key,'reason':'context changed; requires fresh call'});continue
  for folder in sorted((parent/'calls'/role/unit).glob('attempt-*')):
   try:
    st=json.loads((folder/'status.json').read_text())
    if st.get('http_status')!=200 or st.get('finish_reason')!='stop':continue
    req=json.loads((folder/'request.json').read_text());candidate_data=json.loads(req['payload']['messages'][1]['content']);candidate_data.pop('citation_catalog',None);candidate_data.pop('product_catalog',None)
    assert data_sha(candidate_data)==data_sha(data),'Candidate attempt used a different evaluation context'
    content=folder/'content.txt';raw=content.read_text().strip()
    if raw.startswith('```'):raw=raw.split('\n',1)[1].rsplit('```',1)[0].strip()
    parsed=json.loads(raw);out=validate(role,copy.deepcopy(parsed),data,root,unit)
    req=json.loads((folder/'request.json').read_text());binding=req['binding'];modelkey=json.loads((root/'config.json').read_text())['bindings'][role]
    assert binding==json.loads((root/'config.json').read_text())['models'][modelkey]
    if role=='reference_adjudicator':dest=root/'references'/f'{unit}.json'
    else:dest=root/'records'/('score_stability' if unit.startswith('stability-') else 'score_adjudicated' if role=='score_adjudicator' else role)/f"{unit.removeprefix('stability-')}.json"
    assert not dest.exists(),'No successful first judgment may be overwritten'
    out['_agent']={'role':role,'provider':binding['provider'],'requested_model':binding['model'],'returned_model':st.get('returned_model'),'request_sha256':req['input_sha256'],'attempt':st['attempt'],'at':st.get('finished_at',st['at']),'provenance':'actual_agent_response_recovered_by_v5_administrative_validation','raw_content_sha256':sha(content)}
    entry={'recovered_response':True,'record_path':str(dest.relative_to(root)),'record_sha256':hashlib.sha256((json.dumps(out,ensure_ascii=False,indent=2)+'\n').encode()).hexdigest(),'parent_content':os.path.relpath(content,parent),'parent_content_sha256':sha(content),'parent_request':os.path.relpath(folder/'request.json',parent),'parent_request_sha256':sha(folder/'request.json'),'parent_request_fingerprint':req['input_sha256'],'data_sha256':data_sha(data),'binding':binding,'provenance':'ACTUAL_PRIOR_RAW_RESPONSE_NORMALIZED_NO_NEW_MODEL_CALL','normalization_selection':'first chronological admissible response, not scores'}
    write(dest,out)
    manifest['successful'][key]=entry
    for p in [content,folder/'request.json',folder/'status.json',folder/'response.txt']:
     if p.exists():manifest['parent_files'][os.path.relpath(p,parent)]=sha(p)
    row={'key':key,'parent_attempt':st['attempt'],'new_record':str(dest.relative_to(root)),'new_record_sha256':sha(dest),'normalizations':out.get('administrative_normalizations',[]),'alignment_admin_unreviewed_units':out.get('alignment_unreviewed_units',0)}
    if role=='reference_adjudicator':row.update({'confirmed_before':sum(o['status']=='CONFIRMED' for o in parsed['obligations']),'confirmed_after':sum(o['status']=='CONFIRMED' for o in out['obligations']),'unresolved_after':sum(o['status']=='UNRESOLVED' for o in out['obligations'])})
    recovered.append(row);break
   except Exception as e:errors.append({'attempt':folder.name,'error':type(e).__name__+': '+str(e)})
  else:unrecovered.append({'key':key,'reason':'still requires actual Agent repair','attempt_errors':errors})
 write(root/'inheritance_manifest.json',manifest)
 report={'at':now(),'new_model_calls':0,'selection':'first chronological admissible response per failed role; no numerical score selection','recovered_count':len(recovered),'recovered':recovered,'remaining':unrecovered}
 write(root/'validation/prior_response_recovery.json',report);return report
if __name__=='__main__':
 assert not (ROOT/'freeze.json').exists()
 r=recover();print(json.dumps({'recovered':r['recovered_count'],'remaining_roles':len(r['remaining']),'new_api_calls':0}))
