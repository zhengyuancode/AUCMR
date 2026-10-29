"""Frozen, resumable panel execution. No automatic resend of paid requests."""
from collections import Counter
from decimal import Decimal
import fcntl
import json
import os
from pathlib import Path
import sqlite3

from eval.evaluation_runtime import EvaluationRuntime, RuntimeBlocked, RuntimeDeferred, RuntimeResponseError
from eval.agent_evaluation.budget import units
from .protocol import (VERSION, REPO, SLOTS, L4Error, digest, file_hash, read_json, require,
                       local_path, save_new, sealed, unseal, implementation, canonical)
from .assignment import validate as validate_assignment
from .inputs import validate_catalog, alternatives
from .sources import Workspace, message
from .contract import INSTRUCTIONS, initial_payload, parse, validate_ratings


def load(config_path, verify_files=True):
    config_path = Path(config_path).resolve()
    config = read_json(config_path)
    require(config['version'] == VERSION and config['live_authorized'] is False, 'INVALID_PANEL_CONFIG')
    require(config['tool_rounds'] == 2 and config['format_corrections'] == 1, 'FROZEN_ATTEMPT_POLICY_CHANGED')
    require(config['campaign_ledger'] == 'outputs/evaluation_campaign/budget.sqlite', 'SHARED_LEDGER_REQUIRED')
    require(config['max_requests_per_evaluation'] == 4, 'REQUEST_LIMIT_REQUIRED')
    require(set(config['models']) == {'glm','doubao','qwen'}, 'THREE_MODELS_REQUIRED')
    catalog = read_json(local_path(config['catalog_path']))
    allocation = read_json(local_path(config['allocation_path']))
    require(digest(catalog) == config['catalog_sha256'] and digest(allocation) == config['allocation_sha256'], 'CONFIG_BINDING_MISMATCH')
    validate_catalog(catalog, verify_files=verify_files)
    validate_assignment(allocation, catalog)
    require(len(config['pilot_task_ids']) == len(set(config['pilot_task_ids'])) == 3 and
            set(config['pilot_task_ids']) <= {t['task_id'] for t in catalog['targets']}, 'THREE_PILOT_CASES_REQUIRED')
    for model in config['models'].values():
        require(model['cost_control'] == 'PROVIDER_MANAGED', 'PROVIDER_MANAGED_COST_REQUIRED')
        require(model.get('binding_status') != 'BOUND', 'DO_NOT_CLAIM_FORMAL_BINDING')
        require(model['parameters'].get('max_tokens',model['parameters'].get('max_completion_tokens')) == 8192, 'OUTPUT_LIMIT_CHANGED')
    return config, catalog, allocation


def verify_implementation(config):
    require(implementation() == config['implementation'], 'IMPLEMENTATION_CHANGED')


def costs(run, ledger):
    total = Decimal(0)
    unknown = requests = 0
    paths = sorted(Path(run).glob('sessions/*/runtime_requests/*.json'))
    if paths:
        require(Path(ledger).is_file(), 'LEDGER_MISSING')
    with sqlite3.connect(Path(ledger).resolve().as_uri() + '?mode=ro',uri=True) if paths else __import__('contextlib').nullcontext(None) as db:
        for path in paths:
            r = read_json(path)
            from eval.evaluation_runtime import digest as runtime_digest
            require(r['record_sha256'] == runtime_digest({k:v for k,v in r.items() if k != 'record_sha256'}), 'RUNTIME_CACHE_HASH_MISMATCH')
            row = db.execute('SELECT actual,state FROM reservations WHERE id=?',(r['request_id'],)).fetchone()
            require(row is not None, 'MISSING_RESERVATION')
            amount = (r.get('result') or {}).get('cost_cny')
            requests += 1
            if amount is None:
                require(row[0] is None and row[1] in {'UNPRICED','RESERVED'}, 'UNKNOWN_CHARGE_CHANGED')
                unknown += 1
            else:
                require(row == (units(amount),'SETTLED'), 'LEDGER_AMOUNT_MISMATCH')
                total += Decimal(str(amount))
    return {'request_records':requests,'known_estimated_cny':str(total),'unknown_cost_requests':unknown,
            'cost_basis':'TOKEN_ESTIMATE_NOT_INVOICE','campaign_ledger':str(ledger)}


