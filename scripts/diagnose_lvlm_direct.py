"""Capture LVLM-Direct responses without changing its generation behavior.
No requests are made unless --live is explicitly supplied.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def classify(record):
    if record.get("error_type"):
        return "call_error"
    if "raw_response" not in record:
        return "no_call"
    if record.get("parsed") is None:
        return "parse_failed_or_json_null"
    final = record.get("normalized") or {}
    if final.get("claims") or final.get("raw_text"):
        return "accepted"
    if record["parsed"] == {}:
        return "explicit_empty_object"
    return "parsed_but_empty_needs_review"


def capture_slot(baselines, baseline, slot, filled, context, directory):
    record = {"slot": slot}
    original = baselines._call_lvlm

    def capture(prompt, images):
        record["prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        record["images"] = [{"path": str(p), "sha256": hashlib.sha256(Path(p).read_bytes()).hexdigest()} for p in images]
        try:
            raw = original(prompt, images)
        except Exception as exc:
            record["error_type"] = type(exc).__name__
            raise
        record["raw_response"] = raw
        # Save before parsing so an unexpected normalizer exception loses no response.
        write_json(directory / (slot + ".json"), record)
        record["parsed"] = baselines._parse_json_with_repair(raw)
        return raw

    try:
        with patch.object(baselines, "_call_lvlm", capture):
            result = baseline.slot_filler(slot, filled, context)
        record["normalized"] = result
        return result
    except Exception as exc:
        record["normalization_or_setup_error_type"] = type(exc).__name__
        raise
    finally:
        record["status"] = classify(record)
        write_json(directory / (slot + ".json"), record)
        print(slot, record["status"], flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--config", default="experiment.junxi.unified93.json")
    parser.add_argument("--project", default="microcare")
    parser.add_argument("--use-case", default="Record Voucher Distribution")
    parser.add_argument("--actor", default="VMU User")
    args = parser.parse_args(argv)
    if not args.live:
        print("No requests made. Pass --live explicitly to diagnose one case.")
        return 0
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    assert any(x["project"] == args.project and x["use_case"] == args.use_case and x["actor"] == args.actor for x in config["experiment"]["use_cases"]), "Case not in configuration"
    os.environ["AUCMR_CONFIG"] = str(config_path)
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    import eval.baselines as baselines
    from eval.shared_orchestrator import run_shared_orchestrator
    diagnostics = repo / "tmp" / "diagnostics"
    diagnostics.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="lvlm-direct-", dir=diagnostics))
    # Reuse the exact existing composite cache, but never call run_and_save.
    output = Path(config["paths"]["output"])
    if not output.is_absolute():
        output = config_path.parent / output
    baseline = baselines.LvlMDirectBaseline(str(output / "baselines"))
    print("DIAGNOSTIC_DIRECTORY", directory, flush=True)
    result = run_shared_orchestrator(
        use_case_name=args.use_case, actor=args.actor,
        slot_filler=lambda slot, filled, context: capture_slot(baselines, baseline, slot, filled, context, directory),
        method=baseline.name, project=args.project,
        raise_on_error=bool(baselines._cfg("runtime.fail_fast", True)),
    )
    write_json(directory / "diagnostic-result.json", result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
