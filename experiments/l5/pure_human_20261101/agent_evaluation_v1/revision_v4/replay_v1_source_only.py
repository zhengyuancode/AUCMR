"""Pre-freeze regression replay of OLD source-only responses. Not v2 formal records."""
from pathlib import Path
import json,collections,datetime
from runner import reference_validator,review_validator,final_reference_validator
from transport import write
R=Path(__file__).resolve().parent;OLD=R.parent
if __name__=='__main__':
 rows=[]
 for p in sorted((OLD/'calls').glob('reference*/*/attempt-*/status.json')):
  role=p.parts[-4];status=json.loads(p.read_text());result={'path':str(p.relative_to(OLD)),'role':role,'v1_status':status['status']}
  try:
   request=json.loads((p.parent/'request.json').read_text());data=json.loads(request['payload']['messages'][1]['content']);source=data['source']
   if (p.parent/'content.txt').exists():text=(p.parent/'content.txt').read_text().strip()
   else:
    b=json.loads((p.parent/'response.txt').read_text());text=b['choices'][0]['message']['content'].strip()
   if status.get('finish_reason')!='stop':raise ValueError('Original response incomplete; not repaired by replay')
   if text.startswith('```'):text=text.split('\n',1)[1].rsplit('```',1)[0].strip()
   response=json.loads(text)
   if role=='reference_review':out=review_validator(response,source,data['builder_draft'],data['independent_draft'])
   elif role=='reference_adjudicator':out=final_reference_validator(response,source,[data['builder_draft'],data['independent_draft'],data['blind_draft'],{'obligations':data['review'].get('omissions',[])}])
   else:out=reference_validator(response,source)
   result['v2_validation']='PASSED'
  except Exception as e:result.update({'v2_validation':'FAILED','error':type(e).__name__+': '+str(e)})
  rows.append(result)
 summary={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source_only':True,'formal_v2_records_created':0,'old_responses_not_reused_for_formal_v2':True,'counts':dict(collections.Counter(x['v2_validation'] for x in rows)),'old_failed_now_passed':sum(x['v1_status']=='TECHNICAL_FAILURE' and x['v2_validation']=='PASSED' for x in rows),'remaining_errors':dict(collections.Counter(x.get('error','').split(':')[0] for x in rows if x['v2_validation']=='FAILED')),'items':rows}
 write(R/'validation/v1_source_regression.json',summary);print(json.dumps({k:v for k,v in summary.items() if k!='items'},ensure_ascii=False))
