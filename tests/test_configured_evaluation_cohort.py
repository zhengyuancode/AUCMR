"""Offline checks of the actual selector and metrics; no API/model imports."""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def selector():
    tree = ast.parse((ROOT / 'eval/run_experiments.py').read_text(encoding='utf-8-sig'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ExperimentRunner')
    node = copy.deepcopy(next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'select_paper_cohort'))
    node.decorator_list = []
    namespace = {'json': json}
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<actual cohort selector>', 'exec'), namespace)
    return namespace['select_paper_cohort']


class ConfiguredCohortTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.select = selector()
        self.methods = ['AUCMR (CREST full)', 'CREST w/o cross-slot', 'LVLM-Direct',
                        'TextRAG-Elaborate', 'VDocRAG-Elaborate', 'ViDoRAG-Elaborate', 'ReAct-Elaborate']

    def result(self, method, name, refused=False):
        path = self.root / (str(len(list(self.root.iterdir()))) + '.json')
        data = {'method': method, 'use_case_name': name, 'actor': 'User',
                'slots': {'goal': {'claims': [], 'raw_text': ''}},
                'quality_flags': [{'flag_type': 'unresolvable_use_case'}] if refused else []}
        path.write_text(json.dumps(data), encoding='utf-8')
        ev = path.with_suffix('.evidence.json')
        ev.write_text('{"evidence_cards": []}', encoding='utf-8')
        return (method, 'project', name, 'User'), (str(path), str(ev))

    def test_all_93_cases_across_seven_methods_including_refusal(self):
        results = dict(self.result(m, str(i), refused=(i == 0 and m in self.methods[:2]))
                       for m in self.methods for i in range(93))
        before = {p: Path(p).read_bytes() for pair in results.values() for p in pair}
        selected, report = self.select(results, self.methods, {('project', str(i), 'User') for i in range(93)})
        self.assertEqual(len(selected), 651)
        self.assertEqual(report['evaluated_count'], 93)
        self.assertEqual(report['aucmr_refusal_count'], 1)
        self.assertTrue(report['refusals_included'])
        self.assertEqual(report['excluded_use_cases'], [])
        self.assertEqual(before, {p: Path(p).read_bytes() for p in before})

    def test_all_refused_is_still_a_valid_evaluation_cohort(self):
        selected, report = self.select(dict([self.result(self.methods[0], 'refused', True)]), self.methods[:1])
        self.assertEqual(len(selected), 1)
        self.assertEqual(report['valid_count'], 1)

    def test_missing_result_for_refused_case_is_not_silently_dropped(self):
        with self.assertRaisesRegex(RuntimeError, 'missing method/use-case'):
            self.select(dict([self.result(self.methods[0], 'refused', True)]), self.methods[:2])

    def test_configured_case_missing_from_all_methods_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'missing method/use-case'):
            self.select(dict([self.result(self.methods[0], 'present')]), self.methods[:1],
                        {('project', 'present', 'User'), ('project', 'absent', 'User')})

    def test_unconfigured_cases_and_unselected_methods_are_excluded(self):
        results = dict([self.result(self.methods[0], 'chosen'), self.result(self.methods[0], 'old'),
                        self.result(self.methods[1], 'chosen')])
        selected, report = self.select(results, self.methods[:1], {('project', 'chosen', 'User')})
        self.assertEqual(list(selected), [(self.methods[0], 'project', 'chosen', 'User')])
        self.assertEqual(report['candidate_count'], 1)

    def test_empty_configured_cohort_does_not_fallback(self):
        with self.assertRaises(RuntimeError):
            self.select(dict([self.result(self.methods[0], 'old')]), self.methods[:1], set())

    def test_formal_entrypoint_passes_configured_identities(self):
        tree = ast.parse((ROOT / 'run.py').read_text(encoding='utf-8-sig'))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_evaluation')
        call = next(n for n in ast.walk(fn) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and n.func.attr == 'select_paper_cohort')
        value = next(k.value for k in call.keywords if k.arg == 'configured_identities')
        self.assertEqual(ast.unparse(value), '_configured_use_case_ids(config)')

    def test_refusal_metrics_keep_seven_empty_slots_and_denominator(self):
        spec = importlib.util.spec_from_file_location('_offline_metrics', ROOT / 'eval/compute_metrics.py')
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(mod)
        computer = mod.MetricsComputer(nli_judge=object())
        _, paths = self.result(self.methods[0], 'refused', True)
        with patch('socket.socket.connect', side_effect=AssertionError('Offline test attempted network')):
            metrics = computer.compute_all(*paths, run_nli=False, run_l2=False)
            aggregate = computer.compute_aggregate([metrics], self.methods[0])
        self.assertEqual(metrics.empty_slot_count, 7)
        self.assertEqual(metrics.total_claims, 0)
        self.assertEqual(aggregate['n_ucs'], 1)
        self.assertEqual(aggregate['l1_empty_slots'], 7)
        self.assertEqual(aggregate['l2_slot_fill_rates']['main_flow'], 0.0)
        self.assertEqual(metrics.quality_flags, [{'flag_type': 'unresolvable_use_case'}])


if __name__ == '__main__':
    unittest.main()
