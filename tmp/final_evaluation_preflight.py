import json
from collections import Counter, defaultdict
from pathlib import Path

import run
from eval.compute_metrics import MetricsComputer
from eval.run_experiments import ExperimentRunner


config, config_path = run.load_config(str(run.DEFAULT_CONFIG))
output_root = run.resolve_path(config_path, config["paths"]["output"])
methods = run.selected_methods(config, None)
runner = ExperimentRunner(output_dir=str(output_root), baseline_names=[])

all_results = {}
for directory in run.ACTIVE_GENERATION_DIRS:
    active = output_root / directory
    if active.is_dir():
        all_results.update(runner.collect_crest_results(str(active)))

generated = Counter(key[0] for key in all_results)
selected, cohort = runner.select_paper_cohort(all_results, methods)
selected_counts = Counter(key[0] for key in selected)

errors = []
audit_versions = defaultdict(Counter)
l1_counts = defaultdict(lambda: [0, 0])
slot_key_counts = defaultdict(Counter)
computer = MetricsComputer()

for key, (uc_path, ev_path) in selected.items():
    method, project, use_case, actor = key
    try:
        uc = json.loads(Path(uc_path).read_text(encoding="utf-8"))
        ev = json.loads(Path(ev_path).read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append({"key": key, "error": f"JSON read failed: {exc}"})
        continue
    if uc.get("method") != method:
        errors.append({"key": key, "error": f"method mismatch: {uc.get('method')}"})
    if uc.get("use_case_name") != use_case or uc.get("actor") != actor:
        errors.append({"key": key, "error": "identity mismatch"})
    version = (uc.get("citation_audit") or {}).get("version", "missing")
    audit_versions[method][version] += 1
    slot_key_counts[method][len(uc.get("slots") or {})] += 1
    if method != "LVLM-Direct":
        _rate, cited, total = computer.compute_l1_scg(uc, ev)
        l1_counts[method][0] += cited
        l1_counts[method][1] += total
        if cited > total:
            errors.append({"key": key, "error": f"N_cite {cited} > N_all {total}"})

payload = {
    "configured_methods": methods,
    "generated_result_pairs": dict(generated),
    "formal_valid_use_cases": cohort.get("valid_count"),
    "formal_rejected_use_cases": cohort.get("rejected_use_cases"),
    "selected_result_pairs": dict(selected_counts),
    "citation_audit_versions": {
        method: dict(counts) for method, counts in audit_versions.items()
    },
    "slot_key_counts": {
        method: dict(counts) for method, counts in slot_key_counts.items()
    },
    "deterministic_l1_before_nli": {
        method: {
            "n_cite": values[0],
            "n_all": values[1],
            "citcov": round(values[0] / values[1] * 100, 2) if values[1] else None,
        }
        for method, values in l1_counts.items()
    },
    "errors": errors,
}
print(json.dumps(payload, ensure_ascii=False, indent=2))
if errors:
    raise SystemExit(1)
if cohort.get("valid_count") != 14:
    raise SystemExit(f"Expected 14 formal valid use cases, got {cohort.get('valid_count')}")
if any(selected_counts.get(method, 0) != 14 for method in methods):
    raise SystemExit(f"Formal cohort is not paired 14-per-method: {dict(selected_counts)}")
print("FINAL_EVALUATION_PREFLIGHT_PASS")
