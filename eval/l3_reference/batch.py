"""Build the remaining PURE references once; adopt the two user-approved pilots.

No generation outputs or coverage scores are inputs to this entry point.
"""
import argparse
import copy
import fcntl
import json
import os
import re
import threading
import time
from pathlib import Path

from eval.agent_evaluation.common import (
    REPO, append_jsonl, canonical, digest, file_hash, now, read_json, write_json,
)
from eval.agent_evaluation.budget import Budget
from . import VERSION
from .acceptance import AcceptanceTransport
from .runner import PREPARATION, PILOTS, STAGES, task, freeze
from .validation import require, confined, source_context, validate_frozen
from .batch_failures import current_failures, record as failure_record, fatal_error, inventory

APPROVED = REPO / 'docs/codex_evaluation/l3/20261102-agent-final-v1'
BINDINGS = REPO / 'outputs/agent_evaluation/l3-reference-acceptance-20261031-v1/config.snapshot.json'
DEFAULT_RUN = REPO / 'outputs/agent_evaluation/l3-pure54-agent-final-20261102-v1'
SCHEMA = 'AUCMR_L3_COMMON_REFERENCES_1.0'


def locked(path, value):
    if path.exists():
        require(read_json(path) == value, 'Snapshot changed; resume refused: ' + str(path))
    else:
        write_json(path, value)


def initial_images(ctx):
    """Bootstrap two relevant pages; full text and all page tools remain available."""
    ignored = {'a', 'an', 'the', 'of', 'to', 'and', 'or', 'in', 'on', 'with', 'for', 'from'}
    terms = set(re.findall(r'[a-z0-9]+', ctx['target']['name'].casefold())) - ignored
    ranked = sorted(ctx['pages'], key=lambda p: (
        -sum(len(re.findall(r'\b' + re.escape(t) + r'\b', ctx['pages'][p].casefold())) for t in terms), p))
    return sorted(ranked[:2])


def prepare(root, *, bundle=None, approved=None, binding_file=None):
    bundle = bundle or PREPARATION / 'stage1-source-only'
    approved = approved or APPROVED
    binding_file = binding_file or BINDINGS
    data = read_json(bundle / 'targets.json')
    ids = [t['target_id'] for t in data['targets']]
    require(len(ids) == 54 and len(set(ids)) == 54, 'Expected exactly 54 unique PURE targets')
    adopted = read_json(approved / 'manifest.json')
    seed_rows = adopted['references']
    require({x['target_id'] for x in seed_rows} == set(PILOTS), 'Approved pilot set changed')
    seeds = {}
    for row in seed_rows:
        path = approved / (row['target_id'] + '.json')
        require(file_hash(path) == row['sha256'], 'Approved pilot hash changed')
        seeds[row['target_id']] = read_json(path)
    contexts = {tid: source_context(bundle, tid) for tid in ids}
    for tid, ref in seeds.items():
        require(ref['source_sha256'] == contexts[tid]['source']['pdf_sha256'], 'Pilot source changed')
        require(ref['denominator'] == len(ref['requirements']), 'Pilot denominator mismatch')
        require(len({r['requirement_id'] for r in ref['requirements']}) == len(ref['requirements']), 'Duplicate pilot IDs')
    previous = read_json(binding_file)
    # Do not carry the old generation-output bindings into any Agent's input.
    config = {
        'protocol_version': VERSION,
        'models': copy.deepcopy(previous['models']),
        'cost_control': 'PROVIDER_MANAGED',
        'budgets': {'live_authorized': False, 'campaign_hard_cap': None},
        'campaign_ledger': previous['campaign_ledger'],
        'http_timeout_seconds': 600,
        'source_bundle_sha256': file_hash(bundle / 'allowlist.json'),
        'reference_limits': {'max_requests_per_task': 8, 'max_format_repairs': 2,
                             'max_total_format_repairs': 2, 'max_total_requests': 52 * 5 * 8},
        'bootstrap_image_pages': {tid: initial_images(ctx) for tid, ctx in contexts.items()},
        'policy': 'One A build, B blind inventory and comparison, C blind inventory and final adjudication. Adopt final Agent output; previous acceptance rules advisory. No additional audit/rewrite loop. Preserve unresolved requirements.',
        'source_only': True,
        'binding_record_sha256': file_hash(binding_file),
    }
    # Keep the already selected models and accepted output settings.
    remaining = [tid for tid in ids if tid not in seeds]
    plan = {
        'schema_version': SCHEMA, 'protocol_version': VERSION,
        'targets': data['targets'], 'remaining_target_ids': remaining,
        'approved_pilots': seed_rows, 'approved_manifest_sha256': file_hash(approved / 'manifest.json'),
        'source_bundle': str(bundle.resolve()), 'source_allowlist_sha256': file_hash(bundle / 'allowlist.json'),
        'approval': 'User confirmed both references and authorized remaining 52 on 2026-11-02.',
        'construction_stages': list(STAGES), 'additional_acceptance_loop': False,
        'coverage_scoring': 'NOT_AUTHORIZED_BY_THIS_ENTRY_POINT',
    }
    root.mkdir(parents=True, exist_ok=True)
    locked(root / 'plan.snapshot.json', plan)
    locked(root / 'config.snapshot.json', config)
    locked(root / 'approved-pilots.snapshot.json', adopted)
    for tid, ref in seeds.items():
        path = root / 'references' / (tid + '.json')
        original = approved / (tid + '.json')
        if path.exists():
            require(path.read_bytes() == original.read_bytes(), 'Imported pilot changed')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(original.read_bytes())
    return config, plan, contexts, seeds


