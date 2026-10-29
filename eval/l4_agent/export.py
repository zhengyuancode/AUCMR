"""Lossless method rows; averages require three complete agent assessments."""
from collections import defaultdict, Counter
import csv
import io
from .protocol import SLOTS, digest, require, save_new, save_new_bytes, read_json, local_path


def export(run, records, snapshot, costs):
    rows, grouped = [], defaultdict(list)
    allocation = read_json(local_path(snapshot['config']['allocation_path']))
    by_id = {e['evaluation_id']:e for e in allocation['evaluations']}
    for record in records:
        identity = {k:record[k] for k in ('evaluation_id','task_id','agent_id','model_key')}
        if record['status'] == 'COMPLETE':
            for rating in record['ratings']:
                row = {**identity,'status':rating['status'],'method':rating['method'],
                       **{'pcs_'+s:rating['pcs'][s] for s in SLOTS},'pcs_mean':rating['pcs_mean'],
                       'prc':rating['prc'],'pcs_reason':rating.get('pcs_reason',rating.get('reason')),
                       'prc_reason':rating.get('prc_reason'),'error':None}
                rows.append(row)
                grouped[(record['task_id'],rating['method'])].append(row)
        else:
            for method in by_id[record['evaluation_id']]['method_order']:
                rows.append({**identity,'status':'TECHNICAL_FAILURE','method':method,
                             **{'pcs_'+s:None for s in SLOTS},'pcs_mean':None,'prc':None,
                             'pcs_reason':None,'prc_reason':None,'error':record['error']})
    aggregates = []
    for (task,method), items in sorted(grouped.items()):
        covered = len(items) == 3 and {i['model_key'] for i in items} == {'glm','doubao','qwen'}
        aggregates.append({'task_id':task,'method':method,'available_agent_ratings':len(items),
                           'three_model_coverage':covered,
                           'pcs_mean':sum(i['pcs_mean'] for i in items)/3 if covered else None,
                           'prc_mean':sum(i['prc'] for i in items)/3 if covered and all(i['prc'] is not None for i in items) else None})
    states = Counter(r['status'] for r in records)
    summary = {'scope':snapshot['scope'],'planned':len(snapshot['evaluation_ids']),'completed':states['COMPLETE'],
               'technical_failures':states['TECHNICAL_FAILURE'],'remaining':len(snapshot['evaluation_ids'])-len(records),
               'costs':costs,'failures':[{'evaluation_id':r['evaluation_id'],'error':r['error']} for r in records if r['status']=='TECHNICAL_FAILURE'],
               'research_status':'SIMULATED_AGENT_PILOT' if snapshot['scope']=='pilot' else 'SIMULATED_AGENT_EVALUATION',
               'statistical_unit_notice':'Agent personas are not independent human participants; retain model/case/agent identifiers.'}
    bundle = {'snapshot_sha256':digest(snapshot),'summary':summary,'ratings':rows,'case_method_means':aggregates}
    path = run/'export.json'
    if path.exists():
        require(read_json(path) == bundle, 'EXPORT_CHANGED')
    else:
        save_new(path,bundle)
    # The JSON above is the immutable source of truth. A CSV is generated once.
    path = run/'ratings.csv'
    out = io.StringIO(newline='')
    fields = ['evaluation_id','task_id','agent_id','model_key','method','status',*['pcs_'+s for s in SLOTS],
              'pcs_mean','prc','pcs_reason','prc_reason','error']
    writer = csv.DictWriter(out,fieldnames=fields)
    writer.writeheader()
    # JSON retains exact text. CSV text cells must not execute model-supplied formulas.
    def csv_safe(value):
        if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):
            return "'" + value
        return value
    writer.writerows({k:csv_safe(v) for k,v in row.items()} for row in rows)
    text = out.getvalue()
    if path.exists():
        require(path.read_bytes() == text.encode('utf-8-sig'), 'CSV_EXPORT_CHANGED')
    else:
        save_new_bytes(path,text.encode('utf-8-sig'))
    return summary
