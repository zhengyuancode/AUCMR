import json
import tempfile
from pathlib import Path

import run
from eval.compute_metrics import NLIJudge


METHOD = "AUCMR (CREST full)"


class FakeRunner:
    def __init__(self):
        self.calls = []

    def compute_all_metrics(self, results, run_nli=False, run_l2=False, max_workers=1):
        self.calls.append((run_nli, run_l2, max_workers, len(results)))
        return {METHOD: [object()]}

    def evaluation_audit(self, method_metrics):
        return {
            METHOD: [{
                "use_case": "UC",
                "actor": "Actor",
                "l1": {"n_all": 3, "n_cite": 2, "n_ent": 1},
                "l2": {"summary": {"R1": 0}},
            }]
        }

    def aggregate_and_report(self, method_metrics):
        return {
            METHOD: {
                "method": METHOD,
                "n_ucs": 1,
                "l1_n_all": 3,
                "l1_n_cite": 2,
                "l1_n_ent": 1,
                "l1_citcov": 66.7,
                "l1_nli_g": 33.3,
                "l1_chg": 50.0,
                "l1_empty_slots": 0,
                "l1_esc_per_uc": 0.0,
                "l2_violations_per_uc": {f"R{i}": 0.0 for i in range(1, 8)},
                "l2_total_per_uc": 0.0,
                "l2_evaluated": True,
                "l2_valid": True,
                "consistency_judged_ucs": 1,
                "consistency_parse_failures": 0,
            }
        }


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    runner = FakeRunner()
    run._write_evaluation_outputs(
        {"experiment": {"evaluation": {"l1_workers": 4}}},
        runner, {
            (METHOD, "p", "u", "a"): ("u", "e"),
            ("LVLM-Direct", "p", "u", "a"): ("u2", "e2"),
        }, root, "l1",
    )
    assert runner.calls == [(True, False, 4, 1)]
    assert (root / "evaluation_audit_l1.json").is_file()
    assert (root / "aggregated_metrics_l1.json").is_file()
    assert (root / "table_l1.tex").is_file()
    assert not (root / "evaluation_audit_l2.json").exists()
    l1 = json.loads((root / "aggregated_metrics_l1.json").read_text(encoding="utf-8"))
    assert "l1_n_ent" in l1[METHOD]
    assert "l2_total_per_uc" not in l1[METHOD]

    runner = FakeRunner()
    run._write_evaluation_outputs(
        {"experiment": {"evaluation": {"l2_workers": 4}}},
        runner, {(METHOD, "p", "u", "a"): ("u", "e")}, root, "l2",
    )
    assert runner.calls == [(False, True, 4, 1)]
    assert (root / "evaluation_audit_l2.json").is_file()
    assert (root / "aggregated_metrics_l2.json").is_file()
    assert (root / "table_l2_consistency.tex").is_file()
    l2 = json.loads((root / "aggregated_metrics_l2.json").read_text(encoding="utf-8"))
    assert "l2_total_per_uc" in l2[METHOD]
    assert "l1_n_ent" not in l2[METHOD]

    for stage in ("l1", "l2"):
        (root / f"evaluation_cohort_{stage}.json").write_text("{}", encoding="utf-8")
        current, state = run._evaluation_stage_current(root, stage, 0.0)
        assert current, state


responses = iter(["not json", "still not json", '{"entailment":"entailment","rationale":"ok"}'])
judge = NLIJudge(model_fn=lambda prompt: next(responses), max_attempts=3)
result = judge.judge("claim", ["evidence"])
assert result["entailment"] == "entailment"
assert result["judge_attempts"] == 3
print("SPLIT_EVALUATION_SMOKE_PASS")
