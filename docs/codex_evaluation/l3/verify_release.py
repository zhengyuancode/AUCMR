"""Read-only, standard-library verification of the published L3 evidence.

No evaluation imports, credentials, network access or output files. Use
--with-local-archive to additionally check the retained original material.
"""
import argparse
import collections
import decimal
import hashlib
import json
import math
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
RUN = REPO / 'outputs/agent_evaluation/l3-coverage-pure54-20261104-v1'
PUB = RUN / 'publication'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_text())


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_path(value):
    path = (REPO / value).resolve()
    require(not Path(value).is_absolute() and path.is_relative_to(REPO), 'Invalid repository-relative path')
    return path


def check_file(entry):
    path = local_path(entry['path'])
    require(path.stat().st_size == entry['bytes'], 'Size changed: '+entry['path'])
    require(file_hash(path) == entry['sha256'], 'Hash changed: '+entry['path'])


def verify(with_archive=False):
    release = read(PUB / 'manifest.json')
    for entry in release['files']:
        check_file(entry)
    for entry in release['shared_runtime_dependencies']:
        check_file(entry)
    inputs = read(PUB / 'inputs.json')
    result = read(RUN / 'results.json')
    reliability = read(RUN / 'reliability.json')
    stability = read(RUN / 'stability.json')
    verification = read(RUN / 'verification.json')
    audit = read(RUN / 'independent-verification-20261105-v9.json')
    selection = read(RUN / 'review-selection.json')
    policy = read(RUN / 'technical-recovery-policy-20261105.json')
    methods = inputs['methods']
    refs = inputs['references']
    require(len(inputs['targets']) == len(refs) == 54 and len(methods) == 7, 'Cohort mismatch')
    require(len(inputs['protected_files']) == 433, 'Protection inventory mismatch')
    require(result['status'] == 'COMPLETE' and result['completed_cases'] == result['expected_cases'] == 378, 'Incomplete coverage')
    require(len(result['rows']) == len(inputs['cases']) == 378, 'Case count mismatch')
    require(len({r['case_id'] for r in result['rows']}) == 378, 'Duplicate result case')
    require(result['human_validation'] == 'NOT_RUN' and result['official_gold'] is False, 'Reference status overstated')
    ref_counts = collections.Counter(r['status'] for ref in refs.values() for r in ref['requirements'])
    require(ref_counts == {'CONFIRMED': 901, 'REFERENCE_UNRESOLVED': 138}, 'Reference denominator changed')
    protected = {x['path']: x['sha256'] for x in inputs['protected_files']}
    for case in inputs['cases'].values():
        require(case['binding']['path'] in protected and case['binding']['sha256'] == protected[case['binding']['path']], 'Frozen binding mismatch')
        require('regeneration' not in case['binding']['path'], 'Regenerated output in primary cohort')

    views = {}
    for view in read(PUB / 'label-index.json')['views']:
        key = view['phase'], view['role'], view['case_id']
        require(key not in views, 'Duplicate view')
        views[key] = view['labels']
    selected = {(r['case_id'], r['requirement_id']) for r in selection['selected']}
    require(not selection['missing_initial'], 'Initial judgment missing')
    recovery = {(t['case_id'], rid) for t in policy['tasks'].values() if t['phase'] == 'INITIAL' for rid in t['requirement_ids']}
    require(len(recovery) == 215 and recovery <= selected, 'Technical recovery must receive complete C review')
    both = agreed = 0
    for item in result['rows']:
        cid = item['case_id']; case = inputs['cases'][cid]; ref = refs[item['target_id']]
        for key in ['target_id', 'project_id', 'method']:
            require(item[key] == case[key], 'Case metadata changed')
        require(item['artifact_sha256'] == case['binding']['sha256'] and item['reference_sha256'] == ref['reference_sha256'], 'Result source mismatch')
        a = views['INITIAL', 'A', cid]; b = views['INITIAL', 'B', cid]
        c = views.get(('C_FINAL', 'C', cid), {}); blind = views.get(('C_BLIND', 'C', cid), {})
        reconciled = {}
        for req in ref['requirements']:
            rid = req['requirement_id']
            if (cid, rid) in selected:
                require(rid in c and rid in blind, 'Selected judgment lacks blind/final review')
                reconciled[rid] = c[rid]
            else:
                require(a[rid][0] == b[rid][0], 'Unreviewed disagreement')
                reconciled[rid] = a[rid]
            if req['status'] == 'CONFIRMED' and item['artifact_status'] == 'PRESENT':
                both += 1; agreed += a[rid][0] == b[rid][0]
        require(reconciled == {rid: [r['label'], r['technical_status']] for rid, r in item['reconciled_judgments'].items()}, 'Reconciled labels differ from final result')
        for name, rows in [('A_FIRST', a), ('B_FIRST', b), ('RECONCILED', reconciled)]:
            counts = collections.Counter()
            for req in ref['requirements']:
                label, status = rows[req['requirement_id']]
                require(status == 'OK', 'Technical failure in completed view')
                if req['status'] == 'REFERENCE_UNRESOLVED':
                    require(label == 'UNRESOLVED', 'Reference uncertainty was rescored')
                counts[label] += 1
            score = item['scores'][name]
            require(score['N'] == len(ref['requirements']) and collections.Counter(score['counts']) == counts, 'Per-case counts differ')
            require(score['RC_full'] == counts['FULL']/score['N'], 'Partial credit or denominator mismatch')
    require(both == reliability['both_initial_completed'] and agreed == reliability['initial_agreements'], 'Reliability counts differ')
    require(reliability['initial_agreement_rate'] == agreed/both, 'Reliability rate differs')
    metrics = {}
    for method in methods:
        subset = [r for r in result['rows'] if r['method'] == method]
        require(len(subset) == 54, 'Method has incomplete cohort')
        for name in ['A_FIRST', 'B_FIRST', 'RECONCILED']:
            docs = collections.defaultdict(list); counts = collections.Counter(); n = 0
            for item in subset:
                score = item['scores'][name]
                docs[item['project_id']].append(score['RC_full']); counts.update(score['counts']); n += score['N']
            macro = sum(sum(v)/len(v) for v in docs.values())/len(docs)
            saved = result['method_aggregates'][method][name]
            require(n == saved['N'] == 1039 and len(docs) == 4, 'Method denominator differs')
            require(math.isclose(saved['equal_document_macro'], macro, abs_tol=1e-15) and saved['micro'] == counts['FULL']/n, 'Aggregate differs')
            require(collections.Counter(saved['counts']) == counts, 'Aggregate counts differ')
            if name == 'RECONCILED':
                metrics[method] = {'equal_document_macro': macro, 'micro': counts['FULL']/n}
                require(math.isclose(audit['metrics'][method]['equal_document_macro'], macro, abs_tol=1e-15), 'Saved audit differs')
    sample = read(RUN / 'stability-selection.json')
    require(len(sample) == len({r['id'] for r in sample}) == 629, 'Stability sample differs')
    for role in ['A', 'B']:
        identical = sum(views['INITIAL', role, r['case_id']][r['requirement_id']][0] == views['STABILITY', role, r['case_id']][r['requirement_id']][0] for r in sample)
        saved = stability['results'][role]
        require(saved['sampled'] == saved['completed'] == 629 and saved['identical_labels'] == identical and saved['agreement'] == identical/629, 'Stability counts differ')
    costs = read(PUB / 'call-index.json')
    for run, saved in costs['by_run'].items():
        calls = [c for c in costs['calls'] if c['run'] == run]
        require(len(calls) == len({c['request_id'] for c in calls}) == saved['recorded_calls'], 'Duplicate/missing call')
        known = 0; unknown = []
        for call in calls:
            if call['cost_cny'] is None:
                require(call['ledger_state'] == 'UNPRICED' and call['ledger_actual_micro_cny'] is None, 'Unknown cost became zero')
                unknown.append(call['request_id'])
            else:
                amount = int((decimal.Decimal(str(call['cost_cny']))*1000000).to_integral_value(rounding=decimal.ROUND_CEILING))
                require(call['ledger_state'] == 'SETTLED' and call['ledger_actual_micro_cny'] == amount, 'Known ledger amount differs')
                known += amount
        require(known == saved['known_ledger_micro_cny'] and sorted(unknown) == sorted(saved['unpriced_request_ids']), 'Cost summary differs')
        if unknown:
            require(saved['unpriced_amount_cny'] is None, 'Unknown amount missing')
    coverage_cost = costs['by_run'][str(RUN.relative_to(REPO))]
    require(coverage_cost['recorded_calls'] == verification['recorded_calls'] == 3102, 'Coverage call count differs')
    require(coverage_cost['known_ledger_micro_cny'] == audit['known_ledger_micro_cny'] == 214106696, 'Coverage known cost differs')
    require(coverage_cost['unpriced_request_ids'] == verification['unpriced_request_ids'] and len(coverage_cost['unpriced_request_ids']) == 1, 'Unknown coverage call changed')
    revision = read(local_path(release['final_coverage_revision']))
    for name, signature in revision['files'].items():
        require(file_hash(REPO/'eval/l3_coverage'/name) == signature, 'Final implementation differs from recorded revision')
    archive_files = None
    if with_archive:
        archive = read(PUB / 'archive-index.json'); check_file(archive['inventory'])
        archive_files = 0
        for line in local_path(archive['inventory']['path']).read_text().splitlines():
            check_file(json.loads(line)); archive_files += 1
        for entry in inputs['protected_files']:
            check_file(entry)
        for entry in [inputs['source_plan'], inputs['source_protection_record']]:
            check_file(entry)
        for snapshot in read(PUB/'configurations.json')['snapshots']:
            check_file(snapshot['source'])
    return {'status': 'PASS', 'cases': 378, 'protected_inputs': 433, 'requirements_per_method': 1039,
            'reference_statuses': dict(ref_counts), 'known_coverage_micro_cny': 214106696,
            'unpriced_coverage_calls': 1, 'unpriced_amount_cny': None, 'local_archive_files_checked': archive_files,
            'metrics': metrics}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-local-archive', action='store_true')
    print(json.dumps(verify(parser.parse_args().with_local_archive), ensure_ascii=False, indent=2))
