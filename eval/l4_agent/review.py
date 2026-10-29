"""Read-only material and request-size review. Never creates a live runtime."""
from collections import Counter
from .protocol import canonical, digest
from .runner import load, verify_implementation
from .inputs import alternatives
from .sources import Workspace, message
from .contract import INSTRUCTIONS, initial_payload
from eval.evaluation_cost_policy import check_provider_request


def check(config_path):
    config,catalog,allocation = load(config_path)
    verify_implementation(config)
    agents = {a['agent_id']:a for a in allocation['agents']}
    checked, no_content, empty, sizes, source_sizes = 0, 0, 0, [], {}
    for e in allocation['evaluations']:
        views = alternatives(catalog,e)
        workspace = Workspace(catalog,e,config['material_limits'])
        initial = initial_payload(workspace.task,agents[e['agent_id']],views,workspace.initial())
        messages = [{'role':'system','content':INSTRUCTIONS},message(initial)]
        request = check_provider_request(config['models'][e['model_key']],messages)
        sizes.append(request['request_bytes'])
        source_sizes[workspace.task['project_id']] = {'pages':sum(len(p) for p in workspace.pages.values()),
                                                    'text_chars':sum(len(t) for p in workspace.pages.values() for t in p)}
        checked += 1
        if e['model_key'] == 'glm':
            no_content += sum(v['no_content'] for v in views.values())
            empty += sum(len(v['empty_slots']) for v in views.values())
    return {'version':config['version'],'agents':42,'cases':93,'agent_case_evaluations':checked,
            'method_outputs':651,'empty_method_outputs':no_content,'empty_slots':empty,
            'initial_request_bytes':{'min':min(sizes),'max':max(sizes)},'source_material':source_sizes,
            'pilot_task_ids':config['pilot_task_ids'],'pilot_evaluations':9,
            'base_calls':279,'maximum_calls':1116,'pilot_base_calls':9,'pilot_maximum_calls':36,
            'scope':'OFFLINE_INPUT_AND_ENGINEERING_LIMITS_ONLY','real_model_calls':0,
            'token_capacity_or_semantic_validity':'NOT_PROVEN_BY_OFFLINE_REVIEW'}
