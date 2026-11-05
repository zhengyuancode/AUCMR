"""Auditable administrative tolerance, never a substitute for Agent judgment.

Pure functions: no files, models, scores, method identities, or external access.
All inputs are deep-copied. Raw responses remain the caller's responsibility.
"""
from copy import deepcopy
import re


def _object(value, name):
    if not isinstance(value, dict):
        raise ValueError(name + ' must be an object')
    return value


def _array(value, name):
    if not isinstance(value, list):
        raise ValueError(name + ' must be an array')
    return value


def _audit(out, entry):
    audit = out.setdefault('administrative_normalizations', [])
    _array(audit, 'administrative_normalizations')
    audit.append(entry)


def _unique(values):
    result = []
    for value in values:
        if value not in result:
            result.append(deepcopy(value))
    return result


def materialize_explicit_unresolved(record, drafts):
    """Represent an EXISTING explicit unresolved decision as a reference item.

    Only decisions with decision='unresolved' and an explicitly empty final_ids
    qualify. An undisposed candidate never receives an inferred decision.
    Text/category/scope/atomicity come from its original draft; the decision's
    own reason and evidence remain visible. Such items do not become CONFIRMED.
    """
    out = deepcopy(_object(record, 'reference'))
    obligations = _array(out.get('obligations'), 'reference.obligations')
    decisions = _array(out.get('decisions'), 'reference.decisions')
    pending = [d for d in decisions if d.get('decision') == 'unresolved'
               and d.get('final_ids') == []]
    if not pending:
        return out
    needed = set()
    for decision in pending:
        origins = _array(decision.get('candidate_ids'), 'decision.candidate_ids')
        if not origins or any(not isinstance(x, str) or not x for x in origins):
            raise ValueError('explicit unresolved decision needs stable candidate ids')
        needed.update(origins)
    candidates = {}
    for draft in _array(drafts, 'drafts'):
        for candidate in _array(_object(draft, 'draft').get('obligations'), 'draft.obligations'):
            ident = candidate.get('id')
            if ident not in needed:
                continue
            if not isinstance(ident, str) or not ident:
                raise ValueError('draft candidate requires stable id')
            if ident in candidates:
                fields = ('text', 'category', 'scope', 'atomicity')
                if any(candidates[ident].get(k) != candidate.get(k) for k in fields):
                    raise ValueError('ambiguous duplicated draft candidate id: ' + ident)
            else:
                candidates[ident] = candidate
    ids = [o.get('id') for o in obligations]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate final obligation id')
    used = set(ids)
    next_num = max([int(m.group(1)) for ident in used if isinstance(ident, str)
                    and (m := re.fullmatch(r'R(\d+)', ident))] or [0]) + 1
    for index, decision in enumerate(decisions):
        if decision.get('decision') != 'unresolved' or decision.get('final_ids') != []:
            continue
        origins = _array(decision.get('candidate_ids'), 'decision.candidate_ids')
        if not origins or len(origins) != len(set(origins)):
            raise ValueError('explicit unresolved decision needs distinct candidate ids')
        if not isinstance(decision.get('reason'), str) or not decision['reason'].strip():
            raise ValueError('explicit unresolved decision needs its own substantive reason')
        new_ids = []
        for ident in origins:
            if ident not in candidates:
                raise ValueError('unresolved decision refers to unknown candidate: ' + str(ident))
            mapped = [o for o in obligations if ident in o.get('lineage', [])]
            if mapped:
                if any(o.get('status') != 'UNRESOLVED' for o in mapped):
                    raise ValueError('unresolved decision conflicts with an existing final disposition: ' + ident)
                new_ids.extend(o['id'] for o in mapped)
                continue
            original = candidates[ident]
            while f'R{next_num:03d}' in used:
                next_num += 1
            final_id = f'R{next_num:03d}'
            next_num += 1
            used.add(final_id)
            item = deepcopy(original)
            item.update({
                'id': final_id,
                'status': 'UNRESOLVED',
                'reason': decision['reason'],
                'evidence': deepcopy(decision.get('evidence') or original.get('evidence', [])),
                'lineage': [ident],
                'reported_candidate_status': original.get('status'),
                'reported_candidate_lineage': deepcopy(original.get('lineage', [])),
                'materialized_from_explicit_decision': True,
                'explicit_decision_index': index,
            })
            obligations.append(item)
            new_ids.append(final_id)
            unresolved = out.setdefault('unresolved', [])
            if unresolved is None:
                unresolved = out['unresolved'] = []
            _array(unresolved, 'reference.unresolved').append({
                'id': final_id,
                'candidate_id': ident,
                'reason': decision['reason'],
                'origin': 'EXPLICIT_AGENT_UNRESOLVED_DECISION',
            })
            _audit(out, {'action': 'materialize_existing_unresolved_decision',
                         'decision_index': index, 'candidate_id': ident,
                         'final_id': final_id,
                         'note': 'Representation only; no new semantic judgment or CONFIRMED item.'})
        decision['final_ids'] = _unique(new_ids)
    return out


