import copy
import json
from pathlib import Path

import pytest

from eval import l2_rules_batch as b
from eval.evaluation_runtime import RuntimeResponseError
from eval.compute_metrics import CONSISTENCY_RULES


def artifact():
    return {"artifact_id": "A-fixture", "input_hash": "fixture", "task_id": "T", "method": "M",
            "primary_actor_slot": {"claims": [{"content": "Customer"}]},
            "task_input": {"name": "Withdraw", "primary_actor": "Customer"},
            "assertions": [{"slot": "trigger", "original_index": 0, "text": "Customer requests a withdrawal."},
                           {"slot": "main_flow", "original_index": 0, "text": "Customer withdraws cash."}]}


def issue():
    return {"rule_violated": "mainflow_trigger_continuity", "offending_slot": "main_flow",
            "offending_claim_index": 0, "rationale": "Explicit unrelated operation."}


class Runtime:
    def __init__(self, body=None, error=False):
        self.calls = 0
        self.body = body or '{"violations": []}'
        self.error = error

    def request(self, *args, **kwargs):
        self.calls += 1
        receipt = {"body": self.body, "response_model": "fixture", "cost_cny": 0.001}
        if self.error:
            raise RuntimeResponseError("failure", receipt=receipt)
        return receipt


def test_unauthorized_before_config_or_runtime():
    with pytest.raises(b.Stop, match="LIVE_NOT_AUTHORIZED"):
        b.execute(Path('/does-not-exist'), '', False, 'calibrate')


def test_reuses_completed_group_without_resending(tmp_path):
    runtime = Runtime(json.dumps({"violations": [issue()]}))
    first = b.run_group(runtime, tmp_path, artifact(), "vertical", "fixture")
    again = b.run_group(runtime, tmp_path, artifact(), "vertical", "fixture")
    assert runtime.calls == 1 and first == again
    assert first["counts"] == {"R1": 0, "R2": 1, "R3": 0}


def test_retains_failed_response_without_retry_or_zero(tmp_path):
    runtime = Runtime('{"violations": [', error=True)
    first = b.run_group(runtime, tmp_path, artifact(), "vertical", "fixture")
    again = b.run_group(runtime, tmp_path, artifact(), "vertical", "fixture")
    assert runtime.calls == 1 and first == again
    assert first["status"] == "TECHNICAL_FAILURE" and first["counts"] is None
    assert first['receipt']['body'] == '{"violations": ['


@pytest.mark.parametrize('change', [{'rule_violated': 'altflow_diverge_points'},
    {'offending_claim_index': 99}, {'offending_claim_index': True}, {'offending_slot': 'absent'},
    {'rationale': ''}, {'extra': 'ignore me'}])
def test_wrong_rule_location_or_fields_rejected(change):
    with pytest.raises(ValueError):
        b.parse_group(json.dumps({'violations': [{**issue(), **change}]}), artifact(), 'vertical')


def test_no_json_repair_but_whole_fence_accepted():
    with pytest.raises(ValueError):
        b.parse_group('{"violations": [', artifact(), 'vertical')
    assert b.parse_group('```json\n{"violations": []}\n```', artifact(), 'vertical') == []


def test_duplicate_exact_finding_once():
    assert len(b.parse_group(json.dumps({'violations': [issue(), issue()]}), artifact(), 'vertical')) == 1


def test_tampered_record_and_changed_prompt_block_reuse(tmp_path):
    runtime = Runtime()
    b.run_group(runtime, tmp_path, artifact(), 'vertical', 'fixture')
    a = artifact(); a['task_input']['name'] = 'Changed'
    with pytest.raises(b.Stop, match='GROUP_IDENTITY_CHANGED'):
        b.run_group(runtime, tmp_path, a, 'vertical', 'fixture')
    path = tmp_path / 'groups/A-fixture-vertical.json'
    x = json.loads(path.read_text()); x['status'] = 'FORGED'; path.write_text(json.dumps(x))
    with pytest.raises(b.Stop, match='RECORD_HASH_MISMATCH'):
        b.run_group(runtime, tmp_path, artifact(), 'vertical', 'fixture')
    assert runtime.calls == 1


