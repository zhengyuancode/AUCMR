"""Independent L4 CLI; live evaluation requires frozen binding and explicit authorization."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4

from .engine import evaluate_run, validate_run
from .export import export_records
from .fixtures import FixtureClient, make_fixtures
from .inputs import freeze
from .runner import run_evaluation
from .protocol import L4Error, REPO, network_blocked, new_run, read_json, save_new


def main(argv=None):
    parser = argparse.ArgumentParser(description="Independent Agent L4; live calls are disabled unless explicitly authorized")
    commands = parser.add_subparsers(dest="command", required=True)
    inv = commands.add_parser("inventory", help="Read only structure and hashes of explicit real inputs")
    inv.add_argument("--config", type=Path, default=REPO / "config/agent_evaluation_l4.json")
    commands.add_parser("mock", help="Construct and score synthetic fixtures with networking blocked")
    for name in ("validate", "export"):
        sub = commands.add_parser(name)
        sub.add_argument("--run", type=Path, required=True)
    live = commands.add_parser("run", help="Evaluate an explicitly bound batch; no live calls without --authorize-live")
    live.add_argument("--config", type=Path)
    live.add_argument("--run-dir", type=Path)
    live.add_argument("--resume", type=Path, metavar="RUN_DIR")
    live.add_argument("--campaign-ledger", type=Path,
                      help="Shared campaign ledger, default outputs/evaluation_campaign/budget.sqlite")
    live.add_argument("--authorize-live", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            result = run_evaluation(args.config, run_dir=args.run_dir, resume=args.resume,
                                    campaign_ledger=args.campaign_ledger, explicit_live=args.authorize_live)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        with network_blocked():
            if args.command == "inventory":
                config = read_json(args.config)
                run = new_run("inventory")
                manifest = freeze(config, run)
                result = {"run_dir": str(run), "planned": len(manifest["artifacts"]),
                          "states": {state: sum(a["state"] == state for a in manifest["artifacts"]) for state in sorted({a["state"] for a in manifest["artifacts"]})},
                          "sources": [{k: s[k] for k in ("source_id", "state", "sha256")} for s in manifest["sources"]],
                          "explicit_refusals": sum(a.get("explicit_refusal", False) for a in manifest["artifacts"]),
                          "semantic_evaluation": "NOT_RUN", "real_model_calls": 0}
                save_new(run / "inventory_report.json", result)
            elif args.command == "mock":
                run = new_run("mock")
                config = make_fixtures(run)
                freeze(config, run)
                client = FixtureClient(read_json(run / "fixtures/fixture_manifest.json")["scenarios"])
                records = evaluate_run(run, client, mock=True)
                validate_run(run)
                summary = export_records(records, config, run / "exports")
                result = {"run_dir": str(run), "data_class": "MOCK_ONLY", "research_status": "NOT_RESEARCH_DATA",
                          "records": len(records), "mock_requests": client.calls, "real_model_calls": 0,
                          "formal_evaluation": "NOT_RUN", "validation": "PASS", "groups": summary["groups"]}
                save_new(run / "mock_report.json", result)
            else:
                config, manifest, records = validate_run(args.run)
                result = {"run_dir": str(args.run), "validation": "PASS", "records": len(records), "real_model_calls": 0}
                if args.command == "export":
                    destination = args.run / ("exports-" + uuid4().hex[:8])
                    export_records(records, config, destination)
                    result["export_dir"] = str(destination)
            print(json.dumps({k: v for k, v in result.items() if k != "groups"}, ensure_ascii=False, indent=2))
        return 0
    except (L4Error, ValueError, OSError, KeyError, TypeError) as exc:
        result = {"status": "STOPPED", "reason": str(exc)}
        if args.command == "run":
            result["accounting"] = "Inspect saved runtime receipts and the shared campaign ledger; failures may have incurred cost."
        else:
            result["real_model_calls"] = 0
        print(json.dumps(result, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
