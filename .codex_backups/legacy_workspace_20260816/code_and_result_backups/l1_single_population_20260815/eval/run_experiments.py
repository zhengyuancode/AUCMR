"""
实验编排脚本。

运行所有 baseline 方法 + 计算 L1–L3 指标，输出论文表格。

用法:
  # 跑所有 baseline
  python eval/run_experiments.py --baselines --project jtvauctions --uc-names uc_names.json

  # 只算指标（已有结果）
  python eval/run_experiments.py --compute-metrics --results-dir results
"""

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eval.compute_metrics import (
    COCKBURN_SLOTS,
    CONSISTENCY_RULES,
    MetricsComputer,
    NLIJudge,
    UCMetrics,
)
from eval.baselines import (
    LvlMDirectBaseline,
    TextRAGElaborateBaseline,
    ViDoRAGElaborateBaseline,
    VDocRAGElaborateBaseline,
    get_all_baselines,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("experiments")


# ============================================================
# Paper table generation
# ============================================================

def generate_latex_table_l1(aggregate_results: dict[str, dict]) -> str:
    """Generate LaTeX for L1 Evidence Grounding table."""
    method_order = [
        "LVLM-Direct", "TextRAG-Elaborate", "ViDoRAG-Elaborate",
        "VDocRAG-Elaborate", "ReAct-Elaborate",
        "CREST w/o cross-slot", "AUCMR (CREST full)"
    ]

    rows = []
    for method in method_order:
        if method == "LVLM-Direct":
            continue  # no stable page IDs; excluded from L1 citation evaluation
        r = aggregate_results.get(method, {})
        bold = "\\textbf{" if method == "AUCMR (CREST full)" else ""
        bold_end = "}" if method == "AUCMR (CREST full)" else ""
        rows.append(
            f"{bold}{method}{bold_end} & {r.get('l1_n_preconstraint', 0)} & "
            f"{r.get('l1_n_cite', 0)} & {r.get('l1_n_all', 0)} & "
            f"{r.get('l1_n_ent', 0)} & "
            f"{r.get('l1_citcov', 0.0):.1f} & {r.get('l1_nli_g', 0.0):.1f} & "
            f"{r.get('l1_empty_slots', 0)} / "
            f"{r.get('l1_esc_per_uc', 0.0):.2f} \\\\"
        )

    return "\n".join(rows)


def generate_latex_table_l2(aggregate_results: dict[str, dict]) -> str:
    """Generate LaTeX for L2 Structural Completeness table."""
    method_order = [
        "LVLM-Direct", "TextRAG-Elaborate", "ViDoRAG-Elaborate",
        "VDocRAG-Elaborate", "ReAct-Elaborate",
        "CREST w/o cross-slot", "AUCMR (CREST full)"
    ]

    rows = []
    for method in method_order:
        r = aggregate_results.get(method, {})
        fill = r.get("l2_slot_fill_rates", {})
        all8 = r.get("l2_all_8_rate", 0.0)

        parts = [method]
        for slot in COCKBURN_SLOTS:
            parts.append(f"{fill.get(slot, 0.0):.1f}")
        parts.append(f"{all8:.1f}")

        if method == "AUCMR (CREST full)":
            rows.append("\\textbf{" + "} & \\textbf{".join(parts) + "} \\\\")
        else:
            rows.append(" & ".join(parts) + " \\\\")

    return "\n".join(rows)


def generate_latex_table_l3(aggregate_results: dict[str, dict]) -> str:
    """Generate the paper's L2 R1-R7 internal-consistency rows."""
    method_order = [
        "LVLM-Direct", "TextRAG-Elaborate", "ViDoRAG-Elaborate",
        "VDocRAG-Elaborate", "ReAct-Elaborate",
        "CREST w/o cross-slot", "AUCMR (CREST full)"
    ]

    rule_order = ["R1", "R2", "R3", "R4", "R5", "R6", "R7"]

    rows = []
    for method in method_order:
        r = aggregate_results.get(method, {})
        violations = r.get("l2_violations_per_uc", {})
        total = r.get("l2_total_per_uc", 0.0)

        parts = [method]
        for rule_id in rule_order:
            parts.append(f"{violations.get(rule_id, 0.0):.2f}")
        parts.append(f"{total:.2f}")

        if method == "AUCMR (CREST full)":
            rows.append("\\textbf{" + "} & \\textbf{".join(parts) + "} \\\\")
        else:
            rows.append(" & ".join(parts) + " \\\\")

    return "\n".join(rows)


# ============================================================
# Main experiment runner
# ============================================================

class ExperimentRunner:
    """Orchestrates the full experiment pipeline."""

    def __init__(self, output_dir: str = "./results", baseline_names: list[str] | None = None):
        self.output_dir = output_dir
        self.metrics_computer = MetricsComputer(nli_judge=None)  # Will use Qwen for NLI
        self.baselines = get_all_baselines(
            output_dir=os.path.join(output_dir, "baselines"), names=baseline_names
        )

    def load_uc_list(self, uc_list_path: str) -> list[dict]:
        """Load use case list from JSON file.

        Format: [{"project": "jtvauctions", "use_case": "拍卖商品", "actor": "顾客"}, ...]
        """
        with open(uc_list_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def run_baselines(self, uc_list: list[dict]) -> list[str]:
        """Run all baselines on all use cases. Returns list of output paths."""
        all_paths = []
        for baseline in self.baselines:
            for uc in uc_list:
                path = baseline.run_and_save(
                    uc["use_case"], uc["actor"], uc["project"], uc.get("file_names")
                )
                all_paths.append(path)
        return all_paths

    def collect_crest_results(
        self, results_dir: str
    ) -> dict[tuple[str, str, str], tuple[str, str]]:
        """Collect CREST result JSON + evidence JSON pairs.

        Returns: {(project, uc_name): (uc_json_path, evidence_json_path)}
        """
        result_map = {}
        for root, dirs, files in os.walk(results_dir):
            for f in files:
                if f.endswith(".json") and not f.endswith("-evidence.json"):
                    uc_path = os.path.join(root, f)
                    ev_path = uc_path.replace(".json", "-evidence.json")
                    if os.path.exists(ev_path):
                        try:
                            with open(uc_path) as fp:
                                data = json.load(fp)
                            key = (
                                data.get("method", "AUCMR (CREST full)"),
                                os.path.basename(root),
                                data.get("use_case_name", ""),
                                data.get("actor", ""),
                            )
                            result_map[key] = (uc_path, ev_path)
                        except Exception:
                            pass
        return result_map

    @staticmethod
    def select_paper_cohort(
        result_map: dict,
        expected_methods: list[str],
    ) -> tuple[dict, dict]:
        """Use AUCMR-full goal fast-fail flags to define a shared valid cohort."""
        full_method = "AUCMR (CREST full)"
        full_results = {key[1:]: paths for key, paths in result_map.items() if key[0] == full_method}
        if not full_results:
            raise RuntimeError("Cannot define the paper cohort: AUCMR (CREST full) results are missing")
        valid = set()
        rejected = []
        for identity, (uc_path, _ev_path) in full_results.items():
            with open(uc_path, encoding="utf-8") as handle:
                data = json.load(handle)
            flags = data.get("quality_flags", []) or []
            if any(flag.get("flag_type") == "unresolvable_use_case" for flag in flags):
                rejected.append({
                    "project": identity[0], "use_case": identity[1], "actor": identity[2],
                })
            else:
                valid.add(identity)
        if not valid:
            raise RuntimeError("AUCMR full rejected every candidate use case")

        missing = []
        for method in expected_methods:
            identities = {key[1:] for key in result_map if key[0] == method}
            for identity in sorted(valid - identities):
                missing.append({
                    "method": method, "project": identity[0],
                    "use_case": identity[1], "actor": identity[2],
                })
        if missing:
            raise RuntimeError(
                "Paper cohort is incomplete; missing method/use-case results: "
                + json.dumps(missing, ensure_ascii=False)
            )
        selected = {key: paths for key, paths in result_map.items() if key[1:] in valid}
        manifest = {
            "selection_rule": "AUCMR full results without unresolvable_use_case",
            "candidate_count": len(full_results),
            "valid_count": len(valid),
            "valid_use_cases": [
                {"project": item[0], "use_case": item[1], "actor": item[2]}
                for item in sorted(valid)
            ],
            "rejected_use_cases": sorted(rejected, key=lambda item: (item["project"], item["use_case"])),
            "methods": expected_methods,
        }
        return selected, manifest

    @staticmethod
    def evaluation_audit(method_metrics: dict[str, list[UCMetrics]]) -> dict:
        return {
            method: [
                {
                    "use_case": metric.use_case_name,
                    "actor": metric.actor,
                        "l1": {
                        "n_final": metric.total_claims,
                        "n_preconstraint": metric.pre_constraint_claim_count,
                        "n_cite_preconstraint": metric.pre_constraint_cited_claim_count,
                        "n_ent": metric.total_entailed_claims,
                        "empty_slots": metric.empty_slot_count,
                        "judge_failures": metric.nli_parse_failures,
                        "claims": metric.nli_details,
                    },
                    "l2": metric.l2_details,
                }
                for metric in metrics
            ]
            for method, metrics in method_metrics.items()
        }

    def compute_all_metrics(
        self, result_map: dict[tuple[str, str, str], tuple[str, str]],
        run_nli: bool = False,
        run_l2: bool = False,
    ) -> dict[str, list[UCMetrics]]:
        """Compute L1-L3 metrics for all results, grouped by method."""
        method_metrics = defaultdict(list)
        total = len(result_map)

        for i, (key, (uc_path, ev_path)) in enumerate(result_map.items()):
            method_key, project, uc_name, actor = key
            logger.info(f"[{i+1}/{total}] Computing metrics for {uc_name} ({project})")

            try:
                uc_data = json.load(open(uc_path))
                method = uc_data.get("method", "AUCMR (CREST full)")
                metrics = self.metrics_computer.compute_all(
                    uc_path, ev_path, run_nli=run_nli, run_l2=run_l2
                )
                method_metrics[method].append(metrics)
            except Exception as e:
                logger.error(f"Failed to compute metrics for {uc_path}: {e}")

        return dict(method_metrics)

    def aggregate_and_report(
        self, method_metrics: dict[str, list[UCMetrics]]
    ) -> dict[str, dict]:
        """Aggregate metrics across UCs and return paper-ready data."""
        aggregated = {}
        for method, metrics_list in method_metrics.items():
            agg = self.metrics_computer.compute_aggregate(metrics_list, method)
            aggregated[method] = agg

        invalid = {
            method: {
                "l1_judge_failures": result.get("l1_judge_failures", 0),
                "l2_parse_failures": result.get("consistency_parse_failures", 0),
            }
            for method, result in aggregated.items()
            if (
                result.get("l1_evaluated", False) and not result.get("l1_valid", False)
            ) or (
                result.get("l2_evaluated", False) and not result.get("l2_valid", False)
            )
        }
        if invalid:
            raise RuntimeError(
                "Evaluation is incomplete; refusing to aggregate failed judge calls: "
                + json.dumps(invalid, ensure_ascii=False)
            )

        # Print summary
        print("\n" + "=" * 80)
        print("AGGREGATED RESULTS")
        print("=" * 80)

        for method in ["LVLM-Direct", "TextRAG-Elaborate", "ViDoRAG-Elaborate",
                       "VDocRAG-Elaborate", "CREST w/o cross-slot", "AUCMR (CREST full)"]:
            agg = aggregated.get(method, {})
            if not agg:
                continue
            n = agg.get("n_ucs", 0)
            print(f"\n{method} (n={n}):")
            print(
                f"  L1 CitCov(pre-constraint)={agg.get('l1_citcov', 'n/a')}  "
                f"NLI-G(final)={agg.get('l1_nli_g', 'n/a')}"
            )
            print(f"  L2 All-8={agg.get('l2_all_8_rate', 'n/a')}%")
            print(f"  L3 Total violations/UC={agg.get('l3_total_per_uc', 'n/a')}")

        return aggregated


def main():
    ap = argparse.ArgumentParser(description="AUCMR Experiment Runner")
    ap.add_argument("--baselines", action="store_true", help="Run baseline methods")
    ap.add_argument("--compute-metrics", action="store_true", help="Compute L1-L3 metrics")
    ap.add_argument("--run-nli", action="store_true", help="Run NLI verification (expensive)")
    ap.add_argument("--run-l2", action="store_true", help="Run independent R1-R7 judge")
    ap.add_argument("--methods", nargs="+", help="Only run selected baseline method names")
    ap.add_argument("--uc-list", help="Path to UC list JSON file")
    ap.add_argument("--results-dir", default="./results", help="Directory with CREST results")
    ap.add_argument("--output-dir", default="./results", help="Output directory for baselines")
    ap.add_argument("--project", help="Project name for baselines")
    ap.add_argument("--uc-name", help="Single use case name for baselines")
    ap.add_argument("--actor", help="Actor name for baselines")
    args = ap.parse_args()

    runner = ExperimentRunner(output_dir=args.output_dir, baseline_names=args.methods)

    if args.baselines:
        if args.uc_list:
            uc_list = runner.load_uc_list(args.uc_list)
        elif args.uc_name and args.actor and args.project:
            uc_list = [{
                "project": args.project,
                "use_case": args.uc_name,
                "actor": args.actor,
            }]
        else:
            print("ERROR: --baselines requires --uc-list or (--project + --uc-name + --actor)")
            sys.exit(1)

        runner.run_baselines(uc_list)

    if args.compute_metrics:
        # Collect CREST results
        crest_results = runner.collect_crest_results(args.results_dir)
        logger.info(f"Found {len(crest_results)} CREST results")

        # Also collect baseline results
        baseline_dir = os.path.join(args.output_dir, "baselines")
        if os.path.isdir(baseline_dir):
            baseline_results = runner.collect_crest_results(baseline_dir)
            logger.info(f"Found {len(baseline_results)} baseline results")
            crest_results.update(baseline_results)

        # Compute metrics
        method_metrics = runner.compute_all_metrics(
            crest_results, run_nli=args.run_nli, run_l2=args.run_l2
        )

        # Aggregate
        aggregated = runner.aggregate_and_report(method_metrics)

        # Save aggregated results
        agg_path = os.path.join(args.output_dir, "aggregated_metrics.json")
        with open(agg_path, "w", encoding="utf-8") as f:
            json.dump(aggregated, f, ensure_ascii=False, indent=2)
        logger.info(f"Aggregated metrics saved to {agg_path}")

        # Print LaTeX tables
        print("\n" + "=" * 80)
        print("LATEX TABLE: L1 Evidence Grounding")
        print("=" * 80)
        print(generate_latex_table_l1(aggregated))

        print("\n" + "=" * 80)
        print("LATEX TABLE: L2 Structural Completeness")
        print("=" * 80)
        print(generate_latex_table_l2(aggregated))

        print("\n" + "=" * 80)
        print("LATEX TABLE: L3 Internal Consistency")
        print("=" * 80)
        print(generate_latex_table_l3(aggregated))


if __name__ == "__main__":
    main()
