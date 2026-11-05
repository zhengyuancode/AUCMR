"""Three explicit supplementary inputs; unchanged L2 V5; never overwrite frozen inputs."""
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import l2_rules_batch as b

PREP = Path(__file__).resolve().parent
IDS = {'A-aca2c7b230a04b913fb0be0b', 'A-54ec13adff3b53f5b5ef92f5', 'A-ad8bfa559b014090b921cf1e'}


def bind(old, path):
    obj = b.read_json(path)
    if obj['use_case_name'] != old['task_input']['name'] or obj['actor'] != old['task_input']['primary_actor']:
        raise b.Stop('REGEN_IDENTITY_CHANGED')
    assertions = []
    for slot in b.SLOTS:
        for i, claim in enumerate(obj['slots'].get(slot, {}).get('claims', [])):
            if not claim['content'].strip(): raise b.Stop('EMPTY_CLAIM')
            row = {'assertion_id': f'{slot}:{i}', 'slot': slot, 'original_index': i, 'text': claim['content']}
            if 'step_number' in claim: row['step_number'] = claim['step_number']
            assertions.append(row)
    if not assertions: raise b.Stop('NO_REGENERATED_CONTENT')
    return {**old, 'input_path': str(path.relative_to(ROOT)), 'input_hash': b.file_hash(path),
            'expected_input_hash': b.file_hash(path), 'input_status': 'PRESENT', 'assertions': assertions,
            'primary_actor_slot': obj['slots']['primary_actor'], 'explicit_refusal': False,
            'empty_slots': [s for s in b.SLOTS if not obj['slots'].get(s, {}).get('claims')],
            'omitted_slots': [s for s in b.SLOTS if s not in obj['slots']],
            'input_batch_id': 'EXPLICIT_REGEN_SUPPLEMENT_20261030',
            'supersedes_input_hash': old['input_hash'], 'supersedes_input_path': old['input_path']}


def run(authorized, expected_hash):
    if not authorized: raise b.Stop('LIVE_NOT_AUTHORIZED')
    pp = PREP / 'policy.json'
    if b.file_hash(pp) != expected_hash: raise b.Stop('POLICY_CHANGED')
    policy = b.read_json(pp)
    def verify():
        for p, h in policy['protected_files'].items():
            if b.file_hash(ROOT / p) != h: raise b.Stop('PROTECTED_FILE_CHANGED:' + p)
        cp = ROOT / policy['config']
        b.verify(b.read_json(cp), cp, policy['config_sha256'])
    verify()
    c = b.read_json(ROOT / policy['config'])
    gate = b.read_signed(ROOT / c['run_dir'] / 'validation.json')
    if not gate['passed'] or gate['config_sha256'] != policy['config_sha256']: raise b.Stop('V5_NOT_VALIDATED')
    artifacts = b.read_json(PREP / 'artifacts.json')
    if len(artifacts) != 3 or {a['artifact_id'] for a in artifacts} != IDS: raise b.Stop('SCOPE_CHANGED')
    root = ROOT / policy['run_dir']; root.mkdir(parents=True, exist_ok=True)
    with (root / 'execution.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        spec = importlib.util.spec_from_file_location('wait_only', ROOT / policy['waiting_module'])
        wait = importlib.util.module_from_spec(spec); spec.loader.exec_module(wait)
        def emit(kind, **fields):
            row = {'event': kind, 'at': b.now(), **fields}
            b.save_once(root / 'events' / (uuid.uuid4().hex + '.json'), row)
            print('L2_SUPPLEMENT ' + json.dumps(row), flush=True)
        runtime = b.EvaluationRuntime(c['model'], root / 'batch', 'L2RULES', ROOT / c['ledger'], live_authorized=True, http_timeout_seconds=180)
        runtime.budget = wait.WaitingBudget(runtime.budget, emit=emit)
        for a in artifacts:
            verify()
            path = root / 'records' / (a['artifact_id'] + '.json')
            if path.exists():
                row = b.read_signed(path)
                if row['input_hash'] != a['input_hash'] or row['policy_sha256'] != expected_hash: raise b.Stop('RECORD_CHANGED')
            else:
                groups = [b.run_group(runtime, root / 'batch', a, g, c['model']['model_id']) for g in b.GROUPS]
                violations = [v for g in groups for v in g['violations']]
                complete = all(g['status'] == 'COMPLETE' for g in groups)
                row = b.save_once(path, {'artifact_id': a['artifact_id'], 'target_id': a['task_id'], 'method': a['method'],
                    'name': a['task_input']['name'], 'input_hash': a['input_hash'], 'input_path': a['input_path'],
                    'input_status': a['input_status'], 'config_sha256': policy['config_sha256'], 'policy_sha256': expected_hash,
                    'groups': [{'record': g, 'origin': 'REGEN_SUPPLEMENT'} for g in groups], 'violations': violations,
                    'rule_resolution': {r: x for g in groups for r, x in g['rule_resolution'].items()},
                    'status': 'COMPLETE' if complete else 'TECHNICAL_FAILURE',
                    'counts': {r: sum(v['rule_id'] == r for v in violations) for r in b.RULES} if complete else None,
                    'supersedes_input_hash': a['supersedes_input_hash'], 'provenance': 'ONE_REGEN_THEN_UNCHANGED_V5_L2'})
            emit('ITEM_SAVED', artifact_id=a['artifact_id'], status=row['status'], **b.costs(root / 'batch'))
        verify()
        old = b.read_signed(ROOT / policy['original_result_manifest'])
        selected = []
        for item in old['records']:
            path = root / 'records' / (item['artifact_id'] + '.json') if item['artifact_id'] in IDS else Path(item['path'])
            row = b.read_signed(path)
            selected.append({'artifact_id': item['artifact_id'], 'path': str(path), 'sha256': row['sha256']})
        rows = [b.read_signed(Path(x['path'])) for x in selected]
        all_artifacts = b.read_json(ROOT / c['artifacts'])
        replacement = {a['artifact_id']: a for a in artifacts}
        all_artifacts = [replacement.get(a['artifact_id'], a) for a in all_artifacts]
        tables = b.summarize(rows, all_artifacts)
        note = 'Explicit three-input regeneration supplement; original frozen results retained; not a uniform rerun or automatic promotion to the original cohort.'
        b.save_once(root / 'result-manifest.json', {'records': selected, 'policy_sha256': expected_hash, 'provenance': note})
        b.save_once(root / 'tables.json', {**tables, 'provenance': note})
        final = {'planned': len(rows), 'completed': sum(r['status'] == 'COMPLETE' for r in rows),
                 'technical_failures': sum(r['status'] == 'TECHNICAL_FAILURE' for r in rows),
                 'no_evaluable_content': sum(r['status'] == 'NO_EVALUABLE_CONTENT' for r in rows),
                 'common_cases': len(tables['common_target_ids']), 'new_costs': b.costs(root / 'batch'),
                 'prior_l2_responses': 1269, 'prior_l2_estimated_cny': '3.223284', 'historical_unknown_requests': 1,
                 'protected_files_verified': len(policy['protected_files']), 'provenance': note}
        b.save_once(root / 'final.json', final); emit('FINISHED', **final)


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--authorize-live', action='store_true'); p.add_argument('--policy-sha256', required=True)
    args = p.parse_args(); os.chdir(ROOT)
    try: run(args.authorize_live, args.policy_sha256)
    except Exception as exc:
        print('L2_STOPPED ' + (str(exc) if isinstance(exc, b.Stop) else type(exc).__name__), flush=True)
        raise SystemExit(2)
