"""Bounded paper-scale expert simulation with immutable paid-response recovery."""
import json
from copy import deepcopy

from eval.evaluation_runtime import RuntimeBlocked, RuntimeDeferred, RuntimeResponseError
from .expert_contract import EXPERT_VERSION, EXPERT_INSTRUCTIONS, schema, validate, empty_score, absent
from .expert_sources import load
from .protocol import L4Error, RUBRIC, SLOTS, digest, read_json, save_new
from .inputs import anonymous
from .records import seal, require, derive, parse_response

MAX_ATTEMPTS = 2  # Initial judgment plus one format/contract correction; no HTTP retries.


def source_history(accesses):
    return [{'origin': 'RUNTIME_SOURCE_BOOTSTRAP', 'tool_calls': [], 'tool_results': [
        {'call': {'name': a['operation'], 'arguments': deepcopy(a['arguments'])}, 'result': deepcopy(a['result'])}
        for a in accesses]}]


def request_for(stage, artifact, task, sources, pack):
    return {'system': EXPERT_INSTRUCTIONS, 'rubric': deepcopy(RUBRIC),
            'artifact': anonymous(artifact, task, sources), 'stage_id': stage,
            'request_organization': EXPERT_VERSION, 'rater_type': 'MODEL_SIMULATED_EXPERT',
            'output_schema': schema(stage, artifact, pack['accesses']),
            'source_context': {'sha256': pack['sha256'], 'coverage': pack['coverage'],
                               'image_pages': pack['image_pages'],
                               'notice': 'All methods for this task receive this identical PDF context. Text extraction is not image inspection.'},
            'program_handled_empty_slots': artifact['empty_slots'], 'tools': {}}


def save_once(path, value):
    if path.exists():
        if read_json(path) != value:
            raise L4Error('IMMUTABLE_EXPERT_RECORD_MISMATCH:' + path.name)
    else:
        save_new(path, value)


def delimiter_diagnostic(raw):
    """Locate delimiter mismatch outside JSON strings; never repair content."""
    stack, quoted, escaped = [], False, False
    closes = {'{': '}', '[': ']'}
    for pos, char in enumerate(raw):
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in closes:
            stack.append((char, pos))
        elif char in '}]':
            expected = closes[stack[-1][0]] if stack else 'END_OF_DOCUMENT'
            if char != expected:
                return {'offset': pos, 'actual': char, 'expected': expected,
                        'open_container_offset': stack[-1][1] if stack else None}
            stack.pop()
    if quoted:
        return {'problem': 'UNTERMINATED_STRING'}
    if stack:
        return {'problem': 'UNCLOSED_CONTAINER', 'open_container_offset': stack[-1][1],
                'expected': closes[stack[-1][0]]}
    return None


def json_diagnostic(raw):
    """Describe the parser error, never repair or accept malformed output."""
    if not isinstance(raw,str):return {'problem':'Response is not text'}
    try:
        json.loads(raw)
    except json.JSONDecodeError as exc:
        result={'problem':exc.msg,'line':exc.lineno,'column':exc.colno,'offset':exc.pos}
        delimiters = delimiter_diagnostic(raw)
        if delimiters:
            result['delimiter_mismatch'] = delimiters
        if exc.msg=='Extra data':
            result['unexpected_suffix']=raw[exc.pos:exc.pos+80]
            result['instruction']='顶层 JSON 对象结束后有多余字符。完整保留所有合法内层括号、分数、理由及问题内容，不重新评分。'
            # Diagnostic input to a NEW model response, never an accepted score.
            # No truncation reconstruction, fuzzy parsing, or inner-value edits.
            if raw[exc.pos:].strip() and set(raw[exc.pos:].strip()) <= {'}', ']'}:
                try:
                    result['complete_object_for_reserialization']=parse_response(raw[:exc.pos])
                except (ValueError,TypeError):
                    pass
        else:result['instruction']='按指出的容器位置修正 JSON 语法；对象用 } 闭合，数组用 ] 闭合。保留原判断，不重新评分。'
        return result
    return None


