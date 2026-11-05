import json
from pathlib import Path

import pytest

from eval.compute_metrics import NLIJudge
from eval.l1_recording import L1Recording, file_hash
from eval.l1_repair import RepairJudge, RepairRecording, repair_prompt


GOOD = json.dumps(dict(entailment='neutral', slot_fit=True, rationale='Missing evidence',
                       slot_fit_rationale='An actor action belongs in main_flow'))
PROMPT = 'Use-case slot: main_flow\n\nClaim: A\n\nEvidence:\n[This evidence card has no textual content.]'


def test_request_changes_only_presentation():
    result = repair_prompt(PROMPT, ['image'])
    assert 'Claim: A' in result and 'Read its attached source page image' in result
    assert 'actual slot for this request is main_flow' in result
    assert 'never a boolean' in result
    assert '[This evidence card has no textual content.]' in repair_prompt(PROMPT, [])
    with pytest.raises(RuntimeError):
        repair_prompt('No slot', [])


@pytest.mark.parametrize('raw', [
    '{"entailment":false,"slot_fit":true}',
    '{"entailment":"neutral","slot_fit":true}',
    GOOD + '\n' + GOOD.replace('true', 'false'),
    GOOD[:-1] + ',"slot_fit":false}',
    GOOD + ' extra commentary',
])
def test_parser_still_rejects_invalid_judgments(raw):
    assert RepairJudge.__new__(RepairJudge)._parse_response(raw) is None


def test_single_complete_json_and_fence_accepted():
    parse = RepairJudge.__new__(RepairJudge)._parse_response
    assert parse(GOOD) == parse('```json\n' + GOOD + '\n```') == json.loads(GOOD)


def test_old_success_reused_repair_retained_and_not_resent(tmp_path):
    parse = NLIJudge.__new__(NLIJudge)._parse_response
    old = L1Recording(tmp_path/'old', {'old': True})
    new = L1Recording(tmp_path/'new', {'repair': True}, ['test-secret'])
    old.judge(PROMPT, [], lambda: GOOD, parse)
    receipt = next((old.root/'requests').glob('*.json'))
    before = file_hash(receipt)
    wrapper = RepairRecording(old, new, {})
    assert wrapper.judge(PROMPT, [], lambda: pytest.fail('Repeated paid request'), parse)[1]
    assert file_hash(receipt) == before
    bad_prompt = PROMPT.replace('Claim: A', 'Claim: B')
    with pytest.raises(RuntimeError):
        old.judge(bad_prompt, [], lambda: '{"entailment":false}', parse)
    bad = next(p for p in (old.root/'requests').glob('*.json') if p != receipt)
    with pytest.raises(RuntimeError, match='not authorized'):
        wrapper.judge(bad_prompt, [], lambda: pytest.fail('Unauthorized repair'), parse)
    wrapper.allowed_rejections[bad.name] = file_hash(bad)
    old_hash = file_hash(bad)
    calls = []
    def dispatch():
        calls.append(1)
        return GOOD
    assert wrapper.judge(bad_prompt, [], dispatch, parse)[1] is False
    assert wrapper.judge(bad_prompt, [], dispatch, parse)[1] is True
    assert len(calls) == 1 and file_hash(bad) == old_hash
    assert old._read(bad)['state'] == 'REJECTED'


def test_failed_repair_never_retries_and_redacts(tmp_path):
    parse = NLIJudge.__new__(NLIJudge)._parse_response
    old = L1Recording(tmp_path/'old', {})
    new = L1Recording(tmp_path/'new', {}, ['test-secret'])
    wrapper = RepairRecording(old, new, {})
    with pytest.raises(RuntimeError):
        wrapper.judge(PROMPT, [], lambda: 'test-secret malformed', parse)
    with pytest.raises(RuntimeError, match='not resent'):
        wrapper.judge(PROMPT, [], lambda: pytest.fail('Repeated failed repair'), parse)
    assert 'test-secret' not in next((new.root/'requests').glob('*.json')).read_text()


def test_pending_never_retries(tmp_path):
    parse = NLIJudge.__new__(NLIJudge)._parse_response
    old = L1Recording(tmp_path/'old', {})
    new = L1Recording(tmp_path/'new', {})
    wrapper = RepairRecording(old, new, {})
    def interrupt():
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        wrapper.judge(PROMPT, [], interrupt, parse)
    with pytest.raises(RuntimeError, match='not resent'):
        wrapper.judge(PROMPT, [], lambda: pytest.fail('Repeated pending request'), parse)