def validate_record(record, evaluation, snapshot):
    unseal(record)
    require(record['evaluation_id'] == evaluation['evaluation_id'] and record['snapshot_sha256'] == digest(snapshot), 'SAVED_IDENTITY_CHANGED')
    require(record['status'] in {'COMPLETE','TECHNICAL_FAILURE'}, 'INVALID_FINAL_STATUS')
    require(record['status'] != 'TECHNICAL_FAILURE' or record['ratings'] is None, 'FAILURE_CANNOT_HAVE_SCORES')
    require(record['task_id'] == evaluation['task_id'] and record['agent_id'] == evaluation['agent_id'] and
            record['model_key'] == evaluation['model_key'], 'SAVED_AGENT_CHANGED')
    if record['status'] == 'COMPLETE':
        require(len(record['ratings']) == 7, 'SAVED_RATING_COUNT')
        for i,r in enumerate(record['ratings']):
            require(r['method'] == evaluation['method_order'][i] and r['label'] == chr(65+i), 'SAVED_METHOD_CHANGED')
            require(set(r['pcs']) == set(SLOTS) and all(type(s) is int and 0 <= s <= 3 for s in r['pcs'].values()), 'SAVED_PCS_INVALID')
            require(r['pcs_mean'] == sum(r['pcs'].values())/7, 'SAVED_MEAN_INVALID')
            require(r['status'] in {'SCORED','NO_EVALUABLE_CONTENT'}, 'SAVED_RATING_STATUS')
            if r['status'] == 'SCORED':
                require(type(r['prc']) is int and 1 <= r['prc'] <= 5 and
                        all(isinstance(r[k],str) and r[k].strip() for k in ('pcs_reason','prc_reason')), 'SAVED_PRC_INVALID')
            else:
                require(r['pcs_mean'] == 0 and r['prc'] is None, 'EMPTY_RESULT_INVALID')


def validate_attempt_evidence(path):
    attempt = unseal(read_json(path))
    receipt = attempt.get('receipt')
    if receipt and receipt.get('request_id'):
        cache = path.parent.parent/'runtime_requests'/(receipt['request_id']+'.json')
        require(cache.is_file(), 'MISSING_PAID_REQUEST_EVIDENCE')
        raw = read_json(cache)
        from eval.evaluation_runtime import digest as runtime_digest
        require(raw.get('record_sha256') == runtime_digest({k:v for k,v in raw.items() if k!='record_sha256'}), 'RUNTIME_CACHE_HASH_MISMATCH')
        require(raw.get('request_id') == receipt['request_id'], 'REQUEST_ID_MISMATCH')
        # Interrupted requests have no result. Completed receipts must retain
        # identical charge, usage, identity and body in the runtime evidence.
        if raw.get('result'):
            for key in ('cost_cny','raw_usage','response_model','body','finish_reason'):
                require(raw['result'].get(key) == receipt.get(key), 'ATTEMPT_RECEIPT_MISMATCH:'+key)
    return attempt