def collect(root, plan, contexts, seeds, verified=None):
    """Validate all available references, then write a partial or complete collection."""
    entries = []
    for t in plan['targets']:
        tid = t['target_id']; path = root / 'references' / (tid + '.json')
        if not path.exists():
            continue
        ref = read_json(path)
        if tid in seeds:
            require(ref == seeds[tid], 'User-approved pilot changed')
        else:
            signature = file_hash(path)
            if verified is None or verified.get(tid) != signature:
                validate_frozen(ref, contexts[tid]['bundle'], root)
                if verified is not None:
                    verified[tid] = signature
        entries.append({'target': t, 'reference_path': str(path.relative_to(root)),
                        'reference_file_sha256': file_hash(path), 'reference': ref,
                        'adoption': 'USER_APPROVED_PILOT' if tid in seeds else 'FINAL_THREE_AGENT_OUTPUT'})
    complete = len(entries) == len(plan['targets'])
    payload = {'schema_version': SCHEMA, 'protocol_version': VERSION, 'status': 'COMPLETE' if complete else 'PARTIAL',
               'expected_targets': len(plan['targets']), 'completed_targets': len(entries),
               'source_allowlist_sha256': plan['source_allowlist_sha256'], 'policy': 'USER_APPROVED_FINAL_AGENT_REFERENCES',
               'coverage_status': 'NOT_RUN', 'human_expert_review': 'NOT_RUN', 'is_official_pure_gold': False,
               'references': entries}
    payload['content_sha256'] = digest(payload)
    dest = root / ('common-references.json' if complete else 'common-references.partial.json')
    if complete:
        locked(dest, payload)
        write_json(root / 'common-references.manifest.json', {
            'status': 'COMPLETE', 'targets': len(entries), 'requirements': sum(len(x['reference']['requirements']) for x in entries),
            'source_unresolved': sum(r['status'] == 'REFERENCE_UNRESOLVED' for x in entries for r in x['reference']['requirements']),
            'collection': dest.name, 'sha256': file_hash(dest),
            'plan_sha256': file_hash(root / 'plan.snapshot.json'), 'coverage_status': 'NOT_RUN'})
    else:
        write_json(dest, payload)
    return payload


def one_line(p):
    return (f"[{p['updated']}] {p['status']} | 总进度 {p['completed_targets']}/54 "
            f"新增 {p['new_completed']}/52 | 失败待修 {p.get('failed_targets',0)} 已处理 {p.get('processed_targets',p['completed_targets'])}/54 | 阶段 {p['completed_stages']}/260 | "
            f"当前 {p.get('current_target_name') or '-'} {p.get('current_stage') or '-'} "
            f"请求#{p.get('attempt', 0)} | 调用 {p['calls']} "
            f"费用估算 ¥{p['estimated_cny']:.6f} 未计价 {p['unpriced_calls']} | "
            f"当前阶段已等待 {p.get('stage_elapsed_seconds', 0)}秒")


