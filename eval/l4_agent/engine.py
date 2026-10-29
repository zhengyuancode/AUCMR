"""One judge, injectable client, explicit authorization before any client call."""
from __future__ import annotations

from eval.evaluation_cost_policy import interface_ready

import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
from typing import Protocol

from .inputs import anonymous, assert_current, local_path
from .protocol import L4Error, PROMPT, RUBRIC, SLOTS, digest, read_json, require_run, save_new
from .records import absent, empty_score, evaluate_response, parse_response, seal, validate_record
from .sources import SourceTools
from eval.evaluation_runtime import RuntimeBlocked, RuntimeResponseError, RuntimeDeferred


class JudgeClient(Protocol):
    # A client must not open a connection in its constructor. No default SDK or key lookup.
    def respond(self, request: dict, history: list[dict]) -> str: ...


def authorize(config: dict, *, mock: bool, explicit_live: bool = False) -> None:
    if mock:
        if config["data_class"] != "MOCK_ONLY":
            raise L4Error("Mock scores are forbidden for real inputs")
        return
    model = config["model"]
    if config.get("live_authorized") is not True or explicit_live is not True:
        raise L4Error("LIVE_NOT_AUTHORIZED: stopped before client request")
    if not interface_ready(model) or not all(model.get(k) for k in ("provider", "model_id", "config_id")):
        raise L4Error("MODEL_NOT_BOUND: stopped before client request")
    if config["judge"]["version"] == "NOT_BOUND":
        raise L4Error("JUDGE_VERSION_NOT_BOUND")
    if config["data_class"] != "REAL_AGENT_EVALUATION" or model.get("supports_images") is not True:
        raise L4Error("Live evaluation requires an explicit real-evaluation batch and a verified image-capable client")


def request_for(artifact: dict, task: dict, sources: list, policy: dict) -> dict:
    return {"system": PROMPT, "rubric": RUBRIC, "artifact": anonymous(artifact, task, sources),
            "source_policy": dict(policy),
            "tools": {
                "source_index": {"required": ["source_id"]},
                "source_search": {"required": ["source_id", "query"]},
                "source_page": {"required": ["source_id", "pdf_page"], "optional": ["image", "dpi", "bbox"]}}}


def initial_record(artifact: dict, config: dict, manifest: dict, run_id: str) -> dict:
    state = artifact["state"]
    valid = state in {"PRESENT", "EMPTY_OUTPUT", "REFUSED"}
    no_content = state in {"EMPTY_OUTPUT", "REFUSED"} or artifact.get("explicit_refusal", False)
    pcs = {s: empty_score() if valid and s in artifact["empty_slots"] else absent(state, "未进行有效内容评分。") for s in SLOTS}
    return {"record_type": "AGENT_L4_ARTIFACT", "data_class": config["data_class"],
            "research_status": "NOT_RESEARCH_DATA" if config["data_class"] == "MOCK_ONLY" else "UNVALIDATED_AGENT_ESTIMATE",
            "run_id": run_id, "artifact_id": artifact["artifact_id"], "task_id": artifact["task_id"],
            "project_id": artifact["project_id"], "comparison_group": artifact["comparison_group"], "method": artifact["method"], "artifact_state": state,
            "explicit_refusal": artifact.get("explicit_refusal", False), "structure_issues": artifact["issues"],
            "input_sha256": artifact["input_sha256"], "manifest_sha256": manifest["manifest_sha256"],
            "sources": [{k: s[k] for k in ("source_id", "sha256", "state")} for s in manifest["sources"] if s["project_id"] == artifact["project_id"]],
            "versions": manifest["versions"], "judge_id": config["judge"]["judge_id"], "judge_version": config["judge"]["version"],
            "model_config_id": config["model"]["config_id"], "model_config_sha256": digest(config["model"]),
            "pcs": pcs, "prc": {"score": None, "status": "NO_EVALUABLE_CONTENT" if no_content else state,
                                "reason": "尚无可评价内容或有效判断。", "modifications": [], "unresolved": [], "validation_errors": []},
            "raw_response": None, "parse_status": "RULE_ONLY" if state in {"EMPTY_OUTPUT", "REFUSED"} else "NOT_RUN",
            "validation_errors": [], "source_access": [], "request_transcript": [], "model_requests": 0,
            "runtime_receipts": [], "runtime_failure": None, "stop_run": False}


