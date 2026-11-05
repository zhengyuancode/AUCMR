"""L2-only pre-dispatch concurrency wait; preserves the frozen V5 runtime/cache."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval import l2_rules_batch as b

CONTROL = Path(__file__).resolve().parent
OriginalRuntime = b.EvaluationRuntime
RESERVE_CODES = {
    'Concurrency limit': 'CONCURRENCY_LIMIT',
    'Unknown/incomplete billing upper bound': 'BILLING_BOUND_MISSING',
    'Unknown/invalid pricing': 'INVALID_PRICING',
    'Unresolved provider charge blocks new requests': 'UNRESOLVED_CHARGE',
    'Campaign budget exhausted': 'CAMPAIGN_BUDGET_EXHAUSTED',
    'Request already reserved/charged; reconcile before retry': 'REQUEST_ALREADY_RESERVED',
}


def event(kind, **fields):
    row = {'event': kind, 'at': b.now(), **fields}
    b.save_once(CONTROL / 'events' / (uuid.uuid4().hex + '.json'), row)
    print('L2_DISPATCH ' + json.dumps(row), flush=True)


class WaitingBudget:
    def __init__(self, budget, emit=event, sleep=time.sleep, clock=time.monotonic, max_wait=600):
        self.inner, self.emit, self.sleep, self.clock = budget, emit, sleep, clock
        self.max_wait = max_wait

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def reserve(self, request_id, provider, upper_bound):
        started = self.clock()
        waits = 0
        while True:
            try:
                # The original transaction remains the sole concurrency authority.
                self.inner.reserve(request_id, provider, upper_bound)
            except ValueError as exc:
                code = RESERVE_CODES.get(str(exc), 'RESERVATION_BLOCKED_UNCLASSIFIED')
                if code != 'CONCURRENCY_LIMIT':
                    self.emit('NOT_SENT', reason=code, request_id=request_id)
                    raise
                if waits == 0:
                    self.emit('WAITING_NOT_SENT', reason=code, request_id=request_id)
                if self.clock() - started >= self.max_wait:
                    self.emit('NOT_SENT', reason='CONCURRENCY_WAIT_TIMEOUT', request_id=request_id)
                    raise
                waits += 1
                self.sleep(min(5, max(0, self.max_wait - (self.clock() - started))))
            else:
                if waits:
                    self.emit('RESERVATION_ACQUIRED', request_id=request_id, polls=waits)
                return


class DiagnosticRuntime(OriginalRuntime):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.budget = WaitingBudget(self.budget)

    def request(self, messages, *, task_id, attempt=1):
        try:
            return super().request(messages, task_id=task_id, attempt=attempt)
        except b.RuntimeDeferred as exc:
            reasons = {
                'LIVE_NOT_AUTHORIZED': 'LIVE_NOT_AUTHORIZED',
                'MODEL_KEY_NOT_SET': 'MODEL_KEY_NOT_SET',
                'Campaign request blocked: budget, concurrency, existing request, or unknown charge': 'RESERVATION_BLOCKED_SEE_DISPATCH_EVENT',
            }
            event('NOT_SENT', task_id=task_id,
                  reason=reasons.get(str(exc), 'REQUEST_VALIDATION_BLOCKED'))
            raise


def run(authorized, expected_hash):
    if not authorized:
        raise b.Stop('LIVE_NOT_AUTHORIZED')
    policy_path = CONTROL / 'policy.json'
    if b.file_hash(policy_path) != expected_hash:
        raise b.Stop('CONTROL_FINGERPRINT_CHANGED')
    policy = b.read_json(policy_path)
    for p, h in policy['implementation'].items():
        if b.file_hash(ROOT / p) != h:
            raise b.Stop('CONTROL_IMPLEMENTATION_CHANGED')
    config_path = ROOT / policy['config']
    b.verify(b.read_json(config_path), config_path, policy['config_sha256'])
    spec = importlib.util.spec_from_file_location('frozen_l2_continue', ROOT / policy['entrypoint'])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    previous = b.EvaluationRuntime
    b.EvaluationRuntime = DiagnosticRuntime
    try:
        event('RESUME', strategy='WAIT_ONLY_BEFORE_DISPATCH', max_wait_seconds=600)
        return module.execute(config_path, policy['config_sha256'], True, 'batch')
    finally:
        b.EvaluationRuntime = previous


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--authorize-live', action='store_true')
    p.add_argument('--policy-sha256', required=True)
    args = p.parse_args()
    os.chdir(ROOT)
    try:
        code = run(args.authorize_live, args.policy_sha256)
    except Exception as exc:
        # Never print arbitrary exceptions or provider content.
        event('STOPPED', reason=str(exc) if isinstance(exc, b.Stop) else type(exc).__name__)
        code = 2
    else:
        event('FINISHED', exit_code=code)
    raise SystemExit(code)
