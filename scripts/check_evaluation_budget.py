"""Offline regression or read-only binding audit. No live mode and no ledger creation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from contextlib import ExitStack
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SUITES = {
    'billing_runtime': ['tests/test_evaluation_billing_budget.py', 'tests/test_evaluation_runtime.py'],
    'l3': ['tests/agent_evaluation'], 'l2': ['tests/agent_l2_independent'], 'l4': ['tests/agent_l4'],
    'legacy_and_cohort': ['tests/test_evaluation_audit_regressions.py', 'tests/test_paper_evaluation.py',
                          'tests/test_partial_evaluation.py', 'tests/test_serialized_slot_compatibility.py',
                          'tests/test_evaluation_batch.py', 'tests/test_configured_evaluation_cohort.py']}


def audit(draft):
    from eval.evaluation_billing import validate_billing, amount
    from eval.evaluation_request_budget import validate_limits, check_request
    from eval.agent_evaluation.common import read_json
    paths = {layer: draft / (layer + '.json') for layer in ('l2', 'l3', 'l4')}
    before = {name: hashlib.sha256(p.read_bytes()).hexdigest() for name, p in paths.items()}
    configs = {name: read_json(p) for name, p in paths.items()}
    bindings = {'L2': configs['l2']['model'], **{'L3_' + k: configs['l3']['models'][k] for k in ('A', 'B', 'C')},
                'L4': configs['l4']['model']}
    findings = {}
    for name, binding in bindings.items():
        problems = []
        if binding.get('binding_status') != 'BOUND':
            problems.append('MODEL_NOT_BOUND')
        env = binding.get('api_key_env')
        key_present = bool(isinstance(env, str) and os.environ.get(env))
        if not key_present:
            problems.append('KEY_NOT_SET')
        # Keep independent missing conditions visible in one report.
        for field in ('complete', 'verified_upper_bound', 'all_charges_in_token_usage'):
            if binding.get('billing', {}).get(field) is not True:
                problems.append('billing.' + field)
        for field in ('tariff', 'usage_contract'):
            if not isinstance(binding.get('billing', {}).get(field), dict):
                problems.append('billing.' + field)
        if type(binding.get('billing', {}).get('reasoning_in_output')) is not bool:
            problems.append('billing.reasoning_in_output')
        for label, validator in (('billing', validate_billing), ('request_limits', validate_limits)):
            try:
                validator(binding)
            except (ValueError, TypeError, KeyError) as exc:
                problems.append(label + ': ' + str(exc))
        bounds = binding.get('per_request_upper_bound', {})
        for component in ('input', 'output', 'reasoning', 'images', 'other'):
            try:
                amount(bounds.get(component))
            except ValueError:
                problems.append('per_request_upper_bound.' + component)
        if name != 'L2':
            if binding.get('capabilities', {}).get('images') is not True:
                problems.append('IMAGE_CAPABILITY_NOT_BOUND')
            limits = binding.get('request_limits', {})
            for key in ('image_tokens_by_detail', 'max_images', 'max_image_pixels', 'max_image_bytes', 'max_image_base64_bytes'):
                if not limits.get(key):
                    problems.append('request_limits.' + key)
        smoke = None
        try:
            smoke = check_request(binding, [{'role': 'user', 'content': 'OFFLINE_BOUND_CHECK_ONLY'}])
        except (ValueError, TypeError, KeyError) as exc:
            problems.append('request_check: ' + str(exc))
        findings[name] = {'model': binding.get('api_model_id', binding.get('model_id')),
                          'key_env': env, 'key_present': key_present,
                          'missing_conditions': list(dict.fromkeys(problems)),
                          'constructed_text_request_check': smoke}
    unchanged = all(hashlib.sha256(paths[name].read_bytes()).hexdigest() == h for name, h in before.items())
    if not unchanged:
        raise RuntimeError('Draft changed concurrently during read-only check')
    return {'audit_status': 'COMPLETE', 'billing_request_bindings_ready': all(not r['missing_conditions'] for r in findings.values()),
            'live_start_authorized': False, 'models': findings,
            'draft_hashes': before, 'draft_unchanged': unchanged, 'files_written': 0,
            'ledger_created': False, 'real_model_calls': 0,
            'remaining_nonbilling_checks': ['model capability/JSON fixtures', 'semantic calibration and pilot acceptance',
                                           'full current request material checks', 'campaign projection and explicit live authorization'],
            'note': 'Constructed text check is not full-task readiness. No model/account capability or provider tokenizer was tested.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--draft', type=Path)
    mode.add_argument('--offline-tests', action='store_true')
    mode.add_argument('--offline-suite', choices=list(SUITES), help=argparse.SUPPRESS)
    args = parser.parse_args()
    with ExitStack() as stack:
        for target, name in ((socket, 'create_connection'), (socket, 'getaddrinfo'),
                             (socket.socket, 'connect'), (socket.socket, 'connect_ex'), (socket.socket, 'sendto')):
            stack.enter_context(patch.object(target, name, side_effect=AssertionError('OFFLINE_NETWORK_DENIED')))
        if args.draft:
            print(json.dumps(audit(args.draft.resolve()), ensure_ascii=False, indent=2))
            return 0
        if args.offline_suite:
            import pytest
            os.chdir(ROOT)
            return pytest.main(['-q', '--import-mode=importlib', '-p', 'no:cacheprovider', *SUITES[args.offline_suite]])
        # Import-isolation tests deliberately forbid other evaluation layers.
        # Each suite therefore starts in a fresh, network-denied interpreter.
        results = []
        for name in SUITES:
            code = subprocess.call([sys.executable, '-B', str(Path(__file__).resolve()), '--offline-suite', name], cwd=ROOT)
            results.append({'suite': name, 'exit_code': code})
        print(json.dumps({'offline_suites': results, 'real_model_calls': 0, 'network': 'DENIED'}, indent=2))
        return 0 if all(r['exit_code'] == 0 for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
