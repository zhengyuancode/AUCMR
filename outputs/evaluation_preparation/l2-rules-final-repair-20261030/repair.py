"""Two fixed L2 failure dispositions; unchanged V5 rules, successful records and ledger."""
import argparse
import copy
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
QUOTE_ID = 'A-47f541a5f62941ae92f33e7a'
INDEX_ID = 'A-ce63f251373d1c7e9cc0e0a9'


def escape_known_quotes(raw):
    # Fixed known response only, not a general permissive JSON parser.
    before = 'The "undo" action is unavailable'
    after = 'The \\"undo\\" action is unavailable'
    if raw.count(before) != 1:
        raise b.Stop('EXPECTED_QUOTE_DEFECT_CHANGED')
    repaired = raw.replace(before, after)
    if repaired.replace(after, before) != raw:
        raise b.Stop('NON_FORMAT_CHANGE')
    return repaired


def audit_empty(a, old):
    obj = b.read_json(ROOT / a['input_path'])
    if b.file_hash(ROOT / a['input_path']) != a['input_hash']:
        raise b.Stop('INPUT_CHANGED')
    slots = obj.get('slots', {})
    counts = {s: len(slots.get(s, {}).get('claims', [])) for s in b.SLOTS}
    raw = {s: bool(slots.get(s, {}).get('raw_text', '').strip()) for s in b.SLOTS}
    pre = sum(len(v.get('claims', [])) for s, v in obj.get('citation_audit', {}).get('pre_constraint_slots', {}).items() if s in b.SLOTS)
    flags = [x for x in obj.get('quality_flags', []) if x.get('flag_type') in ('goal_evidence_missing', 'unresolvable_use_case')]
    if any(counts.values()) or any(raw.values()) or pre:
        raise b.Stop('EMPTY_CLASSIFICATION_REQUIRES_REVIEW')
    status = 'EXPLICIT_REFUSAL' if any(x.get('flag_type') == 'unresolvable_use_case' for x in flags) else 'VALID_EMPTY'
    if status != old['input_status'] or old['counts'] is not None:
        raise b.Stop('EMPTY_CLASSIFICATION_MISMATCH')
    return {'artifact_id': a['artifact_id'], 'input_path': a['input_path'], 'input_hash': a['input_hash'],
            'status': status, 'seven_slot_claim_counts': counts, 'raw_text_present': raw,
            'pre_constraint_claims': pre, 'explicit_generation_flags': flags,
            'conclusion': 'NO_RECOVERABLE_EVALUATION_CONTENT_NOT_ZERO_VIOLATIONS'}


def rebuild(old, group, policy_hash):
    new = copy.deepcopy({k: v for k, v in old.items() if k != 'sha256'})
    for wrapper in new['groups']:
        if wrapper['record']['group'] == 'lateral':
            wrapper.update(record=group, origin=group['repair_origin'])
    new['violations'] = [v for x in new['groups'] for v in x['record']['violations']]
    new['status'] = 'COMPLETE' if all(x['record']['status'] == 'COMPLETE' for x in new['groups']) else 'TECHNICAL_FAILURE'
    new['counts'] = {r: sum(v['rule_id'] == r for v in new['violations']) for r in b.RULES} if new['status'] == 'COMPLETE' else None
    new['supersedes_record_sha256'] = old['sha256']
    new['repair_policy_sha256'] = policy_hash
    new['provenance'] = 'V5_ORIGINAL_WITH_EXPLICIT_FAILURE_ONLY_DISPOSITION'
    return new