class Progress:
    def __init__(self, root, plan):
        self.root, self.plan = root, plan
        self.mutex = threading.RLock(); self.done = threading.Event(); self.thread = None
        self.stage_start = time.monotonic()
        self.state = {'status': 'PREPARED', 'current_target_id': None, 'current_target_name': None,
                      'current_stage': None, 'attempt': 0, 'coverage_calls': 0}

    def update(self, event, **changes):
        with self.mutex:
            if event in {'STAGE_START','STAGE_DONE','REFERENCE_COMPLETE','FINISHED'} or changes.get('status')=='READY_TO_RESUME':
                for field in ('reason','error_type'):self.state.pop(field,None)
            if event == 'STAGE_START':
                self.stage_start = time.monotonic()
                for field in ('unverified_locators','locator_changes','request_status','cached'):self.state.pop(field,None)
            self.state.update(changes)
            calls = [read_json(p) for p in (self.root / 'tasks').glob('*/*/call-*.json')]
            completed_ids={p.stem for p in (self.root/'references').glob('*.json')}
            completed=len(completed_ids)
            failed=len(set(current_failures(self.root))-completed_ids)
            self.state.update(updated=now(), last_event=event, completed_targets=completed,
                              new_completed=max(0, completed-2), remaining_targets=54-completed,
                              failed_targets=failed,processed_targets=completed+failed,not_started_targets=54-completed-failed,
                              completed_stages=len(list((self.root / 'tasks').glob('*/*/result.json'))),
                              calls=len(calls), estimated_cny=round(sum(c['cost_cny'] for c in calls if c.get('cost_cny') is not None), 6),
                              unpriced_calls=sum(c.get('cost_cny') is None for c in calls),
                              stage_elapsed_seconds=int(time.monotonic()-self.stage_start))
            write_json(self.root / 'progress.json', self.state)
            append_jsonl(self.root / 'events.jsonl', {'event': event, **self.state})
            line = one_line(self.state)
            with (self.root / 'run.log').open('a') as log:
                log.write(line + '\n')
            print(line, flush=True)

    def __enter__(self):
        def heartbeat():
            while not self.done.wait(15):
                self.update('HEARTBEAT')
        self.thread = threading.Thread(target=heartbeat, daemon=True); self.thread.start()
        return self

    def __exit__(self, *args):
        self.done.set()
        if self.thread:
            self.thread.join()


