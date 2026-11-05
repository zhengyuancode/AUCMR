"""Live API + schema check using only a synthetic example, never formal L5 data."""
from pathlib import Path
import json,concurrent.futures,datetime
from transport import Client,write
from contracts import validate_score
from tests_contracts import SOURCE,REF,PRODUCT
R=Path(__file__).resolve().parent
if __name__=='__main__':
 target=R/'validation/live_synthetic'/datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ');target.mkdir(parents=True)
 (target/'metric_rules.txt').write_text((R/'metric_rules.txt').read_text())
 cfg=json.loads((R/'config.json').read_text());prompts=json.loads((R/'prompts.json').read_text());client=Client(target,cfg,prompts)
 data={'source':SOURCE,'reference':REF,'product':{'artifact_code':'P_SYNTHETIC','task_id':'T_SYN','text':PRODUCT}}
 def one(role):
  try:
   record=client.call(role,'P_SYNTHETIC',data,lambda x:validate_score(x,SOURCE,REF,PRODUCT),target/'records'/role/'P_SYNTHETIC.json')
   return {'role':role,'passed':True,'returned_model':record['_agent']['returned_model']}
  except Exception as exc:return {'role':role,'passed':False,'error':str(exc)}
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(one,['score_primary','score_secondary']))
 write(target/'summary.json',{'synthetic_only':True,'formal_judgments_created':0,'results':results});print(json.dumps({'path':str(target),'results':results}),flush=True)
