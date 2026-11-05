"""Explicit freeze and run commands. No implicit execution on import."""
from pathlib import Path
import argparse
import concurrent.futures
import datetime as dt
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from runtime.inputs import load_case
from runtime.pipeline import execute,render_result
from runtime.transport import LoggedProvider,utcnow

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,value):p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

def frozen_files():
    files=[]
    for folder in ['runtime','prompts','config','inputs','tools','tests','protocol']:
        for p in sorted((ROOT/folder).rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc',):files.append(p)
    return files

def load_credentials():
    # Only credential settings are imported; values are never logged or snapshotted.
    upstream=ROOT.parents[2]
    sys.path.insert(0,str(upstream))
    from config.settings import LLM_ENDPOINT
    config=json.loads((ROOT/'config/run.json').read_text())
    if LLM_ENDPOINT.model_name!=config['model']['model_name'] or LLM_ENDPOINT.base_url.rstrip('/')!=config['model']['base_url'].rstrip('/'):
        raise RuntimeError('Existing model endpoint differs from frozen configuration')
    if not LLM_ENDPOINT.api_key:raise RuntimeError('Existing AUCMR credential missing')
    os.environ[config['model']['api_key_env']]=LLM_ENDPOINT.api_key

def freeze(run_id):
    run=ROOT/'runs'/run_id
    run.mkdir(parents=True,exist_ok=False)
    config=json.loads((ROOT/'config/run.json').read_text())
    if not os.environ.get(config['model']['api_key_env']):raise RuntimeError('Generation credential not available')
    for case in config['cases']:load_case(ROOT,case)
    offline=json.loads((ROOT/'validation/offline.json').read_text())
    if not offline.get('passed'):raise RuntimeError('Offline validation did not pass')
    write(run/'config.snapshot.json',config)
    manifest={'frozen_at':utcnow(),'run_id':run_id,'protocol':config['protocol'],
              'b_mode':'frozen_original_PURE_human_text','case_order':config['cases'],
              'files':{str(p.relative_to(ROOT)):digest(p) for p in frozen_files()},
              'offline_validation_sha256':digest(ROOT/'validation/offline.json'),
              'python':platform.python_version(),
              'packages':{p:importlib.metadata.version(p) for p in ['requests','json-repair']},
              'model_calls_at_freeze':0,'no_quality_selection':True,
              'network_retries':0,'source_read_policy':'frozen independent requirements only; no B, images or file/tools accessible to model'}
    write(run/'freeze.json',manifest)
    return run

def verify_freeze(run):
    m=json.loads((run/'freeze.json').read_text())
    for file,expected in m['files'].items():
        if digest(ROOT/file)!=expected:raise RuntimeError(f'Frozen implementation/input changed: {file}')
    if json.loads((run/'config.snapshot.json').read_text())!=json.loads((ROOT/'config/run.json').read_text()):
        raise RuntimeError('Config snapshot mismatch')

def run_one(run,case,config):
    verify_freeze(run)
    out=run/case
    # Existence means an attempt was started; never silently overwrite/re-run it.
    out.mkdir(exist_ok=False)
    write(out/'status.json',{'status':'RUNNING','started_at':utcnow(),'case':case})
    provider=LoggedProvider(ROOT,out,config)
    try:
        initial=load_case(ROOT,case)
        result=execute(initial,provider,config,out,ROOT)
        text=render_result(result)
        (out/'A.txt').write_text(text)
        status={'case':case,'status':'COMPLETED','completed_at':utcnow(),
                'output_sha256':digest(out/'A.txt'),'result_sha256':digest(out/'result.json'),
                'request_count':provider.counter,'quality_flags':result['quality_flags']}
    except Exception as exc:
        (out/'traceback.txt').write_text(traceback.format_exc())
        status={'case':case,'status':'FAILED','finished_at':utcnow(),'error_type':type(exc).__name__,
                'error':str(exc),'request_count':provider.counter}
    write(out/'status.json',status)
    print(json.dumps({'case':case,'status':status['status'],'requests':provider.counter}),flush=True)
    return status

def run_all(run,workers):
    verify_freeze(run)
    config=json.loads((run/'config.snapshot.json').read_text())
    statuses=[]
    # Parallel samples have independent providers, files and state; this is not agent delegation.
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(run_one,run,c,config) for c in config['cases']]
        for future in concurrent.futures.as_completed(futures):
            statuses.append(future.result()); write(run/'progress.json',statuses)
    verify_freeze(run)
    summary={'status':'COMPLETED' if all(x['status']=='COMPLETED' for x in statuses) else 'INCOMPLETE',
             'cases':sorted(statuses,key=lambda x:x['case']),'finished_at':utcnow()}
    write(run/'summary.json',summary)
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('command',choices=['freeze','run','verify']);p.add_argument('--run-id',required=True)
    p.add_argument('--workers',type=int,default=4);a=p.parse_args()
    if '/' in a.run_id or a.run_id.startswith('.'):raise ValueError('Invalid run ID')
    load_credentials()
    run=ROOT/'runs'/a.run_id
    if a.command=='freeze':print(freeze(a.run_id))
    elif a.command=='verify':verify_freeze(run);print('Frozen files verified')
    else:
        result=run_all(run,a.workers);print(result['status']);sys.exit(0 if result['status']=='COMPLETED' else 1)
