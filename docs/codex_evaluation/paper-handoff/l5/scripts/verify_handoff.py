"""Read-only experimental audit; writes verification/manifests only in this handoff.

No API calls or credentials. --refresh-manifest deliberately re-seals a rebuilt
handoff after all source, arithmetic and presentation checks pass.
"""
from pathlib import Path
from collections import Counter
import argparse
import csv
import copy
import datetime
import hashlib
import importlib.util
import json
import math
import random
import re
import statistics

HERE = Path(__file__).resolve().parents[1]
REPO = HERE.parents[3]
EXP = REPO / 'experiments/l5/pure_human_20261101'
MERGED = EXP / 'agent_evaluation_v1/scope_adjudication_v1/continuation_v2/merged'
ROLES = ('score_primary', 'score_secondary', 'score_adjudicated')
METRICS = ('C', 'K', 'R')
MODES = ('independent_mean', 'adjudicated')
CHECKS = []
SPAN_COUNTS = Counter()

def read(p):
    return json.loads(Path(p).read_text())

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def check(condition, description):
    if not condition:
        raise AssertionError(description)

def equal(a, b, label='value'):
    if isinstance(a, dict):
        check(isinstance(b, dict) and set(a) == set(b), label + ': keys')
        for k in a:
            equal(a[k], b[k], label + '/' + str(k))
    elif isinstance(a, list):
        check(isinstance(b, list) and len(a) == len(b), label + ': length')
        for i, (x, y) in enumerate(zip(a, b)):
            equal(x, y, label + '/' + str(i))
    elif isinstance(a, (int, float)) and not isinstance(a, bool):
        check(isinstance(b, (int, float)) and math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12), label + ': numeric mismatch')
    else:
        check(a == b, label + ': mismatch')

def passed(name, **details):
    CHECKS.append({'check': name, 'status': 'PASS', **details})

def safe_repo(p):
    p = Path(p)
    check(not p.is_absolute(), 'Expected repository-relative path')
    full = (REPO / p).resolve()
    check(full.is_relative_to(REPO) and full.exists(), 'Missing repository artifact: ' + str(p))
    return full

def verify_span(text, span, kind):
    check(isinstance(span, dict), kind + ': missing literal span')
    start, end = span['start'], span['end']
    check(0 <= start < end <= len(text), kind + ': invalid offsets')
    check(text[start:end] == span['exact_quote'], kind + ': exact quote/offset mismatch')
    SPAN_COUNTS[kind] += 1

def source_evidence(evidence, parts):
    for e in evidence:
        part = parts[e['part_id']]
        check(e.get('pdf_page') == part['pdf_page'], 'Source PDF page mismatch')
        verify_span(part['text'], e['source_span'], 'source_quote_spans')

def percentile(xs, q):
    xs = sorted(xs)
    position = (len(xs) - 1) * q
    lo = int(position)
    return xs[lo] if position == lo else xs[lo] + (xs[lo+1] - xs[lo]) * (position-lo)

def bootstrap(values):
    rng = random.Random(20261101)
    draws = [statistics.fmean(rng.choices(values, k=len(values))) for _ in range(10000)]
    return [percentile(draws, .025), percentile(draws, .975)]

def calculate(record, reference):
    confirmed = {o['id'] for o in reference['obligations'] if o['status'] == 'CONFIRMED'}
    coverage = {x['obligation_id']: x['value'] for x in record['coverage']}
    check(len(coverage) == len(record['coverage']) and confirmed <= coverage.keys(), 'Coverage identifiers')
    values = [coverage[k] for k in confirmed]
    check(all(v in (0, .5, 1) and not isinstance(v, bool) for v in values), 'Final coverage label')
    units = record['units']
    check(not record['empty_behavior'] and bool(units), 'Final artifacts must be nonempty')
    seen = set()
    for u in units:
        check(u['id'] not in seen and u['judgment'] in ('S', 'X', 'U', 'Q'), 'Unit identity/label')
        check(u['category'] in ('condition', 'main', 'branch', 'result'), 'Unit category')
        for key in ('duplicate_of', 'same_assertion_as'):
            check(u.get(key) is None or u[key] in seen, 'Deduplication must target earlier unit')
        seen.add(u['id'])
    counts = Counter(u['judgment'] for u in units if u.get('duplicate_of') is None and u.get('same_assertion_as') is None)
    denominator = sum(counts[k] for k in ('S', 'X', 'U'))
    check(denominator > 0 and len(confirmed) > 0, 'Final main metric denominator')
    return {'C': sum(values)/len(values), 'K': counts['S']/denominator,
            'R': sum(u.get('duplicate_of') is not None for u in units)/len(units)}

