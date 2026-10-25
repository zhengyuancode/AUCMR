"""Durable receipts for paid requests and local tool results, without rerunning judgments."""
from __future__ import annotations

import copy

from ..common import Blocked, EvaluationError, append_jsonl, digest, now, read_json, read_jsonl, write_json


def append_unique(path, item, key="record_id"):
    if not any(row.get(key) == item[key] for row in read_jsonl(path)):
        append_jsonl(path, item)


class TaskRecovery:
    def __init__(self, run_dir, task_id, fingerprint, budget, transport):
        self.root = run_dir / "task_progress" / digest(task_id)
        self.budget, self.transport = budget, transport
        path = self.root / "input.json"
        if path.exists() and read_json(path)["input_hash"] != fingerprint:
            raise EvaluationError("Interrupted task inputs changed; resume refused")
        if not path.exists():
            write_json(path, {"input_hash": fingerprint})

    def request(self, request_id, binding, messages, task, attempt, upper):
        path = self.root / f"request-{attempt}.json"
        payload_hash = digest([request_id, binding, messages])
        if path.exists():
            saved = read_json(path)
            if saved["payload_hash"] != payload_hash:
                raise EvaluationError("Cached request differs from resumed request")
        else:
            # A reservation without a receipt might already have been billed.
            # Budget.reserve refuses to resend it; reconcile that charge manually.
            self.budget.reserve(request_id, binding["provider"], upper)
            saved = {"payload_hash": payload_hash, "started": now(), "result": None,
                     "error": None, "status": "OK"}
            try:
                saved["result"] = self.transport.request(binding, messages, task, attempt)
            except Exception as exc:
                saved.update(error=type(exc).__name__, status="TRANSPORT_FAILED")
            # Persist before settlement so a crash between the two can recover.
            write_json(path, saved)
        with self.budget.connect() as db:
            row = db.execute("SELECT state FROM reservations WHERE id=?", (request_id,)).fetchone()
        if not row:
            raise Blocked("Saved response has no campaign reservation")
        if row[0] == "RESERVED":
            result = saved["result"]
            cost = result.get("cost_cny") if result else (0 if self.transport.mode == "mock" else None)
            try:
                self.budget.settle(request_id, cost,
                                   force_unknown=bool(result and result.get("request_budget_violation")))
            except Blocked:
                saved["status"] = "UNKNOWN"
        elif row[0] != "SETTLED":
            saved["status"] = "UNKNOWN"
        return saved["result"], saved["error"], saved["status"], saved["started"]

    def tool(self, attempt, index, tool, source):
        path = self.root / f"tool-{attempt}-{index}.json"
        if path.exists():
            saved = read_json(path)
            if saved["tool_hash"] != digest(tool):
                raise EvaluationError("Cached local tool differs from resumed tool")
            known = {entry["access_id"] for entry in source.entries}
            for entry in saved["entries"]:
                if entry["access_id"] in known:
                    continue
                source.entries.append(entry)
                known.add(entry["access_id"])
                if entry.get("operation") == "page":
                    key = (entry["source_id"], entry["pdf_page"])
                    source.read_pages.add(key)
                if entry.get("operation") == "search":
                    source.searches += 1
            source.crossrefs.update(map(tuple, saved.get("crossrefs", [])))
            return copy.deepcopy(saved["answer"])
        before = len(source.entries)
        answer = source.invoke(tool["name"], tool["arguments"])
        write_json(path, {"tool_hash": digest(tool), "answer": answer,
                          "entries": source.entries[before:], "crossrefs": sorted(source.crossrefs)})
        return answer
