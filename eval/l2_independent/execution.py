"""Persistent L2 batches and export. A transport is never used to score real inputs in mock mode."""
import copy
from eval.implementation import implementation_manifest
import json
from pathlib import Path
from uuid import uuid4

from .common import (COMPLETE, L2Error, REPO, PROTOCOL_VERSION, PROMPT_VERSION,
                     confined, digest, file_hash, new_run, read_json, write_json)
from .evaluation import (assert_reusable, cache_identity, evaluate,
                         require_live_authorization)
from .inputs import collect, load_config
from .integration import check_l3_alignment
from .protocol import prompt_hash
from .reporting import export_summary


class RuntimeClient:
    def __init__(self, runtime, config, task_id, attempt):
        self.runtime = runtime
        self.is_live = runtime.is_live
        self.model_config_hash = digest(config["model"])
        self.task_id, self.attempt = task_id, attempt
        self.envelope = None
        self.error = None
        self.calls_before = runtime.real_model_calls

    def complete(self, messages):
        try:
            self.envelope = self.runtime.request(messages, task_id=self.task_id, attempt=self.attempt)
        except Exception as exc:
            self.error = exc
            raise
        body = self.envelope["body"]
        if self.envelope.get("finish_reason") != "stop":
            raise L2Error("Response did not finish normally")
        return body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)


class _NoRequestClient:
    def __init__(self, config, is_live):
        self.is_live = is_live
        self.model_config_hash = digest(config["model"])

    def complete(self, messages):
        raise AssertionError("Placeholder records must not request a model")


def _pending(artifact, config, run_id, is_live, reason="NOT_STARTED"):
    blocked = copy.deepcopy(artifact)
    if blocked["input_status"] == "PRESENT":
        blocked["input_status"] = reason
    record = evaluate(blocked, config, _NoRequestClient(config, is_live), run_id,
                      explicit_live_authorization=is_live)
    record["input_status"] = artifact["input_status"]
    record["assertion_count_is_complete"] = artifact["input_status"] in {
        "PRESENT", "VALID_EMPTY", "EXPLICIT_REFUSAL"}
    record["cache_key"] = cache_identity(artifact, config)
    record["expected_input_hash"] = artifact.get("expected_input_hash")
    record["real_model_calls"] = 0
    return record


def _sealed_write(path, value):
    # One immutable envelope lets readers detect incomplete or accidental changed records.
    write_json(path, {"sha256": digest(value), "value": value})


def _sealed_read(path):
    wrapper = read_json(path)
    if not isinstance(wrapper, dict) or set(wrapper) != {"sha256", "value"}:
        raise L2Error("Invalid saved record envelope")
    if wrapper["sha256"] != digest(wrapper["value"]):
        raise L2Error("Saved record integrity mismatch")
    return wrapper["value"]


def _run_path(repo, selected):
    root = (Path(repo).resolve() / "outputs/agent_l2_independent").resolve()
    path = Path(selected).resolve()
    if path == root or not path.is_relative_to(root):
        raise L2Error("Select an explicit L2 run under outputs/agent_l2_independent")
    return path


def _record_files(run_dir, artifact_id):
    return sorted((run_dir / "attempts" / artifact_id).glob("*/record.json"))


def _last_record(run_dir, artifact, config):
    files = _record_files(run_dir, artifact["artifact_id"])
    if not files:
        return None
    record = _sealed_read(files[-1])
    if record.get("cache_key") != cache_identity(artifact, config):
        raise L2Error("Saved record input/model/prompt identity differs")
    if record["status"] in COMPLETE:
        assert_reusable(record, artifact, config)
    return record


def _next_attempt(run_dir, artifact_id):
    parent = run_dir / "attempts" / artifact_id
    numbers = [int(p.name) for p in parent.iterdir() if p.is_dir() and p.name.isdigit()] if parent.exists() else []
    latest = max(numbers, default=0)
    if latest and not (parent / f"{latest:04d}" / "record.json").exists():
        return latest
    return latest + 1


