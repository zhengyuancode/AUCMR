"""Read-only progress snapshot: no API calls or credentials."""
from pathlib import Path
import json,collections,datetime,os
R=Path(__file__).resolve().parent
def read(p):return json.loads(p.read_text())
if __name__=='__main__':
 print(datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat())
 state=read(R/'state.json') if (R/'state.json').exists() else {'status':'NOT_STARTED'}
 print('Stage:',state.get('stage'),'| Status:',state.get('status'))
 if (R/'process.json').exists():
  pid=read(R/'process.json')['pid']
  try:os.kill(pid,0);alive=True
  except ProcessLookupError:alive=False
  print('PID:',pid,'| visible in this PID namespace:',alive)
  if not alive:print('PID not visible here; sandbox namespaces may differ. Use state/log timestamps or check on execution host before concluding stopped.')
 for role,n in [('reference_builder',29),('reference_independent',29),('reference_adjudicator_blind',29),('reference_review',29),('score_primary',58),('score_secondary',58),('alignment',58),('score_adjudicated',58),('score_stability',10)]:
  print(f'{role:30} {len(list((R/"records"/role).glob("*.json")))}/{n}')
 if (R/'inheritance_manifest.json').exists():
  im=read(R/'inheritance_manifest.json');print('Inherited/recovered prior role records (all stages):',len(im['successful']),'| parent failed source roles:',len(im['failed']))
 print('Final references:',len(list((R/'references').glob('*.json'))),'/29 | ready target freezes:',len(list((R/'reference_freezes').glob('*.json'))),'/29 | all-target freeze:',(R/'reference_freeze.final.json').exists())
 if (R/'pending_errors.json').exists():
  pending=read(R/'pending_errors.json');print('Last completed phase pending items:',pending['count'],'| phase:',pending['phase'])
 jobs=[read(p) for p in (R/'calls').glob('*/*/job.json')]
 statuses=[read(p) for p in (R/'calls').glob('*/*/attempt-*/status.json')]
 print('HTTP:',dict(collections.Counter(str(x.get('http_status')) for x in statuses)))
 failed=[{'role':j.get('role'),'unit':j.get('unit'),'error':j.get('error')} for j in jobs if j.get('status')=='FAILED']
 print('Failed logical calls:',len(failed))
 for x in failed[:15]:print(json.dumps(x,ensure_ascii=False))
 if (R/'results/REPORT.md').exists():print('Results:',R/'results/REPORT.md')