def main(refresh=False):
    manifest_path = HERE / 'package_manifest.json'
    if manifest_path.exists() and not refresh:
        for entry in read(manifest_path)['files']:
            p = HERE / entry['path']
            check(p.is_file() and sha(p) == entry['sha256'], 'Handoff changed: ' + entry['path'])
        passed('existing_package_hashes')
    source_manifest = read(HERE / 'artifact_manifest.json')['files']
    check(len({x['path'] for x in source_manifest}) == len(source_manifest), 'Duplicate source paths')
    for x in source_manifest:
        p = safe_repo(x['path'])
        check(p.stat().st_size == x['bytes'] and sha(p) == x['sha256'], 'Source hash mismatch: ' + x['path'])
    passed('source_hashes', files=len(source_manifest))

    cases = read(HERE / 'results/per_case.json')
    candidates = read(EXP / 'screening/candidates.json')
    equal(dict(Counter(c['decision'] for c in candidates)), {'INCLUDE': 78, 'EXCLUDE': 35, 'PENDING_BOUNDARY': 3, 'DUPLICATE': 6}, 'screening inventory')
    projects = sorted({c['document'] for c in cases})
    check(len(cases) == 29 and len(projects) == 10, 'Paired material counts')
    for project in projects:
        group = [c for c in candidates if c['document'] == project and c['decision'] == 'INCLUDE']
        for c in group:
            expected = hashlib.sha256(('PURE-L5-20261101-project-balanced-v1|' + c['id']).encode()).hexdigest()
            check(c['selection_hash'] == expected, 'Selection hash mismatch')
        chosen = {c['id'] for c in sorted(group, key=lambda c:c['selection_hash'])[:3]}
        check(chosen == {c['case_id'] for c in cases if c['document'] == project}, 'Project-balanced selection')
    passed('screening_and_fixed_selection', projects=10, targets=29, candidate_occurrences=122)

    refs, records, scores = {}, {}, {}
    code_to_case = {}
    ref_counts = Counter()
    for case in cases:
        cid, task = case['case_id'], case['task_id']
        inp = read(HERE / f'materials/inputs/{cid}.json')
        equal(inp, read(EXP / f'inputs/{cid}.json'), 'Unchanged input ' + cid)
        source = read(EXP / f'agent_evaluation_v1/revision_v5/bundles/source/{task}.json')
        parts = {p['source_part_id']:p for p in source['requirements']}
        b_span = next(c['B_span'] for c in candidates if c['id']==cid)
        for p in inp['requirements']:
            equal(p['text'], parts[p['source_part_id']]['text'], 'Generation/evaluation common source')
            start,end = p['normalized_lines']
            check(max(start,b_span['start_line']) >= min(end+1,b_span['end_line_exclusive']), 'Input intersects hidden B line span')
        ref = read(HERE / f'materials/references/{task}.json')
        refs[task] = ref
        ref_counts.update(o['status'] for o in ref['obligations'])
        for o in ref['obligations']:
            source_evidence(o['evidence'], parts)
        for method, code in case['artifact_codes'].items():
            code_to_case[code] = case
            folder = 'generated' if method == 'A' else 'controls'
            p = HERE / f'materials/{folder}/{cid}.txt'
            original = EXP / (f'runs/formal-v2-credential-recovery/{cid}/A.txt' if method == 'A' else f'private_controls/{cid}.txt')
            check(sha(p) == sha(original), 'Unchanged artifact ' + cid + method)
            if method == 'B':
                check(sha(HERE/f'materials/controls/{cid}.raw.txt') == sha(EXP/f'private_controls/{cid}.raw.txt'), 'Raw B unchanged')
            product = read(EXP/f'agent_evaluation_v1/revision_v5/bundles/products/{code}.json')['text']
            check(p.read_text() == product, 'Anonymous display unchanged')
            for role in ROLES:
                record = read(HERE/f'materials/ratings/{role}/{code}.json')
                check(record['case_id'] == cid and record['method'] == method and record['task_id'] == task, 'Pair identity')
                check(sha(safe_repo(record['source_record'])) == record['source_record_sha256'], 'Rating provenance')
                original_record = read(safe_repo(record['source_record']))
                equal(record['coverage'], original_record['coverage'], 'Unchanged final coverage')
                equal(record['units'], original_record['units'], 'Unchanged final units')
                records[code,role] = record
                scores[code,role] = calculate(record, ref)
                equal(scores[code,role], {k:record['final_metric_values'][k] for k in METRICS}, 'Independent formula recomputation')
                for judgment in record['coverage'] + record['units']:
                    source_evidence(judgment.get('evidence', []), parts)
                    for key in ('product_spans', 'conflict_product_spans'):
                        for span in judgment.get(key, []):
                            verify_span(product, span, 'product_quote_spans')
                    if 'product_span' in judgment:
                        verify_span(product, judgment['product_span'], 'product_quote_spans')
    equal(dict(ref_counts), {'CONFIRMED':295, 'UNRESOLVED':4}, 'Reference inventory')
    check(len(records) == 174, 'Final judgment record count')
    passed('unaltered_materials_and_judgments', targets=29, artifacts=58, rating_records=174)
    passed('input_control_direct_line_boundaries', targets=29, intersections=0, scope='Literal line intervals only; semantic parallel-copy audit remains a recorded material judgment')
    passed('literal_source_and_product_spans', **dict(SPAN_COUNTS))
    passed('item_level_metric_recalculation', records=174, source_confirmed=295, source_unresolved=4)

    for case in cases:
        for mode in MODES:
            for metric in METRICS:
                values = {}
                for method, code in case['artifact_codes'].items():
                    values[method] = statistics.fmean(scores[code,r][metric] for r in ROLES[:2]) if mode == 'independent_mean' else scores[code,ROLES[2]][metric]
                values.update(A_minus_B=values['A']-values['B'], paired=True)
                equal(values, case[mode][metric], 'Per-case aggregation')
    project_results = read(HERE/'results/per_project.json')
    for row in project_results:
        group = [c for c in cases if c['document'] == row['document']]
        for mode in MODES:
            for metric in METRICS:
                values = {k:statistics.fmean(c[mode][metric][k] for c in group) for k in ('A','B','A_minus_B')}
                values.update(paired_targets=len(group), unavailable_targets=[])
                equal(values, row[mode][metric], 'Project aggregation')
    overall = read(HERE/'results/overall.json')
    for mode in MODES:
        for metric in METRICS:
            values = {k:statistics.fmean(r[mode][metric][k] for r in project_results) for k in ('A','B','A_minus_B')}
            values.update(A_minus_B_CI95=bootstrap([r[mode][metric]['A_minus_B'] for r in project_results]), projects_evaluable=10, projects_planned=10, paired_targets=29, targets_planned=29)
            equal(values, overall[mode][metric], 'Overall/project bootstrap')
    passed('independent_aggregation_and_bootstrap', modes=2, metrics=3, seed=20261101, replicates=10000)

    # Cross-check the original read-only arithmetic function, including blind
    # alignment, against the handoff. Never call save_aggregate (which writes).
    spec = importlib.util.spec_from_file_location('l5_frozen_metrics', EXP/'agent_evaluation_v1/revision_v5/metrics.py')
    metrics = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(metrics)
    # Apply exactly the final adopted boundary dispositions: a source-excluded
    # obligation cannot block C; an explicitly approved diagnostic attached to
    # a definite coverage label is retained as a note, not missing data.
    decisions = read(EXP/'agent_evaluation_v1/completeness_audit_v1/compatibility_v2/decision_register.json')['items']
    canonical = lambda x:json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',',':'))
    accepted = {(x['artifact_code'], x['annotation_id']) for x in decisions if x['resolution']=='DIAGNOSTIC'}
    code_by_content = {canonical(read(p)):p.stem for p in (MERGED/'records').glob('score_*/*.json')}
    raw_score = metrics.score_record
    def adopted_score(record, reference):
        code = code_by_content[canonical(record)]
        out = copy.deepcopy(record)
        statuses = {o['id']:o['status'] for o in reference['obligations']}
        coverage = {c['obligation_id']:c['value'] for c in record['coverage']}
        retained = []
        for issue in record.get('unresolved', []):
            issue_id = hashlib.sha256(canonical(issue).encode()).hexdigest()[:20]
            target = issue.get('item_id')
            excluded = issue.get('metric')=='C' and statuses.get(target) in ('UNRESOLVED','EXCLUDED')
            diagnostic = issue.get('metric')=='C' and (code,issue_id) in accepted and statuses.get(target)=='CONFIRMED' and coverage.get(target) in (0,.5,1)
            if not (excluded or diagnostic):
                retained.append(issue)
        out['unresolved'] = retained
        return raw_score(out, reference)
    metrics.score_record = adopted_score
    original_aggregate = metrics.aggregate(MERGED)
    for key in ('per_case', 'per_project', 'overall', 'agreement'):
        equal(original_aggregate[key], read(HERE/f'results/{key}.json'), 'Frozen computation/' + key)
    passed('agreement_and_blind_alignment', coverage_pairs=590, deduplicated_equivalent_pairs=397)
    stability = read(HERE/'results/agreement_and_stability.json')['stability']
    check(len(stability['items']) == 10 and len({code_to_case[x['artifact_code']]['document'] for x in stability['items']}) == 10, 'Stability project stratification')
    for item in stability['items']:
        code = item['artifact_code']
        equal(item['first'], scores[code,'score_secondary'], 'Stability original rating')
        repeat = read(MERGED/f'records/score_stability/{code}.json')
        equal(item['repeat'], calculate(repeat, refs[code_to_case[code]['task_id']]), 'Stability repeat arithmetic')
        equal(item['absolute_difference'], {k:abs(item['first'][k]-item['repeat'][k]) for k in METRICS}, 'Stability difference')
    equal(stability['mean_absolute_difference'], {k:statistics.fmean(i['absolute_difference'][k] for i in stability['items']) for k in METRICS}, 'Stability mean')
    passed('stability_recomputation', artifacts=10, projects=10)

    for filename, expected_count, lookup in [
        ('results_overall.csv',6,lambda r:overall[r['mode']][r['metric']]),
        ('results_per_case.csv',174,lambda r:next(c for c in cases if c['case_id']==r['case_id'])[r['mode']][r['metric']]),
        ('results_per_project.csv',60,lambda r:next(p for p in project_results if p['document']==r['project'])[r['mode']][r['metric']])]:
        rows = list(csv.DictReader((HERE/'tables'/filename).open()))
        check(len(rows)==expected_count, 'Result table count')
        for row in rows:
            target = lookup(row)
            for key in ('A','B','A_minus_B'):
                equal(float(row[key]), target[key], 'CSV value')
            if 'A_minus_B_CI95' in row:
                equal(json.loads(row['A_minus_B_CI95']), target['A_minus_B_CI95'], 'CSV interval')
    passed('result_tables', rows=240)

    links = 0
    for p in HERE.rglob('*'):
        if not p.is_file() or p.suffix not in ('.md','.json','.csv','.txt'):
            continue
        content = p.read_text()
        check(not re.search(r'/home/[^\s]+', content), 'Machine-specific absolute path in ' + p.name)
        check(not re.search(r'\bsk-[A-Za-z0-9_-]{20,}', content), 'Possible credential in ' + p.name)
        if p.suffix == '.md':
            for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', content):
                if target.startswith(('https://','http://','#')):
                    continue
                path = (p.parent/target.split('#')[0]).resolve()
                check(path.is_file(), 'Broken Markdown link: ' + target)
                links += 1
    passed('portable_paths_and_local_links', local_links=links)
    result = {'status':'PASS', 'checked_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'scope':'Offline file identity, literal location and arithmetic checks only; not semantic or human validation',
              'network_calls':0, 'experimental_files_modified':False, 'checks':CHECKS,
              'summary':{'projects':10,'targets':29,'artifacts':58,'final_rating_records':174,
                         'confirmed_source_obligations':295,'source_unresolved_obligations':4,'main_metric_NA':0}}
    (HERE/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    entries = [{'path':p.relative_to(HERE).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)}
               for p in sorted(HERE.rglob('*')) if p.is_file() and p != manifest_path and '__pycache__' not in p.parts]
    manifest_path.write_text(json.dumps({'path_base':'this handoff directory','files':entries},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':'PASS','checks':len(CHECKS),'source_files':len(source_manifest),
                      'handoff_files':len(entries),'quote_spans':dict(SPAN_COUNTS)},ensure_ascii=False))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--refresh-manifest', action='store_true', help='Re-seal deliberate local rebuild after source and arithmetic checks')
    main(parser.parse_args().refresh_manifest)
