"""Four preselected L1 repairs; no batch launch and no automatic retries."""
import argparse
import fcntl
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = ROOT / 'outputs/evaluation_preparation/full93-v306-20261028'
OLD = BASE / 'l1-workspace/l1_checkpoints'
os.environ['AUCMR_CONFIG'] = str(BASE / 'l1-config.json')
sys.path.insert(0, str(ROOT))

from config import settings
from eval.l1_recording import L1Recording, digest, file_hash, make_recording
from eval.l1_repair import RepairJudge, RepairRecording, repair_prompt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--authorize-live', action='store_true')
    ap.add_argument('--check-only', action='store_true')
    args = ap.parse_args()
    if not args.authorize_live and not args.check_only:
        raise RuntimeError('LIVE_NOT_AUTHORIZED')
    os.chdir(ROOT)
    control = json.loads((HERE/'control.json').read_text())
    for name, expected in control['protected_hashes'].items():
        if file_hash(ROOT/name) != expected:
            raise RuntimeError('FROZEN_FILE_CHANGED: ' + name)
    manifest = json.loads((OLD/'manifest.json').read_text())['data']
    judge = RepairJudge(max_attempts=1)
    original = make_recording(OLD, judge, manifest['bindings'])
    for binding in manifest['bindings']:
        for kind in ('input', 'evidence'):
            if file_hash(binding[kind+'_path']) != binding[kind+'_hash']:
                raise RuntimeError('INPUT_CHANGED')
    selected = []
    for name in control['validation_requests']:
        path = OLD/'requests'/name
        receipt = original._read(path)
        if receipt['state'] != 'REJECTED' or file_hash(path) != control['allowed_rejections'][name]:
            raise RuntimeError('ORIGINAL_REJECTION_CHANGED')
        for img in receipt['request']['images']:
            if file_hash(img['path']) != img['sha256']:
                raise RuntimeError('IMAGE_CHANGED')
        selected.append((name, receipt['request']))
    if len(selected) != 4 or len(set(control['validation_requests'])) != 4:
        raise RuntimeError('INVALID_VALIDATION_SCOPE')
    if args.check_only:
        print(json.dumps({'check': 'PASS', 'selected': len(selected), 'model_calls': 0}))
        return
    names = ['AUCMR_EVAL_ZHIPU_API_KEY', 'AUCMR_EVAL_ARK_API_KEY', 'AUCMR_JUNXI_DASHSCOPE_API_KEY']
    presence = {n: bool(os.environ.get(n)) for n in names}
    print(json.dumps(presence), flush=True)
    if not all(presence.values()):
        raise RuntimeError('MODEL_KEY_NOT_SET')
    context = {'version': 'L1_REQUEST_PRESENTATION_REPAIR_1', 'control': control,
               'original_manifest': str(OLD/'manifest.json'),
               'original_results_are_reused_not_regenerated': True}
    repairs = L1Recording(HERE/'checkpoints', context, original.secrets)
    recorder = RepairRecording(original, repairs, control['allowed_rejections'])
    factory = settings.create_openai_client
    active = {}
    calls = 0

    def create(endpoint):
        client = factory(endpoint).with_options(max_retries=0)
        def completion(**kwargs):
            nonlocal calls
            if calls >= 4:
                raise RuntimeError('VALIDATION_CALL_LIMIT')
            calls += 1
            response = client.chat.completions.create(**kwargs)
            # Keep the complete provider response, including usage, without keys.
            repairs._save(HERE/'responses'/active['name'], {
                'original_request': str(OLD/'requests'/active['name']),
                'repair_request_key': active['key'],
                'response': response.model_dump(mode='json'),
                'cost_cny': None, 'cost_basis': 'UNPRICED_NOT_ZERO'}, exclusive=True)
            return response
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=completion)))

    outcomes = []
    with (BASE/'execution.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with patch.object(settings, 'create_openai_client', create):
            for name, request in selected:
                images = [i['path'] for i in request['images']]
                active['name'] = name
                active['key'] = digest({'prompt': repair_prompt(request['prompt'], images),
                                        'images': request['images']})
                print('L1_REPAIR_REQUEST ' + name, flush=True)
                try:
                    parsed, replayed = recorder.judge(request['prompt'], images,
                        lambda: judge._default_nli_call(request['prompt'], images), judge._parse_response)
                except RuntimeError as exc:
                    # Only format rejection may continue through the fixed validation set.
                    if not (str(exc).startswith('L1 malformed raw response retained: ') or
                            str(exc).startswith('L1 recorded REJECTED; preserved, not resent: ')):
                        raise
                    outcome = {'original_request': name, 'status': 'REJECTED'}
                else:
                    outcome = {'original_request': name, 'status': 'VALID',
                               'parsed': parsed, 'replayed': replayed}
                outcomes.append(outcome)
                repairs._save(HERE/'validation.json', {'outcomes': outcomes, 'new_calls_this_run': calls,
                              'semantic_review': 'PENDING', 'full_batch_started': False})
                print(json.dumps(outcome, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
