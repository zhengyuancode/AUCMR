"""Read frozen generation inputs; never import scores from the retired evaluator."""
from copy import deepcopy
from .protocol import SLOTS, file_hash, local_path, read_json, require, L4Error
from eval.serialized_slots import omitted_claims_are_empty


def validate_config(config):
    # Historical batch-generation scripts may still check a frozen v1 catalog.
    require(len(config.get('methods', [])) == 7, 'SEVEN_METHODS_REQUIRED')
    require(isinstance(config.get('targets'), list), 'TARGETS_REQUIRED')
    require(isinstance(config.get('input_bindings'), list), 'INPUT_BINDINGS_REQUIRED')


def validate_catalog(catalog, verify_files=True):
    validate_config(catalog)
    tasks = {t['task_id']: t for t in catalog['targets']}
    require(len(tasks) == len(catalog['targets']) == 93, 'EXPECTED_93_UNIQUE_CASES')
    require(len(set(catalog['methods'])) == 7, 'DUPLICATE_METHOD')
    expected = {(t, m) for t in tasks for m in catalog['methods']}
    actual = [(b['task_id'], b['method']) for b in catalog['input_bindings']]
    require(len(actual) == len(set(actual)) == 651 and set(actual) == expected, 'CATALOG_COVERAGE_MISMATCH')
    projects = {t['project_id'] for t in tasks.values()}
    require(projects == {s['project_id'] for s in catalog['sources']}, 'SOURCE_COVERAGE_MISMATCH')
    require(len({s['source_id'] for s in catalog['sources']}) == len(catalog['sources']), 'DUPLICATE_SOURCE_ID')
    if verify_files:
        if 'source_catalog' in catalog:
            require(file_hash(local_path(catalog['source_catalog'])) == catalog['source_catalog_sha256'], 'FROZEN_CATALOG_CHANGED')
        for b in catalog['input_bindings'] + catalog['sources']:
            require(file_hash(local_path(b['path'])) == b.get('sha256', b.get('expected_sha256')), 'FROZEN_INPUT_CHANGED:' + b['path'])
    return tasks


def content(binding, task):
    path = local_path(binding['path'])
    require(file_hash(path) == binding['sha256'], 'FROZEN_INPUT_CHANGED:' + binding['path'])
    raw = read_json(path)
    require(raw.get('use_case_name') == task['name'] and raw.get('actor') == task['actor'], 'TASK_IDENTITY_MISMATCH')
    require(isinstance(raw.get('slots'), dict), 'INVALID_INPUT_SLOTS')
    require('goal' in raw['slots'], 'MISSING_INPUT_GOAL')
    flags = raw.get('quality_flags', [])
    require(isinstance(flags, list), 'INVALID_QUALITY_FLAGS')
    refusal = raw.get('refusal') is True or raw.get('status') == 'REFUSED' or any(
        isinstance(x, dict) and x.get('flag_type') == 'unresolvable_use_case' for x in flags)
    slots, missing = {}, []
    for slot in SLOTS:
        if slot not in raw['slots']:
            missing.append(slot)
            continue
        item = raw['slots'][slot]
        require(isinstance(item, dict), 'INVALID_INPUT_SLOT:' + slot)
        if omitted_claims_are_empty(item, slot, refusal=refusal):
            slots[slot] = {'claims': []}
            continue
        require(isinstance(item.get('claims'), list), 'INVALID_INPUT_CLAIMS:' + slot)
        claims = []
        for claim in item['claims']:
            require(isinstance(claim, dict) and isinstance(claim.get('content'), str) and claim['content'].strip(), 'INVALID_INPUT_CLAIM:' + slot)
            require('step_number' not in claim or type(claim['step_number']) in {str, int}, 'INVALID_STEP_NUMBER')
            claims.append({k: deepcopy(claim[k]) for k in ('content', 'step_number') if k in claim})
        text = item.get('raw_text')
        require(text is None or isinstance(text, str), 'INVALID_RAW_TEXT')
        require(claims or not text or not text.strip(), 'RAW_TEXT_WITHOUT_CLAIMS')
        slots[slot] = {'claims': claims}
        if text:
            slots[slot]['raw_text'] = text
    require(not missing or refusal and all(not x['claims'] for x in slots.values()), 'MISSING_INPUT_SLOT')
    for slot in missing:
        slots[slot] = {'claims': []}
    return {'slots': slots, 'empty_slots': [s for s in SLOTS if not slots[s]['claims']],
            'no_content': all(not x['claims'] for x in slots.values()), 'explicit_refusal': refusal}


def alternatives(catalog, evaluation):
    task = next(t for t in catalog['targets'] if t['task_id'] == evaluation['task_id'])
    bindings = {(b['task_id'], b['method']): b for b in catalog['input_bindings']}
    return {chr(65+i): content(bindings[(task['task_id'], method)], task)
            for i, method in enumerate(evaluation['method_order'])}
