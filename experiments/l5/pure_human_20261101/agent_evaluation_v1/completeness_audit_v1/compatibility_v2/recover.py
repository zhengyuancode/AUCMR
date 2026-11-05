from pathlib import Path
import sys,json,copy,re,shutil
HERE=Path(__file__).resolve().parent
AUDIT=HERE.parent
sys.path.insert(0,str(AUDIT))
from audit import read,write,sha,now,canonical,verify,ROOT as ORIGINAL_ROOT
from locator import locate
from citations import resolve_lines

def resolve(entry,data):
 parts={p['source_part_id']:p for p in data['source']['requirements']}
 code=data['product']['artifact_code'];e=copy.deepcopy(entry);ident=e['part_id']
 product=ident==code
 assert ident in parts or product,'Unknown citation ID '+str(ident)
 raw=data['product']['text'] if product else parts[ident]['text']
 if 'line_start' in e:
  # Same exact line resolver; product page is administrative and removed below.
  part={'text':raw,'pdf_page':1} if product else parts[ident]
  out=resolve_lines(e,{ident:part})
  if product:out.pop('pdf_page',None)
  out['citation_domain']='product' if product else 'shared_source'
  return out
 quote=e.get('quote');assert isinstance(quote,str) and quote.strip(),'Missing literal quote and line anchors'
 fragments=re.split(r'(?:\.\.\.|…)',quote)
 if len(fragments)>1:
  segments=[];end=-1
  for frag in fragments:
   if not frag.strip():continue
   span=locate(raw,frag.strip());assert span['start']>=end,'Ellipsis segments must be ordered';end=span['end'];segments.append(span)
  assert len(segments)>=2,'Ellipsis requires at least two located fragments'
  return {**e,'citation_domain':'product' if product else 'shared_source','discontinuous':True,'verified_segments':segments}
 return {**e,'citation_domain':'product' if product else 'shared_source','verified_source_span':locate(raw,quote)}

def normalize(raw,data):
 out=copy.deepcopy(raw);assert out['artifact_code']==data['product']['artifact_code']
 expected={x['annotation_id'] for x in data['annotation_cases']}
 if not expected and out.get('decisions'):
  out['unsolicited_decisions']=out['decisions'];out['decisions']=[]
 rows=out['decisions'];assert len(rows)==len(expected) and {x['annotation_id'] for x in rows}==expected,'Decision IDs differ from requested set'
 def norm(row):
  assert row['reason'].strip()
  evidence=[resolve(e,data) for e in row['evidence']]
  source=[e for e in evidence if e['citation_domain']=='shared_source'];assert source,'Shared-source evidence is required'
  row['evidence']=source;row['product_evidence']=[e for e in evidence if e['citation_domain']=='product']
 for row in rows:
  assert row['disposition'] in ('DIAGNOSTIC','UNRESOLVED');norm(row)
 if out.get('paired_scope_concern') is not None:norm(out['paired_scope_concern'])
 # Unsolicited notes cannot alter any metric and are not presented as validated judgments.
 if out.get('unsolicited_decisions'):out['unsolicited_note']='Not requested, not used for annotation decisions or scoring; raw extra output retained.'
 return out

def check():
 verify()
 for p,h in read(HERE/'freeze.json')['files'].items():assert sha(HERE/p)==h,p

def prepare():
 assert not (HERE/'freeze.json').exists()
 for name in ('PROTOCOL.md','scan.json','aggregate.py','source_evidence_audit.json'):shutil.copyfile(AUDIT/name,HERE/name)
 write(HERE/'freeze.json',{'at':now(),'phase':'before_mechanical_recovery_and_reaggregation','upstream_freeze_sha256':sha(AUDIT/'freeze.json'),'upstream_review_freeze_sha256':sha(AUDIT/'review_freeze.json'),'files':{str(p.relative_to(HERE)):sha(p) for p in HERE.glob('*') if p.is_file()}})
 print('Compatibility rules frozen.')

def recover():
 check();jobs=[];history=[]
 for role,code in read(AUDIT/'config.json')['audit_queue']:
  data=read(AUDIT/f'bundles/{code}.json');dest=HERE/f'reviews/{role}/{code}.json';prior=AUDIT/f'reviews/{role}/{code}.json'
  if prior.exists():
   dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(prior,dest);jobs.append({'role':role,'code':code,'status':'COMPLETED','provenance':'UNCHANGED_SUCCESSFUL_REVIEW'});continue
  success=False
  for f in sorted((AUDIT/f'calls/{role}/{code}').glob('attempt-*/content.txt')):
   try:
    raw=read(f);out=normalize(raw,data);request=read(f.parent/'request.json');status=read(f.parent/'status.json')
    assert status['http_status']==200 and status['finish_reason']=='stop'
    out['_agent']={'role':role,'provider':request['binding']['provider'],'requested_model':request['binding']['model'],'returned_model':status['returned_model'],'at':status['finished_at'],'provenance':'RECOVERED_ACTUAL_RESPONSE_WITH_MECHANICAL_CITATION_COMPATIBILITY','original_content':str(f.relative_to(AUDIT)),'original_content_sha256':sha(f),'original_request_sha256':sha(f.parent/'request.json')}
    write(dest,out);jobs.append({'role':role,'code':code,'status':'COMPLETED','provenance':'FIRST_CHRONOLOGICAL_VALID_ACTUAL_RESPONSE','content':str(f.relative_to(AUDIT))});success=True;break
   except Exception as exc:history.append({'role':role,'code':code,'attempt':f.parent.name,'error':str(exc)})
  if not success:jobs.append({'role':role,'code':code,'status':'FAILED'})
 write(HERE/'recovery_history.json',history);write(HERE/'review_completion.json',{'at':now(),'jobs':jobs,'failed':sum(x['status']=='FAILED' for x in jobs),'new_calls':0,'historical_failed_validation_attempts_retained':True})
 write(HERE/'review_freeze.json',{'at':now(),'files':{str(p.relative_to(HERE)):sha(p) for p in (HERE/'reviews').rglob('*.json')}})
 print(json.dumps(read(HERE/'review_completion.json')))

def aggregate():
 check()
 import aggregate as implementation
 implementation.ROOT=HERE
 implementation.main()
 check()

if __name__=='__main__':
 if '--prepare' in sys.argv:prepare()
 elif '--aggregate' in sys.argv:aggregate()
 else:recover()
