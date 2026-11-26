"""Recompute all packaged cohorts offline using only the bundled source data."""
from contextlib import redirect_stdout
from pathlib import Path
import hashlib
import importlib.util
import io
import json
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    merged = json.loads((root / 'data/combined_r1/results.json').read_text())
    module_path = root / 'methods/calculate.py'
    spec = importlib.util.spec_from_file_location('frozen_calculator', module_path)
    calc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(calc)
    checked = {}
    with tempfile.TemporaryDirectory(prefix='aucmr_paper_recompute_') as tmp:
        tmp = Path(tmp)
        paths = {}
        for row in merged['positions']:
            dest = tmp / row['position_id']
            dest.mkdir()
            source = {}
            if row['generation'] is not None:
                p = dest / 'generation.json'
                p.write_text(json.dumps(row['generation'], ensure_ascii=False))
                source['generation_path'] = str(p)
                source['generation_sha256'] = hashlib.sha256(p.read_bytes()).hexdigest()
            for layer in ('l1', 'l2'):
                evaluation = row['evaluation'].get(layer)
                if evaluation is not None:
                    p = dest / (layer + '.json')
                    p.write_text(json.dumps(evaluation['result'], ensure_ascii=False))
                    source[layer + '_path'] = str(p)
            paths[row['position_id']] = source
        for cohort in ('combined93', 'batch2_50', 'new45', 'first43'):
            original = root / 'metrics' / cohort
            inputs = json.loads((original / 'inputs.original.json').read_text())
            for row in inputs['positions']:
                for key in ('generation_path', 'generation_sha256', 'l1_path', 'l2_path'):
                    row[key] = paths[row['position_id']].get(key)
            manifest = tmp / (cohort + '_inputs.json')
            manifest.write_text(json.dumps(inputs, ensure_ascii=False))
            out = tmp / cohort
            with redirect_stdout(io.StringIO()):
                calc.calculate(manifest, out)
            actual = json.loads((out / 'results.json').read_text())
            expected = json.loads((original / 'results.json').read_text())
            assert actual == expected, 'RECOMPUTATION_MISMATCH:' + cohort
            checked[cohort] = {'scope': actual['scope'], 'all_fields_match': True}
    print(json.dumps({'offline_recomputation': checked, 'new_paid_requests': 0}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
