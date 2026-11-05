"""Read-only standard-library audit of the L5 Git publication; no model calls."""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import random
import statistics

HERE=Path(__file__).resolve().parent
HAND=HERE.parent
REPO=HERE.parents[4]
ROLES=('score_primary','score_secondary','score_adjudicated')
MODES=('independent_mean','adjudicated')
METRICS=('C','K','R')

def read(p):return json.loads(p.read_text())
def require(ok,message):
    if not ok:raise ValueError(message)
def close(a,b,message):
    require(math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-12),message)
def check_file(entry):
    p=(REPO/entry['path']).resolve()
    require(not Path(entry['path']).is_absolute() and p.is_relative_to(REPO),'Nonportable path')
    require(p.stat().st_size==entry['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==entry['sha256'],'File changed: '+entry['path'])
def percentile(xs,q):
    position=(len(xs)-1)*q;lo=int(position)
    return xs[lo] if position==lo else xs[lo]+(xs[lo+1]-xs[lo])*(position-lo)
def interval(values):
    rng=random.Random(20261101)
    draws=sorted(statistics.fmean(rng.choices(values,k=len(values))) for _ in range(10000))
    return [percentile(draws,.025),percentile(draws,.975)]

def verify(with_archive=False):
    for entry in read(HERE/'manifest.json')['files']:check_file(entry)
    for entry in read(HERE/'implementation-check.json')['checks']:check_file(entry)
    refs=read(HERE/'reference-labels.json');records=read(HERE/'rating-labels.json')
    require(len(refs)==29 and len(records)==174,'Incomplete evidence')
    counts=collections.Counter(x['status'] for ref in refs.values() for x in ref['obligations'])
    require(counts=={'CONFIRMED':295,'UNRESOLVED':4},'Reference denominator differs')
    scores={}
    for record in records:
        confirmed={x['id'] for x in refs[record['task_id']]['obligations'] if x['status']=='CONFIRMED'}
        coverage={x['obligation_id']:x['value'] for x in record['coverage']}
        require(len(coverage)==len(record['coverage']) and confirmed<=coverage.keys(),'Coverage identifiers differ')
        values=[coverage[k] for k in confirmed]
        require(all(v in (0,.5,1) and not isinstance(v,bool) for v in values),'Invalid coverage value')
        units=record['units'];seen=set()
        for unit in units:
            require(unit['id'] not in seen and unit['judgment'] in ('S','X','U','Q'),'Invalid assertion')
            for key in ['duplicate_of','same_assertion_as']:
                require(unit[key] is None or unit[key] in seen,'Invalid deduplication link')
            seen.add(unit['id'])
        labels=collections.Counter(x['judgment'] for x in units if x['duplicate_of'] is None and x['same_assertion_as'] is None)
        n=sum(labels[k] for k in ['S','X','U'])
        require(bool(units) and n>0 and bool(values),'Undefined denominator')
        value={'C':sum(values)/len(values),'K':labels['S']/n,'R':sum(x['duplicate_of'] is not None for x in units)/len(units)}
        for metric in METRICS:close(value[metric],record['final_metric_values'][metric],'Per-record metric differs')
        key=(record['artifact_code'],record['role'])
        require(key not in scores,'Duplicate rating');scores[key]=value
    cases=read(HAND/'results/per_case.json');projects=read(HAND/'results/per_project.json');overall=read(HAND/'results/overall.json')
    require(len(cases)==29 and len(projects)==10 and len({c['case_id'] for c in cases})==29,'Cohort differs')
    for case in cases:
        for mode in MODES:
            for metric in METRICS:
                values={method:statistics.fmean(scores[code,role][metric] for role in ROLES[:2]) if mode=='independent_mean' else scores[code,ROLES[2]][metric] for method,code in case['artifact_codes'].items()}
                values['A_minus_B']=values['A']-values['B']
                for key,value in values.items():close(value,case[mode][metric][key],'Per-case metric differs')
                require(case[mode][metric]['paired'] is True,'Unpaired case')
    for project in projects:
        subset=[c for c in cases if c['document']==project['document']]
        require(len(subset)==project['planned_targets'],'Project cohort differs')
        for mode in MODES:
            for metric in METRICS:
                for key in ['A','B','A_minus_B']:
                    close(statistics.fmean(c[mode][metric][key] for c in subset),project[mode][metric][key],'Project aggregate differs')
                require(project[mode][metric]['paired_targets']==len(subset) and not project[mode][metric]['unavailable_targets'],'Unavailable final case')
    for mode in MODES:
        for metric in METRICS:
            saved=overall[mode][metric]
            for key in ['A','B','A_minus_B']:close(statistics.fmean(p[mode][metric][key] for p in projects),saved[key],'Overall aggregate differs')
            ci=interval([p[mode][metric]['A_minus_B'] for p in projects])
            for a,b in zip(ci,saved['A_minus_B_CI95']):close(a,b,'Bootstrap interval differs')
            require(saved['projects_evaluable']==10 and saved['paired_targets']==29,'Overall cohort differs')
    calls=read(HERE/'call-usage-index.json')
    require(len(calls['calls'])==calls['recorded_attempt_directories']==1603,'Call inventory differs')
    require(dict(collections.Counter(x['status'] for x in calls['calls']))==calls['status_counts'],'Call states differ')
    require(calls['monetary_total_cny'] is None and all(x['cost_cny'] is None for x in calls['calls']),'Unknown amount changed')
    archive_count=None
    if with_archive:
        entries=read(HERE/'local-archive-index.json')['files']
        for entry in entries:check_file(entry)
        archive_count=len(entries)
    return {'status':'PASS','projects':10,'paired_targets':29,'ratings':174,'confirmed_obligations':295,'unresolved_obligations':4,
            'bootstrap_replicates':10000,'monetary_total_cny':None,'archive_files_checked':archive_count}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-local-archive',action='store_true')
    print(json.dumps(verify(parser.parse_args().with_local_archive),ensure_ascii=False,indent=2))