def _capture_receipt(record, client):
    receipt = getattr(client, "last_receipt", None)
    if not isinstance(receipt, dict):
        return
    number = record["model_requests"]
    if any(r["model_request"] == number for r in record["runtime_receipts"]):
        return
    record["runtime_receipts"].append({"model_request": number, **deepcopy(receipt)})


def bootstrap_sources(tools, task, sources):
    """Read a small deterministic starting context through the audited tools.

    Uses only target metadata, never method identity or expected scores. Further
    retrieval remains the judge's responsibility under the same tool limits.
    """
    history = []
    def invoke(name, arguments):
        call = {"name": name, "arguments": arguments}
        result = tools.invoke(name, arguments)
        history.append({"origin": "RUNTIME_SOURCE_BOOTSTRAP", "tool_calls": [call],
                        "tool_results": [{"call": call, "result": result}]})
        return result
    words = task["name"].split()
    queries = list(dict.fromkeys([task["name"], " ".join(words[:2]), " ".join(words[-2:])]))
    for source in sources:
        sid = source["source_id"]
        for query in queries:
            found = invoke("source_search", {"source_id": sid, "query": query[:500]})
            hits = found.get("hits", [])
            if hits:
                for hit in hits[:min(2, tools.policy["pages_max"])]:
                    invoke("source_page", {"source_id": sid, "pdf_page": hit["pdf_page"], "image": False})
                break
        else:
            invoke("source_index", {"source_id": sid})
    return history


