"""Explicit V2/V3 reuse and bounded V5 continuation. No historical rewrites."""
import argparse
import copy
import fcntl
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import l2_rules_batch as b


def complete(a, runtime, root, config_hash, previous):
    path = root / 'records' / (a['artifact_id'] + '.json')
    if path.exists():
        r = b.read_signed(path)
        if r['input_hash'] != a['input_hash'] or r['config_sha256'] != config_hash:
            raise b.Stop('RECORD_IDENTITY_CHANGED')
        return r
    if b.file_hash(ROOT / a['input_path']) != a['input_hash']:
        raise b.Stop('INPUT_CHANGED')
    r = {'artifact_id': a['artifact_id'], 'target_id': a['task_id'], 'method': a['method'],
         'name': a['task_input']['name'], 'input_hash': a['input_hash'], 'input_path': a['input_path'],
         'input_status': a['input_status'], 'config_sha256': config_hash,
         'counts': None, 'groups': [], 'violations': [], 'rule_resolution': {},
         'provenance': 'EXPLICIT_HISTORICAL_RESPONSE_REUSE_WITH_V5_REQUESTS_NOT_UNIFORM_RERUN'}
    if a['input_status'] in ('VALID_EMPTY', 'EXPLICIT_REFUSAL'):
        r['status'] = 'NO_EVALUABLE_CONTENT'
    elif a['input_status'] != 'PRESENT':
        raise b.Stop('INVALID_INPUT')
    else:
        old = {x['record']['group']: x['record'] for x in previous.get(a['artifact_id'], {}).get('groups', [])}
        for group in b.GROUPS:
            g = old.get(group)
            if g and g['status'] == 'COMPLETE':
                if g['sha256'] != b.digest({k: v for k, v in g.items() if k != 'sha256'}):
                    raise b.Stop('HISTORICAL_GROUP_CHANGED')
                if g.get('receipt') is not None:
                    if b.parse_group(g['receipt']['body'], a, group) != g['violations']:
                        raise b.Stop('HISTORICAL_RESPONSE_REPLAY_CHANGED')
                elif (g.get('result_basis') != 'INPUT_APPLICABILITY_AND_LITERAL_EVIDENCE'
                      or g.get('model_calls') != 0 or g['violations'] != []
                      or g.get('rule_resolution') != b.rule_plan(a, group)
                      or any(v['state'] == 'MODEL_CHECK' for v in b.rule_plan(a, group).values())
                      or g.get('counts') != dict.fromkeys(b.GROUPS[group], 0)):
                    raise b.Stop('HISTORICAL_INPUT_DISPOSITION_CHANGED')
                origin = 'REUSED_HISTORICAL_RESPONSE'
            else:
                g = b.run_group(runtime, root / 'batch', a, group, runtime.binding['model_id'])
                origin = 'NEW_V5_INPUT_DISPOSITION' if g.get('model_calls') == 0 else 'NEW_V5_REQUEST'
            r['groups'].append({'record': g, 'origin': origin})
            r['violations'].extend(g['violations'])
            r['rule_resolution'].update(b.rule_plan(a, group))
        r['status'] = 'COMPLETE' if all(g['record']['status'] == 'COMPLETE' for g in r['groups']) else 'TECHNICAL_FAILURE'
        if r['status'] == 'COMPLETE':
            r['counts'] = {rule: sum(v['rule_id'] == rule for v in r['violations']) for rule in b.RULES}
    return b.save_once(path, r)


