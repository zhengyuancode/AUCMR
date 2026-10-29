import json
from pathlib import Path
import pytest
from config.settings import ModelEndpoint
from eval.l1_recording import L1Recording
from eval.compute_metrics import NLIJudge, MetricsComputer
from eval.run_experiments import ExperimentRunner
from eval.l4_agent.protocol import network_blocked


@pytest.fixture(autouse=True)
def no_network():
    with network_blocked():
        yield


GOOD = json.dumps({'entailment': 'neutral', 'slot_fit': False,
                   'rationale': 'Insufficient evidence', 'slot_fit_rationale': 'Not established'})


def test_raw_malformed_and_secret_redaction_no_resend(tmp_path):
    store = L1Recording(tmp_path, {'case': 'fixture'}, ['secret-fixture'])
    judge = NLIJudge(endpoint=ModelEndpoint(model_name='fixture'))
    with pytest.raises(RuntimeError, match='malformed'):
        store.judge('claim', [], lambda: '{"bad":"secret-fixture"}', judge._parse_response)
    text = next((tmp_path/'requests').glob('*.json')).read_text()
    assert 'secret-fixture' not in text and '[REDACTED]' in text
    record = json.loads(text)['data']
    assert record['state'] == 'REJECTED' and record['raw_response']
    with pytest.raises(RuntimeError, match='not resent'):
        store.judge('claim', [], lambda: pytest.fail('Resent rejected response'), judge._parse_response)


def test_received_survives_parser_interruption_and_replays_without_call(tmp_path):
    store = L1Recording(tmp_path, {'case': 'fixture'})
    def interrupt(_): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):store.judge('claim', [], lambda: GOOD, interrupt)
    assert json.loads(next((tmp_path/'requests').glob('*.json')).read_text())['data']['state']=='RECEIVED'
    parse = NLIJudge(endpoint=ModelEndpoint(model_name='fixture'))._parse_response
    value, replayed = store.judge('claim', [], lambda: pytest.fail('Resent received response'), parse)
    assert replayed and value['entailment']=='neutral' and value['slot_fit'] is False


def test_pending_is_never_resent_and_manifest_drift_blocks(tmp_path):
    store = L1Recording(tmp_path, {'case': 'fixture'})
    def interrupt(): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):store.judge('claim', [], interrupt, lambda x:x)
    with pytest.raises(RuntimeError, match='PENDING'):
        store.judge('claim', [], lambda: pytest.fail('Resent pending request'), lambda x:x)
    with pytest.raises(RuntimeError, match='changed'):L1Recording(tmp_path, {'case':'changed'})


def test_completed_material_and_failed_claim_survive_batch_failure(tmp_path):
    pairs={}
    for name in ['A','B']:
        claim={'content':name,'evidence_refs':['E1']}
        p=tmp_path/(name+'.json');e=tmp_path/(name+'-evidence.json')
        p.write_text(json.dumps({'use_case_name':name,'actor':'Actor','slots':{'goal':{'claims':[claim]}},
            'citation_audit':{'version':'pre_constraint_draft_v1','pre_constraint_slots':{
                'goal':{'claims':[claim],'exposed_evidence_refs':['E1']}}}}))
        e.write_text(json.dumps({'evidence_cards':[{'card_id':'E1','content':'source'}]}))
        pairs[('AUCMR (CREST full)','P',name,'Actor')]=(str(p),str(e))
    calls=[]
    def model(prompt):calls.append(prompt);return GOOD if 'Claim: A\n' in prompt else '{"entailment":"neutral"}'
    def runner(fn):
        r=ExperimentRunner.__new__(ExperimentRunner)
        r.metrics_computer=MetricsComputer(NLIJudge(endpoint=ModelEndpoint(model_name='fixture'),model_fn=fn,max_attempts=1))
        return r
    directory=tmp_path/'checkpoints'
    with pytest.raises(RuntimeError, match='malformed'):
        runner(model).compute_all_metrics(pairs,run_nli=True,require_l1_audit=True,l1_record_dir=directory)
    assert len(calls)==2 and len(list((directory/'materials').glob('*.json')))==1
    with pytest.raises(RuntimeError, match='not resent'):
        runner(lambda _:pytest.fail('Paid request resent')).compute_all_metrics(
            pairs,run_nli=True,require_l1_audit=True,l1_record_dir=directory)
    assert len(list((directory/'requests').glob('*.json')))==2
