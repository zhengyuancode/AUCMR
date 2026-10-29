"""Paper rubric and small, immutable storage primitives."""
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
# Retain the public offline guard used by L1 tests, without changing L1 code.
from eval.agent_evaluation.transport import deny_network as network_blocked

REPO = Path(__file__).resolve().parents[2]
VERSION = 'L4_PANEL_4.0.0'
SLOTS = ('goal', 'preconditions', 'trigger', 'main_flow', 'alternative_flow',
         'exception_flow', 'postconditions')
RUBRIC = {
    'PCS': {'0': '槽位内容错误或与该槽位要求基本无关',
            '1': '主要内容存在明显错误，仅有少量内容正确',
            '2': '总体正确，但仍存在少量错误或不准确之处',
            '3': '内容正确且不存在明显事实性错误'},
    'PRC': {'1': '难以直接使用，需要基本重写',
            '2': '可用性较低，需要大幅修改',
            '3': '基本可用，但仍需要一定程度的人工修改',
            '4': '可用性较高，仅需要少量人工修改',
            '5': '能够直接支撑后续设计活动，或仅需要极少量修订'}}


class L4Error(ValueError):
    """Integrity/configuration errors stop execution; model item errors are records."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def require(condition, message):
    if not condition:
        raise L4Error(message)


def local_path(value):
    path = (REPO / value).resolve()
    require(path.is_relative_to(REPO), 'PATH_OUTSIDE_REPOSITORY')
    return path


def save_new(path, value):
    save_new_bytes(path,(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8'))


def save_new_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def sealed(value):
    return {**value, 'sha256': digest(value)}


def unseal(value):
    require(value.get('sha256') == digest({k: v for k, v in value.items() if k != 'sha256'}),
            'RECORD_HASH_MISMATCH')
    return value


def implementation():
    paths = list(Path(__file__).parent.glob('*.py'))
    paths += [REPO / 'eval' / name for name in (
        'evaluation_runtime.py', 'evaluation_billing.py', 'evaluation_request_budget.py',
        'evaluation_response_format.py', 'evaluation_cost_policy.py', 'serialized_slots.py')]
    paths += [REPO / 'eval/agent_evaluation' / name for name in ('budget.py', 'common.py')]
    return {str(p.relative_to(REPO)): file_hash(p) for p in sorted(paths)}
