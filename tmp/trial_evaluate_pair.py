from __future__ import annotations

import json
from pathlib import Path

import run
from eval.run_experiments import ExperimentRunner


USE_CASE = "Complete Checkout and Payment"
METHODS = (run.WITHOUT_DRA, run.FULL)


def main() -> None:
    config, config_path = run.load_config(str(run.DEFAULT_CONFIG))
    output_root = run.resolve_path(config_path, config["paths"]["output"])
    report_root = output_root / "history" / "goal_boundary_trial_20260816"
    report_root.mkdir(parents=True, exist_ok=True)

    runner = ExperimentRunner(output_dir=str(output_root), baseline_names=[])
    all_results = {}
    for directory in ("crest_without_dra", "aucmr_full"):
        source = output_root / directory
        if source.is_dir():
            all_results.update(runner.collect_crest_results(str(source)))

    selected = {
        key: paths
        for key, paths in all_results.items()
        if key[0] in METHODS and key[2] == USE_CASE
    }
    found = {key[0] for key in selected}
    missing = set(METHODS) - found
    if missing:
        raise RuntimeError(f"Trial pair is incomplete; missing methods: {sorted(missing)}")

    versions = {}
    for key, (uc_path, ev_path) in selected.items():
        uc = json.loads(Path(uc_path).read_text(encoding="utf-8"))
        ev = json.loads(Path(ev_path).read_text(encoding="utf-8"))
        versions[key[0]] = {
            "generation_version": uc.get("generation_version"),
            "main_revision": uc.get("artifact_revision_id"),
            "evidence_revision": ev.get("artifact_revision_id"),
        }
        if uc.get("generation_version") != run.CREST_GENERATION_VERSION:
            raise RuntimeError(
                f"{key[0]} is stale: {uc.get('generation_version')!r} != "
                f"{run.CREST_GENERATION_VERSION!r}"
            )
        if not versions[key[0]]["main_revision"] or (
            versions[key[0]]["main_revision"] != versions[key[0]]["evidence_revision"]
        ):
            raise RuntimeError(f"{key[0]} main/evidence pair is not atomic")

    metrics = runner.compute_all_metrics(
        selected,
        run_nli=True,
        run_l2=True,
        max_workers=2,
    )
    aggregated = runner.aggregate_and_report(metrics)
    audit = runner.evaluation_audit(metrics)

    (report_root / "aggregated_metrics.json").write_text(
        json.dumps(aggregated, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (report_root / "evaluation_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (report_root / "artifact_versions.json").write_text(
        json.dumps(versions, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    compact = {
        method: {
            "n_all": row.get("l1_n_all"),
            "n_cite": row.get("l1_n_cite"),
            "n_ent": row.get("l1_n_ent"),
            "CitCov": row.get("l1_citcov"),
            "NLI-G": row.get("l1_nli_g"),
            "CHG": row.get("l1_chg"),
            "ESC/UC": row.get("l1_esc_per_uc"),
            "R1-R7/UC": row.get("l2_total_per_uc"),
        }
        for method, row in aggregated.items()
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    print(f"TRIAL_REPORT={report_root}")


if __name__ == "__main__":
    main()
