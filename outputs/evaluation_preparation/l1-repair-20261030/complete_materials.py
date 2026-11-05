"""Finish only the 42 retained L1 failures, reusing validation and old receipts."""
import argparse
import fcntl
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import run_repair as validation
from eval.compute_metrics import MetricsComputer
from eval.l1_recording import L1Recording, digest, file_hash, make_recording
from eval.l1_repair import RepairJudge, RepairRecording, repair_prompt
from eval.agent_evaluation.common import write_json

HERE, ROOT, OLD, BASE = validation.HERE, validation.ROOT, validation.OLD, validation.BASE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--authorize-live', action='store_true')
    args = ap.parse_args()
    if not args.authorize_live:
        raise RuntimeError('LIVE_NOT_AUTHORIZED')
    os.chdir(ROOT)
    names = ['AUCMR_EVAL_ZHIPU_API_KEY', 'AUCMR_EVAL_ARK_API_KEY', 'AUCMR_JUNXI_DASHSCOPE_API_KEY']
    presence = {n: bool(os.environ.get(n)) for n in names}
    print(json.dumps(presence), flush=True)
    if not all(presence.values()):
        raise RuntimeError('MODEL_KEY_NOT_SET')
    control = json.loads((HERE/'control.json').read_text())
    continuation = json.loads((HERE/'continuation-control.json').read_text())
    protected = {**control['protected_hashes'], **continuation['protected_hashes']}
    for name, expected in protected.items():
        if file_hash(ROOT/name) != expected:
            raise RuntimeError('FROZEN_FILE_CHANGED: ' + name)
    judge = RepairJudge(max_attempts=1)
    manifest = json.loads((OLD/'manifest.json').read_text())['data']
    if len(continuation['bindings']) != 42 or any(
            b not in manifest['bindings'] for b in continuation['bindings']):
        raise RuntimeError('INVALID_REPAIR_SCOPE')
    original = make_recording(OLD, judge, manifest['bindings'])
    context = {'version': 'L1_REQUEST_PRESENTATION_REPAIR_1', 'control': control,
               'original_manifest': str(OLD/'manifest.json'),
               'original_results_are_reused_not_regenerated': True}
    repairs = L1Recording(HERE/'checkpoints', context, original.secrets)
    original_hashes = json.loads((HERE/'original-hashes.json').read_text())
    for name, expected in original_hashes.items():
        if file_hash(ROOT/name) != expected:
            raise RuntimeError('HISTORICAL_FILE_CHANGED: ' + name)
    recorder = RepairRecording(original, repairs, control['allowed_rejections'])
    judge._recorder = recorder
    computer = MetricsComputer(nli_judge=judge)
    active = {}
    completed = skipped = 0
    factory = validation.settings.create_openai_client

    def create(endpoint):
        client = factory(endpoint).with_options(max_retries=0)
        def completion(**kwargs):
            for name, expected in protected.items():
                if file_hash(ROOT/name) != expected:
                    raise RuntimeError('FROZEN_FILE_CHANGED')
            response = client.chat.completions.create(**kwargs)
            repairs._save(HERE/'responses'/(active['key']+'.json'), {
                'material': active['binding'], 'repair_request_key': active['key'],
                'original_request': active['original'],
                'response': response.model_dump(mode='json'),
                'cost_cny': None, 'cost_basis': 'UNPRICED_NOT_ZERO'}, exclusive=True)
            return response
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=completion)))

    dispatch = judge._default_nli_call
    def tracked(prompt, image_paths=None):
        request = {'prompt': prompt, 'images': [
            {'path': str(Path(p).resolve()), 'sha256': file_hash(p)} for p in image_paths or []]}
        path = OLD/'requests'/(digest(request)+'.json')
        active['original'] = str(path) if path.exists() else None
        request['prompt'] = repair_prompt(prompt, image_paths)
        active['key'] = digest(request)
        return dispatch(prompt, image_paths)
    judge._default_nli_call = tracked
    with (BASE/'execution.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with patch.object(validation.settings, 'create_openai_client', create):
            for index, binding in enumerate(continuation['bindings'], 1):
                for kind in ('input', 'evidence'):
                    if file_hash(binding[kind+'_path']) != binding[kind+'_hash']:
                        raise RuntimeError('INPUT_CHANGED')
                active['binding'] = binding
                failed = HERE/'failed-materials'/(digest(binding)+'.json')
                if repairs.load_metrics(binding) is not None:
                    completed += 1
                elif failed.exists():
                    repairs._read(failed)
                    skipped += 1
                else:
                    print('L1_REPAIR_MATERIAL ' + binding['target_id'] + ' ' + binding['method'], flush=True)
                    try:
                        metrics = computer.compute_all(binding['input_path'], binding['evidence_path'],
                                                       run_nli=True, run_l2=False, bound_method=binding['method'])
                    except RuntimeError as exc:
                        if not (str(exc).startswith('L1 malformed raw response retained: ') or
                                str(exc).startswith('L1 recorded REJECTED; preserved, not resent: ')):
                            raise
                        repairs._save(failed, {'identity': binding, 'error': str(exc), 'metrics': None}, exclusive=True)
                        skipped += 1
                    else:
                        repairs.save_metrics(binding, metrics)
                        completed += 1
                status = {'status': 'FINISHED' if index == len(continuation['bindings']) else 'RUNNING',
                          'retained_completed': 516, 'repaired_materials': completed,
                          'repair_failed_materials': skipped, 'unprocessed_repairs': len(continuation['bindings'])-index,
                          'repair_request_records': len(list((repairs.root/'requests').glob('*.json'))),
                          'cost_cny': None, 'cost_basis': 'UNPRICED_NOT_ZERO'}
                write_json(HERE/'progress.json', status)
                print('L1_REPAIR_PROGRESS ' + json.dumps(status), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        write_json(HERE/'last-stop.json', {'status': 'STOPPED', 'error_type': type(exc).__name__})
        print('L1_REPAIR_STOPPED ' + type(exc).__name__, flush=True)
        raise SystemExit(2)
