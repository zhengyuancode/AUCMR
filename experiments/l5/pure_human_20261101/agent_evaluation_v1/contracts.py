"""Mechanical contracts for the source-only Agent evaluation.

These checks establish schema, literal provenance and cross-record consistency.
They do NOT establish semantic support, complete atomization, or correct judgments.
No filesystem access, subprocesses, network, or model calls occur in this module.
"""
from copy import deepcopy
import re

CATEGORIES = frozenset(('condition', 'main', 'branch', 'result'))
REFERENCE_STATUSES = frozenset(('CONFIRMED', 'UNRESOLVED', 'EXCLUDED'))
JUDGMENTS = frozenset(('S', 'X', 'U', 'Q'))


def fail(message):
    raise ValueError(message)


def mapping(value, context):
    if not isinstance(value, dict):
        fail(f'{context}: expected object')
    return value


def sequence(value, context):
    if not isinstance(value, list):
        fail(f'{context}: expected array')
    return value


def nonempty(value, context):
    if not isinstance(value, str) or not value.strip():
        fail(f'{context}: expected nonempty string')
    return value


def identifier(value, context):
    nonempty(value, context)
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]*', value):
        fail(f'{context}: invalid stable identifier')
    return value


def check_agent_provenance(record):
    if record.get('provenance') in ('human_entered', 'human_consensus'):
        fail('Agent record must not claim human provenance')


def exact_occurrences(text, quote):
    """Find all literal substring starts, including overlaps, no normalization."""
    nonempty(quote, 'quote')
    starts = []
    offset = 0
    while True:
        start = text.find(quote, offset)
        if start < 0:
            return starts
        starts.append(start)
        offset = start + 1


def literal_span(text, quote, occurrence=0, context='quote'):
    if type(occurrence) is not int or occurrence < 0:
        fail(f'{context}: occurrence must be a nonnegative integer')
    starts = exact_occurrences(text, quote)
    if occurrence >= len(starts):
        fail(f'{context}: requested occurrence {occurrence}, but exact case-sensitive quote has {len(starts)} matches; index is zero-based per identical string, not per meaning')
    start = starts[occurrence]
    return {'start': start, 'end': start + len(quote),
            'occurrence': occurrence, 'occurrence_count': len(starts)}


def source_parts(source):
    mapping(source, 'source')
    parts = {}
    for i, part in enumerate(sequence(source.get('requirements'), 'source.requirements')):
        mapping(part, f'requirements[{i}]')
        part_id = nonempty(part.get('source_part_id'), f'requirements[{i}].source_part_id')
        if part_id in parts:
            fail(f'duplicate source_part_id: {part_id}')
        if not isinstance(part.get('text'), str):
            fail(f'{part_id}: source text must be string')
        page = part.get('pdf_page')
        if type(page) is not int or page < 1:
            fail(f'{part_id}: pdf_page must be 1-based integer')
        parts[part_id] = part
    return parts


def validate_evidence(evidence, parts, context, required=False):
    entries = sequence(evidence, context)
    if required and not entries:
        fail(f'{context}: source evidence required')
    enriched = []
    for i, original in enumerate(entries):
        entry = deepcopy(mapping(original, f'{context}[{i}]'))
        part_id = nonempty(entry.get('part_id'), f'{context}[{i}].part_id')
        if part_id not in parts:
            fail(f'{context}[{i}]: unknown source part {part_id}')
        quote = nonempty(entry.get('quote'), f'{context}[{i}].quote')
        part = parts[part_id]
        entry['source_span'] = literal_span(part['text'], quote, entry.get('occurrence', 0), f'{context}[{i}]')
        if 'pdf_page' in entry and entry['pdf_page'] != part['pdf_page']:
            fail(f'{context}[{i}]: incorrect PDF page')
        entry['pdf_page'] = part['pdf_page']
        enriched.append(entry)
    return enriched


def validate_reference(record, source):
    """Return a deep-copied reference enriched with exact source offsets/pages."""
    out = deepcopy(mapping(record, 'reference'))
    check_agent_provenance(out)
    parts = source_parts(source)
    nonempty(out.get('scope'), 'reference.scope')
    obligations = sequence(out.get('obligations'), 'reference.obligations')
    sequence(out.get('unresolved'), 'reference.unresolved')
    seen = set()
    for i, item in enumerate(obligations):
        mapping(item, f'obligations[{i}]')
        ident = identifier(item.get('id'), f'obligations[{i}].id')
        if ident in seen:
            fail(f'duplicate obligation id: {ident}')
        seen.add(ident)
        nonempty(item.get('text'), f'{ident}.text')
        for field in ('scope', 'atomicity', 'reason'):
            nonempty(item.get(field), f'{ident}.{field}')
        if item.get('category') not in CATEGORIES:
            fail(f'{ident}: invalid category')
        if item.get('status') not in REFERENCE_STATUSES:
            fail(f'{ident}: invalid reference status')
        for origin in sequence(item.get('lineage'), f'{ident}.lineage'):
            nonempty(origin, f'{ident}.lineage item')
        item['evidence'] = validate_evidence(item.get('evidence'), parts, f'{ident}.evidence',
                                             required=item['status'] == 'CONFIRMED')
    out['mechanical_validation'] = {
        'literal_source_quotes': True,
        'complete_semantic_extraction_verified': False,
        'source_support_verified': False,
        'note': 'Semantic support, scope and completeness require independent Agent review.'}
    return out


