"""Explicit L4 live entry point; injectable transport is restricted to fixtures."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from eval.evaluation_cost_policy import provider_managed
from eval.evaluation_runtime import EvaluationRuntime
from .client import RuntimeJudgeClient
from .engine import authorize, evaluate_run, validate_run
from .export import export_records
from .inputs import assert_current, freeze, local_path
from .protocol import L4Error, REPO, new_run, read_json, require_run, save_new

DEFAULT_CAMPAIGN_LEDGER = "outputs/evaluation_campaign/budget.sqlite"


def run_evaluation(config_path: Path | None = None, *, run_dir: Path | None = None,
                   resume: Path | None = None, campaign_ledger: Path | None = None,
                   explicit_live: bool = False, transport=None) -> dict:
    if resume is not None and (config_path is not None or run_dir is not None):
        raise L4Error("--resume uses its frozen config; do not also pass --config or --run-dir")
    mock = transport is not None and getattr(transport, "mode", None) == "mock"
    if transport is not None and not mock:
        raise L4Error("Injected transports must explicitly declare mode=mock")
    if resume is not None:
        run = require_run(local_path(str(resume)))
        config, _ = assert_current(run)
        frozen_ledger = local_path(config["runtime"]["campaign_ledger"])
        if campaign_ledger is not None and local_path(str(campaign_ledger)) != frozen_ledger:
            raise L4Error("Campaign ledger cannot change during resume")
        ledger = frozen_ledger
    else:
        path = config_path or REPO / "config/agent_evaluation_l4.json"
        config = deepcopy(read_json(path))
        options = config.setdefault("runtime", {})
        ledger = local_path(str(campaign_ledger or options.get("campaign_ledger", DEFAULT_CAMPAIGN_LEDGER)))
        options["campaign_ledger"] = ledger.relative_to(REPO).as_posix()
        options.setdefault("budget_cap_cny", 500)
    # This check precedes runtime construction and any possible credential access.
    authorize(config, mock=mock, explicit_live=explicit_live)
    if mock and config["data_class"] != "MOCK_ONLY":
        raise L4Error("Mock transport cannot score a real input batch")
    cap = config["runtime"].get("budget_cap_cny")
    if provider_managed(config["model"]):
        cap = 500  # Compatibility metadata only; runtime disables local enforcement.
    if type(cap) not in {int, float} or not 0 < cap <= 500:
        raise L4Error("Campaign budget must be positive and at most CNY 500")
    if resume is None:
        if run_dir is None:
            run = new_run("live" if not mock else "transport-mock")
        else:
            run = require_run(local_path(str(run_dir)))
            if run.exists() and any(run.iterdir()) and not mock:
                raise L4Error("New live run directory must be empty; use --resume for existing work")
            run.mkdir(parents=True, exist_ok=True)
        freeze(config, run)
    if mock and not ledger.is_relative_to((run / "fixtures").resolve()):
        raise L4Error("Mock transport requires a separate ledger inside this run's fixtures")
    runtime = EvaluationRuntime(config["model"], run, "L4", ledger,
                                budget_cap_cny=cap, live_authorized=explicit_live,
                                transport=transport,
                                http_timeout_seconds=config["runtime"].get("http_timeout_seconds", 120))
    if mock and runtime.is_live:
        raise L4Error("A mock input batch cannot use a live runtime")
    client = RuntimeJudgeClient(runtime)
    records = evaluate_run(run, client, mock=mock, explicit_live=explicit_live, resume=resume is not None)
    validate_run(run)
    report_path = run / "run_report.json"
    if report_path.exists():
        return read_json(report_path)
    destination = run / "exports"
    if destination.exists():
        # Preserve a partially written export after interruption; scores are not rerun.
        destination = run / ("exports-" + uuid4().hex[:8])
    export_records(records, config, destination)
    costs = [r["runtime_cost_cny"] for r in records]
    calls = [r["real_model_calls"] for r in records]
    report = {"run_dir": str(run), "export_dir": str(destination), "data_class": config["data_class"],
              "research_status": "NOT_RESEARCH_DATA" if mock else ("MODEL_SIMULATED_EXPERT_NOT_HUMAN_EXPERIMENT" if config.get('request_organization') == 'L4_SIMULATED_EXPERT_3.0.6' else "UNVALIDATED_AGENT_ESTIMATE"),
              "rater_type": "MODEL_SIMULATED_EXPERT" if config.get('request_organization') == 'L4_SIMULATED_EXPERT_3.0.6' else "AGENT_JUDGE",
              "records": len(records), "technical_failures": sum(r["processing_status"] == "TECHNICAL_FAILURE" for r in records),
              "technically_valid": sum(r["parse_status"] in {"VALID", "RULE_ONLY"} for r in records),
              "semantic_unresolved": sum(r["processing_status"] == "UNRESOLVED" for r in records),
              "request_organization": config.get("request_organization", "WHOLE_ARTIFACT_V1"),
              "runtime_cost_cny": sum(costs) if all(c is not None for c in costs) else None,
              "runtime_cost_basis": "USAGE_LIST_PRICE_OR_CONSERVATIVE_UPPER_ESTIMATE_NOT_INVOICE",
              "real_model_calls": (0 if mock else len(list(runtime.cache_dir.glob("*.json")))) if config.get("request_organization") else (sum(calls) if all(c is not None for c in calls) else None),
              "real_model_calls_this_invocation": runtime.real_model_calls,
              "campaign_ledger": ledger.relative_to(REPO).as_posix(), "campaign_budget_cap_cny": None if provider_managed(config["model"]) else cap,
              "cost_control": "PROVIDER_MANAGED" if provider_managed(config["model"]) else "LOCAL_CAP",
              "resume_policy": "Saved paid responses and final records remain immutable. Pre-dispatch deferrals remain pending and resume without redrawing paid judgments.",
              "validation": "PASS"}
    save_new(report_path, report)
    return report