def test_unknown_cost_is_fatal_and_not_resent(tmp_path):
    class Unknown(Runtime):
        def request(self, *args, **kwargs):
            self.calls += 1
            return {'body': '{"violations": []}', 'response_model': 'fixture', 'cost_cny': None}
    runtime = Unknown()
    for _ in range(2):
        with pytest.raises(b.Stop, match='UNKNOWN_COST'):
            b.run_group(runtime, tmp_path, artifact(), 'vertical', 'fixture')
    assert runtime.calls == 1


def test_common_denominator_and_failure_not_zero():
    artifacts = [{'task_id': t, 'method': m} for t in ['T1', 'T2'] for m in ['M1', 'M2']]
    rows = [{'target_id': a['task_id'], 'method': a['method'], 'status': 'COMPLETE',
             'counts': dict.fromkeys(b.RULES, 0)} for a in artifacts]
    rows[-1].update(status='TECHNICAL_FAILURE', counts=None)
    rows[0]['counts']['R2'] = 2
    result = b.summarize(rows, artifacts)
    assert result['common_target_ids'] == ['T1']
    assert result['primary_common_case_table'][0]['rule_means']['R2'] == 2
    assert result['coverage_table'][1]['technical_failures'] == 1


def test_engineering_envelope_does_not_change_rule_text():
    a = artifact()
    a['assertions'] = [{'slot': s, 'original_index': 0, 'text': 'Existing content.'} for s in b.SLOTS]
    for group, old in [('vertical', b._VERTICAL_SYSTEM_PROMPT), ('lateral', b._LATERAL_SYSTEM_PROMPT)]:
        new = b.messages(a, group)[0]['content']
        descriptions = b.re.findall(r'(?ms)^\d\. (.*?)(?=^\d\. |\Z)', old.split('Rules:\n')[1].split('\nBe conservative:')[0])
        assert all(d.strip() in new for d in descriptions)
        assert 'JSON array of violations' not in b.messages(a, group)[1]['content']


def test_primary_actor_slot_is_bound_and_visible_not_empty():
    a = artifact()
    user = b.messages(a, 'lateral')[1]['content']
    assert 'primary_actor:\n  [0] Customer' in user
    del a['primary_actor_slot']
    with pytest.raises(b.Stop, match='PRIMARY_ACTOR_SLOT_NOT_BOUND'):
        b.messages(a, 'lateral')


@pytest.mark.parametrize('rule', b.RULES)
def test_empty_required_input_cannot_become_violation_on_another_valid_slot(rule):
    a = artifact()
    a['assertions'] = [{'slot': s, 'original_index': 0, 'text': 'Existing text.'}
                       for s in b.SLOTS if s != b.RULE_INPUTS[rule][0]]
    ref_slot = next(c['slot'] for c in a['assertions'])
    name = CONSISTENCY_RULES[rule]
    raw = json.dumps({'violations': [{'rule_violated': name, 'offending_slot': ref_slot,
                                     'offending_claim_index': 0, 'rationale': 'Required slot missing.'}]})
    with pytest.raises(ValueError, match='RULE_INPUT_EMPTY:' + rule):
        b.parse_group(raw, a, 'vertical' if rule in b.GROUPS['vertical'] else 'lateral')


def test_r4_uses_whole_flow_not_every_step_and_never_silently_drops_error(tmp_path):
    a = artifact()
    a['assertions'].append({'slot': 'main_flow', 'original_index': 1, 'text': 'The system updates the balance.'})
    bad = {'rule_violated': 'actor_mainflow_presence', 'offending_slot': 'main_flow',
           'offending_claim_index': 1, 'rationale': 'Customer does not appear in this step.'}
    runtime = Runtime(json.dumps({'violations': [bad]}))
    row = b.run_group(runtime, tmp_path, a, 'lateral', 'fixture')
    assert row['result_basis'] == 'INPUT_APPLICABILITY_AND_LITERAL_EVIDENCE'
    assert row['rule_resolution']['R4']['state'] == 'SATISFIED_LITERAL_ACTOR_PRESENCE'
    assert row['rule_resolution']['R4']['evidence'][0]['assertion_id'] == 'main_flow:0'
    assert row['receipt'] is None and runtime.calls == 0
    with pytest.raises(ValueError, match='R4_ACTOR_PRESENT'):
        b.parse_group(runtime.body, a, 'lateral')
    b.run_group(runtime, tmp_path, a, 'lateral', 'fixture')
    assert runtime.calls == 0