def component(stage, artifact, task, sources, config, manifest, run_dir, client, pack):
    path = run_dir / 'components' / artifact['artifact_id'] / (stage + '.json')
    identity = {'artifact_id': artifact['artifact_id'], 'input_sha256': artifact['input_sha256'],
                'stage': stage, 'manifest_sha256': manifest['manifest_sha256'],
                'organization': EXPERT_VERSION, 'source_context_sha256': pack['sha256']}
    if path.exists():
        saved = read_json(path)
        require(saved['identity'] == identity, 'EXPERT_CHECKPOINT_IDENTITY_MISMATCH')
        validate_component(saved, artifact, pack['accesses'])
        return saved
    saved = {'identity': identity, 'transcript': [], 'receipts': [], 'status': 'FAILED',
             'error': 'REQUEST_LIMIT', 'raw_response': None, 'value': None, 'stop_run': False}
    history = source_history(pack['accesses'])
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = request_for(stage, artifact, task, sources, pack)
        turn = {'request': deepcopy(request), 'history': deepcopy(history), 'raw_response': None}
        receipt = None
        try:
            raw = client.respond(request, history)
        except RuntimeDeferred:
            raise  # Runtime request cache ensures a resumed dispatch is never repeated.
        except RuntimeResponseError as exc:
            receipt = getattr(client, 'last_receipt', None)
            raw = receipt.get('body') if isinstance(receipt, dict) else None
            turn['runtime_error'] = str(exc)
        except RuntimeBlocked as exc:
            receipt = getattr(exc, 'receipt', None) or getattr(client, 'last_receipt', None)
            turn['runtime_error'] = str(exc)
            raw = receipt.get('body') if isinstance(receipt, dict) else None
            saved.update(error=str(exc), stop_run=True)
        receipt = receipt or getattr(client, 'last_receipt', None)
        if isinstance(receipt, dict):
            saved['receipts'].append(deepcopy(receipt))
        turn['raw_response'] = raw
        saved['raw_response'] = raw
        saved['transcript'].append(turn)
        # Receipts already persisted by shared runtime; publish audit before validation.
        save_once(run_dir / 'attempts' / artifact['artifact_id'] / (stage + '-' + str(attempt) + '.json'),
                  {'identity': identity, 'attempt': attempt, 'turn': turn, 'receipt': receipt})
        if saved['stop_run']:
            break
        if isinstance(receipt, dict) and receipt.get('cost_cny') is None:
            saved.update(error='UNKNOWN_COST_STOP', stop_run=True)
            break
        try:
            if isinstance(receipt, dict) and receipt.get('finish_reason') == 'length':
                raise ValueError('TRUNCATED_RESPONSE')
            saved['value'] = validate(raw, stage, artifact, pack['accesses'])
            prior = history[-1].get('validation_feedback', {}).get('json_diagnostic') if history else None
            if prior and 'complete_object_for_reserialization' in prior:
                require(saved['value'] == prior['complete_object_for_reserialization'], 'FORMAT_CORRECTION_CHANGED_JUDGMENT')
            saved.update(status='VALID', error=None)
            break
        except (ValueError, TypeError, KeyError) as exc:
            saved['value'] = None
            saved['error'] = str(exc)
            history.append({'validation_feedback': {'errors': [str(exc)], 'rejected_response': raw,
                            'json_diagnostic': json_diagnostic(raw),
                            'incomplete_response': bool(isinstance(receipt, dict) and receipt.get('finish_reason') == 'length')}})
    saved['sha256'] = digest(saved)
    save_new(path, saved)
    return saved


def validate_component(part, artifact, accesses):
    require(part['sha256'] == digest({k: v for k, v in part.items() if k != 'sha256'}), 'EXPERT_STAGE_HASH_MISMATCH')
    require(1 <= len(part['transcript']) <= MAX_ATTEMPTS, 'EXPERT_ATTEMPT_LIMIT')
    require(part['raw_response'] == part['transcript'][-1]['raw_response'], 'EXPERT_RAW_MISMATCH')
    if part['status'] == 'VALID':
        require(not part['error'] and not part['stop_run'], 'EXPERT_VALID_STAGE_WITH_ERROR')
        if part['receipts']:
            require(part['receipts'][-1].get('finish_reason') != 'length', 'TRUNCATED_JUDGMENT')
        value = validate(part['raw_response'], part['identity']['stage'], artifact, accesses)
        prior = part['transcript'][-1]['history'][-1].get('validation_feedback', {}).get('json_diagnostic')
        if prior and 'complete_object_for_reserialization' in prior:
            require(value == prior['complete_object_for_reserialization'], 'FORMAT_CORRECTION_CHANGED_JUDGMENT')
        require(value == part['value'], 'EXPERT_JUDGMENT_MISMATCH')
    else:
        require(part['status'] == 'FAILED' and part['value'] is None, 'EXPERT_FAILED_STAGE_WITH_SCORE')


