"""Read-only progress view. No credentials, API calls, or experiment mutations."""
from pathlib import Path
import json,collections,datetime
R=Path(__file__).resolve().parents[1];RUN=R/'runs/formal-v2-credential-recovery'
def read(p):
 try:return json.loads(p.read_text())
 except (FileNotFoundError,json.JSONDecodeError):return None
config=read(RUN/'config.snapshot.json')
if not config:raise SystemExit('Recovery run has not started')
counts=collections.Counter();http=collections.Counter();rows=[];allcalls=0
for case in config['cases']:
 d=RUN/case;s=read(d/'status.json') or {'status':'NOT_STARTED'};state=s['status'];counts[state]+=1
 finished=[];pending=[]
 for p in sorted((d/'requests').glob('*/request.json')):
  req=read(p) or {};st=read(p.parent/'status.json')
  if st:
   finished.append(st);http[str(st.get('http_status','NO_HTTP'))]+=1;allcalls+=1
  else:pending.append(req)
 if pending:stage=pending[-1].get('stage','request in progress')
 elif finished:stage=max(finished,key=lambda x:x.get('finished_at','')).get('stage','')
 else:stage=''
 rows.append((case,state,len(finished),stage,s.get('error','')))
summary=read(RUN/'summary.json')
print(datetime.datetime.now().astimezone().isoformat(timespec='seconds'))
print('Run:',RUN.name,'|',summary.get('status') if summary else 'IN PROGRESS (no final summary)')
print('Cases:',dict(counts),'/',len(config['cases']),'| finished requests:',allcalls,'| HTTP:',dict(http))
print('Credential: AUCMR_JUNXI_DEEPSEEK_API_KEY (value hidden)')
print(f"{'CASE':36} {'STATE':12} {'CALLS':>5}  CURRENT / LAST STAGE")
for case,state,n,stage,error in rows:
 print(f'{case:36} {state:12} {n:5}  {stage}')
 if error:print('  ERROR:',error)
if summary:
 print('\nGeneration ended. To verify and package: python -B evaluation_tools/finalize_generation.py')
else:
 print('\nThe existing process is running; this command does not restart it.')