def test_r4_real_absence_accepted_once_but_multiple_step_findings_rejected():
    a = artifact()
    a['assertions'][-1]['text'] = 'The Cashier closes the account.'
    row = {'rule_violated': 'actor_mainflow_presence', 'offending_slot': 'main_flow',
           'offending_claim_index': -1, 'rationale': 'Customer absent throughout this nonempty flow.'}
    assert len(b.parse_group(json.dumps({'violations': [row]}), a, 'lateral')) == 1
    with pytest.raises(ValueError, match='R4_MORE_THAN_ONE'):
        b.parse_group(json.dumps({'violations': [row, {**row, 'rationale': 'Another step also lacks Customer.'}]}), a, 'lateral')


def test_empty_slot_minus_one_reference_still_invalid():
    a = artifact()
    bad = {**issue(), 'offending_slot': 'goal', 'offending_claim_index': -1}
    with pytest.raises(ValueError, match='INVALID_RULE_OR_LOCATION'):
        b.parse_group(json.dumps({'violations': [bad]}), a, 'vertical')


def test_prompt_distinguishes_missing_input_from_semantic_pass():
    request = b.messages(artifact(), 'lateral')
    assert 'actor_mainflow_presence' not in request[0]['content']
    assert 'goal_mainflow_alignment' not in request[0]['content']
    assert b.rule_plan(artifact(), 'lateral')['R5']['state'] == 'NOT_APPLICABLE_EMPTY_INPUT'


def test_empty_group_records_not_applicable_without_a_request(tmp_path):
    a = artifact(); a['assertions'] = []
    runtime = Runtime()
    result = b.run_group(runtime, tmp_path, a, 'vertical', 'fixture')
    assert runtime.calls == 0 and result['receipt'] is None
    assert all(v['state'] == 'NOT_APPLICABLE_EMPTY_INPUT' for v in result['rule_resolution'].values())


def test_pronoun_or_alias_is_not_automatically_declared_present():
    a = artifact(); a['assertions'][-1]['text'] = 'They withdraw cash.'
    assert b.rule_plan(a, 'lateral')['R4']['state'] == 'MODEL_CHECK'
    assert 'actor_mainflow_presence' in b.messages(a, 'lateral')[0]['content']


def test_all_applicable_rules_checked_without_requiring_positive_findings():
    a = artifact()
    a['assertions'] = [{'slot': s, 'original_index': 0, 'text': 'Existing content.'} for s in b.SLOTS]
    for group in b.GROUPS:
        system = b.messages(a, group)[0]['content']
        assert 'Check EVERY listed rule independently: ' + ', '.join(b.GROUPS[group]) in system
        assert 'do not stop after the first finding' in system
        assert 'do not invent findings' in system
        assert b.parse_group('{"violations": []}', a, group) == []


def test_continuation_reuses_input_disposition_and_response_without_dispatch(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location('l2_continue_v5', b.ROOT / 'outputs/evaluation_preparation/l2-rules-v5-20261029/continue.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    (tmp_path / 'input.json').write_text('{}')
    a = {**artifact(), 'input_path': 'input.json', 'input_hash': b.file_hash(tmp_path / 'input.json'), 'input_status': 'PRESENT'}
    runtime = Runtime(); runtime.binding = {'model_id': 'fixture'}
    old = {'groups': [{'record': b.run_group(runtime, tmp_path / 'old', a, group, 'fixture')} for group in b.GROUPS]}
    assert runtime.calls == 1
    row = module.complete(a, runtime, tmp_path / 'new', 'config', {a['artifact_id']: old})
    assert row['status'] == 'COMPLETE' and runtime.calls == 1
    assert module.complete(a, runtime, tmp_path / 'new', 'config', {}) == row
    assert runtime.calls == 1
    corrupt = copy.deepcopy(old)
    g = corrupt['groups'][1]['record']; g['rule_resolution']['R4']['evidence'][0]['matched_text'] = 'forged'
    g['sha256'] = b.digest({k: v for k, v in g.items() if k != 'sha256'})
    with pytest.raises(b.Stop, match='HISTORICAL_INPUT_DISPOSITION_CHANGED'):
        module.complete(a, runtime, tmp_path / 'bad', 'config', {a['artifact_id']: corrupt})
    assert runtime.calls == 1
