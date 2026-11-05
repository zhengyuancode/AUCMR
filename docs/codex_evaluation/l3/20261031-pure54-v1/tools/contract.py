"""Offline record contract and fixed-denominator counting; never calls an Agent."""
import argparse
from collections import Counter
import hashlib
import json
import re
from pathlib import Path

VERSION = 'AUCMR_L3_PURE_COVERAGE_3.0.0'
LABELS = ('FULL', 'PARTIAL', 'NOT_COVERED', 'CONTRADICTED', 'UNRESOLVED')

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def require(condition, message):
    if not condition:
        raise ValueError(message)

def sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None

def validate_reference(ref, source_bundle=None, run_root=None):
    require(source_bundle is not None and run_root is not None, 'actual source bundle and request provenance required')
    from eval.l3_reference.validation import validate_frozen
    return validate_frozen(ref, source_bundle, run_root)

def score(ref, result, artifact_id, artifact_sha256, *, source_bundle=None, run_root=None):
    reqs = validate_reference(ref, source_bundle, run_root)
    require(sha(artifact_sha256) and bool(artifact_id), 'expected input identity missing')
    rows = []
    if result is not None:
        require(result['protocol_version'] == VERSION and result['target_id'] == ref['target_id'], 'result target/protocol mismatch')
        require(result['reference_sha256'] == ref['content_sha256'] and result['reference_version'] == ref['version'], 'result uses stale/different reference')
        require(result['artifact_id'] == artifact_id and result['artifact_sha256'] == artifact_sha256, 'result input mismatch')
        rows = result['judgments']
    byid = {r['requirement_id']: r for r in rows}
    require(len(byid) == len(rows), 'duplicate judgment')
    require(set(byid) <= {r['requirement_id'] for r in reqs}, 'unknown reference ID')
    counts = Counter({x:0 for x in (*LABELS, 'TECHNICAL_FAILURE')})
    for r in reqs:
        row = byid.get(r['requirement_id'])
        if r['status'] == 'REFERENCE_UNRESOLVED':
            require(row is None or (row['label'] == 'UNRESOLVED' and row['technical_status'] == 'OK'), 'unresolved reference cannot be scored as confirmed')
            counts['UNRESOLVED'] += 1
            continue
        if row is None:
            counts['TECHNICAL_FAILURE'] += 1
            continue
        require(bool(row.get('rationale')), 'judgment reason missing')
        if row['technical_status'] != 'OK':
            require(row['label'] is None, 'technical failure must not have semantic label')
            counts['TECHNICAL_FAILURE'] += 1
            continue
        require(row['label'] in LABELS, 'unknown semantic label')
        if row['label'] in ('FULL', 'PARTIAL', 'CONTRADICTED'):
            require(bool(row.get('matches')), 'output evidence missing')
            require(all(m.get('slot') and m.get('claim_id') and m.get('quote') for m in row['matches']), 'incomplete output locator')
        require(row.get('reviewed_complete_output') is True, 'complete output context was not reviewed')
        counts[row['label']] += 1
    n = len(reqs)
    require(sum(counts.values()) == n, 'fixed denominator mismatch')
    return {'protocol_version': VERSION, 'target_id': ref['target_id'], 'artifact_id': artifact_id,
        'artifact_sha256': artifact_sha256, 'reference_sha256': ref['content_sha256'], 'reference_version': ref['version'],
        'N': n, 'counts': dict(counts), 'RC_full': counts['FULL']/n if n else None,
        'status': 'REFERENCE_EMPTY' if not n else 'INCOMPLETE' if counts['TECHNICAL_FAILURE'] else 'COMPLETE_WITH_UNRESOLVED' if counts['UNRESOLVED'] else 'COMPLETE',
        'reference_unresolved': sum(r['status'] == 'REFERENCE_UNRESOLVED' for r in reqs),
        'semantic_validity_certified_by_this_tool': False}

def read_source(root, manifest, relative):
    """Intended allowlist contract for a future source tool, not a repo-wide sandbox."""
    base = Path(root).resolve()
    candidate = (base / relative).resolve()
    require(candidate.is_relative_to(base) and relative in manifest['files'], 'source path not allowed')
    data = candidate.read_bytes()
    require(hashlib.sha256(data).hexdigest() == manifest['files'][relative], 'source input changed')
    return data

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', required=True)
    parser.add_argument('--result')
    parser.add_argument('--artifact-id', required=True)
    parser.add_argument('--artifact-sha256', required=True)
    parser.add_argument('--source-bundle', required=True)
    parser.add_argument('--run-root', required=True)
    args = parser.parse_args()
    ref = json.loads(Path(args.reference).read_text())
    result = json.loads(Path(args.result).read_text()) if args.result else None
    print(json.dumps(score(ref, result, args.artifact_id, args.artifact_sha256, source_bundle=args.source_bundle, run_root=args.run_root), ensure_ascii=False, indent=2))