def _finish(run_dir, config, artifacts, is_live, status, reused=0):
    records = [_last_record(run_dir, a, config) or _pending(a, config, run_dir.name, is_live)
               for a in artifacts]
    snapshot = run_dir / "checkpoints" / (uuid4().hex + ".json")
    _sealed_write(snapshot, {"status": status, "records": records})
    return {"status": status, "run_dir": str(run_dir), "checkpoint": str(snapshot),
            "registered_pairs": len(artifacts), "saved_records": sum(
                bool(_record_files(run_dir, a["artifact_id"])) for a in artifacts),
            "completed_checks": sum(r["status"] in COMPLETE for r in records),
            "reused_records": reused,
            "real_model_calls": sum(r.get("real_model_calls", 0) for r in records),
            "technical_failures": sum(r["status"] == "TECHNICAL_FAILURE" for r in records),
            "partial_or_unresolved": sum(r["status"] == "PARTIAL_UNRESOLVED" for r in records),
            "real_semantic_evaluation": "UNVALIDATED" if is_live else "NOT_RUN",
            "research_data_status": "LIVE_UNVALIDATED" if is_live else "NOT_RESEARCH_DATA"}


def run_batch(config_path, *, repo=REPO, authorize_live=False, resume=None,
              campaign_ledger=None, budget_cap_cny=500, max_attempts=2, transport=None):
    repo = Path(repo).resolve()
    fake = transport is not None and getattr(transport, "mode", None) == "mock"
    config = load_config(config_path, allow_fixture=fake)
    fixture = config["data_origin"] == "FIXTURE"
    if fake and not fixture:
        raise L2Error("Fake transports cannot assign judgments to real inputs")
    if fixture and not fake:
        raise L2Error("Fixture runtime batches require an explicit injected mock transport")
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or not 1 <= max_attempts <= 3:
        raise L2Error("max_attempts must be between 1 and 3")
    if not fixture:
        require_live_authorization(config, authorize_live)
    alignment = check_l3_alignment(config, repo)
    if alignment["status"] != "PASS":
        raise L2Error("Input alignment changed; prepare a reviewed local binding")
    artifacts = collect(config, repo, allow_fixture=fixture)
    # Known file changes block the batch. Unavailable files remain explicit technical failures.
    if any(not a["hash_matches_binding"] and a["input_status"] not in {"MISSING_FILE", "READ_FAILED"}
           for a in artifacts):
        raise L2Error("Frozen input content changed")
    ledger = Path(campaign_ledger) if campaign_ledger is not None else repo / "outputs/evaluation_campaign/budget.sqlite"
    ledger = ledger.resolve()
    if fixture and not ledger.is_relative_to(repo / "outputs/agent_l2_independent"):
        raise L2Error("Fixture requests need an isolated ledger under the L2 fixture output root")
    identity = {"config_hash": digest(config), "artifacts_hash": digest(artifacts),
                "implementation": implementation_manifest("l2"),
                "protocol_version": PROTOCOL_VERSION, "prompt_version": PROMPT_VERSION,
                "prompt_hash": prompt_hash(), "campaign_ledger": str(ledger),
                "budget_cap_cny": budget_cap_cny, "max_attempts": max_attempts,
                "evaluation_mode": "MOCK_ONLY" if fixture else "LIVE"}
    if resume is None:
        run_dir = new_run("run", repo / "outputs/agent_l2_independent")
        write_json(run_dir / "config.snapshot.json", config)
        write_json(run_dir / "artifacts.json", artifacts)
        write_json(run_dir / "alignment.json", alignment)
        write_json(run_dir / "run_manifest.json", {
            "kind": "L2_EVALUATION_RUN", "run_id": run_dir.name, **identity,
            "data_origin": config["data_origin"],
            "research_data_status": "NOT_RESEARCH_DATA" if fixture else "LIVE_UNVALIDATED"})
    else:
        run_dir = _run_path(repo, resume)
        manifest = read_json(run_dir / "run_manifest.json")
        if manifest.get("kind") != "L2_EVALUATION_RUN" or any(manifest.get(k) != v for k, v in identity.items()):
            raise L2Error("Resume identity differs; inputs, binding, prompt, budget and retry policy must stay frozen")
        if digest(read_json(run_dir / "config.snapshot.json")) != identity["config_hash"]:
            raise L2Error("Saved config was changed")
        if digest(read_json(run_dir / "artifacts.json")) != identity["artifacts_hash"]:
            raise L2Error("Saved artifacts were changed")
    # Lazy import keeps inventory/mock independent of transport and its budget infrastructure.
    from eval.evaluation_runtime import EvaluationRuntime, RuntimeBlocked, RuntimeDeferred
    try:
        runtime = EvaluationRuntime(config["model"], run_dir / "runtime", "L2", ledger,
                                    budget_cap_cny=budget_cap_cny,
                                    live_authorized=authorize_live, transport=transport)
    except Exception as exc:
        _sealed_write(run_dir / "events" / (uuid4().hex + ".json"),
                      {"state": "RUNTIME_BLOCKED", "error_type": type(exc).__name__})
        return _finish(run_dir, config, artifacts, not fixture, "BLOCKED")
    if runtime.is_live == fixture:
        raise L2Error("Runtime mode does not match input origin")
    reused = 0
    try:
        for artifact in artifacts:
            previous = _last_record(run_dir, artifact, config)
            if previous is not None:
                if previous["status"] in COMPLETE or previous["status"] in {"PARTIAL_UNRESOLVED", "NO_EVALUABLE_CONTENT"}:
                    reused += 1
                    continue
                if not (previous.get("retryable", False) or previous.get("retry_on_resume", False)):
                    continue
            attempt = _next_attempt(run_dir, artifact["artifact_id"])
            while attempt <= max_attempts:
                attempt_dir = run_dir / "attempts" / artifact["artifact_id"] / f"{attempt:04d}"
                attempt_dir.mkdir(parents=True, exist_ok=True)
                started = {"cache_key": cache_identity(artifact, config), "attempt": attempt,
                           "state": "STARTED", "task_id": artifact["task_id"]}
                if (attempt_dir / "started.json").exists():
                    if _sealed_read(attempt_dir / "started.json") != started:
                        raise L2Error("Interrupted attempt identity changed")
                else:
                    _sealed_write(attempt_dir / "started.json", started)
                client = RuntimeClient(runtime, config, cache_identity(artifact, config), attempt)
                record = evaluate(artifact, config, client, run_dir.name,
                                  explicit_live_authorization=authorize_live)
                record.update(attempt=attempt, expected_input_hash=artifact.get("expected_input_hash"),
                              runtime_response=client.envelope, real_model_calls=(
                                  client.envelope.get("real_model_calls", int(runtime.is_live))
                                  if client.envelope else 0))
                # Paid malformed responses and unknown-cost requests are never retried automatically.
                error = client.error
                if isinstance(error, RuntimeDeferred):
                    # No request was dispatched. Keep this attempt open so scheduling
                    # failures do not exhaust retries or become permanent score records.
                    _sealed_write(run_dir / "events" / (uuid4().hex + ".json"), {
                        "state": "DEFERRED_BEFORE_DISPATCH", "dispatched": False,
                        "artifact_id": artifact["artifact_id"], "attempt": attempt,
                        "error_type": type(error).__name__, "real_model_calls": 0})
                    return _finish(run_dir, config, artifacts, runtime.is_live, "BLOCKED", reused)
                record["retryable"] = bool(error is not None and getattr(error, "retryable", False))
                record["retry_on_resume"] = bool(
                    isinstance(error, RuntimeBlocked)
                    and getattr(error, "receipt", None) is None)
                if error is not None:
                    record["runtime_error_type"] = type(error).__name__
                    record["runtime_request_id"] = getattr(error, "request_id", None)
                    record["runtime_evidence_path"] = str(getattr(error, "evidence_path", ""))
                    metadata = getattr(error, "receipt", None)
                    if isinstance(metadata, dict):
                        record["runtime_error_metadata"] = metadata
                        record["real_model_calls"] = metadata.get("real_model_calls", 0)
                receipt = client.envelope or (getattr(error, "receipt", None) if error else None)
                if isinstance(receipt, dict):
                    for field in ("usage", "cost_cny", "response_model", "finish_reason"):
                        record[field] = receipt.get(field)
                    record["runtime_request_id"] = receipt.get("request_id")
                    record["cached_response"] = bool(receipt.get("cached", False))
                record["new_real_model_calls"] = max(0, runtime.real_model_calls - client.calls_before)
                record["real_model_calls"] = max(record["real_model_calls"], record["new_real_model_calls"])
                _sealed_write(attempt_dir / "record.json", record)
                if isinstance(error, RuntimeBlocked):
                    return _finish(run_dir, config, artifacts, runtime.is_live, "BLOCKED", reused)
                if record["status"] != "TECHNICAL_FAILURE" or not record["retryable"]:
                    break
                attempt += 1
    except KeyboardInterrupt:
        _sealed_write(run_dir / "events" / (uuid4().hex + ".json"),
                      {"state": "INTERRUPTED", "completed_records_preserved": True})
        return _finish(run_dir, config, artifacts, runtime.is_live, "INTERRUPTED", reused)
    records = [_last_record(run_dir, a, config) for a in artifacts]
    status = "PASS" if all(r is not None and r["status"] != "TECHNICAL_FAILURE" for r in records) else "INCOMPLETE"
    return _finish(run_dir, config, artifacts, runtime.is_live, status, reused)