def evaluate_one(config, catalog, evaluation, agent, run, snapshot, runtime_factory=EvaluationRuntime):
    eid = evaluation['evaluation_id']
    session = run / 'sessions' / eid
    record = {'version':VERSION,'evaluation_id':eid,'task_id':evaluation['task_id'],
              'agent_id':agent['agent_id'],'model_key':agent['model_key'],
              'snapshot_sha256':digest(snapshot),'status':'TECHNICAL_FAILURE','error':None,
              'ratings':None,'source_access':[],'attempts':[],
              'research_status':'SIMULATED_RATERS_NOT_HUMAN_PARTICIPANTS'}
    # Input/infrastructure errors remain fatal: never turn altered material into scores.
    views = alternatives(catalog,evaluation)
    if all(v['no_content'] for v in views.values()):
        verify_implementation(config)
        record.update(status='COMPLETE',ratings=[{
            'method':method,'label':chr(65+i),'status':'NO_EVALUABLE_CONTENT',
            'pcs':dict.fromkeys(SLOTS,0),'pcs_mean':0,'prc':None,
            'reason':'原生成输出无可评价正文；不是技术失败。'} for i,method in enumerate(evaluation['method_order'])])
        return sealed(record)
    workspace = Workspace(catalog,evaluation,config['material_limits'])
    source = workspace.initial()
    task = workspace.task
    messages = [{'role':'system','content':INSTRUCTIONS}, message(initial_payload(task,agent,views,source))]
    runtime = runtime_factory(config['models'][agent['model_key']],session,'L4P',
                              local_path(config['campaign_ledger']),live_authorized=True,
                              http_timeout_seconds=600)
    read_rounds = corrections = 0
    for round_id in range(config['max_requests_per_evaluation']):
        verify_implementation(config)
        # Exact inputs are checked again before each new/replayed model round.
        for b in catalog['input_bindings']:
            if b['task_id'] == evaluation['task_id']:
                require(file_hash(local_path(b['path'])) == b['sha256'], 'FROZEN_INPUT_CHANGED')
        for s in workspace.sources.values():
            require(file_hash(local_path(s['path'])) == s.get('sha256',s.get('expected_sha256')), 'SOURCE_HASH_MISMATCH')
        checkpoint = session / 'attempts' / f'{round_id:02}.json'
        signature = digest(messages)
        if checkpoint.exists():
            attempt = validate_attempt_evidence(checkpoint)
            require(attempt['messages_sha256'] == signature, 'REPLAY_MESSAGE_MISMATCH')
        else:
            receipt, error = None, None
            print(f'L4_PANEL_REQUEST {eid} round={round_id}',flush=True)
            try:
                receipt = runtime.request(messages,task_id=eid+':round:'+str(round_id))
            except (RuntimeBlocked,RuntimeResponseError,RuntimeDeferred) as exc:
                receipt = getattr(exc,'receipt',None)
                error = str(exc)
                if any(s in error.lower() for s in ('integrity','ledger','reservation','not_authorized','key_not_set','model_not_bound')):
                    raise L4Error('RUNTIME_INTEGRITY_FAILURE:' + error) from None
            attempt = sealed({'messages_sha256':signature,'receipt':receipt,'error':error})
            save_new(checkpoint,attempt)
        receipt = attempt['receipt']
        record['attempts'].append({'round':round_id,'path':str(checkpoint.relative_to(REPO)),
                                   'request_id':receipt.get('request_id') if receipt else None})
        if not receipt or receipt.get('cost_cny') is None:
            record['error'] = attempt['error'] or 'UNKNOWN_COST'
            break  # Preserve unknown cost and skip this item; never call it zero.
        if attempt['error'] and attempt['error'].startswith('SECRET_ECHOED'):
            raise L4Error('SECRET_ECHOED: redacted evidence retained; execution stopped')
        # Only a schema/JSON rejection can receive the predeclared correction.
        # Do not turn a security rejection or missing model identity into success.
        if attempt['error'] and not attempt['error'].startswith(('INVALID_JSON_RESPONSE','INCOMPLETE_FINISH_REASON')):
            record['error'] = attempt['error']
            break
        try:
            require(receipt.get('finish_reason') == 'stop', 'TRUNCATED_OR_INVALID_FINISH')
            obj = parse(receipt['body'])
            if set(obj) == {'tool_calls'}:
                require(read_rounds < config['tool_rounds'], 'TOOL_ROUND_LIMIT')
                try:
                    returned = workspace.tools(obj['tool_calls'])
                except L4Error as exc:
                    if any(x in str(exc) for x in ('HASH','FROZEN_INPUT','TASK_IDENTITY','MISSING_INPUT')):
                        raise OSError('SOURCE_INTEGRITY_FAILURE:' + str(exc)) from exc
                    raise
                messages.extend([{'role':'assistant','content':canonical(obj)},
                                 message({'historical_source_reads':returned,'notice':'历史工具结果，不是返回模板。'})])
                read_rounds += 1
                if read_rounds == config['tool_rounds']:
                    messages.append({'role':'user','content':'已达到本次阅读轮数，请基于已经读取的材料返回完整ratings，不再请求工具。'})
                continue
            scores = validate_ratings(obj,views)
            ratings = []
            for i, method in enumerate(evaluation['method_order']):
                label = chr(65+i)
                if views[label]['no_content']:
                    ratings.append({'method':method,'label':label,'status':'NO_EVALUABLE_CONTENT',
                                    'pcs':dict.fromkeys(views[label]['slots'],0),'pcs_mean':0,'prc':None,
                                    'reason':'原生成输出无可评价正文；不是技术失败。'})
                else:
                    v = scores[label]
                    ratings.append({'method':method,'label':label,'status':'SCORED',**v,
                                    'pcs_mean':sum(v['pcs'].values())/7})
            record.update(status='COMPLETE',ratings=ratings,error=None)
            break
        except (ValueError,KeyError,TypeError) as exc:
            record['error'] = str(exc)
            if corrections >= config['format_corrections']:
                break
            corrections += 1
            messages.extend([{'role':'assistant','content':receipt.get('body') or ''},
                             {'role':'user','content':canonical({'validation_error':str(exc),
                              'instruction':'仅纠正字段、JSON格式或范围错误，保留判断和理由，不抬高或压低评分。返回完整ratings或仍被允许的规范工具请求。'})}])
    else:
        record['error'] = 'REQUEST_LIMIT'
    record['source_access'] = workspace.audit
    return sealed(record)


