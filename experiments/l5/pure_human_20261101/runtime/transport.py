"""Logged, source-only text requests. No tools, retrieval, or implicit HTTP retries."""
import datetime as dt
import json
import os
from pathlib import Path
import time
import requests
from .core.agents import ModelProvider

def utcnow():return dt.datetime.now(dt.timezone.utc).isoformat()

class LoggedProvider(ModelProvider):
    def __init__(self,root,outdir,config):
        self.root=root; self.outdir=outdir; self.config=config; self.stage='initial'
        self.counter=0; self.call_records=[]
        self.contract=(root/'prompts/task_contract.txt').read_text()
        self.requests_dir=outdir/'requests'; self.requests_dir.mkdir(parents=True,exist_ok=True)
        super().__init__(self._call,self._no_images)

    def _no_images(self,*args,**kwargs):raise RuntimeError('Image path is disabled')

    def _call(self,prompt,image_paths=None,assistant='',is_overlap=False):
        if image_paths:raise RuntimeError('L5 model cannot read source PDFs or images')
        model=self.config['model']; key=os.environ.get(model['api_key_env'])
        if not key:raise RuntimeError(f"Required credential environment variable is absent: {model['api_key_env']}")
        self.counter+=1; call_id=f'{self.counter:04d}'
        d=self.requests_dir/call_id; d.mkdir(exist_ok=False)
        messages=[{'role':'system','content':self.contract},{'role':'user','content':prompt}]
        if assistant:
            messages.extend([{'role':'assistant','content':assistant},
                             {'role':'user','content':'Return only valid JSON conforming to the requested schema. Preserve task evidence constraints.'}])
        payload={'model':model['model_name'],'messages':messages,'stream':False,
                 'chat_template_kwargs':{'enable_thinking':False}}
        for k in ['temperature','max_tokens']:
            if model.get(k) is not None:payload[k]=model[k]
        request={'call_id':call_id,'stage':self.stage,'started_at':utcnow(),
                 'url':model['base_url'].rstrip('/')+'/chat/completions','payload':payload,
                 'automatic_network_retries':0}
        (d/'request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2)+'\n')
        start=time.monotonic()
        status={'call_id':call_id,'stage':self.stage,'status':'STARTED'}
        try:
            response=requests.post(request['url'],json=payload,
                    headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},
                    timeout=self.config['transport']['timeout_seconds'])
            # Preserve complete provider response, but never headers/credentials.
            response_text=response.text.replace(key,'[REDACTED]')
            (d/'response_body.txt').write_text(response_text)
            status['http_status']=response.status_code
            if response.status_code!=200:raise RuntimeError(f'Provider HTTP {response.status_code}; see saved response')
            body=json.loads(response_text)
            (d/'response.json').write_text(json.dumps(body,ensure_ascii=False,indent=2)+'\n')
            status.update({'response_model':body.get('model'),'response_id':body.get('id'),
                           'usage':body.get('usage'),'finish_reason':body['choices'][0].get('finish_reason')})
            if status['finish_reason'] not in ('stop',None):raise RuntimeError('Non-complete model finish reason')
            content=body['choices'][0]['message'].get('content')
            if not isinstance(content,str) or not content.strip():raise RuntimeError('Empty model content')
            (d/'content.txt').write_text(content)
            status['status']='COMPLETED'
            return content
        except Exception as exc:
            status.update({'status':'FAILED','error_type':type(exc).__name__,
                           'error':str(exc).replace(key,'[REDACTED]')})
            raise
        finally:
            status.update({'finished_at':utcnow(),'elapsed_seconds':round(time.monotonic()-start,3)})
            (d/'status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2)+'\n')
            self.call_records.append(status)
            with (self.outdir/'request_events.jsonl').open('a') as f:f.write(json.dumps(status,ensure_ascii=False)+'\n')
