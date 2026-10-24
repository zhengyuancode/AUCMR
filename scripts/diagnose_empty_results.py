"""Read frozen results; explicitly opt in to isolated, single-slot diagnostic calls.

This never calls run_and_save or promotes diagnostic responses into formal results.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from contextlib import ExitStack
from unittest.mock import patch

ORDER = ['goal', 'primary_actor', 'preconditions', 'trigger', 'main_flow',
         'alternative_flow', 'exception_flow', 'postconditions']
METHODS = ['LVLM-Direct', 'TextRAG-Elaborate', 'VDocRAG-Elaborate',
           'ViDoRAG-Elaborate', 'ReAct-Elaborate']


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def checked_result(root, target):
    path = (root / target['relative_path']).resolve()
    path.relative_to(root.resolve())
    raw = path.read_bytes()
    digest = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    if digest != target['blob_sha']:
        raise ValueError('Result changed since audit: ' + target['relative_path'])
    d = json.loads(raw.decode('utf-8-sig'))
    for key in ('method', 'actor', 'use_case_name'):
        if d.get(key) != target[key]:
            raise ValueError('Result identity mismatch: ' + key)
    return path, raw, d


def prior_context(result, slot):
    return copy.deepcopy({k: result.get('slots', {})[k] for k in ORDER[:ORDER.index(slot)]
                          if k in result.get('slots', {})})


def classify(events, normalized, error_type):
    if error_type:
        return 'technical_failure'
    if normalized and (normalized.get('claims') or normalized.get('raw_text')):
        return 'nonempty_in_diagnostic_not_a_replacement'
    parses = [e for e in events if e['stage'] == 'parse']
    if not parses:
        return 'no_final_parse_observed'
    if parses[-1].get('error_type') or parses[-1].get('parsed') is None:
        return 'parse_failed_or_json_null'
    if parses[-1].get('raw_response', '').strip() == '{}':
        return 'explicit_empty_object'
    filters = [e for e in events if e['stage'] == 'whitelist']
    if filters and filters[-1]['before_claims'] > 0 and filters[-1]['after_claims'] == 0:
        return 'all_candidates_removed_by_citation_filter'
    return 'empty_after_normalization_needs_raw_review'


def exception_trace(exc):
    """Record stack locations only, never messages, source lines or local values."""
    chain, seen = [], set()
    while exc is not None and id(exc) not in seen and len(chain) < 5:
        seen.add(id(exc))
        frames = [{"file": Path(frame.f_code.co_filename).name,
                   "function": frame.f_code.co_name, "line": line}
                  for frame, line in traceback.walk_tb(exc.__traceback__)]
        chain.append({"type": type(exc).__name__, "frames": frames[-16:]})
        exc = exc.__cause__ if exc.__cause__ is not None else exc.__context__
    return chain


def capture(baselines, runner, slot, filled, context):
    events, normalized, error_type, trace = [], None, None, []
    parse = baselines._parse_json_with_repair
    whitelist = baselines._enforce_evidence_whitelist

    def parse_wrapper(raw):
        event = {'stage': 'parse', 'raw_response': raw}
        events.append(event)
        try:
            value = parse(raw)
            event['parsed'] = copy.deepcopy(value)
            return value
        except Exception as exc:
            event['error_type'] = type(exc).__name__
            raise

    def filter_wrapper(value, allowed):
        result = whitelist(value, allowed)
        events.append({'stage': 'whitelist', 'allowed_refs': list(allowed),
                       'before_claims': len(value.get('claims', []) or []),
                       'after_claims': len(result.get('claims', []) or []),
                       'pre_filter': copy.deepcopy(value)})
        return result

    with ExitStack() as stack:
        stack.enter_context(patch.object(baselines, '_parse_json_with_repair', parse_wrapper))
        stack.enter_context(patch.object(baselines, '_enforce_evidence_whitelist', filter_wrapper))
        for name in ('_call_lvlm', '_call_llm_text'):
            original = getattr(baselines, name)
            def wrap(*args, _fn=original, _name=name, **kwargs):
                event = {'stage': 'call', 'function': _name}
                if args and isinstance(args[0], str):
                    event['prompt_sha256'] = hashlib.sha256(args[0].encode()).hexdigest()
                events.append(event)
                try:
                    return _fn(*args, **kwargs)
                except Exception as exc:
                    event['error_type'] = type(exc).__name__
                    event['error_trace'] = exception_trace(exc)
                    raise
            stack.enter_context(patch.object(baselines, name, wrap))
        try:
            normalized = runner.slot_filler(slot, filled, context)
        except Exception as exc:
            error_type = type(exc).__name__
            trace = exception_trace(exc)
    swallowed = next((e['error_type'] for e in events if e.get('error_type')), None)
    error_type = error_type or swallowed
    trace = trace or next((e['error_trace'] for e in events if e.get('error_trace')), [])
    return {'events': events, 'normalized': normalized, 'error_type': error_type,
            'status': classify(events, normalized, error_type), 'error_trace': trace}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='experiment.junxi.unified93.json')
    parser.add_argument('--manifest', default='scripts/empty_results_targets.json')
    parser.add_argument('--scope', choices=['pilot', 'all'], default='pilot')
    parser.add_argument('--methods', nargs='+', choices=METHODS)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args(argv)
    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8-sig'))
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    root = Path(config['paths']['output'])
    if not root.is_absolute(): root = config_path.parent / root
    targets = [t for t in manifest['targets'] if args.scope == 'all' or t['pilot']]
    if args.methods:
        targets = [t for t in targets if t['method'] in args.methods]
    configured = {(c['project'], c['use_case'], c['actor']) for c in config['experiment']['use_cases']}
    for target in targets:
        if target['method'] not in METHODS or (target['project'],target['use_case_name'],target['actor']) not in configured:
            raise ValueError('Target not in allowed methods/configuration')
    # Check every source before any request. Never quietly test changed results.
    sources = [checked_result(root, t) for t in targets]
    print(json.dumps({'targets': len(targets), 'slot': 'main_flow', 'live': args.live,
                      'audit_commit': manifest['audit_commit']}, ensure_ascii=False), flush=True)
    if not args.live: return 0
    repo = Path(__file__).resolve().parents[1]
    os.environ['AUCMR_CONFIG'] = str(config_path)
    sys.path.insert(0, str(repo))
    import eval.baselines as baselines
    diagnostics = repo / 'tmp' / 'diagnostics'
    diagnostics.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix='empty-results-', dir=diagnostics))
    print('DIAGNOSTIC_DIRECTORY', out, flush=True)
    runners = {b.name: b for b in baselines.get_all_baselines(str(root/'baselines'), sorted({t['method'] for t in targets}))}
    summaries = []
    for index, (target, (path, raw, result)) in enumerate(zip(targets, sources), 1):
        record = {'target': target, 'reproduction': 'new_call_with_frozen_saved_prior_slots',
                  'original_sha256': hashlib.sha256(raw).hexdigest()}
        try:
            runner = runners[target['method']]
            runner._run_file_names = target['file_names']
            runner._evidence_records = {}
            if runner.requires_registry:
                baselines._init_registry(target['project'], target['file_names'])
            context = {k: target[k] for k in ('project', 'use_case_name', 'actor', 'method')}
            record['prior_slots'] = prior_context(result, 'main_flow')
            record.update(capture(baselines, runner, 'main_flow', copy.deepcopy(record['prior_slots']), context))
        except Exception as exc:
            record.update(status='setup_failure', error_type=type(exc).__name__, error_trace=exception_trace(exc))
        record['original_unchanged'] = path.read_bytes() == raw
        dump(out / f'{index:03d}.json', record)
        summaries.append({'index':index, 'method':target['method'], 'use_case':target['use_case_name'], 'status':record['status']})
        dump(out/'summary.json', summaries)
        print(json.dumps(summaries[-1], ensure_ascii=False), flush=True)
        if not record['original_unchanged']:
            raise RuntimeError('Source result changed during diagnosis; stopped')
        if record['status'] in ('setup_failure', 'technical_failure'):
            return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