def validate_issues(issues):
    sequence(issues, 'score.unresolved')
    for i, issue in enumerate(issues):
        mapping(issue, f'unresolved[{i}]')
        if issue.get('metric') not in ('C', 'K', 'R', 'ALL'):
            fail(f'unresolved[{i}]: specify affected metric C/K/R/ALL')
        nonempty(issue.get('reason'), f'unresolved[{i}].reason')
    return issues


def validate_score(record, source, reference, product_text, allow_unresolved=False):
    """Validate and locate a parsed Agent judgment without changing its labels."""
    out = deepcopy(mapping(record, 'score'))
    check_agent_provenance(out)
    if not isinstance(product_text, str):
        fail('product text must be a string')
    parts = source_parts(source)
    # The canonical reference is already validated/frozen by the executor.
    items = sequence(mapping(reference, 'reference').get('obligations'), 'reference.obligations')
    applicable = {x['id'] for x in items if x.get('status') == 'CONFIRMED'}
    if len({x['id'] for x in items}) != len(items):
        fail('reference contains duplicate IDs')
    issues = validate_issues(out.get('unresolved'))
    if issues and not allow_unresolved:
        fail('unresolved semantic judgments permitted only in adjudicated records')
    affected = {x['metric'] for x in issues}
    coverage = sequence(out.get('coverage'), 'score.coverage')
    seen = set()
    for i, item in enumerate(coverage):
        mapping(item, f'coverage[{i}]')
        ident = item.get('obligation_id')
        if ident not in applicable or ident in seen:
            fail(f'coverage[{i}]: unknown, inapplicable, or duplicated obligation {ident}')
        seen.add(ident)
        if 'value' not in item:
            fail(f'{ident}: coverage value required; explicit null only for unresolved adjudication')
        value = item['value']
        if value is None:
            if not allow_unresolved or not affected.intersection(('C', 'ALL')):
                fail(f'{ident}: null coverage requires adjudication C/ALL unresolved issue')
        elif type(value) not in (int, float) or value not in (0, 0.5, 1):
            fail(f'{ident}: invalid coverage value')
        nonempty(item.get('reason'), f'{ident}.reason')
        item['evidence'] = validate_evidence(item.get('evidence'), parts, f'{ident}.evidence')
        quotes = sequence(item.get('product_quotes'), f'{ident}.product_quotes')
        item['product_spans'] = [literal_span(product_text, nonempty(q, f'{ident}.product_quote'),
                                               context=f'{ident}.product_quote') for q in quotes]
        if value in (0.5, 1) and not quotes:
            fail(f'{ident}: positive coverage requires a literal product quote')
    if seen != applicable:
        fail(f'missing applicable coverage IDs: {sorted(applicable - seen)}')
    units = sequence(out.get('units'), 'score.units')
    if type(out.get('empty_behavior')) is not bool:
        fail('empty_behavior must be boolean')
    if out['empty_behavior'] != (len(units) == 0):
        fail('empty_behavior must agree with unit count')
    if not units and any(c['value'] not in (0, None) for c in coverage):
        fail('empty behavioral output cannot receive positive coverage')
    unit_ids = set()
    assertion_roots = {}
    assertion_judgments = {}
    atomizations = set()
    for i, item in enumerate(units):
        mapping(item, f'units[{i}]')
        ident = identifier(item.get('id'), f'units[{i}].id')
        if ident in unit_ids:
            fail(f'duplicate unit id: {ident}')
        quote = nonempty(item.get('quote'), f'{ident}.quote')
        gloss = nonempty(item.get('text'), f'{ident}.text')
        if gloss.strip() in (ident, source.get('task_id'), source.get('primary_actor')):
            fail(f'{ident}: a bare identity is not a behavioral atom')
        if 'occurrence' not in item:
            fail(f'{ident}: quote occurrence required')
        span = literal_span(product_text, quote, item['occurrence'], ident)
        item['product_span'] = span
        atom_key = (span['start'], span['end'], gloss.strip())
        if atom_key in atomizations:
            fail(f'{ident}: same literal span and identical atomic gloss counted twice')
        atomizations.add(atom_key)
        if item.get('category') not in CATEGORIES:
            fail(f'{ident}: invalid category')
        if 'judgment' not in item:
            fail(f'{ident}: judgment field required; explicit null only for unresolved adjudication')
        label = item['judgment']
        if label is None:
            if not allow_unresolved or not affected.intersection(('K', 'ALL')):
                fail(f'{ident}: null judgment requires adjudication K/ALL unresolved issue')
        elif label not in JUDGMENTS:
            fail(f'{ident}: invalid judgment')
        nonempty(item.get('reason'), f'{ident}.reason')
        item['evidence'] = validate_evidence(item.get('evidence'), parts, f'{ident}.evidence',
                                              required=label in ('S', 'Q'))
        conflicts = item.get('conflict_product_quotes', [])
        sequence(conflicts, f'{ident}.conflict_product_quotes')
        item['conflict_product_spans'] = [literal_span(product_text, nonempty(q, f'{ident}.conflict_quote'),
                                                       context=f'{ident}.conflict_quote') for q in conflicts]
        if label == 'X' and not item['evidence'] and not conflicts:
            fail(f'{ident}: X requires source evidence or an internal contradiction quote')
        if 'same_assertion_as' not in item:
            fail(f'{ident}: same_assertion_as required (null if first assertion)')
        same_assertion = item['same_assertion_as']
        if same_assertion is not None and same_assertion not in unit_ids:
            fail(f'{ident}: same_assertion_as must refer to an earlier semantic unit')
        assertion_root = assertion_roots[same_assertion] if same_assertion is not None else ident
        if same_assertion is not None and label != assertion_judgments[assertion_root]:
            unresolved_null = (label is None or assertion_judgments[assertion_root] is None)
            if not (allow_unresolved and unresolved_null and affected.intersection(('K', 'ALL'))):
                fail(f'{ident}: equivalent assertion root must have the same correctness judgment')
        if 'duplicate_of' not in item:
            fail(f'{ident}: duplicate_of required (null if not redundant)')
        duplicate = item['duplicate_of']
        if duplicate is not None and duplicate not in unit_ids:
            fail(f'{ident}: duplicate must refer to an earlier semantic unit')
        if duplicate is not None and (same_assertion is None or assertion_roots[duplicate] != assertion_root):
            fail(f'{ident}: redundant duplicate must share its earlier assertion root')
        if type(item.get('scope_excess')) is not bool:
            fail(f'{ident}: scope_excess must be boolean')
        unit_ids.add(ident)
        assertion_roots[ident] = assertion_root
        if assertion_root == ident:
            assertion_judgments[ident] = label
    out['mechanical_validation'] = {
        'literal_product_mapping': True,
        'literal_source_quotes': True,
        'complete_semantic_atomization_verified': False,
        'judgment_accuracy_verified': False,
        'note': 'Literal mapping and schema checks do not verify semantic completeness or judgments.'}
    return out