def run(root, config, plan, contexts, seeds, transport, budget, reporter, *, continue_on_error=False, retry_failed=False):
    verified = {}
    collect(root, plan, contexts, seeds, verified)
    inventory(root,plan)
    reporter.update('START', status='RUNNING')
    for number, tid in enumerate(plan['remaining_target_ids'], 1):
        ctx = contexts[tid]; dest = root / 'references' / (tid + '.json')
        if dest.exists():
            validate_frozen(read_json(dest), ctx['bundle'], root)
            if tid in current_failures(root):failure_record(root,tid,'ALREADY_COMPLETE')
            reporter.update('RESUME_SKIP_COMPLETED', current_target_id=tid, current_target_name=ctx['target']['name'])
            continue
        if continue_on_error and tid in current_failures(root) and not retry_failed:
            reporter.update('SKIP_RECORDED_FAILURE',current_target_id=tid,current_target_name=ctx['target']['name'],current_stage='DEFERRED')
            continue
        results = {}
        stage='A_BUILD'
        try:
            for stage in STAGES:
                reporter.update('STAGE_START', current_target_id=tid, current_target_name=f"{ctx['target']['project_id']}/{ctx['target']['name']} ({number}/52)",
                                current_stage=stage, attempt=0)
                materials = {}
                if stage in ('B_COMPARE', 'C_FINAL'):
                    materials = {'A': results['A_BUILD']['draft'], 'B': results['B_BLIND']['draft']}
                if stage == 'C_FINAL':
                    materials.update(B_review=results['B_COMPARE']['draft'], C=results['C_BLIND']['draft'])
                results[stage] = task(root, ctx, stage, config, transport, budget, materials, on_event=reporter.update,defer_paid_repairs=continue_on_error)
                reporter.update('STAGE_DONE')
            stage='FREEZE'
            freeze(root, ctx, results)
        except Exception as exc:
            if not continue_on_error or fatal_error(exc,root,tid,stage):raise
            failed=failure_record(root,tid,stage,error=exc)
            inventory(root,plan)
            reporter.update('TARGET_FAILED_CONTINUING',status='RUNNING',reason=failed['reason'],error_type=failed['error_type'])
            continue
        if tid in current_failures(root):failure_record(root,tid,'COMPLETE')
        verified[tid] = file_hash(dest)  # freeze already checked actual receipts and source proof.
        collect(root, plan, contexts, seeds, verified)
        inventory(root,plan)
        reporter.update('REFERENCE_COMPLETE')
    result = collect(root, plan, contexts, seeds)
    outcome=inventory(root,plan,pass_finished=True)
    if result['status']!='COMPLETE':require(continue_on_error and outcome['status']=='FINISHED_WITH_FAILURES','Final collection is incomplete')
    reporter.update('FINISHED', status=outcome['status'], current_stage=None, current_target_id=None, current_target_name=None)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', default=str(DEFAULT_RUN))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare-only', action='store_true')
    mode.add_argument('--authorize-live', action='store_true')
    mode.add_argument('--status', action='store_true')
    parser.add_argument('--continue-on-error',action='store_true',help='Defer failed cases and finish other targets; remember this policy for the run.')
    parser.add_argument('--retry-failed',action='store_true',help='After repairs, retry recorded failures using cached stages and receipts.')
    args = parser.parse_args()
    require(not args.retry_failed or args.authorize_live,'Retry failed targets requires live execution mode')
    root = Path(args.run_dir).resolve()
    require(root.is_relative_to(REPO / 'outputs/agent_evaluation') and root != REPO / 'outputs/agent_evaluation', 'Independent L3 output directory required')
    if args.status:
        require((root / 'progress.json').exists(), 'No batch progress yet')
        print(one_line(read_json(root / 'progress.json')), flush=True)
        return
    if args.authorize_live:
        names = ['AUCMR_EVAL_ZHIPU_API_KEY', 'AUCMR_EVAL_ARK_API_KEY', 'AUCMR_JUNXI_DASHSCOPE_API_KEY']
        present = {n: bool(os.environ.get(n)) for n in names}
        print(canonical(present), flush=True)
        require(all(present.values()), 'Required key unavailable; no model call made')
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config, plan, contexts, seeds = prepare(root)
        policy_path=root/'execution-policy.json'
        if args.continue_on_error:
            policy={'continue_on_error':True,'defer_paid_format_repairs':True,'authority':'User requested skipping failed cases and fixing them together after the first pass.','model_inputs_unchanged':True}
            locked(policy_path,policy)
        continue_on_error=args.continue_on_error or args.retry_failed or (policy_path.exists() and read_json(policy_path).get('continue_on_error') is True)
        reporter = Progress(root, plan)
        collect(root, plan, contexts, seeds)
        if args.prepare_only:
            reporter.update('PREPARED', status='PREPARED')
            return
        ledger = REPO / config['campaign_ledger']
        require(ledger.exists(), 'Existing common ledger missing; refusing a replacement ledger')
        live_config = copy.deepcopy(config); live_config['budgets']['live_authorized'] = True
        with reporter:
            try:
                budget = Budget(ledger, provider_managed=True)
                run(root, config, plan, contexts, seeds,
                    AcceptanceTransport(live_config, root, budget), budget, reporter,
                    continue_on_error=continue_on_error,retry_failed=args.retry_failed)
            except BaseException as exc:
                # Only type is recorded here; provider response/error bodies stay in redacted receipts.
                stop = {'time': now(), 'status': 'STOPPED', 'error_type': type(exc).__name__,
                        'current_target_id': reporter.state.get('current_target_id'),
                        'current_stage': reporter.state.get('current_stage')}
                append_jsonl(root / 'stops.jsonl', stop)
                reporter.update('STOPPED', status='STOPPED', error_type=type(exc).__name__)
                raise


if __name__ == '__main__':
    main()
