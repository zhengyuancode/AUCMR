"""python -m eval.l2_independent: offline checks and explicitly authorized saved batches."""
import argparse
import json
from pathlib import Path

from .common import L2Error, REPO, read_json


def main(argv=None, *, repo=REPO, transport=None):
    repo = Path(repo).resolve()
    parser = argparse.ArgumentParser(description="Independent whole-use-case L2")
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("inventory", help="Read explicit real files; structure/hash only")
    inv.add_argument("--config", type=Path, default=repo / "config/agent_evaluation_l2_independent.json")
    sub.add_parser("mock", help="Evaluate fixed synthetic cases with mock responses; block network")
    prep = sub.add_parser("prepare-inputs", help="Create an explicit local input manifest and derived config")
    prep.add_argument("--config", type=Path, default=repo / "config/agent_evaluation_l2_independent.json")
    prep.add_argument("--manifest", type=Path, help="Optional server manifest with exactly matching frozen bindings")
    run = sub.add_parser("run", help="Run or resume an explicitly authorized isolated L2 batch")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--authorize-live", action="store_true")
    run.add_argument("--resume", type=Path)
    run.add_argument("--campaign-ledger", type=Path, default=repo / "outputs/evaluation_campaign/budget.sqlite")
    run.add_argument("--budget-cap-cny", type=float, default=500)
    run.add_argument("--max-attempts", type=int, default=2)
    exp = sub.add_parser("export", help="Export an explicitly selected saved run without model calls")
    exp.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "inventory":
            from .runner import inventory
            result = inventory(args.config.resolve(), repo=repo)
        elif args.command == "mock":
            from .runner import run_mock
            result = run_mock()
        elif args.command == "prepare-inputs":
            from .bindings import prepare_inputs
            result = prepare_inputs(args.config.resolve(), repo=repo, manifest_path=args.manifest,
                                    allow_fixture=transport is not None and getattr(transport, "mode", None) == "mock")
        elif args.command == "run":
            from .execution import run_batch
            result = run_batch(args.config.resolve(), repo=repo, authorize_live=args.authorize_live,
                               resume=args.resume, campaign_ledger=args.campaign_ledger,
                               budget_cap_cny=args.budget_cap_cny, max_attempts=args.max_attempts,
                               transport=transport)
        else:
            source = args.run_dir.resolve()
            kind = read_json(source / "run_manifest.json").get("kind")
            if kind == "L2_EVALUATION_RUN":
                from .execution import export_run
                result = export_run(source, repo=repo)
            else:
                from .runner import reexport
                result = reexport(source)
    except (L2Error, OSError, ValueError, KeyError) as exc:
        result = {"status": "BLOCKED", "error_type": type(exc).__name__, "reason": str(exc)}
        if args.command != "run":
            result["real_model_calls"] = 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("binding_validation", "PASS") == "PASS" and result.get("status", "PASS") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
