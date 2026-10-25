"""Regression cases from the controller audit. No provider or generation calls."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from eval.compute_metrics import NLIJudge
from eval.l2_judge import _validate_issue, _location_is_valid, L2Judge
from eval.run_experiments import ExperimentRunner, generate_latex_table_l1, generate_latex_table_l3
from eval.legacy_contracts import claim_population, cohort_inventory, result_identity, target_id
from eval.implementation import implementation_manifest
from eval.agent_evaluation.transport import normalize_response
from eval.agent_evaluation.__main__ import frozen_runtime_hash


def test_l1_malformed_judgment_blocks_valid_aggregation():
    from config.settings import ModelEndpoint
    from eval.compute_metrics import MetricsComputer, UCMetrics
    judge=NLIJudge(endpoint=ModelEndpoint(model_name='constructed'), max_attempts=1,
                   model_fn=lambda _: '{"entailment":"entailment"}')
    computer=MetricsComputer(nli_judge=judge)
    entailed,details,failures=computer.compute_l1_nli_g(
        {'slots':{'goal':{'claims':[{'content':'Goal','evidence_refs':['E1']}]}}},
        {'evidence_cards':[{'card_id':'E1','content':'Constructed evidence'}]})
    assert entailed==0 and len(failures)==1
    metric=UCMetrics('Task','Actor',total_claims=1,nli_evaluated=True,nli_parse_failures=failures)
    runner=ExperimentRunner.__new__(ExperimentRunner);runner.metrics_computer=computer
    with pytest.raises(RuntimeError):runner.aggregate_and_report({'AUCMR (CREST full)':[metric]})


def test_missing_key_is_deferred_before_any_request(tmp_path):
    from tests.test_evaluation_runtime import binding, MESSAGES
    from eval.evaluation_runtime import EvaluationRuntime, RuntimeDeferred
    runtime=EvaluationRuntime(binding(),tmp_path/'run','L4',tmp_path/'budget.sqlite',live_authorized=True)
    with patch.dict('os.environ',{},clear=True), patch.object(runtime,'_http') as http:
        with pytest.raises(RuntimeDeferred) as caught:runtime.request(MESSAGES,task_id='fixture')
        assert caught.value.receipt is None
        http.assert_not_called()
    assert runtime.real_model_calls==0 and runtime.budget.summary()['states']==[]


def test_legacy_stage_exports_frozen_identity_and_rule_esc(tmp_path,monkeypatch):
    import run
    from config.settings import ModelEndpoint
    methods=['TextRAG-Elaborate','AUCMR (CREST full)']
    result_map={}
    for i,method in enumerate(methods):
        path=tmp_path/(str(i)+'.json'); evidence=tmp_path/(str(i)+'-evidence.json')
        claim={'content':'Goal','evidence_refs':['E1']}
        slots={s:{'claims':[]} for s in ('goal','preconditions','trigger','main_flow','alternative_flow','exception_flow','postconditions')}
        slots['goal']['claims']=[claim]
        path.write_text(json.dumps({'use_case_name':'Task','actor':'Actor','method':method,'slots':slots,
            'citation_audit':{'version':'pre_constraint_draft_v1','pre_constraint_slots':{
                'goal':{'claims':[claim],'exposed_evidence_refs':['E1']}}}}),encoding='utf-8')
        evidence.write_text(json.dumps({'evidence_cards':[{'card_id':'E1','content':'Goal'}]}),encoding='utf-8')
        result_map[method,'P','Task','Actor']=(str(path),str(evidence))
    runner=ExperimentRunner(str(tmp_path/'unused'),baseline_names=[])
    runner.metrics_computer.nli_judge=NLIJudge(endpoint=ModelEndpoint(model_name='fixture'),model_fn=lambda _:json.dumps({
        'entailment':'entailment','slot_fit':True,'rationale':'fixture source','slot_fit_rationale':'fixture goal'}))
    monkeypatch.setattr('eval.l2_judge.chat_completion',lambda *args,**kwargs:'[]')
    for stage in ('l1','l2'):
        destination=tmp_path/stage
        run._write_evaluation_outputs({},runner,result_map,destination,stage)
        manifest=json.loads((destination/f'evaluation_manifest_{stage}.json').read_text(encoding='utf-8'))
        assert len(manifest['input_bindings'])==2 and manifest['implementation']==implementation_manifest('legacy')
        audit=json.loads((destination/f'evaluation_audit_{stage}.json').read_text(encoding='utf-8'))
        assert all(r['project_id']=='P' and len(r['input_hash'])==64 and len(r['evidence_hash'])==64
                   for rows in audit.values() for r in rows)
    summary=json.loads((tmp_path/'l2/aggregated_metrics_l2.json').read_text(encoding='utf-8'))
    assert all(r['l2_empty_slots']==6 and r['consistency_judged_ucs']==1 for r in summary.values())
    assert '6 / 6.00' in (tmp_path/'l2/table_l2_consistency.tex').read_text(encoding='utf-8')


def test_l3_raw_http_error_shape_is_persisted_before_settlement(tmp_path,monkeypatch):
    import io
    from eval.agent_evaluation.transport import LiveTransport
    from eval.agent_evaluation.agents.recovery import TaskRecovery
    from eval.agent_evaluation.budget import Budget
    from tests.test_evaluation_billing_budget import binding as bounded_binding, tier
    binding = bounded_binding()
    binding['api_key_env'] = 'CONSTRUCTED_KEY'
    binding['billing']['tariff']['tiers'] = [tier(0, None, 1, 1, 1)]
    raw={'model':'fixture','choices':[], 'usage':{'prompt_tokens':100,'completion_tokens':50},
         'echo':'constructed-secret'}
    class Opener:
        def open(self,*args,**kwargs):return io.BytesIO(json.dumps(raw).encode())
    monkeypatch.setenv('CONSTRUCTED_KEY','constructed-secret')
    monkeypatch.setattr('urllib.request.build_opener',lambda *args:Opener())
    transport=LiveTransport.__new__(LiveTransport)
    budget=Budget(tmp_path/'budget.sqlite')
    recovery=TaskRecovery(tmp_path,'fixture','fingerprint',budget,transport)
    result,_,_,_=recovery.request('request',binding,[{'role':'user','content':'x'*200}],{'task_id':'fixture'},1,
        {'input':.001,'output':.001,'reasoning':0,'images':0,'other':0})
    saved=json.loads(next((tmp_path/'task_progress').rglob('request-1.json')).read_text(encoding='utf-8'))
    assert saved['result']['raw_response']['choices']==[]
    assert saved['result']['raw_usage']==raw['usage']
    assert 'constructed-secret' not in json.dumps(saved)
    assert result['cost_cny']==pytest.approx(.00015)
    assert budget.summary()['states']==['SETTLED']


def test_legacy_stage_currency_checks_implementation_and_files(tmp_path):
    import run
    _,paths=write_pair(tmp_path,'AUCMR')
    binding=result_identity(('AUCMR (CREST full)','P','Task','Actor'),paths)
    for name in run.EVALUATION_STAGE_FILES['l1']:(tmp_path/name).write_text('{}',encoding='utf-8')
    manifest={'version':run.L1_EVALUATION_VERSION,'implementation':implementation_manifest('legacy'),'input_bindings':[binding]}
    (tmp_path/'evaluation_manifest_l1.json').write_text(json.dumps(manifest),encoding='utf-8')
    assert run._evaluation_stage_current(tmp_path,'l1',0)[0]
    with patch('eval.implementation.implementation_manifest',return_value={'changed':'code'}):
        assert not run._evaluation_stage_current(tmp_path,'l1',0)[0]
    Path(paths[1]).write_text('{"evidence_cards":[{}]}',encoding='utf-8')
    assert not run._evaluation_stage_current(tmp_path,'l1',0)[0]


@pytest.mark.parametrize('change', [
    {'slot_fit': None}, {'slot_fit':'UNKNOWN'}, {'slot_fit':'true'}, {'slot_fit':1},
    {'rationale':''}, {'slot_fit_rationale':None}, {'entailment':'unknown'},
])
def test_l1_invalid_field_is_technical_not_semantic(change):
    judge=NLIJudge(endpoint=object())
    response={'entailment':'entailment','slot_fit':True,'rationale':'source supports', 'slot_fit_rationale':'correct role'}
    response.update(change)
    assert judge._parse_response(json.dumps(response)) is None


def test_l1_required_field_and_valid_negative():
    judge=NLIJudge(endpoint=object())
    assert judge._parse_response('{"entailment":"entailment"}') is None
    parsed=judge._parse_response(json.dumps({'entailment':'neutral','slot_fit':False,'rationale':'missing support','slot_fit_rationale':'wrong role'}))
    assert parsed['entailment']=='neutral' and parsed['slot_fit'] is False


@pytest.mark.parametrize('index', ['bad', True, 0.9, -2, None])
def test_rule_l2_invalid_index_is_not_coerced(index):
    assert _validate_issue({'rule_violated':'altflow_diverge_points', 'offending_slot':'alternative_flow',
                            'offending_claim_index':index,'rationale':'a concrete problem'}) is None


def test_rule_l2_empty_location_and_wrong_group_fail_validation():
    issue={'rule_violated':'altflow_diverge_points','offending_slot':'alternative_flow',
           'offending_claim_index':-1,'rationale':'the branch points to step 99'}
    violation=_validate_issue(issue)
    assert not _location_is_valid(violation,{'alternative_flow':{'claims':[]}})
    responses=iter([json.dumps([issue]),'[]'])
    judge=L2Judge(model_fn=lambda *args:next(responses), endpoint=object())
    result=judge.judge('Task','Actor',{'alternative_flow':{'claims':[{'content':'branch'}]}})
    assert result['parse_failures'] and not any(result['summary'].values())
    assert _validate_issue({**issue,'rationale':''}) is None


def test_absent_method_never_exports_zero():
    assert generate_latex_table_l1({}) == ''
    assert generate_latex_table_l3({}) == ''
    for fn in (generate_latex_table_l1,generate_latex_table_l3):
        text=fn({},expected_methods=['AUCMR','ReAct-Elaborate'])
        assert text.count('NOT_RUN')==2 and '0.00' not in text and '0.0' not in text
        assert 'NOT_RUN' in fn({'AUCMR':{}})
        with pytest.raises(ValueError,match='Duplicate'):
            fn({'AUCMR':{},'AUCMR (CREST full)':{}})


def write_pair(tmp_path,method=None):
    data={'use_case_name':'Task','actor':'Actor','slots':{'goal':{'claims':[{'content':'Goal'}]}}}
    if method is not None:data['method']=method
    p=tmp_path/'input.json'; p.write_text(json.dumps(data),encoding='utf-8')
    e=tmp_path/'evidence.json'; e.write_text('{"evidence_cards":[]}',encoding='utf-8')
    return data,(str(p),str(e))


def test_bound_identity_not_default_aucmr_and_hashes(tmp_path):
    _,paths=write_pair(tmp_path)
    key=('TextRAG-Elaborate','P','Task','Actor')
    identity=result_identity(key,paths)
    assert identity['method']=='TextRAG-Elaborate'
    assert len(identity['input_hash'])==len(identity['evidence_hash'])==64
    assert identity['project_id']=='P' and identity['target_id']==target_id('P','Task','Actor')
    assert identity['claim_population']=='LEGACY_FINAL_FALLBACK'
    from scripts.prepare_evaluation_batch import encoded
    import hashlib
    assert identity['target_id']=='UC-'+hashlib.sha256(encoded(('P','Actor','Task'))).hexdigest()[:16]


def test_mismatched_identity_stops_before_metric_calls(tmp_path):
    _,paths=write_pair(tmp_path,'AUCMR')
    runner=ExperimentRunner.__new__(ExperimentRunner)
    with patch.object(runner,'metrics_computer',create=True) as computer:
        with pytest.raises(ValueError,match='method differs'):
            runner.compute_all_metrics({('ReAct-Elaborate','P','Task','Actor'):paths},run_nli=True)
        computer.compute_all.assert_not_called()


def test_missing_matrix_pairs_retained(tmp_path):
    _,paths=write_pair(tmp_path,'AUCMR')
    result=cohort_inventory({('AUCMR (CREST full)','P','Task','Actor'):paths},
                           ['AUCMR (CREST full)','ReAct-Elaborate'],{('P','Task','Actor')})
    assert len(result['registered_pairs'])==2 and result['missing_pairs']==1


def test_formal_l1_no_silent_fallback_or_partial_audit(tmp_path):
    data,_=write_pair(tmp_path)
    with pytest.raises(ValueError,match='fall back'):claim_population(data,required=True)
    data['citation_audit']={'version':'pre_constraint_draft_v1','pre_constraint_slots':{
        'main_flow':{'claims':[],'exposed_evidence_refs':[]}}}
    with pytest.raises(ValueError,match='missing from citation audit'):claim_population(data,required=True)
    data['citation_audit']['pre_constraint_slots']['goal']={'claims':[{'content':'Goal'}],'exposed_evidence_refs':[]}
    assert claim_population(data,required=True)['claim_population']=='AUDIT_CANDIDATES'


@pytest.mark.parametrize('claim', [None, {}, {'content':''}, {'content':7},
                                  {'content':'Goal','evidence_refs':'E1'}])
def test_formal_l1_malformed_audit_member_is_rejected(claim):
    data={'slots':{},'citation_audit':{'version':'pre_constraint_draft_v1',
        'pre_constraint_slots':{'goal':{'claims':[claim],'exposed_evidence_refs':[]}}}}
    with pytest.raises(ValueError,match='Invalid citation audit claim'):
        claim_population(data,required=True)
    data['citation_audit']['pre_constraint_slots']['goal']['claims']=[{'content':'Uncited claim'}]
    assert claim_population(data,required=True)['claim_population']=='AUDIT_CANDIDATES'


def test_collector_checks_path_bound_method_task_and_duplicate(tmp_path):
    runner=ExperimentRunner.__new__(ExperimentRunner)
    directory=tmp_path/'aucmr_full/P';directory.mkdir(parents=True)
    path=directory/'Actor_Task.json';evidence=directory/'Actor_Task-evidence.json'
    data={'actor':'Actor','use_case_name':'Task','method':'TextRAG-Elaborate','slots':{}}
    path.write_text(json.dumps(data),encoding='utf-8');evidence.write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError,match='method differs'):
        runner.collect_crest_results(str(tmp_path))
    del data['method'];path.write_text(json.dumps(data),encoding='utf-8')
    results=runner.collect_crest_results(str(tmp_path))
    assert list(results)==[('AUCMR (CREST full)','P','Task','Actor')]
    duplicate=tmp_path/'archive/aucmr_full/P';duplicate.mkdir(parents=True)
    (duplicate/path.name).write_bytes(path.read_bytes());(duplicate/evidence.name).write_bytes(evidence.read_bytes())
    with pytest.raises(ValueError,match='Duplicate'):
        runner.collect_crest_results(str(tmp_path))


@pytest.mark.parametrize('field,value',[('method','AUCMR'),('actor','Another actor'),('artifact_revision_id','other')])
def test_evidence_identity_or_revision_mismatch_blocks_evaluation(tmp_path,field,value):
    _,paths=write_pair(tmp_path,'TextRAG-Elaborate')
    Path(paths[1]).write_text(json.dumps({field:value,'evidence_cards':[]}),encoding='utf-8')
    with pytest.raises(ValueError):
        result_identity(('TextRAG-Elaborate','P','Task','Actor'),paths)


def test_bound_visual_method_reaches_actual_l1_image_judging(tmp_path):
    from eval.compute_metrics import MetricsComputer
    from PIL import Image
    image=tmp_path/'page.png';Image.new('RGB',(3,3),'white').save(image)
    data,paths=write_pair(tmp_path)
    data['slots']['goal']['claims']=[{'content':'Goal','evidence_refs':['E1']}]
    Path(paths[0]).write_text(json.dumps(data),encoding='utf-8')
    Path(paths[1]).write_text(json.dumps({'evidence_cards':[{'card_id':'E1','content':'',
        'image_paths':[str(image)]}]}),encoding='utf-8')
    before=[Path(p).read_bytes() for p in paths]
    class Judge:
        def __init__(self):self.images=[]
        def judge(self,claim,evidence_texts,image_paths=None,slot_name=None):
            self.images.append(image_paths)
            return {'entailment':'entailment','slot_fit':True,'rationale':'source','slot_fit_rationale':'goal'}
    judge=Judge();runner=ExperimentRunner.__new__(ExperimentRunner)
    runner.metrics_computer=MetricsComputer(nli_judge=judge)
    result=runner.compute_all_metrics({('ViDoRAG-Elaborate','P','Task','Actor'):paths},run_nli=True)
    assert judge.images==[[str(image)]]
    assert result['ViDoRAG-Elaborate'][0].total_entailed_claims==1
    assert [Path(p).read_bytes() for p in paths]==before


def test_l3_malformed_response_keeps_raw_usage():
    binding={'billing':{'all_charges_in_token_usage':True,'reasoning_in_output':True,
                        'input_cny_per_million':1,'output_cny_per_million':1}}
    raw={'model':'fixture','choices':[], 'usage':{'prompt_tokens':100,'completion_tokens':50}}
    result=normalize_response(raw,binding)
    assert result['raw_response']==raw and result['raw_usage']==raw['usage']
    assert result['cost_cny']==pytest.approx(.00015) and result['body'] is None
    for raw in ({'choices':None}, {'choices':[{'message':None}],'usage':[]}, []):
        result=normalize_response(raw,binding)
        assert result['raw_response']==raw and result['cost_cny'] is None


def test_implementation_freezes_shared_budget_and_changes_l3_acceptance():
    for layer in ('l2','l3','l4'):
        frozen=implementation_manifest(layer)
        assert 'eval/agent_evaluation/budget.py' in frozen
        assert 'eval/implementation.py' in frozen
    first=frozen_runtime_hash({}, {})
    with patch('eval.agent_evaluation.__main__.implementation_manifest', return_value={'changed':'implementation'}):
        assert frozen_runtime_hash({}, {}) != first