def validate_alignment(record, left, right):
    """Require a blind semantic partition; never align by coincident IDs."""
    out = deepcopy(mapping(record, 'alignment'))
    check_agent_provenance(out)
    left_ids = {x['id'] for x in sequence(left.get('units'), 'left.units')}
    right_ids = {x['id'] for x in sequence(right.get('units'), 'right.units')}
    if len(left_ids) != len(left['units']) or len(right_ids) != len(right['units']):
        fail('duplicate unit IDs in source scores')
    seen_l, seen_r, seen_g = set(), set(), set()
    for i, group in enumerate(sequence(out.get('groups'), 'alignment.groups')):
        mapping(group, f'groups[{i}]')
        ident = identifier(group.get('id'), f'groups[{i}].id')
        if ident in seen_g:
            fail(f'duplicate alignment group ID: {ident}')
        seen_g.add(ident)
        ls = sequence(group.get('left_ids'), f'{ident}.left_ids')
        rs = sequence(group.get('right_ids'), f'{ident}.right_ids')
        if len(set(ls)) != len(ls) or len(set(rs)) != len(rs):
            fail(f'{ident}: repeated unit in alignment group')
        if not set(ls) <= left_ids or not set(rs) <= right_ids:
            fail(f'{ident}: unknown unit ID')
        if set(ls) & seen_l or set(rs) & seen_r:
            fail(f'{ident}: unit assigned to multiple groups')
        relation = group.get('relation')
        if relation == 'equivalent':
            if len(ls) != 1 or len(rs) != 1:
                fail(f'{ident}: equivalent requires one unit per side')
        elif relation == 'split_merge':
            if not ls or not rs or (len(ls) == 1 and len(rs) == 1):
                fail(f'{ident}: split_merge requires both sides and multiple units on at least one')
        elif relation == 'unmatched':
            if bool(ls) == bool(rs):
                fail(f'{ident}: unmatched requires units on exactly one side')
        else:
            fail(f'{ident}: invalid alignment relation')
        nonempty(group.get('reason'), f'{ident}.reason')
        seen_l.update(ls)
        seen_r.update(rs)
    if seen_l != left_ids or seen_r != right_ids:
        fail('alignment must partition every unit from both initial judgments')
    return out
