"""Provider availability only, with synthetic data and Junxi credentials."""
from pathlib import Path
import requests,os,json,datetime,concurrent.futures
ROOT=Path(__file__).resolve().parent
STAMP=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
BINDINGS={
 'qwen':{'provider':'Alibaba Model Studio','model':'qwen3.7-plus','base_url':'https://dashscope.aliyuncs.com/compatible-mode/v1','key_env':'AUCMR_JUNXI_DASHSCOPE_API_KEY','extra':{'enable_thinking':False}},
 'deepseek':{'provider':'DeepSeek','model':'deepseek-v4-flash','base_url':'https://api.deepseek.com','key_env':'AUCMR_JUNXI_DEEPSEEK_API_KEY','extra':{'thinking':{'type':'disabled'}}},
}
def one(name,m):
 p=ROOT/'preflight'/STAMP/name;p.mkdir(parents=True,exist_ok=True)
 payload={'model':m['model'],'messages':[{'role':'system','content':'Synthetic connectivity check. Return JSON only.'},{'role':'user','content':'Return {"status":"ok"}'}], 'max_tokens':100,'temperature':0,**m['extra']}
 (p/'request.json').write_text(json.dumps({'binding':m,'payload':payload},indent=2))
 key=os.environ[m['key_env']];record={'at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 try:
  resp=requests.post(m['base_url']+'/chat/completions',json=payload,headers={'Authorization':'Bearer '+key},timeout=60)
  (p/'response.txt').write_text(resp.text.replace(key,'[REDACTED]'))
  record['http_status']=resp.status_code
  if resp.status_code==200:
   b=resp.json();record.update({'returned_model':b.get('model'),'usage':b.get('usage'),'content':b['choices'][0]['message'].get('content'),'finish_reason':b['choices'][0].get('finish_reason')})
 except Exception as e:record['error']=str(e).replace(key,'[REDACTED]')
 (p/'status.json').write_text(json.dumps(record,indent=2));return {name:record}
if __name__=='__main__':
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as p:
  for x in p.map(lambda kv:one(*kv),BINDINGS.items()):print(json.dumps(x),flush=True)
