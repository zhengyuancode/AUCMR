"""Deterministic, document-stratified incomplete allocation, not a strict BIBD."""
from collections import Counter, defaultdict
import random
from .protocol import VERSION, digest, require

MODELS = ('glm', 'doubao', 'qwen')
ROLES = (
    ('需求分析', '从任务范围、参与者目标与需求表达的角度阅读。'),
    ('软件架构', '从接口边界、系统行为及后续设计的角度阅读。'),
    ('测试与质量', '从前提、触发、分支、异常和可验证性的角度阅读。'),
    ('维护与交付', '从状态变化、可理解性和实施交接的角度阅读。'))


def build(catalog, seed):
    tasks = catalog['targets']
    require(len(tasks) == 93 and len({t['task_id'] for t in tasks}) == 93, 'EXPECTED_93_CASES')
    require(len(catalog['methods']) == 7, 'EXPECTED_SEVEN_METHODS')
    agents, evaluations = [], []
    for mi, model in enumerate(MODELS):
        rng = random.Random(seed + mi)
        groups = defaultdict(list)
        for task in tasks:
            groups[task['project_id']].append(task)
        for group in groups.values():
            rng.shuffle(group)
        capacities = [7] * 9 + [6] * 5
        blocks = [[] for _ in capacities]
        documents = [Counter() for _ in capacities]
        # Largest documents first; spread each document across agent blocks.
        for project, group in sorted(groups.items(), key=lambda x: (-len(x[1]), x[0])):
            for task in group:
                candidates = [i for i in range(14) if len(blocks[i]) < capacities[i]]
                tie = {i: rng.random() for i in candidates}
                i = min(candidates, key=lambda i: (documents[i][project], len(blocks[i]), tie[i]))
                blocks[i].append(task['task_id'])
                documents[i][project] += 1
        for i, block in enumerate(blocks):
            role, perspective = ROLES[i % len(ROLES)]
            agents.append({'agent_id': f'{model.upper()}-{i+1:02}', 'model_key': model,
                           'role': role, 'perspective': perspective, 'task_ids': sorted(block),
                           'identity_type': 'SIMULATED_AGENT_NOT_HUMAN'})
    # Randomize a new Latin rotation block every seven evaluations. Positions
    # stay balanced without repeatedly preserving one cyclic method ordering.
    display_rng = random.Random(seed)
    display_orders = []
    while len(display_orders) < len(tasks)*3:
        order = list(catalog['methods'])
        display_rng.shuffle(order)
        shifts = list(range(7))
        display_rng.shuffle(shifts)
        display_orders.extend(order[s:]+order[:s] for s in shifts)
    for ti, task in enumerate(tasks):
        for mi, model in enumerate(MODELS):
            agent = next(a for a in agents if a['model_key'] == model and task['task_id'] in a['task_ids'])
            displayed = display_orders[ti*3+mi]
            evaluations.append({'evaluation_id': task['task_id'] + '--' + agent['agent_id'],
                                'task_id': task['task_id'], 'agent_id': agent['agent_id'],
                                'model_key': model, 'method_order': displayed})
    result = {'version': VERSION, 'seed': seed, 'design': 'MODEL_STRATIFIED_BALANCED_INCOMPLETE_ALLOCATION',
              'catalog_sha256': digest(catalog), 'agents': agents, 'evaluations': evaluations}
    validate(result, catalog)
    return result


def validate(plan, catalog):
    require(plan['version'] == VERSION and plan['catalog_sha256'] == digest(catalog), 'ALLOCATION_CATALOG_MISMATCH')
    agents = {a['agent_id']: a for a in plan['agents']}
    require(len(agents) == len(plan['agents']) == 42, 'EXPECTED_42_UNIQUE_AGENTS')
    tasks = {t['task_id'] for t in catalog['targets']}
    require(len(plan['evaluations']) == 279, 'EXPECTED_279_EVALUATIONS')
    seen = set()
    by_task = defaultdict(list)
    by_agent = defaultdict(list)
    positions = defaultdict(Counter)
    for e in plan['evaluations']:
        require(e['evaluation_id'] not in seen, 'DUPLICATE_EVALUATION')
        seen.add(e['evaluation_id'])
        require(e['task_id'] in tasks and e['agent_id'] in agents, 'INVALID_ALLOCATION_ID')
        require(e['evaluation_id'] == e['task_id'] + '--' + e['agent_id'], 'UNSTABLE_EVALUATION_ID')
        require(e['model_key'] == agents[e['agent_id']]['model_key'], 'AGENT_MODEL_MISMATCH')
        require(len(e['method_order']) == 7 and set(e['method_order']) == set(catalog['methods']), 'INVALID_METHOD_ORDER')
        by_task[e['task_id']].append(e['model_key'])
        by_agent[e['agent_id']].append(e['task_id'])
        for i, method in enumerate(e['method_order']):
            positions[method][i] += 1
    require(set(by_task) == tasks and all(sorted(v) == sorted(MODELS) for v in by_task.values()), 'THREE_MODEL_COVERAGE_REQUIRED')
    for model in MODELS:
        group = [a for a in agents.values() if a['model_key'] == model]
        require(len(group) == 14 and Counter(len(by_agent[a['agent_id']]) for a in group) == {7: 9, 6: 5}, 'AGENT_LOAD_MISMATCH')
    for aid, agent in agents.items():
        require(sorted(by_agent[aid]) == sorted(agent['task_ids']) and len(set(agent['task_ids'])) == len(agent['task_ids']), 'AGENT_TASK_LIST_MISMATCH')
    require(all(max(v.values()) - min(v.values()) <= 1 and len(v) == 7 for v in positions.values()), 'METHOD_POSITION_IMBALANCE')
    return {'agents': 42, 'cases': 93, 'evaluations': 279, 'methods': 7, 'ratings': 1953}