def execute(cpath, chash, authorized, phase):
    if not authorized:
        raise b.Stop('LIVE_NOT_AUTHORIZED')
    c = b.read_json(cpath); b.verify(c, cpath, chash)
    root = ROOT / c['run_dir']; root.mkdir(parents=True, exist_ok=True)
    with (root / 'execution.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        artifacts = b.read_json(ROOT / c['artifacts'])
        if len(artifacts) != 651 or len({(a['task_id'], a['method']) for a in artifacts}) != 651:
            raise b.Stop('SCOPE_CHANGED')
        for a in artifacts:
            if b.file_hash(ROOT / a['input_path']) != a['input_hash']:
                raise b.Stop('INPUT_CHANGED')
        previous = {r['artifact_id']: r for r in (b.read_signed(ROOT / p) for p in c['resume_records'])}
        runtime = b.EvaluationRuntime(c['model'], root / 'batch', 'L2RULES', ROOT / c['ledger'], live_authorized=True, http_timeout_seconds=180)
        if phase == 'validate':
            cal = b.EvaluationRuntime(c['model'], root / 'calibration', 'L2RULES', ROOT / c['ledger'], live_authorized=True, http_timeout_seconds=180)
            positive = b.run_group(cal, root / 'calibration', c['positive_control'], 'lateral', c['model']['model_id'])
            passed = positive['status'] == 'COMPLETE' and {v['rule_id'] for v in positive['violations']} == set(b.GROUPS['lateral'])
            checked = []
            for a in artifacts:
                if a['artifact_id'] in c['validation_targets']:
                    b.verify(c, cpath, chash)
                    r = complete(a, runtime, root, chash, previous)
                    checked.append({'artifact_id': a['artifact_id'], 'status': r['status']})
                    passed &= r['status'] == 'COMPLETE'
            if len(checked) != 5: raise b.Stop('VALIDATION_SCOPE_CHANGED')
            result = {'passed': passed, 'config_sha256': chash, 'checked': checked,
                      'positive_control_passed': positive['status'] == 'COMPLETE' and {v['rule_id'] for v in positive['violations']} == set(b.GROUPS['lateral']),
                      'validation_requests': b.costs(root / 'batch'), 'calibration_requests': b.costs(root / 'calibration')}
            b.save_once(root / 'validation.json', result)
            print('L2_VALIDATION ' + json.dumps(result), flush=True)
            return 0 if passed else 2
        gate = b.read_signed(root / 'validation.json')
        if not gate['passed'] or gate['config_sha256'] != chash:
            raise b.Stop('VALIDATION_NOT_PASSED')
        rows = []; failures = consecutive = 0
        for a in artifacts:
            b.verify(c, cpath, chash)
            r = complete(a, runtime, root, chash, previous); rows.append(r)
            failures += r['status'] == 'TECHNICAL_FAILURE'
            consecutive = consecutive + 1 if r['status'] == 'TECHNICAL_FAILURE' else 0
            progress = {'processed': len(rows), 'completed': sum(r['status'] == 'COMPLETE' for r in rows),
                        'technical_failures': failures, 'no_evaluable_content': sum(r['status'] == 'NO_EVALUABLE_CONTENT' for r in rows),
                        'unprocessed': 651 - len(rows), 'consecutive_failures': consecutive,
                        'last_artifact_id': a['artifact_id'], 'new_v5_costs': b.costs(root / 'batch'),
                        'historical_costs': c['historical_costs']}
            b.write_json(root / 'progress.json', progress)
            b.write_json(root / 'tables.json', {**b.summarize(rows, artifacts),
                'provenance': 'Historical requests and V5 requests explicitly identified; not a uniform-version rerun.'})
            print('L2_PROGRESS ' + json.dumps(progress), flush=True)
            if failures >= 10 or consecutive >= 3:
                raise b.Stop('TECHNICAL_FAILURE_THRESHOLD')
        b.write_json(root / 'final.json', {'status': 'FINISHED', **progress})
        return 0


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--config', type=Path, required=True)
    p.add_argument('--config-sha256', required=True); p.add_argument('--authorize-live', action='store_true')
    p.add_argument('--phase', choices=['validate', 'batch'], required=True)
    args = p.parse_args(); os.chdir(ROOT)
    try:
        raise SystemExit(execute(args.config, args.config_sha256, args.authorize_live, args.phase))
    except Exception as exc:
        reason = str(exc) if isinstance(exc, b.Stop) else type(exc).__name__
        print('L2_STOPPED ' + reason, flush=True)
        root = ROOT / b.read_json(args.config)['run_dir']
        if root.exists(): b.write_json(root / 'last-stop.json', {'reason': reason, 'at': b.now()})
        raise SystemExit(2)
