"""Exact product-line citation compatibility; no network, scoring or identity lookup.
A valid location never establishes semantic coverage, support or redundancy.
"""
from copy import deepcopy


def _line_body(text):
    if text.endswith('\r\n'): return text[:-2]
    if text.endswith(('\r', '\n')): return text[:-1]
    return text


def _integer(value, context):
    if type(value) is not int or value < 1:
        raise ValueError(f'{context}: expected a one-based integer')
    return value


def _occurrences(text, quote):
    starts, offset = [], 0
    while True:
        pos = text.find(quote, offset)
        if pos < 0: return starts
        starts.append(pos); offset = pos + 1


def product_catalog(product):
    """Derive one-based display lines from the supplied anonymous product only."""
    if not isinstance(product, dict): raise ValueError('product: expected object')
    code, text = product.get('artifact_code'), product.get('text')
    if not isinstance(code, str) or not code.strip():
        raise ValueError('product.artifact_code: expected nonempty string')
    if not isinstance(text, str): raise ValueError('product.text: expected string')
    return {'artifact_code': code,
            'lines': [{'line': i, 'text': _line_body(line)}
                      for i, line in enumerate(text.splitlines(keepends=True), 1)]}


def locate_product_lines(product_text, spec):
    """Resolve explicit lines to raw text/offsets; preserve all interior words."""
    if not isinstance(product_text, str) or not isinstance(spec, dict):
        raise ValueError('product line citation: string text and object spec required')
    first = _integer(spec.get('line_start'), 'product.line_start')
    last = _integer(spec.get('line_end', first), 'product.line_end')
    lines = product_text.splitlines(keepends=True)
    if last < first or first > len(lines) or last > len(lines):
        raise ValueError(f'product line range {first}..{last} outside 1..{len(lines)}')
    start = sum(map(len, lines[:first - 1])); end = sum(map(len, lines[:last]))
    quote = _line_body(product_text[start:end]); end = start + len(quote)
    if not quote.strip(): raise ValueError('product line citation: blank lines are not behavioral evidence')
    occurrences = _occurrences(product_text, quote)
    return {'start': start, 'end': end,
            'utf8_byte_start': len(product_text[:start].encode('utf-8')),
            'utf8_byte_end': len(product_text[:end].encode('utf-8')),
            'line_start': first, 'line_end': last, 'quote': quote, 'exact_quote': quote,
            'occurrence': occurrences.index(start), 'occurrence_count': len(occurrences),
            'match_mode': 'product_line_range', 'quote_derived': True,
            'citation_validation': 'LINE_RANGE_LOCATION_ONLY'}


def prepare_score_citations(record, product_text):
    """Return a copy with exact line-derived quotes, never invented labels/units.

    Nonempty coverage.product_lines makes the specified ranges authoritative;
    product_quotes are derived solely from them and old captions retained as
    reported_product_quotes. Units may use line_start/line_end instead of a
    fragile quote; all semantic fields are preserved for actual Agent review.
    """
    if not isinstance(record, dict) or not isinstance(product_text, str):
        raise ValueError('score citation preparation: object record and string text required')
    out = deepcopy(record); audit = out.get('product_citation_normalizations', [])
    if not isinstance(audit, list): raise ValueError('product_citation_normalizations: expected array')
    audit = deepcopy(audit)
    def note(entry):
        if entry not in audit: audit.append(entry)
    if 'coverage' in out:
        if not isinstance(out['coverage'], list): raise ValueError('coverage: expected array')
        for row in out['coverage']:
            if not isinstance(row, dict): raise ValueError('coverage item: expected object')
            ranges = row.get('product_lines')
            if ranges is None or ranges == []: continue
            if not isinstance(ranges, list): raise ValueError('coverage.product_lines: expected array')
            spans = [locate_product_lines(product_text, entry) for entry in ranges]
            if 'product_quotes' in row and 'reported_product_quotes' not in row:
                row['reported_product_quotes'] = deepcopy(row['product_quotes'])
            row['product_quotes'] = list(dict.fromkeys(span['exact_quote'] for span in spans))
            row['product_line_spans'] = spans
            note({'audit_type': 'coverage', 'item_id': row.get('obligation_id'),
                  'action': 'derive_exact_quotes_from_product_lines',
                  'line_ranges': [[s['line_start'], s['line_end']] for s in spans],
                  'semantic_judgment_changed': False})
    if 'units' in out:
        if not isinstance(out['units'], list): raise ValueError('units: expected array')
        for unit in out['units']:
            if not isinstance(unit, dict): raise ValueError('unit: expected object')
            if 'line_start' not in unit and 'line_end' not in unit: continue
            span = locate_product_lines(product_text, unit)
            if 'quote' in unit and unit['quote'] != span['exact_quote'] and 'reported_quote' not in unit:
                unit['reported_quote'] = unit['quote']
            if 'occurrence' in unit and unit['occurrence'] != span['occurrence'] and 'reported_occurrence' not in unit:
                unit['reported_occurrence'] = unit['occurrence']
            unit['quote'] = span['exact_quote']; unit['occurrence'] = span['occurrence']
            unit['product_line_span'] = span; unit['quote_derived_from_line_range'] = True
            note({'audit_type': 'unit', 'item_id': unit.get('id'),
                  'action': 'derive_exact_quote_from_product_lines',
                  'line_ranges': [[span['line_start'], span['line_end']]],
                  'semantic_judgment_changed': False})
    if audit: out['product_citation_normalizations'] = audit
    return out
