"""Create a new, immutable panel plan from the untouched unified93 catalog."""
import argparse
from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from eval.l4_agent.protocol import VERSION, REPO, read_json, file_hash, digest, implementation, save_new, require
from eval.l4_agent.inputs import validate_catalog
from eval.l4_agent.assignment import build


def prepare(destination, seed=20261029):
    destination = (REPO / destination).resolve()
    require(destination.is_relative_to(REPO/'config/l4_panel') and not destination.exists(), 'NEW_PANEL_CONFIG_DIRECTORY_REQUIRED')
    original = REPO/'config/evaluation_batches/unified93-v1/l4.json'
    raw = read_json(original)
    catalog = {k:deepcopy(raw[k]) for k in ('methods','targets','sources','input_bindings')}
    catalog.update(version=VERSION,source_catalog=str(original.relative_to(REPO)),source_catalog_sha256=file_hash(original))
    validate_catalog(catalog)
    plan = build(catalog,seed)
    profile = read_json(REPO/'config/evaluation_models.glm_doubao_qwen.json')
    evidence_path = REPO/'outputs/evaluation_preparation/semantic-pilot-preflight-20261025-225435/interface-usage-evidence.json'
    evidence = read_json(evidence_path)
    models = {}
    for key,letter in zip(('glm','doubao','qwen'),('A','B','C')):
        old = profile['l3'][letter]
        observations = [r for r in evidence['records'][old['api_model_id']] if r['test_success']]
        require(any('-text/' in r['request_path'] for r in observations) and any('-image/' in r['request_path'] for r in observations), 'INTERFACE_EVIDENCE_MISSING')
        for observation in observations:
            require(file_hash(observation['response_path']) == observation['response_sha256'], 'INTERFACE_RESPONSE_CHANGED')
        models[key] = {k:deepcopy(old[k]) for k in ('provider','api_model_id','base_url','api_key_env','parameters')}
        models[key].update(binding_status='NOT_BOUND',cost_control='PROVIDER_MANAGED',capabilities={'images':True},
            interface_observation={'accepted_by_controller':True,'text_and_image_success_observed':True,
                'scope':'Historical short text/image interface observations; not validation of panel scoring.',
                'evidence_path':str(evidence_path.relative_to(REPO)),'evidence_sha256':file_hash(evidence_path)},
            cost_estimate={'input_cny_per_million':old['billing']['input_cny_per_million'],
                'output_cny_per_million':old['billing']['output_cny_per_million'],
                'source':old['pricing_reference']['url'],'reference_checked_date':old['pricing_reference']['checked_date'],
                'basis':'RETAINED_CONSERVATIVE_UNCACHED_ESTIMATE_NOT_INVOICE_NOT_TOKEN_BOUND'},
            request_limits={'max_request_bytes':24_000_000,'max_images':8},
            request_limit_basis='Common engineering caps, not certified provider input/context bounds')
    # Preselect by document length category, before any new evaluation scores.
    projects = ('qheadache','microcare','milstd6016b_sec4')
    pilot = [next(t['task_id'] for t in catalog['targets'] if t['project_id']==p) for p in projects]
    config = {'version':VERSION,'live_authorized':False,'full_experiment_authorized':False,
              'catalog_path':str((destination/'catalog.json').relative_to(REPO)),
              'allocation_path':str((destination/'allocation.json').relative_to(REPO)),
              'catalog_sha256':digest(catalog),'allocation_sha256':digest(plan),'implementation':implementation(),
              'models':models,'campaign_ledger':'outputs/evaluation_campaign/budget.sqlite',
              'pilot_task_ids':pilot,'pilot_selection':'First frozen case in short, medium, long source documents; selected before scores',
              'tool_rounds':2,'format_corrections':1,'max_requests_per_evaluation':4,
              'material_limits':{'initial_source_text_chars':50000,'tools_per_round':4},
              'semantics_status':'NEW_DESIGN_NOT_LIVE_VALIDATED',
              'persona_scope':'Fixed professional lens; each agent-case has an isolated session without other scores',
              'cost_policy':'Provider spending controls; record known estimates and preserve unknown charges; no local currency ceiling'}
    save_new(destination/'catalog.json',catalog)
    save_new(destination/'allocation.json',plan)
    save_new(destination/'config.json',config)
    return config


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',default='config/l4_panel/unified93-v4')
    parser.add_argument('--seed',type=int,default=20261029)
    args = parser.parse_args()
    prepare(args.output,args.seed)
    print('Prepared '+args.output+'; no model calls, no authorization enabled.')