def _judge_one(artifact, task, sources, config, manifest, run_dir, client):
    if config.get('request_organization') == 'L4_SIMULATED_EXPERT_3.0.6':
        from .expert import judge
        return judge(artifact, task, sources, config, manifest, run_dir, client)
    record = initial_record(artifact, config, manifest, run_dir.name)
    if artifact["state"] != "PRESENT":
        return seal(record)
    if any(s["state"] != "READY" for s in sources):
        record["parse_status"] = "SOURCE_UNREADABLE"
        for s in SLOTS:
            if s not in artifact["empty_slots"]:
                record["pcs"][s] = absent("SOURCE_UNREADABLE", "来源缺失、变更或不可读取，不能据此记 0。")
        record["prc"]["status"] = "SOURCE_UNREADABLE"
        return seal(record)
    if config.get("request_organization") == "L4_SLOT_EVIDENCE_2.2.9":
        from .staged import judge_staged
        return judge_staged(artifact, task, sources, config, manifest, run_dir, client)
    request = request_for(artifact, task, sources, config["source_policy"])
    try:
        tools = SourceTools(sources, artifact["project_id"], config["source_policy"],
                            run_dir / "source_backend" / (artifact["artifact_id"] + ".jsonl"), artifact["artifact_id"])
    except (OSError, ValueError, RuntimeError):
        record["parse_status"] = "SOURCE_UNREADABLE"
        record["validation_errors"] = ["SOURCE_ACCESS_INITIALIZATION_FAILED"]
        return seal(record)
    history = bootstrap_sources(tools, task, sources) if config.get("source_bootstrap") is True else []
    validation_retries = 0
    max_validation_retries = config.get("validation_feedback_max", int(config.get("validation_feedback_once") is True))
    if type(max_validation_retries) is not int or not 0 <= max_validation_retries <= 3:
        raise L4Error("Validation feedback must be bounded to 0..3 corrections")
    seed_path = config.get("validation_seed_records", {}).get(artifact["artifact_id"])
    if seed_path:
        prior = read_json(local_path(seed_path))
        signature = prior.get("record_sha256")
        if (signature != digest({k: v for k, v in prior.items() if k != "record_sha256"}) or
                prior.get("artifact_id") != artifact["artifact_id"] or
                prior.get("input_sha256") != artifact["input_sha256"] or
                prior.get("model_config_sha256") != digest(config["model"]) or
                prior.get("source_access") != tools.entries or not prior.get("request_transcript")):
            raise L4Error("Validation seed identity/source/model mismatch")
        checked = evaluate_response(prior["raw_response"], artifact, tools.entries)
        if checked["parse_status"] != "INVALID_RESPONSE":
            raise L4Error("Only a preserved contract-invalid result can seed correction; scores cannot be redrawn")
        last = prior["request_transcript"][-1]
        if last["request"] != request:
            raise L4Error("Validation seed request changed")
        history = deepcopy(last["history"]) + [{"validation_feedback": {
            "errors": checked["validation_errors"], "rejected_response": prior["raw_response"]}}]
        validation_retries = sum("validation_feedback" in h for h in history)
        if validation_retries > max_validation_retries:
            raise L4Error("Cumulative validation correction limit exhausted")
        record["validation_seed"] = {"path": seed_path, "record_sha256": signature,
                                     "prior_responses_replayed_without_calls": prior["model_requests"]}
    for _ in range(config["source_policy"]["request_rounds"]):
        try:
            record["model_requests"] += 1
            raw = client.respond(request, history)
            _capture_receipt(record, client)
            if not isinstance(raw, str):
                raw = ""
            record["raw_response"] = raw
            record["request_transcript"].append({"request": request, "history": list(history), "raw_response": raw})
            try:
                parsed = parse_response(raw)
            except ValueError:
                parsed = {}
            if "tool_calls" not in parsed:
                record.update(evaluate_response(raw, artifact, tools.entries))
                if (record["parse_status"] == "INVALID_RESPONSE" and
                        validation_retries < max_validation_retries):
                    validation_retries += 1
                    history.append({"validation_feedback": {
                        "errors": deepcopy(record["validation_errors"]), "rejected_response": raw}})
                    continue
                break
            calls = parsed["tool_calls"]
            if set(parsed) != {"tool_calls"} or not isinstance(calls, list) or not 1 <= len(calls) <= config["source_policy"]["tools_per_round"]:
                record["parse_status"] = "INVALID_TOOL_REQUEST"
                break
            results = []
            for call in calls:
                if not isinstance(call, dict) or set(call) != {"name", "arguments"}:
                    raise L4Error("Invalid tool envelope")
                results.append({"call": call, "result": tools.invoke(call["name"], call["arguments"])})
            history.append({"tool_calls": calls, "tool_results": results})
        except RuntimeDeferred:
            # No request was dispatched. Paid earlier rounds remain in the runtime
            # cache and will be replayed on resume; do not seal a failed score.
            save_new(run_dir / "scheduling_events" / (uuid4().hex + ".json"), {
                "artifact_id": artifact["artifact_id"], "status": "DEFERRED_BEFORE_DISPATCH",
                "request_round": record["model_requests"], "dispatched": False,
                "prior_runtime_receipts": record["runtime_receipts"],
            })
            raise
        except Exception as exc:
            # The shared runtime has already persisted any response and accounting.
            _capture_receipt(record, client)
            receipt = getattr(client, "last_receipt", None)
            if isinstance(receipt, dict) and receipt.get("body") is not None:
                body = receipt["body"]
                record["raw_response"] = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            record["parse_status"] = "CLIENT_OR_TOOL_ERROR"
            record["validation_errors"] = [type(exc).__name__]
            record["runtime_failure"] = type(exc).__name__
            record["stop_run"] = isinstance(exc, RuntimeBlocked) and not isinstance(exc, RuntimeResponseError)
            break
    else:
        record["parse_status"] = "REQUEST_LIMIT"
    # A later failed correction/tool round invalidates partial scores from an
    # earlier response. Preserve the transcript, but never seal stale scores
    # under a no-response failure status.
    if record["parse_status"] not in {"VALID", "INVALID_RESPONSE", "PARSE_FAILED"}:
        status = record["parse_status"]
        record["pcs"] = {s: empty_score() if s in artifact["empty_slots"] else
                         absent(status, "最后一次请求未取得有效判断。") for s in SLOTS}
        record["prc"] = {"score": None, "status": status,
                         "reason": "最后一次请求未取得有效判断。", "modifications": [],
                         "unresolved": [], "validation_errors": []}
    record["source_access"] = tools.entries
    if tools.integrity_failure:
        record["parse_status"] = "SOURCE_CHANGED"
        for s in SLOTS:
            if s not in artifact["empty_slots"]:
                record["pcs"][s] = absent("SOURCE_CHANGED", "来源在评价期间变化，判断无效。")
        record["prc"].update(score=None, status="SOURCE_CHANGED")
    return seal(record)


