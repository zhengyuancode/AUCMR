#!/usr/bin/env python3
"""Offline L1 paper tables. Standard library only; never imports evaluation code.

Default: validate packaged observations and reproduce the five CSV files.
--from-source: rebuild the package from the explicitly selected final checkpoints.
--check-source: additionally verify the local source files indexed by this package.
No model, network, credentials, or source writes are used in any mode.
"""
import argparse
import ast
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[5]
SLOTS = ('goal', 'preconditions', 'trigger', 'main_flow', 'alternative_flow',
         'exception_flow', 'postconditions')
METHODS = ('AUCMR', 'CREST w/o cross-slot check', 'TextRAG-Elaborate',
           'VDocRAG-Elaborate', 'ViDoRAG-Elaborate', 'ReAct-Elaborate')
ROOTS = {
    'pilot': 'outputs/evaluation_preparation/pilot-l1-l4-v306-20261028/l1-workspace-r1/l1_checkpoints',
    'main': 'outputs/evaluation_preparation/full93-v306-20261028/l1-workspace/l1_checkpoints',
    'completion': 'outputs/evaluation_preparation/l1-repair-20261030/checkpoints',
}
COHORT = 'outputs/evaluation_preparation/formal-cohort-source-review-20261029/formal-cohort.json'
REPAIR = 'outputs/evaluation_preparation/l1-repair-20261030'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sealed(path):
    value = read(path)
    digest = hashlib.sha256(json.dumps(value['data'], sort_keys=True,
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    require(digest == value['sha256'], f'Seal mismatch: {path}')
    return value['data']


def relative(value):
    return str(Path(value).relative_to(REPO)) if str(value).startswith(str(REPO) + '/') else str(value)


def portable(value):
    if isinstance(value, dict):
        return {k: portable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [portable(v) for v in value]
    return relative(value) if isinstance(value, str) else value


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def build_source():
    cohort = read(REPO / COHORT)
    targets = {t['target_id']: t for t in cohort['targets']}
    require(len(targets) == 93 and all(t['decision'] == 'INCLUDE' for t in targets.values()), 'Cohort changed')
    records, materials, source_files, manifests, request_counts = [], [], {}, {}, {}

    def index(path, expected=None):
        path = Path(path)
        digest = sha(path)
        require(expected is None or expected == digest, f'Source hash mismatch: {path}')
        source_files[relative(path)] = digest
        return digest

    index(REPO / COHORT)
    for group, directory in ROOTS.items():
        root = REPO / directory
        manifest = sealed(root / 'manifest.json')
        index(root / 'manifest.json')
        manifests[group] = portable({k: v for k, v in manifest.items() if k not in ('bindings', 'control')})
        request_counts[group] = dict(Counter(sealed(f)['state'] for f in sorted((root / 'requests').glob('*.json'))))
        for path in sorted((root / 'materials').glob('*.json')):
            original = sealed(path)
            identity, metrics = original['identity'], original['metrics']
            index(path)
            index(identity['input_path'], identity['input_hash'])
            index(identity['evidence_path'], identity['evidence_hash'])
            uc = read(identity['input_path'])
            require(metrics['nli_evaluated'] and not metrics['nli_parse_failures'], f'Incomplete material: {path}')
            candidates = []
            audit = uc.get('citation_audit', {}).get('pre_constraint_slots', {})
            require(audit or metrics['total_claims'] == 0, f'Missing candidate audit: {path}')
            for slot in SLOTS:
                block = audit.get(slot, {})
                for ordinal, claim in enumerate(block.get('claims', []) or []):
                    if isinstance(claim, dict) and str(claim.get('content', '')).strip():
                        candidates.append((slot, ordinal, claim, block.get('exposed_evidence_refs', [])))
            require(len(candidates) == len(metrics['nli_details']) == metrics['total_claims'], f'Claim count: {path}')
            claims = []
            for (slot, ordinal, claim, exposed), detail in zip(candidates, metrics['nli_details']):
                require(detail['slot'] == slot and detail['claim'] == claim['content'][:100], f'Claim mapping: {path}')
                # Only dispatched/replayed judgments carry judgment_mode; no-ref cases are deterministic.
                cited = detail.get('judgment_mode') == 'joint_cited_evidence_plus_slot_fit'
                require(cited or detail['rationale'] == 'no valid, exposed evidence cited', f'Unknown judgment kind: {path}')
                claims.append({
                    'slot': slot, 'audit_ordinal': ordinal, 'content': claim['content'],
                    'authored_evidence_refs': claim.get('evidence_refs', []),
                    'exposed_evidence_refs': exposed, 'valid_citation': cited,
                    'judgment': {k: v for k, v in detail.items() if k not in ('claim', 'slot')},
                })
            final_slots = {s: uc.get('slots', {}).get(s, {}).get('claims', []) for s in SLOTS}
            require(sum(not x for x in final_slots.values()) == metrics['empty_slot_count'], f'Empty slots: {path}')
            target = targets[identity['target_id']]
            record = {
                'formal_case_id': target['formal_case_id'], 'target_id': identity['target_id'],
                'project_id': identity['project_id'], 'method': identity['method'],
                'actor': metrics['actor'], 'use_case_name': metrics['use_case_name'],
                'source_group': group, 'citation_audit_version': identity['citation_audit_version'],
                'material_path': relative(path), 'material_sha256': sha(path),
                'identity': portable(identity), 'final_slots': final_slots, 'claims': claims,
                'stored_counts': {k: metrics[k] for k in ('total_claims', 'total_cited_claims',
                    'total_entailed_claims', 'empty_slot_count')},
            }
            records.append(record)
            materials.append({k: record[k] for k in ('formal_case_id', 'target_id', 'project_id',
                'method', 'source_group', 'material_path', 'material_sha256', 'identity')})
    records.sort(key=lambda x: (x['formal_case_id'], METHODS.index(x['method'])))
    materials.sort(key=lambda x: (x['formal_case_id'], METHODS.index(x['method'])))
    # Audit the protected archive; no edits, repairs, or fee assumptions.
    protected_path = REPO / REPAIR / 'original-hashes.json'
    protected = read(protected_path)
    for path, expected in protected.items():
        require(sha(REPO / path) == expected, f'Protected history changed: {path}')
    index(protected_path)
    receipts = []
    for p in sorted((REPO / REPAIR / 'responses').glob('*.json')):
        data = sealed(p)
        response = data['response']
        index(p)
        receipts.append({'path': relative(p), 'sha256': sha(p), 'request_key': data['repair_request_key'],
                         'model': response.get('model'), 'usage': response.get('usage'),
                         'finish_reasons': [c['finish_reason'] for c in response['choices']],
                         'cost_cny': data.get('cost_cny'), 'cost_basis': data.get('cost_basis')})
    for source in cohort['source_documents']:
        index(REPO / source['pdf'], source['sha256'])
    implementation = {}
    for name in ('eval/compute_metrics.py', 'eval/l1_recording.py', 'eval/l1_repair.py',
                 'eval/legacy_contracts.py', 'config/settings.py', 'experiment.junxi.unified93.json'):
        implementation[name] = index(REPO / name)
    code = (REPO / 'eval/compute_metrics.py').read_text(encoding='utf-8')
    judge_class = next(n for n in ast.parse(code).body if isinstance(n, ast.ClassDef) and n.name == 'NLIJudge')
    judge_fn = next(n for n in judge_class.body if isinstance(n, ast.FunctionDef) and n.name == 'judge')
    template = next(n.value for n in ast.walk(judge_fn) if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'prompt' for t in n.targets))
    repair_code = (REPO / 'eval/l1_repair.py').read_text(encoding='utf-8')
    repair_fn = next(n for n in ast.parse(repair_code).body if isinstance(n, ast.FunctionDef) and n.name == 'repair_prompt')
    protocol = {'system_prompt': manifests['main']['system_prompt'],
        'user_template_python': ast.get_source_segment(code, template),
        'completion_request_presentation_python': ast.get_source_segment(repair_code, repair_fn),
        'endpoints': manifests['main']['endpoints'],
        'aggregation': 'micro_counts; N_qualified = entailment AND slot_fit',
        'slots': SLOTS, 'model_authority': 'automated judge, not human expert annotation'}
    (HERE / 'data').mkdir(parents=True, exist_ok=True)
    (HERE / 'data/final-records.jsonl').write_text(''.join(json.dumps(portable(r), ensure_ascii=False,
        separators=(',', ':'), allow_nan=False) + '\n' for r in records), encoding='utf-8')
    write_json(HERE / 'data/cohort.json', {k: cohort[k] for k in ('cohort_id', 'status',
        'selection_authority', 'review_kind', 'review_limitations', 'inclusion_criteria', 'source_documents', 'targets')})
    write_json(HERE / 'data/provenance.json', {'manifests': manifests,
        'source_request_record_counts_not_unique_api_calls': request_counts,
        'repair_receipts': receipts, 'protected_history_files_verified': len(protected),
        'protected_history_manifest': relative(protected_path),
        'implementation_files_at_packaging': implementation})
    write_json(HERE / 'data/protocol.json', protocol)
    write_json(HERE / 'material-index.json', {'path_base': 'AUCMR repository root',
        'materials': materials, 'source_files': source_files,
        'packaged_observations_sha256': sha(HERE / 'data/final-records.jsonl'),
        'packaged_cohort_sha256': sha(HERE / 'data/cohort.json'),
        'packaged_provenance_sha256': sha(HERE / 'data/provenance.json'),
        'packaged_protocol_sha256': sha(HERE / 'data/protocol.json')})


def counts(claims):
    labels = Counter(c['judgment']['entailment'] for c in claims)
    require(set(labels) <= {'entailment', 'neutral', 'contradiction'}, 'Invalid label')
    for c in claims:
        j = c['judgment']
        require(type(j['slot_fit']) is bool, 'Non-boolean slot_fit')
        require(j['qualified_grounding'] == (j['entailment'] == 'entailment' and j['slot_fit']), 'Qualification mismatch')
        require(not j['qualified_grounding'] or c['valid_citation'], 'Qualified without citation')
    return {'n_all': len(claims), 'n_cite': sum(c['valid_citation'] for c in claims),
            'n_qualified': sum(c['judgment']['qualified_grounding'] for c in claims),
            'source_entailed': labels['entailment'],
            'entailed_slot_misfit': sum(c['judgment']['entailment'] == 'entailment' and
                                      not c['judgment']['slot_fit'] for c in claims),
            'neutral': labels['neutral'], 'contradiction': labels['contradiction']}


def ratio(a, b):
    return round(100 * a / b, 8) if b else None


def summary(records, slot=None):
    claims = [c for r in records for c in r['claims'] if slot is None or c['slot'] == slot]
    c = counts(claims)
    slots = SLOTS if slot is None else (slot,)
    empty = sum(not r['final_slots'][s] for r in records for s in slots)
    return {'n_materials': len(records), **c,
            'citcov_pct': ratio(c['n_cite'], c['n_all']),
            'nli_g_pct': ratio(c['n_qualified'], c['n_all']),
            'chg_pct': ratio(c['n_cite'] - c['n_qualified'], c['n_cite']),
            'empty_final_slots': empty,
            'esc_per_material': round(empty / len(records), 8),
            'zero_audit_claim_materials': sum(not any(slot is None or c['slot'] == slot
                                                   for c in r['claims']) for r in records)}


def csv_text(rows):
    import io
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-source', action='store_true')
    parser.add_argument('--check-source', action='store_true')
    parser.add_argument('--verify-only', action='store_true', help='Compare packaged tables without writing')
    args = parser.parse_args()
    require(not (args.from_source and args.verify_only), 'Use one mode')
    if args.from_source:
        build_source()
    index = read(HERE / 'material-index.json')
    for name, key in [('final-records.jsonl', 'observations'), ('cohort.json', 'cohort'), ('provenance.json', 'provenance'), ('protocol.json', 'protocol')]:
        require(sha(HERE / 'data' / name) == index[f'packaged_{key}_sha256'], f'Package hash: {name}')
    records = [json.loads(line) for line in (HERE / 'data/final-records.jsonl').read_text(encoding='utf-8').splitlines()]
    cohort = read(HERE / 'data/cohort.json')
    targets = {t['target_id']: t for t in cohort['targets']}
    expected = {(t, m) for t in targets for m in METHODS}
    actual = [(r['target_id'], r['method']) for r in records]
    require(len(records) == 558 and len(set(actual)) == 558 and set(actual) == expected, 'Cohort coverage')
    for r in records:
        c = counts(r['claims'])
        require((c['n_all'], c['n_cite'], c['n_qualified']) == tuple(r['stored_counts'][k]
            for k in ('total_claims', 'total_cited_claims', 'total_entailed_claims')), f'Count reconciliation: {r["material_path"]}')
        require(all(x['slot'] in SLOTS for x in r['claims']), 'Unknown slot')
        require(sum(not x for x in r['final_slots'].values()) == r['stored_counts']['empty_slot_count'], 'Empty reconciliation')
    tables = {}
    tables['method-summary.csv'] = [{'method': m, **summary([r for r in records if r['method'] == m])} for m in METHODS]
    tables['slot-summary.csv'] = [{'method': m, 'slot': s, **summary([r for r in records if r['method'] == m], s)} for m in METHODS for s in SLOTS]
    tables['project-summary.csv'] = [{'project_id': p, 'method': m, **summary([r for r in records if r['method'] == m and r['project_id'] == p])}
        for p in sorted({r['project_id'] for r in records}) for m in METHODS]
    tables['case-method-results.csv'] = [{**{k: r[k] for k in ('formal_case_id', 'target_id', 'project_id', 'method', 'actor', 'use_case_name', 'source_group', 'citation_audit_version')},
        **summary([r]), 'material_path': r['material_path']} for r in records]
    tables['target-list.csv'] = [{k: t[k] for k in ('formal_case_id', 'target_id', 'project_id', 'actor', 'name', 'display_name_zh', 'source_pdf')} for t in cohort['targets']]
    for name, rows in tables.items():
        text = csv_text(rows)
        path = HERE / 'results' / name
        if args.verify_only:
            require(path.read_text(encoding='utf-8') == text, f'Table differs: {name}')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
    if args.check_source:
        for path, expected_hash in index['source_files'].items():
            require(sha(REPO / path) == expected_hash, f'Indexed source changed: {path}')
        protected = read(REPO / read(HERE / 'data/provenance.json')['protected_history_manifest'])
        for path, expected_hash in protected.items():
            require(sha(REPO / path) == expected_hash, f'Protected history changed: {path}')
    check = {'status': 'PASS', 'scope': 'Arithmetic, membership and stored observation integrity; not semantic ground truth',
             'materials': len(records), 'targets': len(targets), 'methods': len(METHODS),
             'source_groups': dict(sorted(Counter(r['source_group'] for r in records).items())),
             'claims': sum(len(r['claims']) for r in records),
             'zero_claim_materials': sum(not r['claims'] for r in records),
             'table_rows': {name: len(rows) for name, rows in tables.items()},
             'model_calls_by_this_script': 0,
             'packaged_observations_sha256': index['packaged_observations_sha256']}
    if args.verify_only:
        require(read(HERE / 'results/integrity-checks.json') == check, 'Integrity summary changed')
    else:
        write_json(HERE / 'results/integrity-checks.json', check)
    print(json.dumps({**check, 'local_sources_checked_this_run': args.check_source}, ensure_ascii=False))


if __name__ == '__main__':
    main()