def judge(artifact, task, sources, config, manifest, run_dir, client):
    from .engine import initial_record
    record = initial_record(artifact, config, manifest, run_dir.name)
    record.update(request_organization=EXPERT_VERSION, rater_type='MODEL_SIMULATED_EXPERT',
                  research_status='NOT_RESEARCH_DATA' if config['data_class'] == 'MOCK_ONLY' else 'MODEL_SIMULATED_EXPERT_NOT_HUMAN_EXPERIMENT',
                  execution_purpose=config.get('execution_purpose', 'MODEL_EVALUATION'),
                  components=[], source_context_sha256=None)
    record['pcs'] = {s: empty_score() if s in artifact['empty_slots'] else absent('未形成有效评分。') for s in SLOTS}
    no_content = artifact.get('explicit_refusal') or artifact['state'] in {'EMPTY_OUTPUT','REFUSED'}
    record['prc'] = absent('无可评价正文。' if no_content else '未形成有效评分。',
                           'NO_EVALUABLE_CONTENT' if no_content else 'NOT_EVALUATED')
    if artifact['state'] != 'PRESENT':
        return seal(record)
    try:
        if any(s['state'] != 'READY' for s in sources):
            raise L4Error('SOURCE_UNREADABLE')
        pack = load(config, task, sources)
    except (OSError, ValueError, KeyError) as exc:
        record.update(parse_status='SOURCE_UNREADABLE', validation_errors=[str(exc)], stop_run=True)
        return seal(record)
    record['source_access'] = deepcopy(pack['accesses'])
    record['source_context_sha256'] = pack['sha256']
    combined = {'pcs': {}, 'prc': {}}
    stages = [s for s in SLOTS if s not in artifact['empty_slots']]
    if not artifact.get('explicit_refusal'):
        stages.append('prc')
    for stage in stages:
        part = component(stage, artifact, task, sources, config, manifest, run_dir, client, pack)
        record['components'].append(part)
        record['request_transcript'].extend(deepcopy(part['transcript']))
        for receipt in part['receipts']:
            record['runtime_receipts'].append({**deepcopy(receipt), 'model_request': len(record['runtime_receipts']) + 1})
        record['model_requests'] += len(part['transcript'])
        if part['status'] != 'VALID':
            record.update(parse_status='CLIENT_OR_TOOL_ERROR', validation_errors=[stage + ':' + part['error']],
                          runtime_failure='EXPERT_STAGE_FAILED', stop_run=part['stop_run'])
            break
        if stage == 'prc':
            combined['prc'] = part['value']
        else:
            combined['pcs'][stage] = part['value']
    else:
        record['pcs'].update({s: {**v, 'validation_errors': []} for s, v in combined['pcs'].items()})
        if not artifact.get('explicit_refusal'):
            record['prc'] = {**combined['prc'], 'validation_errors': []}
        record.update(parse_status='VALID', validation_errors=[], raw_response=json.dumps(combined, ensure_ascii=False),
                      raw_response_origin='DETERMINISTIC_ASSEMBLY_OF_EXPERT_STAGE_RESPONSES')
    return seal(record)


