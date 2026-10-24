"""Role task execution with independent sessions, bounded tools/retries and immutable attempts."""
from __future__ import annotations

from eval.implementation import implementation_manifest

import json
from pathlib import Path

from jsonschema import ValidationError

from ..common import Blocked, EvaluationError, append_jsonl, canonical, digest, now, read_json, read_jsonl, write_json
from ..schemas import record
from .roles import prompt, role_binding
from .recovery import TaskRecovery, append_unique


class Executor:
    def __init__(self, run_dir: Path, config: dict, transport, budget):
        self.run_dir, self.config, self.transport, self.budget = run_dir, config, transport, budget
        if transport.mode == "mock" and "fixtures" not in run_dir.parts:
            raise EvaluationError("Mock executor must use fixture path")

    def execute(self, *, task_id: str, role: str, view: str, kinds: list[str], materials: dict,
                source, validate, target_id=None, artifact_id=None, version=None, resume=True) -> list[dict]:
        binding = self.config["models"][role_binding(role)]
        system = prompt(role, kinds)
        original_role = materials.get("original_role")
        if original_role:
            system += "\n" + prompt(original_role, kinds)
        envelope = {"prompt": system, "materials": materials, "model": binding, "scenario_version": version,
                    "limits": self.config["judging"], "transport": self.transport.mode,
                    "implementation": implementation_manifest("l3")}
        fingerprint = digest(envelope)
        cached_path = self.run_dir / "tasks" / f"{digest(task_id)}.json"
        if cached_path.exists():
            cached = read_json(cached_path)
            if cached["input_hash"] != fingerprint:
                raise EvaluationError("Task inputs/prompt/model/reference changed; resume refused")
            if resume and cached["status"] == "OK":
                # Resume C blind/final with the same cumulative local page/query allowance.
                prior_session = digest([task_id, role, fingerprint])
                known = {e["access_id"] for e in source.entries}
                for entry in read_jsonl(source.log_path):
                    if entry.get("session_id") == prior_session and entry["access_id"] not in known:
                        source.entries.append(entry)
                        if entry.get("operation") == "page":
                            source.read_pages.add((entry["source_id"], entry["pdf_page"]))
                        if entry.get("operation") == "search":
                            source.searches += 1
                append_jsonl(self.run_dir / "resume_ledger.jsonl", {"task_id": task_id, "input_hash": fingerprint,
                    "target_id": target_id, "scenario_version": version, "status": "SUCCESSFUL_TASK_REUSED", "time": now()})
                return cached["records"]
            if cached["status"] != "OK":
                raise EvaluationError("Bounded attempts exhausted previously; preserved as technical failure")
        recovery = TaskRecovery(self.run_dir, task_id, fingerprint, self.budget, self.transport)
        session = digest([task_id, role, fingerprint])
        source.session_id = session
        task = {"task_id": task_id, "session_id": session, "role": role, "materials": materials}
        append_jsonl(self.run_dir / "task_inputs.jsonl", {"task_id": task_id, "session_id": session,
            "input_hash": fingerprint, "prompt_hash": digest(system), "envelope": envelope, "time": now()})
        messages = [{"role": "system", "content": system}, {"role": "user", "content": canonical(materials)}]
        limit = self.config["judging"]
        retries, request_count = 0, 0
        while request_count < limit["max_requests_per_task"]:
            request_count += 1
            # Conservative UTF-8 byte bound (not a provider token estimate); fail instead of truncate.
            text_bytes = sum(len(canonical(m["content"]).encode()) if isinstance(m["content"], str) else
                sum(len(part.get("text", "").encode()) for part in m["content"]) for m in messages)
            image_count = sum(sum(p.get("type") == "image_url" for p in m["content"]) for m in messages if isinstance(m["content"], list))
            image_bound = binding.get("capabilities", {}).get("image_token_upper_bound", 0 if self.transport.mode == "mock" else None)
            if image_count and image_bound is None:
                raise Blocked("Image input bound is not verified")
            if text_bytes + image_count * (image_bound or 0) > limit["max_input_tokens_per_request"]:
                raise Blocked("Complete context exceeds configured bound; no silent truncation")
            request_id = digest(["L3", str(self.run_dir.resolve()), task_id, fingerprint, request_count])
            upper = {k: 0 for k in ("input", "output", "reasoning", "images", "other")} if self.transport.mode == "mock" else binding.get("per_request_upper_bound")
            result, error, status, start = recovery.request(request_id, binding, messages, task, request_count, upper)
            response_body = result.get("body") if result else None
            try:
                if status != "OK":
                    raise EvaluationError(status)
                if result.get("finish_reason") == "length":
                    status = "TRUNCATED"
                    raise EvaluationError("Truncated output")
                if result.get("finish_reason") != "stop" or not isinstance(result.get("response_model"), str) or not result["response_model"].strip():
                    raise EvaluationError("Incomplete response or missing returned model")
                response = json.loads(response_body) if isinstance(response_body, str) else response_body
                if not isinstance(response, dict) or set(response) not in ({"tool_calls"}, {"records"}):
                    raise EvaluationError("Expected tools or records envelope")
                if "records" in response:
                    validate(response["records"], source.entries)
                else:
                    if not response["tool_calls"]:
                        raise EvaluationError("Empty tool request")
                    for tool in response["tool_calls"]:
                        if set(tool) != {"name", "arguments"}:
                            raise EvaluationError("Invalid tool fields")
            except (EvaluationError, ValidationError, ValueError, TypeError, KeyError) as exc:
                if status == "OK":
                    status = "FORMAT_FAILED"
                error = type(exc).__name__ + ": " + str(exc)[:1000]
            usage = (result or {}).get("usage", {})
            payload = {"provider": "MOCK" if self.transport.mode == "mock" else binding["provider"],
                "requested_model": binding["api_model_id"], "response_model": (result or {}).get("response_model"),
                "provider_binding_hash": digest(binding), "request_id": (result or {}).get("request_id"),
                "task_id": task_id, "attempt": request_count, "request_status": status,
                "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"),
                "reasoning_tokens": usage.get("reasoning_tokens"), "cost_cny": (result or {}).get("cost_cny"),
                "reserved_cny": sum(upper.values()), "search_calls": source.searches, "page_reads": len(source.read_pages)}
            call = record("call", payload, run_id=self.run_dir.name, target_id=target_id, artifact_id=artifact_id,
                          role=role, view="SYSTEM", input_hash=fingerprint, version=version)
            append_unique(self.run_dir / "call_ledger.jsonl", call)
            append_unique(self.run_dir / "request_trace.jsonl", {"request_record_id": call["record_id"], "session_id": session,
                "started": start, "finished": now(), "response": result, "error": error, "messages_hash": digest(messages)}, key="request_record_id")
            if status == "UNKNOWN":
                raise Blocked("Unknown charge; request persisted, manual reconciliation required")
            if status != "OK":
                if retries >= limit["format_or_transport_retries"]:
                    write_json(cached_path, {"input_hash": fingerprint, "status": status, "records": []})
                    raise EvaluationError(f"Task failed after bounded retry: {task_id}")
                retries += 1
                messages.append({"role": "user", "content": "技术或格式失败，请按原任务和Schema重试。不改变任务、ID、标签规则。"})
                continue
            messages.append({"role": "assistant", "content": canonical(response)})
            if "tool_calls" in response:
                for index, tool in enumerate(response["tool_calls"]):
                    answer = recovery.tool(request_count, index, tool, source)
                    if "image_base64" in answer:
                        encoded = answer.pop("image_base64")
                        messages.append({"role": "user", "content": [
                            {"type": "text", "text": canonical(answer)},
                            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}}]})
                    else:
                        messages.append({"role": "user", "content": canonical(answer)})
                continue
            records = [record(r["kind"], r["payload"], run_id=self.run_dir.name, target_id=target_id,
                       artifact_id=artifact_id, role=role, view=view, input_hash=fingerprint, version=version)
                       for r in response["records"]]
            for r in records:
                append_unique(self.run_dir / "records.jsonl", r)
            write_json(cached_path, {"input_hash": fingerprint, "status": "OK", "records": records})
            return records
        raise Blocked("Logical task request limit exhausted")
