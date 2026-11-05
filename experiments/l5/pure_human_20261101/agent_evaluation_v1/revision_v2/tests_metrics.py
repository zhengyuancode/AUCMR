"""Synthetic-only verification: no PURE inputs, production output, or API calls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from metrics import aggregate, cluster_interval, nominal_kappa, score_record, weighted_kappa


def reference():
    return {"obligations": [
        {"id": "O1", "category": "main", "status": "CONFIRMED"},
        {"id": "O2", "category": "result", "status": "CONFIRMED"},
        {"id": "O3", "category": "branch", "status": "UNRESOLVED"},
        {"id": "O4", "category": "condition", "status": "EXCLUDED"},
    ]}


def record(labels=("S", "X", "U", "Q"), coverage=(1, .5)):
    return {"coverage": [{"obligation_id": "O" + str(i+1), "value": v, "reason": "synthetic"} for i, v in enumerate(coverage)],
            "units": [{"id": f"C{i+1}", "judgment": v, "duplicate_of": None, "category": "main", "quote": "synthetic", "reason": "synthetic"}
                      for i, v in enumerate(labels)],
            "unresolved": [], "empty_behavior": not labels}


class MetricTests(unittest.TestCase):
    def test_frozen_formulas(self):
        score = score_record(record(), reference())
        self.assertEqual(score["C"], .75)
        self.assertEqual(score["K"], 1/3)
        self.assertEqual(score["R"], 0)
        self.assertEqual(score["counts"]["Q"], 1)
        self.assertEqual(score["counts"]["reference_unresolved"], 1)
        self.assertEqual(score["denominators"]["C_N"], 2)

    def test_duplicates_count_once_for_k(self):
        rec = record(("S", "S", "U"))
        rec["units"][1]["duplicate_of"] = "C1"
        score = score_record(rec, reference())
        self.assertEqual(score["K"], .5)
        self.assertEqual(score["R"], 1/3)
        self.assertEqual(score["denominators"]["K_S_X_U"], 2)

    def test_no_unsupported_penalty_in_r(self):
        score = score_record(record(("U", "U")), reference())
        self.assertEqual(score["R"], 0)
        self.assertEqual(score["K"], 0)

    def test_necessary_repetition_deduplicated_only_for_k(self):
        rec = record(("S", "S", "U"))
        rec["units"][1]["same_assertion_as"] = "C1"
        score = score_record(rec, reference())
        self.assertEqual(score["K"], .5)
        self.assertEqual(score["R"], 0)
        self.assertEqual(score["denominators"]["K_S_X_U"], 2)

    def test_equivalent_assertion_cycle_invalid(self):
        rec = record()
        rec["units"][0]["same_assertion_as"] = "C2"
        with self.assertRaises(ValueError):
            score_record(rec, reference())

    def test_empty_is_not_perfect(self):
        score = score_record(record((), (0, 0)), reference())
        self.assertEqual((score["C"], score["K"], score["R"]), (0, 0, None))

    def test_empty_declaration_mismatch(self):
        rec = record()
        rec["empty_behavior"] = True
        with self.assertRaises(ValueError):
            score_record(rec, reference())

    def test_empty_cannot_cover(self):
        with self.assertRaises(ValueError):
            score_record(record((), (1, 0)), reference())

    def test_zero_reference_denominator(self):
        rec = record()
        rec["coverage"] = []
        ref = reference()
        ref["obligations"] = ref["obligations"][2:]
        self.assertIsNone(score_record(rec, ref)["C"])

    def test_all_source_q(self):
        score = score_record(record(("Q", "Q")), reference())
        self.assertIsNone(score["K"])
        self.assertEqual(score["R"], 0)

    def test_null_not_source_q(self):
        score = score_record(record(("S", None)), reference())
        self.assertIsNone(score["K"])
        self.assertEqual(score["bounds"]["K"], [.5, 1])
        self.assertEqual(score["counts"]["Q"], 0)

    def test_unresolved_coverage_keeps_bounds(self):
        rec = record(coverage=(1, None))
        rec['unresolved'] = [{"metric": "C", "item_id": "O2", "reason": "specific coverage cannot be resolved"}]
        score = score_record(rec, reference())
        self.assertIsNone(score["C"])
        self.assertEqual(score["bounds"]["C"], [.5, 1])
        self.assertEqual(score["denominators"]["C_N"], 2)

    def test_r_uncertainty_also_affects_k(self):
        rec = record(("S", "U"))
        rec["unresolved"] = [{"metric": "R", "item_id": "C2", "reason": "synthetic uncertain redundancy"}]
        score = score_record(rec, reference())
        self.assertIsNone(score["R"])
        self.assertIsNone(score["K"])
        self.assertEqual(score["bounds"]["R"], [0, 1])
        self.assertIn("segmentation may change M", score["bound_notes"]["R"])

    def test_global_c_issue_does_not_claim_localized_bound(self):
        rec = record(coverage=(1, None))
        rec["unresolved"] = [{"metric": "C", "item_id": "scope", "reason": "entire scope interpretation unresolved"}]
        score = score_record(rec, reference())
        self.assertIsNone(score["C"])
        self.assertEqual(score["bounds"]["C"], [0, 1])

    def test_empty_segmentation_uncertainty_does_not_assume_m_zero(self):
        rec = record((), (0, 0))
        rec["unresolved"] = [{"metric": "R", "item_id": "segmentation", "reason": "unclear whether candidate expresses behaviors"}]
        score = score_record(rec, reference())
        self.assertIsNone(score["R"])
        self.assertEqual(score["bounds"]["R"], [0, 1])
        self.assertEqual(score["bounds"]["K"], [0, 1])

    def test_broad_c_uncertainty(self):
        rec = record()
        rec["unresolved"] = [{"metric": "C", "reason": "scope interpretation unresolved"}]
        score = score_record(rec, reference())
        self.assertIsNone(score["C"])
        self.assertEqual(score["bounds"]["C"], [0, 1])

    def test_missing_and_duplicate_judgments_invalid(self):
        rec = record()
        rec["coverage"].pop()
        with self.assertRaises(ValueError):
            score_record(rec, reference())
        rec = record()
        rec["coverage"].append(copy.deepcopy(rec["coverage"][0]))
        with self.assertRaises(ValueError):
            score_record(rec, reference())

    def test_illegal_label(self):
        rec = record()
        rec["units"][0]["judgment"] = "PASS"
        with self.assertRaises(ValueError):
            score_record(rec, reference())
        rec = record()
        rec["coverage"][0]["value"] = True
        with self.assertRaises(ValueError):
            score_record(rec, reference())

    def test_duplicate_cycles_rejected(self):
        rec = record()
        rec["units"][0]["duplicate_of"] = "C2"
        with self.assertRaises(ValueError):
            score_record(rec, reference())

    def test_category_na_not_zero(self):
        score = score_record(record(), reference())
        self.assertIsNone(score["by_category"]["condition"]["C"])
        self.assertEqual(score["by_category"]["condition"]["C_N"], 0)

    def test_weighted_kappa(self):
        self.assertEqual(weighted_kappa([(0, 0), (.5, .5), (1, 1)]), 1)
        self.assertEqual(weighted_kappa([(0, 1), (1, 0)]), -1)
        self.assertIsNone(weighted_kappa([(1, 1), (1, 1)]))
        self.assertIsNone(weighted_kappa([]))

    def test_nominal_kappa(self):
        self.assertEqual(nominal_kappa([("S", "S"), ("U", "U")]), 1)
        self.assertIsNone(nominal_kappa([("S", "S")]))

    def test_bootstrap_reproducible(self):
        self.assertEqual(cluster_interval([.1, .2, -.1], replicates=100), cluster_interval([.1, .2, -.1], replicates=100))
        self.assertEqual(cluster_interval([.3], replicates=100), [.3, .3])
        self.assertIsNone(cluster_interval([]))

    def test_synthetic_29_pair_aggregate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "organizer_private").mkdir()
            (root / "references").mkdir()
            for role in ("score_primary", "score_secondary", "score_adjudicated"):
                (root / "records" / role).mkdir(parents=True)
            mapping = []
            for i in range(29):
                task = f"T{i+1:03}"
                (root / "references" / f"{task}.json").write_text(json.dumps(reference()))
                for j, kind in enumerate(("A", "B")):
                    code = f"P{2*i+j+1:03}"
                    mapping.append({"artifact_code": code, "task_id": task, "case_id": f"synthetic-{i}", "kind": kind, "document": f"project-{i//3}"})
                    for role in ("score_primary", "score_secondary", "score_adjudicated"):
                        rec = record(("S", "U") if kind == "B" else ("S", "S"), (0, 0) if kind == "B" else (1, 1))
                        (root / "records" / role / f"{code}.json").write_text(json.dumps(rec))
            (root / "organizer_private/mapping.json").write_text(json.dumps(mapping))
            result = aggregate(root)
            self.assertEqual(result["overall"]["independent_mean"]["C"]["A_minus_B"], 1)
            self.assertEqual(result["overall"]["independent_mean"]["K"]["A_minus_B"], .5)
            self.assertEqual(result["overall"]["adjudicated"]["C"]["projects_evaluable"], 10)
            self.assertEqual(result["overall"]["adjudicated"]["C"]["paired_targets"], 29)
            self.assertEqual(result["agreement"]["assertion_nominal_kappa"], None)
            self.assertEqual(result["agreement"]["assertion_alignment_counts"]["artifacts_without_alignment"], 58)
            # Only explicit semantic singleton alignment contributes κ.
            (root / "records/alignment").mkdir()
            alignment = {"groups": [{"id": "G1", "left_ids": ["C1"], "right_ids": ["C1"], "relation": "equivalent", "reason": "synthetic same assertion"},
                                    {"id": "G2", "left_ids": ["C2"], "right_ids": ["C2"], "relation": "equivalent", "reason": "synthetic same assertion"}]}
            (root / "records/alignment/P002.json").write_text(json.dumps(alignment))
            result = aggregate(root)
            self.assertEqual(result["agreement"]["assertion_nominal_kappa"], 1)
            self.assertEqual(result["agreement"]["assertion_alignment_counts"]["judged_one_to_one_pairs"], 2)
            # A technical failure is missing, never interpreted as a zero grade.
            (root / "records/score_secondary/P001.json").unlink()
            result = aggregate(root)
            self.assertEqual(result["overall"]["independent_mean"]["C"]["paired_targets"], 28)
            self.assertEqual(len(result["completion"]["technical_or_missing_records"]), 1)
            first = next(r for r in result["per_case"] if r["case_id"] == "synthetic-0")
            self.assertIsNone(first["independent_mean"]["C"]["A"])
            self.assertEqual(first["adjudicated"]["C"]["A"], 1)


if __name__ == "__main__":
    unittest.main()
