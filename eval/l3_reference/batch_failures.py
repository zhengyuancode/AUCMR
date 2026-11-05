"""Append-only failed-case records and honest first-pass completion summaries."""
import errno
import os
from pathlib import Path
from eval.agent_evaluation.common import append_jsonl, digest, now, read_json, read_jsonl, write_json


def current_failures(root):
    latest={}
    for record in read_jsonl(Path(root)/'failures.jsonl'):
        latest[record['target_id']]=record
    return {tid:r for tid,r in latest.items() if r['state']=='FAILED'}


def record(root,tid,stage,*,error=None):
    state='FAILED' if error is not None else 'RESOLVED'
    item={'time':now(),'target_id':tid,'stage':stage,'state':state}
    if error is not None:
        from eval.evaluation_runtime import _redact
        reason=str(error)
        for key in ('AUCMR_EVAL_ZHIPU_API_KEY','AUCMR_EVAL_ARK_API_KEY','AUCMR_JUNXI_DASHSCOPE_API_KEY'):
            secret=os.environ.get(key)
            if secret:reason=_redact(reason,secret)
        item.update(error_type=type(error).__name__,reason=reason[:1800],
                    task_path='tasks/'+tid+'/'+stage,paid_requests_retried=False)
    item['record_id']=digest(item)
    append_jsonl(Path(root)/'failures.jsonl',item)
    return item


def fatal_error(error,root,tid,stage):
    """Keep shared inputs, credentials, accounting and evidence integrity fail-closed."""
    import sqlite3
    if isinstance(error,(MemoryError,sqlite3.Error)):return True
    if isinstance(error,OSError) and error.errno in {errno.ENOSPC,errno.EDQUOT,errno.EROFS}:return True
    reason=str(error).lower()
    if reason.startswith('provider request rejected:'):return True
    prefixes=('source-only bundle changed','source hash changed','xml hash changed','pdf page count mismatch',
              'task input changed','snapshot changed','cached request differs','interrupted task inputs changed',
              'cached local tool differs','frozen reference changed','source binding not in allowlist',
              'concurrency limit','saved response has no campaign reservation')
    if reason.startswith(prefixes) or 'hash mismatch' in reason or reason.startswith('quote policy'):return True
    folder=Path(root)/'task_progress'/digest(tid+'/'+stage)
    receipts=sorted(folder.glob('request-*.json'),key=lambda p:int(p.stem.split('-')[-1]))
    if receipts:
        response=read_json(receipts[-1]).get('result') or {}
        raw=response.get('raw_response') or {}
        if isinstance(raw,dict) and raw.get('http_status') in {401,402,403,404}:return True
        from .recovery_policy import provider_error
        if provider_error(response):return True
    return False


def inventory(root,plan,*,pass_finished=False):
    root=Path(root);failures=current_failures(root);rows=[]
    for target in plan['targets']:
        tid=target['target_id'];path=root/'references'/(tid+'.json')
        row={'target_id':tid,'name':target.get('name'),'project_id':target.get('project_id')}
        if path.exists():
            row.update(status='COMPLETE',reference_path=str(path.relative_to(root)))
        elif tid in failures:
            row.update(status='FAILED',failure=failures[tid])
        else:row['status']='IN_PROGRESS' if any((root/'tasks'/tid).glob('*/input.json')) else 'NOT_STARTED'
        rows.append(row)
    counts={s:sum(r['status']==s for r in rows) for s in ('COMPLETE','FAILED','NOT_STARTED','IN_PROGRESS')}
    pending=counts['NOT_STARTED']+counts['IN_PROGRESS']
    status='COMPLETE' if counts['COMPLETE']==len(rows) else ('FINISHED_WITH_FAILURES' if pass_finished and not pending else 'PARTIAL')
    payload={'updated':now(),'status':status,'expected_targets':len(rows),'completed_targets':counts['COMPLETE'],
             'failed_targets':counts['FAILED'],'not_started_targets':counts['NOT_STARTED'],'in_progress_targets':counts['IN_PROGRESS'],'pending_targets':pending,
             'processed_targets':counts['COMPLETE']+counts['FAILED'],'coverage_status':'NOT_RUN','targets':rows}
    write_json(root/'batch-results.json',payload)
    write_json(root/'failed-targets.json',{'updated':payload['updated'],'count':counts['FAILED'],
               'targets':[r for r in rows if r['status']=='FAILED'],'history':'failures.jsonl',
               'retry':'Repair first; rerun the same batch with --retry-failed. Completed stages/receipts remain cached.'})
    return payload
