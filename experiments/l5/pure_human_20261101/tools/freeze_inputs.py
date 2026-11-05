from pathlib import Path
import hashlib,json,datetime
R=Path(__file__).resolve().parents[1]
p=R/'protocol/INPUT_AND_CONTROL_FREEZE.json'
if p.exists():raise SystemExit('Already frozen; do not overwrite')
files=[q for d in ['inputs','private_controls','protocol','screening'] for q in sorted((R/d).rglob('*')) if q.is_file() and q!=p]
manifest={'frozen_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'model_calls_in_this_route':0,'B_acquisition':'official PURE2.0 earliest occurrence; no choice based on A','files':{str(q.relative_to(R)):hashlib.sha256(q.read_bytes()).hexdigest() for q in files}}
p.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n');print('Inputs, controls, selection and evaluation protocol frozen:',len(files),'files')