def validate_record(record, artifact, manifest, config):
    require(record['record_sha256'] == digest({k: v for k, v in record.items() if k != 'record_sha256'}), 'RECORD_HASH_MISMATCH')
    require(record['manifest_sha256'] == manifest['manifest_sha256'] and record['versions'] == manifest['versions'], 'STALE_RECORD')
    for key in ('artifact_id', 'task_id', 'project_id', 'comparison_group', 'method', 'input_sha256'):
        require(record.get(key) == artifact.get(key), 'IDENTITY_MISMATCH:' + key)
    require(record['explicit_refusal'] == artifact.get('explicit_refusal',False), 'REFUSAL_MISMATCH')
    require(record['execution_purpose'] == config.get('execution_purpose','MODEL_EVALUATION'), 'EXECUTION_PURPOSE_MISMATCH')
    require(record['artifact_state'] == artifact['state'], 'ARTIFACT_STATE_MISMATCH')
    require(record['model_config_sha256'] == digest(config['model']) and record['model_config_id'] == config['model']['config_id'], 'CONFIG_MISMATCH')
    require(record['judge_id'] == config['judge']['judge_id'] and record['judge_version'] == config['judge']['version'], 'JUDGE_MISMATCH')
    require(record['data_class'] == config['data_class'] and record['request_organization'] == EXPERT_VERSION, 'EXPERT_PROTOCOL_MISMATCH')
    expected_research = 'NOT_RESEARCH_DATA' if config['data_class'] == 'MOCK_ONLY' else 'MODEL_SIMULATED_EXPERT_NOT_HUMAN_EXPERIMENT'
    require(record['research_status'] == expected_research and record['rater_type'] == 'MODEL_SIMULATED_EXPERT', 'EXPERT_LABEL_MISMATCH')
    sources = [s for s in manifest['sources'] if s['project_id'] == artifact['project_id']]
    require(record['sources'] == [{k: s[k] for k in ('source_id','sha256','state')} for s in sources], 'SOURCE_VERSION_MISMATCH')
    task = next(t for t in config['targets'] if t['task_id'] == artifact['task_id'])
    if record['source_context_sha256'] is not None:
        pack = load(config, task, sources)
        require(record['source_context_sha256'] == pack['sha256'] and record['source_access'] == pack['accesses'], 'EXPERT_SOURCE_CONTEXT_MISMATCH')
    stages = [s for s in SLOTS if s not in artifact['empty_slots']]
    if not artifact.get('explicit_refusal'): stages.append('prc')
    parts = record['components']
    require([p['identity']['stage'] for p in parts] == stages[:len(parts)], 'EXPERT_STAGE_SET_MISMATCH')
    combined = {'pcs': {}, 'prc': {}}
    for p in parts:
        require(p['identity'] == {'artifact_id': artifact['artifact_id'], 'input_sha256': artifact['input_sha256'],
                'stage': p['identity']['stage'], 'manifest_sha256': manifest['manifest_sha256'],
                'organization': EXPERT_VERSION, 'source_context_sha256': record['source_context_sha256']}, 'EXPERT_STAGE_IDENTITY_MISMATCH')
        validate_component(p, artifact, record['source_access'])
        for turn in p['transcript']:
            require(turn['request'] == request_for(p['identity']['stage'], artifact, task, sources, pack), 'EXPERT_REQUEST_MISMATCH')
            require(turn['history'][0] == source_history(pack['accesses'])[0], 'EXPERT_CONTEXT_NOT_DELIVERED')
        if p['status'] == 'VALID':
            if p['identity']['stage'] == 'prc': combined['prc'] = p['value']
            else: combined['pcs'][p['identity']['stage']] = p['value']
    require(record['request_transcript'] == [t for p in parts for t in p['transcript']], 'EXPERT_TRANSCRIPT_MISMATCH')
    receipts = [r for p in parts for r in p['receipts']]
    require(record['runtime_receipts'] == [{**r, 'model_request': i+1} for i,r in enumerate(receipts)], 'EXPERT_RECEIPT_MISMATCH')
    require(record['model_requests'] == sum(len(p['transcript']) for p in parts), 'EXPERT_CALL_COUNT_MISMATCH')
    require(set(record['pcs']) == set(SLOTS), 'SEVEN_SLOTS_REQUIRED')
    if record['parse_status'] == 'VALID':
        require(len(parts) == len(stages) and all(p['status'] == 'VALID' for p in parts), 'EXPERT_INCOMPLETE_RESULT')
        require(parse_response(record['raw_response']) == combined, 'EXPERT_ASSEMBLY_MISMATCH')
        for s in SLOTS:
            require(record['pcs'][s] == (empty_score() if s in artifact['empty_slots'] else {**combined['pcs'][s], 'validation_errors': []}), 'EXPERT_SCORE_MISMATCH')
        if not artifact.get('explicit_refusal'):
            require(record['prc'] == {**combined['prc'], 'validation_errors': []}, 'EXPERT_PRC_MISMATCH')
    else:
        require(record['parse_status'] in {'RULE_ONLY','NOT_RUN','SOURCE_UNREADABLE','CLIENT_OR_TOOL_ERROR'}, 'EXPERT_INVALID_FAILURE_STATE')
        require(record['prc']['score'] is None, 'FAILED_PRC_CANNOT_HAVE_SCORE')
        for s,v in record['pcs'].items():
            require(v == empty_score() if s in artifact['empty_slots'] else v['score'] is None, 'FAILED_PCS_CANNOT_HAVE_SCORE')
    require(all(record[k] == v for k,v in derive(record).items()), 'AGGREGATE_MISMATCH')