def normalize_coverage(record, reference):
    """Keep the frozen applicable denominator and retain optional/duplicate data.

    Known inapplicable ratings are supplementary. Same-value duplicates are
    coalesced without choosing a more favorable value. Conflicting duplicates
    become explicit evaluator uncertainty, not source ambiguity or a zero.
    Missing confirmed ratings and unknown IDs are not repaired by guessing.
    """
    out = deepcopy(_object(record, 'score'))
    refs = _array(_object(reference, 'reference').get('obligations'), 'reference.obligations')
    by_id = {o['id']: o for o in refs}
    if len(by_id) != len(refs):
        raise ValueError('duplicate reference IDs')
    coverage = _array(out.get('coverage'), 'score.coverage')
    retained = []
    grouped = {}
    supplementary = out.setdefault('supplementary_coverage', [])
    _array(supplementary, 'supplementary_coverage')
    for item in coverage:
        _object(item, 'coverage item')
        ident = item.get('obligation_id')
        if ident not in by_id:
            raise ValueError('coverage refers to unknown reference ID: ' + str(ident))
        status = by_id[ident].get('status')
        if status not in ('CONFIRMED', 'UNRESOLVED', 'EXCLUDED'):
            raise ValueError('illegal frozen reference status: ' + str(status))
        if status != 'CONFIRMED':
            optional = deepcopy(item)
            optional.update({'reference_status': status,
                             'primary_metric_included': False,
                             'citation_validation': 'RAW_SUPPLEMENTARY_NOT_PRIMARY_VALIDATED',
                             'normalization_reason': 'Frozen L5 denominator includes only CONFIRMED obligations.'})
            supplementary.append(optional)
            _audit(out, {'action': 'retain_inapplicable_rating_as_supplementary',
                         'obligation_id': ident, 'reference_status': status})
            continue
        value = item.get('value')
        if 'value' not in item or (value is not None and
                (type(value) not in (int, float) or value not in (0, .5, 1))):
            raise ValueError('invalid coverage label, not an administrative duplicate: ' + ident)
        if ident not in grouped:
            grouped[ident] = []
            retained.append(ident)
        grouped[ident].append(item)
    normalized = []
    for ident in retained:
        ratings = grouped[ident]
        if len(ratings) == 1:
            normalized.append(ratings[0])
            continue
        item = deepcopy(ratings[0])
        item['duplicate_ratings'] = deepcopy(ratings)
        values = [x['value'] for x in ratings]
        conflict = any(v != values[0] for v in values[1:])
        for field in ('evidence', 'product_quotes', 'product_lines'):
            if any(field in x for x in ratings):
                merged = []
                for rating in ratings:
                    if field in rating:
                        merged.extend(_array(rating[field], ident + '.' + field))
                item[field] = _unique(merged)
        reasons = _unique([x.get('reason', '') for x in ratings if x.get('reason', '')])
        item['reason'] = ('Conflicting duplicate ratings retained for third-role resolution. '
                          if conflict else 'Same-value duplicate ratings coalesced once. ') + ' | '.join(reasons)
        if conflict:
            item['value'] = None
            issue = {'metric': 'C', 'item_id': ident,
                     'reason': 'Conflicting duplicate coverage ratings; no score selected by normalizer. Third role must resolve.',
                     'origin': 'ADMINISTRATIVE_RATING_CONFLICT'}
            issues = out.setdefault('unresolved', [])
            if issues is None:
                issues = out['unresolved'] = []
            _array(issues, 'score.unresolved').append(issue)
            out.setdefault('coverage_conflicts', []).append({'obligation_id': ident,
                                                            'original_ratings': deepcopy(ratings)})
        _audit(out, {'action': 'retain_conflicting_duplicate_ratings_as_uncertain' if conflict
                    else 'coalesce_same_value_duplicate_ratings',
                    'obligation_id': ident, 'original_count': len(ratings),
                    'retained_value': item['value'], 'denominator_entries': 1})
        normalized.append(item)
    out['coverage'] = normalized
    return out


def complete_alignment(record, left, right):
    """Add conservative NOT_REVIEWED singleton groups for omitted units.

    No semantic identity or absence of a counterpart is inferred. Existing
    invalid IDs, duplicated groups, and illegal matching claims are left for
    the strict alignment validator. Third-role review must inspect new groups.
    """
    out = deepcopy(_object(record, 'alignment'))
    groups = _array(out.get('groups'), 'alignment.groups')
    ids = {'left': [u['id'] for u in _array(left.get('units'), 'left.units')],
           'right': [u['id'] for u in _array(right.get('units'), 'right.units')]}
    seen = {'left': set(), 'right': set()}
    used_group_ids = {g.get('id') for g in groups}
    for group in groups:
        for side in ('left', 'right'):
            seen[side].update(_array(group.get(side + '_ids'), 'group.' + side + '_ids'))
    added = []
    for side in ('left', 'right'):
        for ident in ids[side]:
            if ident in seen[side]:
                continue
            base = 'G_AUTO_' + side.upper() + '_' + ident
            group_id = base
            suffix = 2
            while group_id in used_group_ids:
                group_id = base + '_' + str(suffix)
                suffix += 1
            used_group_ids.add(group_id)
            group = {'id': group_id, 'left_ids': [ident] if side == 'left' else [],
                     'right_ids': [ident] if side == 'right' else [],
                     'relation': 'unmatched', 'audit_status': 'NOT_REVIEWED',
                     'reason': 'Unit omitted by the alignment response. Administrative unaligned singleton only; absence of a semantic counterpart is NOT established. Third role must review this unit.'}
            groups.append(group)
            added.append({'side': side, 'unit_id': ident, 'group_id': group_id})
            seen[side].add(ident)
    if added:
        _audit(out, {'action': 'represent_omitted_alignment_units_without_inventing_matches',
                     'units': added,
                     'semantic_assertion': 'NOT_REVIEWED, not a finding that no counterpart exists.'})
    count = sum(len(g.get('left_ids', [])) + len(g.get('right_ids', []))
                for g in groups if g.get('audit_status') == 'NOT_REVIEWED')
    out['alignment_unreviewed_units'] = count
    out['alignment_has_admin_unreviewed_units'] = count > 0
    return out