def export_run(source_run, *, repo=REPO):
    repo = Path(repo).resolve()
    source = _run_path(repo, source_run)
    manifest = read_json(source / "run_manifest.json")
    if manifest.get("kind") != "L2_EVALUATION_RUN":
        raise L2Error("Expected a saved real or explicit fixture runtime batch")
    if manifest.get("protocol_version") != PROTOCOL_VERSION or manifest.get("prompt_hash") != prompt_hash():
        raise L2Error("Protocol/prompt changed; do not silently export under new semantics")
    if manifest.get("implementation") != implementation_manifest("l2"):
        raise L2Error("Evaluation implementation changed; export requires the original frozen code")
    config = read_json(source / "config.snapshot.json")
    artifacts = read_json(source / "artifacts.json")
    if digest(config) != manifest["config_hash"] or digest(artifacts) != manifest["artifacts_hash"]:
        raise L2Error("Saved batch inputs/config changed")
    is_live = manifest["evaluation_mode"] == "LIVE"
    records = [_last_record(source, a, config) or _pending(a, config, source.name, is_live)
               for a in artifacts]
    destination = new_run("export", repo / "outputs/agent_l2_independent")
    table_dir = destination / ("tables" if is_live else "fixtures/export")
    summary = export_summary(table_dir, records, config)
    write_json(destination / "source_run.json", {
        "source_run": source.name, "source_manifest_hash": file_hash(source / "run_manifest.json"),
        "record_count": len(records)})
    write_json(destination / "run_manifest.json", {
        "kind": "L2_EVALUATION_EXPORT", "data_origin": config["data_origin"],
        "evaluation_mode": manifest["evaluation_mode"], "real_model_calls": 0,
        "research_data_status": summary["research_data_status"],
        "files": {p.relative_to(destination).as_posix(): file_hash(p)
                  for p in destination.rglob("*") if p.is_file()}})
    return {"status": "PASS", "run_dir": str(destination), "source_run": source.name,
            "record_count": len(records), "tables": str(table_dir),
            "research_data_status": summary["research_data_status"], "real_model_calls": 0}
