"""One-time build script for v2 contracts, retained for provenance; not a runtime repair."""
from pathlib import Path
p=Path(__file__).parent/'contracts.py';s=p.read_text()
s=s.replace('import re\n','import re\nfrom locator import locate\n')
a=s.index('def literal_span(');b=s.index('\n\ndef source_parts',a)
s=s[:a]+'''def literal_span(text, quote, occurrence=0, context='quote'):
    return locate(text, quote, occurrence, context)


def administrative_list(obj, key, audit, allow_string=False):
    if key not in obj or obj[key] is None:
        obj[key] = []
        audit.append({'field': key, 'action': 'missing_or_null_to_empty_administrative_list'})
    elif allow_string and isinstance(obj[key], str):
        original = obj[key]
        obj[key] = [original] if original.strip() else []
        audit.append({'field': key, 'action': 'scalar_to_list', 'reported': original})
    return obj[key]
''' +s[b:]
a=s.index('def validate_evidence(');b=s.index('\n\ndef validate_reference',a)
s=s[:a]+'''def validate_evidence(evidence, parts, context, required=False):
    if evidence is None:
        evidence = []
    if isinstance(evidence, dict):
        evidence = [evidence]
    entries = sequence(evidence, context)
    if required and not entries:
        fail(f'{context}: source evidence required')
    enriched = []
    for i, original in enumerate(entries):
        entry = deepcopy(mapping(original, f'{context}[{i}]'))
        reported_part = entry.get('part_id')
        quote = nonempty(entry.get('quote'), f'{context}[{i}].quote')
        span = None
        part_id = reported_part
        if part_id in parts:
            try:
                span = literal_span(parts[part_id]['text'], quote, entry.get('occurrence', 0), f'{context}[{i}]')
            except ValueError:
                pass
        if span is None:
            # A locator can correct an administrative part pointer only if the
            # supplied source contains ONE unambiguous matching location.
            candidates = []
            for ident, part in parts.items():
                try:
                    found = literal_span(part['text'], quote, 0, f'{context}[{i}]')
                    if found['occurrence_count'] == 1:
                        candidates.append((ident, found))
                    else:
                        candidates.extend([(ident, None), (ident, None)])
                except ValueError:
                    pass
            if len(candidates) != 1 or candidates[0][1] is None:
                fail(f'{context}[{i}]: quote cannot be uniquely located within supplied source; reported part={reported_part!r}, matches={len(candidates)}')
            part_id, span = candidates[0]
            entry['reported_part_id'] = reported_part
            entry['part_id_corrected'] = True
        part = parts[part_id]
        entry['part_id'] = part_id
        entry['source_span'] = span
        if 'pdf_page' in entry and entry['pdf_page'] != part['pdf_page']:
            entry['reported_pdf_page'] = entry['pdf_page']
            entry['pdf_page_corrected'] = True
        entry['pdf_page'] = part['pdf_page']
        entry['exact_quote'] = span['exact_quote']
        enriched.append(entry)
    return enriched
''' +s[b:]
s=s.replace("    parts = source_parts(source)\n    nonempty(out.get('scope')", "    parts = source_parts(source)\n    audit = out.setdefault('administrative_normalizations', [])\n    administrative_list(out, 'unresolved', audit)\n    nonempty(out.get('scope')",1)
s=s.replace("        for origin in sequence(item.get('lineage'), f'{ident}.lineage'):", "        administrative_list(item, 'lineage', audit, allow_string=True)\n        for origin in sequence(item.get('lineage'), f'{ident}.lineage'):")
s=s.replace("'literal_source_quotes': True", "'source_quotes_located_with_audited_format_tolerance': True")
s=s.replace("    issues = validate_issues(out.get('unresolved'))", "    administrative_list(out, 'unresolved', out.setdefault('administrative_normalizations', []))\n    issues = validate_issues(out.get('unresolved'))")
p.write_text(s)