def evaluate_run(run_dir: Path, client: JudgeClient | None = None, *, mock=False, explicit_live=False, resume=False) -> list[dict]:
    run_dir = require_run(run_dir)
    config, manifest = assert_current(run_dir)
    authorize(config, mock=mock, explicit_live=explicit_live)
    if client is None:
        raise L4Error("No injected client; no model was contacted")
    combined = run_dir / "records.json"
    existing = (run_dir / "records").exists() and any((run_dir / "records").iterdir())
    if (combined.exists() or existing) and not resume:
        raise L4Error("Existing records are immutable; use --resume to retain them")
    if combined.exists():
        return validate_run(run_dir)[2]
    if mock:
        fixture_root = (run_dir / "fixtures").resolve()
        # A MOCK_ONLY label alone cannot make real output paths eligible for mock scores.
        for entry in config["input_bindings"] + config["sources"]:
            if not local_path(entry["path"]).is_relative_to(fixture_root):
                raise L4Error("Mock scoring only accepts this run's constructed fixtures")
        marker = read_json(fixture_root / "fixture_manifest.json")
        if marker.get("data_class") != "FIXTURE" or marker.get("research_status") != "NOT_RESEARCH_DATA":
            raise L4Error("Missing synthetic fixture declaration")
    targets = {t["task_id"]: t for t in config["targets"]}
    records = []
    expected_files = {a["artifact_id"] + ".json" for a in manifest["artifacts"]}
    if existing and any(p.name not in expected_files for p in (run_dir / "records").iterdir()):
        raise L4Error("Unexpected saved record; resume stopped")
    for artifact in manifest["artifacts"]:
        record_path = run_dir / "records" / (artifact["artifact_id"] + ".json")
        if record_path.exists():
            # Completed, unresolved, and failed records all remain immutable.
            record = read_json(record_path)
            if record.get("run_id") != run_dir.name:
                raise L4Error("Saved record belongs to another run")
            validate_record(record, artifact, manifest, config)
            records.append(record)
            if config.get("stop_on_technical_failure") is True and record["processing_status"] == "TECHNICAL_FAILURE":
                raise L4Error("TECHNICAL_FAILURE preserved; inspect saved record " + artifact["artifact_id"])
            continue
        sources = [s for s in manifest["sources"] if s["project_id"] == artifact["project_id"]]
        record = _judge_one(artifact, targets[artifact["task_id"]], sources, config, manifest, run_dir, client)
        validate_record(record, artifact, manifest, config)
        save_new(record_path, record)
        records.append(record)
        if config.get("stop_on_technical_failure") is True and record["processing_status"] == "TECHNICAL_FAILURE":
            raise L4Error("TECHNICAL_FAILURE preserved; inspect saved record " + artifact["artifact_id"])
        if record.get("stop_run"):
            raise L4Error("RUNTIME_BLOCKED: saved failed artifact " + artifact["artifact_id"] + "; campaign stopped")
    assert_current(run_dir)
    save_new(run_dir / "records.json", records)
    return records


def validate_run(run_dir: Path) -> tuple[dict, dict, list]:
    config, manifest = assert_current(require_run(run_dir))
    records = read_json(run_dir / "records.json")
    expected = {a["artifact_id"]: a for a in manifest["artifacts"]}
    if not isinstance(records, list) or len(records) != len(expected) or {r["artifact_id"] for r in records} != set(expected):
        raise L4Error("Missing or duplicate planned record; failed targets must remain visible")
    for record in records:
        if record["run_id"] != run_dir.name:
            raise L4Error("Run ID mismatch")
        validate_record(record, expected[record["artifact_id"]], manifest, config)
        if read_json(run_dir / "records" / (record["artifact_id"] + ".json")) != record:
            raise L4Error("Individual and combined records differ")
    return config, manifest, records
