"""Line-anchored source citation locations, without semantic matching.

No filesystem or model access. Catalogues are derived solely from explicitly
supplied source strings. The resolver extracts exact original source spans;
it does not declare that those spans support a requirement or judgment.
"""
from copy import deepcopy


def _integer(value, context, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{context}: expected integer >= {minimum}')
    return value


def _part(part, context):
    if not isinstance(part, dict) or not isinstance(part.get('text'), str):
        raise ValueError(f'{context}: source part must contain text')
    _integer(part.get('pdf_page'), context + '.pdf_page')
    return part


def _strip_terminal_newline(text):
    # Only display the line body. Do not strip indentation or other whitespace.
    if text.endswith('\r\n'):
        return text[:-2]
    if text.endswith(('\n', '\r')):
        return text[:-1]
    return text


def catalog(source):
    """Return a one-based line catalogue from the supplied source only.

    Line indexes use Python's splitlines(keepends=True), including blank lines.
    Display text omits CR/LF terminators only; original strings remain intact.
    """
    if not isinstance(source, dict) or not isinstance(source.get('requirements'), list):
        raise ValueError('source: requirements array required')
    result, seen = [], set()
    for i, part in enumerate(source['requirements']):
        _part(part, f'requirements[{i}]')
        ident = part.get('source_part_id')
        if not isinstance(ident, str) or not ident.strip() or ident in seen:
            raise ValueError('source_part_id must be nonempty and unique')
        seen.add(ident)
        lines = part['text'].splitlines(keepends=True)
        result.append({'part_id': ident, 'pdf_page': part['pdf_page'],
                       'lines': [{'line': n, 'text': _strip_terminal_newline(line)}
                                 for n, line in enumerate(lines, 1)]})
    return result


def _starts(text, quote):
    found, start = [], 0
    while True:
        pos = text.find(quote, start)
        if pos < 0:
            return found
        found.append(pos)
        start = pos + 1


def resolve_lines(entry, parts, context='source citation'):
    """Resolve {part_id,line_start,line_end?} to exact original text.

    The specified line range is authoritative. A supplied `quote` is retained as
    reported_quote, while quote/exact_quote are extracted, never paraphrased.
    A wrong model-reported page is corrected and explicitly retained in audit
    metadata. Ambiguous meanings are left to source review, not this function.
    """
    if not isinstance(entry, dict) or not isinstance(parts, dict):
        raise ValueError(f'{context}: entry and parts must be objects')
    out = deepcopy(entry)
    ident = out.get('part_id')
    if ident not in parts:
        raise ValueError(f'{context}: unknown source part {ident!r}')
    part = _part(parts[ident], context + '.source')
    first = _integer(out.get('line_start'), context + '.line_start')
    last = _integer(out.get('line_end', first), context + '.line_end')
    lines = part['text'].splitlines(keepends=True)
    if last < first or first > len(lines) or last > len(lines):
        raise ValueError(f'{context}: line range {first}..{last} outside 1..{len(lines)}')
    start = sum(len(x) for x in lines[:first - 1])
    end = sum(len(x) for x in lines[:last])
    # Retain interior line breaks, remove only final selected CR/LF terminator.
    raw = part['text'][start:end]
    quote = _strip_terminal_newline(raw)
    end = start + len(quote)
    if not quote.strip():
        raise ValueError(f'{context}: blank lines do not provide evidence')
    occurrences = _starts(part['text'], quote)
    occurrence = occurrences.index(start)
    if 'quote' in out:
        out['reported_quote'] = out['quote']
    if 'occurrence' in out:
        reported = _integer(out['occurrence'], context + '.occurrence', minimum=0)
        if reported != occurrence:
            out['reported_occurrence'] = reported
            out['occurrence_corrected'] = True
    if 'pdf_page' in out and out['pdf_page'] != part['pdf_page']:
        out['reported_pdf_page'] = out['pdf_page']
        out['pdf_page_corrected'] = True
    out.update({'part_id': ident, 'line_start': first, 'line_end': last,
                'quote': quote, 'exact_quote': quote, 'pdf_page': part['pdf_page'],
                'occurrence': occurrence,
                'quote_derived_from_line_range': True,
                'citation_validation': 'LINE_RANGE_LOCATION_ONLY'})
    out['source_span'] = {
        'start': start, 'end': end,
        'utf8_byte_start': len(part['text'][:start].encode('utf-8')),
        'utf8_byte_end': len(part['text'][:end].encode('utf-8')),
        'occurrence': occurrence, 'occurrence_count': len(occurrences),
        'match_mode': 'source_line_range', 'exact_quote': quote,
        'line_start': first, 'line_end': last,
    }
    return out