def run(expected_hash, authorized):
    if not authorized:
        raise b.Stop('LIVE_NOT_AUTHORIZED')
    pp = PREP / 'policy.json'
    if b.file_hash(pp) != expected_hash:
        raise b.Stop('REPAIR_POLICY_CHANGED')
    policy = b.read_json(pp)
    for p, h in policy['protected_files'].items():
        if b.file_hash(ROOT / p) != h:
            raise b.Stop('PROTECTED_FILE_CHANGED:' + p)
    cp = ROOT / policy['original_config']
    c = b.read_json(cp); b.verify(c, cp, policy['original_config_sha256'])
    root = ROOT / policy['run_dir']; root.mkdir(parents=True, exist_ok=True)
    with (root / 'execution.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        artifacts = b.read_json(ROOT / c['artifacts']); by = {a['artifact_id']: a for a in artifacts}
        original = ROOT / c['run_dir']
        rows = {a['artifact_id']: b.read_signed(original / 'records' / (a['artifact_id'] + '.json')) for a in artifacts}
        if {k for k, r in rows.items() if r['status'] == 'TECHNICAL_FAILURE'} != {QUOTE_ID, INDEX_ID}:
            raise b.Stop('REPAIR_SCOPE_CHANGED')
        audits = [audit_empty(by[k], r) for k, r in rows.items() if r['status'] == 'NO_EVALUABLE_CONTENT']
        if len(audits) != 5: raise b.Stop('EMPTY_SCOPE_CHANGED')
        b.save_once(root / 'classification-review.json', {'items': audits})
        old = rows[QUOTE_ID]
        g = next(x['record'] for x in old['groups'] if x['record']['group'] == 'lateral')
        raw = g['receipt']['body']
        if b.digest(raw) != policy['quote_response_hash']: raise b.Stop('QUOTE_RESPONSE_CHANGED')
        fixed = escape_known_quotes(raw)
        violations = b.parse_group(fixed, by[QUOTE_ID], 'lateral')
        corrected = {k: v for k, v in g.items() if k != 'sha256'}
        corrected.update(status='COMPLETE', violations=violations,
                         counts={r: sum(v['rule_id'] == r for v in violations) for r in b.GROUPS['lateral']},
                         repair_origin='OFFLINE_EXACT_QUOTE_ESCAPE_ONLY', model_calls=0,
                         corrected_body=fixed, error=None, original_error=g.get('error'), original_group_sha256=g['sha256'])
        # Retain the original rejected receipt and runtime state as historical evidence.
        corrected['repair_note'] = 'Original receipt remains rejected; corrected_body was separately parsed without changing judgment fields.'
        corrected = b.save_once(root / 'offline-group.json', corrected)
        rows[QUOTE_ID] = b.save_once(root / 'records' / (QUOTE_ID + '.json'), rebuild(old, corrected, expected_hash))
        old = rows[INDEX_ID]; a = by[INDEX_ID]
        if b.file_hash(ROOT / a['input_path']) != a['input_hash']: raise b.Stop('INPUT_CHANGED')
        spec = importlib.util.spec_from_file_location('l2_wait', ROOT / policy['waiting_module'])
        wait = importlib.util.module_from_spec(spec); spec.loader.exec_module(wait)
        def emit(kind, **fields):
            row = {'event': kind, 'at': b.now(), **fields}
            b.save_once(root / 'events' / (uuid.uuid4().hex + '.json'), row)
            print('L2_DISPATCH ' + json.dumps(row), flush=True)
        runtime = b.EvaluationRuntime(c['model'], root / 'batch', 'L2RULES', ROOT / c['ledger'], live_authorized=True, http_timeout_seconds=180)
        runtime.budget = wait.WaitingBudget(runtime.budget, emit=emit)
        replacement = b.run_group(runtime, root / 'batch', a, 'lateral', c['model']['model_id'])
        replacement = {**replacement, 'repair_origin': 'ONE_NEW_V5_LATERAL_REQUEST', 'original_group_sha256': next(x['record']['sha256'] for x in old['groups'] if x['record']['group'] == 'lateral')}
        replacement.pop('sha256', None)
        replacement['sha256'] = b.digest(replacement)
        rows[INDEX_ID] = b.save_once(root / 'records' / (INDEX_ID + '.json'), rebuild(old, replacement, expected_hash))
        selected = [{'artifact_id': k, 'path': str((root if k in (QUOTE_ID, INDEX_ID) else original) / 'records' / (k + '.json')), 'sha256': r['sha256']} for k, r in rows.items()]
        b.save_once(root / 'result-manifest.json', {'records': selected, 'original_config_sha256': policy['original_config_sha256'], 'repair_policy_sha256': expected_hash})
        b.write_json(root / 'tables.json', {**b.summarize(list(rows.values()), artifacts), 'provenance': 'Original V2/V3/V4/V5 response sources retained, with two explicitly identified failure dispositions; not a uniform rerun.'})
        result = {'completed': sum(r['status'] == 'COMPLETE' for r in rows.values()),
                  'technical_failures': sum(r['status'] == 'TECHNICAL_FAILURE' for r in rows.values()),
                  'no_evaluable_content': len(audits), 'planned': len(artifacts),
                  'new_costs': b.costs(root / 'batch'), 'original_estimated_cny': '3.219234',
                  'historical_unknown_requests': 1}
        b.save_once(root / 'final.json', result)
        print('L2_REPAIR ' + json.dumps(result), flush=True)
        return 0 if not result['technical_failures'] else 2


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--authorize-live', action='store_true'); p.add_argument('--policy-sha256', required=True)
    args = p.parse_args(); os.chdir(ROOT)
    try: code = run(args.policy_sha256, args.authorize_live)
    except Exception as exc:
        print('L2_STOPPED ' + (str(exc) if isinstance(exc, b.Stop) else type(exc).__name__), flush=True)
        code = 2
    raise SystemExit(code)