def run(config_path, run_dir, *, scope='pilot', authorize_live=False, factory=EvaluationRuntime):
    require(authorize_live is True, 'LIVE_NOT_AUTHORIZED')
    config,catalog,allocation = load(config_path)
    verify_implementation(config)
    require(scope in {'pilot','full'}, 'INVALID_SCOPE')
    if scope == 'full':
        require(config.get('full_experiment_authorized') is True, 'FULL_EXPERIMENT_NOT_AUTHORIZED')
    selected = [e for e in allocation['evaluations'] if scope == 'full' or e['task_id'] in config['pilot_task_ids']]
    if scope == 'pilot':
        selected.sort(key=lambda e:(config['pilot_task_ids'].index(e['task_id']),('glm','doubao','qwen').index(e['model_key'])))
    run_dir = local_path(str(run_dir))
    require(run_dir.is_relative_to(REPO/'outputs/agent_l4_panel'), 'PANEL_RUN_DIRECTORY_REQUIRED')
    keys = {m['api_key_env'] for m in config['models'].values()}
    if factory is EvaluationRuntime:
        presence = {k:bool(os.environ.get(k)) for k in sorted(keys)}
        print(json.dumps(presence),flush=True)
        require(all(presence.values()), 'MISSING_MODEL_KEY')
    run_dir.mkdir(parents=True,exist_ok=True)
    with (run_dir/'execution.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        snapshot = {'config':config,'catalog_sha256':digest(catalog),'allocation_sha256':digest(allocation),
                    'scope':scope,'evaluation_ids':[e['evaluation_id'] for e in selected]}
        saved = run_dir/'snapshot.json'
        if saved.exists():
            require(read_json(saved) == snapshot, 'FROZEN_RUN_CHANGED')
        else:
            require(not list(run_dir.glob('records/*.json')), 'RECORDS_WITHOUT_SNAPSHOT')
            save_new(saved,snapshot)
        agents = {a['agent_id']:a for a in allocation['agents']}
        selected_by_id = {e['evaluation_id']:e for e in selected}
        # Audit every saved item before dispatching any remaining request.
        saved_records = {}
        for path in sorted(run_dir.glob('records/*.json')):
            saved_record = read_json(path)
            require(path.stem in selected_by_id, 'UNEXPECTED_SAVED_EVALUATION')
            validate_record(saved_record,selected_by_id[path.stem],snapshot)
            saved_records[path.stem] = saved_record
            for attempt in saved_record['attempts']:
                expected = run_dir/'sessions'/path.stem/'attempts'/f"{attempt['round']:02}.json"
                require(local_path(attempt['path']) == expected and expected.is_file(), 'MISSING_OR_MOVED_ATTEMPT')
        for path in sorted(run_dir.glob('sessions/*/attempts/*.json')):
            validate_attempt_evidence(path)
        costs(run_dir,local_path(config['campaign_ledger']))
        def progress():
            states = Counter(r['status'] for r in saved_records.values())
            print('L4_PANEL_PROGRESS '+json.dumps({'processed':len(saved_records),'planned':len(selected),
                  'completed':states['COMPLETE'],'technical_failures':states['TECHNICAL_FAILURE'],
                  'remaining':len(selected)-len(saved_records),**costs(run_dir,local_path(config['campaign_ledger']))}),flush=True)
        progress()
        for e in selected:
            path = run_dir/'records'/(e['evaluation_id']+'.json')
            if e['evaluation_id'] in saved_records:
                continue
            record = evaluate_one(config,catalog,e,agents[e['agent_id']],run_dir,snapshot,factory)
            validate_record(record,e,snapshot)
            save_new(path,record)
            saved_records[e['evaluation_id']] = record
            progress()
        from .export import export
        records = [saved_records[e['evaluation_id']] for e in selected]
        return export(run_dir,records,snapshot,costs(run_dir,local_path(config['campaign_ledger'])))
