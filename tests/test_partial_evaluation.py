from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


def _write_pair(root: Path, method: str, project: str, case: str, actor: str) -> None:
    if method == "AUCMR (CREST full)":
        directory = root / "aucmr_full" / project
        stem = case.replace(" ", "_")
    elif method == "CREST w/o cross-slot":
        directory = root / "crest_without_dra" / project
        stem = case.replace(" ", "_")
    else:
        directory = root / "baselines" / project
        stem = f"{case.replace(' ', '_')}_{method}"
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "method": method,
        "use_case_name": case,
        "actor": actor,
        "quality_flags": [],
        "slots": {},
    }
    (directory / f"{stem}.json").write_text(json.dumps(payload), encoding="utf-8")
    (directory / f"{stem}-evidence.json").write_text("{}", encoding="utf-8")


def test_partial_evaluation_freezes_same_anchor_cohort_for_every_method(tmp_path):
    import run

    methods = [
        "AUCMR (CREST full)",
        "CREST w/o cross-slot",
        "LVLM-Direct",
        "TextRAG-Elaborate",
        "VDocRAG-Elaborate",
        "ViDoRAG-Elaborate",
        "ReAct-Elaborate",
    ]
    output = tmp_path / "outputs"
    for method in methods:
        _write_pair(output, method, "demo", "Case One", "User")
        if method != "ViDoRAG-Elaborate":
            _write_pair(output, method, "demo", "Case Two", "User")

    config = {
        "paths": {"output": "outputs"},
        "experiment": {
            "methods": methods,
            "use_cases": [
                {"project": "demo", "use_case": "Case One", "actor": "User"},
                {"project": "demo", "use_case": "Case Two", "actor": "User"},
            ],
            "evaluation": {"run_nli": False, "run_l2_judge": False},
        },
    }
    config_path = tmp_path / "experiment.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    captured = {}

    def fake_evaluate(_config, _runner, result_map, report_root):
        captured["keys"] = set(result_map)
        captured["root"] = report_root
        return report_root / "aggregated_metrics.json"

    with patch.object(run, "_write_evaluation_outputs", side_effect=fake_evaluate):
        run.run_partial_evaluation(
            config,
            config_path,
            methods,
            anchor_method="ViDoRAG-Elaborate",
            label="smoke",
        )

    report_root = output / "backup_evaluations" / "smoke"
    manifest = json.loads((report_root / "evaluation_cohort.json").read_text(encoding="utf-8"))
    assert manifest["paired_count"] == 1
    assert manifest["anchor_available_count"] == 1
    assert manifest["paired_count_by_project"] == {"demo": 1}
    assert len(captured["keys"]) == len(methods)
    assert {key[2] for key in captured["keys"]} == {"Case One"}
    assert len(manifest["snapshot_files"]) == len(methods) * 2
    assert all((report_root / relative).is_file() for relative in manifest["snapshot_files"])
