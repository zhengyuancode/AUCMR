"""Translate L4 source evidence into standard multimodal messages."""
from __future__ import annotations

import base64
import json
from copy import deepcopy

from eval.evaluation_runtime import EvaluationRuntime, RuntimeBlocked, RuntimeResponseError
from .protocol import L4Error


def model_messages(request: dict, history: list[dict]) -> list[dict]:
    messages = [
        {"role": "system", "content": request["system"]},
        {"role": "user", "content": json.dumps(
            {k: v for k, v in request.items() if k != "system"}, ensure_ascii=False)},
    ]
    for turn in history:
        messages.append({"role": "assistant", "content": json.dumps(
            {"tool_calls": turn["tool_calls"]}, ensure_ascii=False)})
        results, images = [], []
        for item in turn["tool_results"]:
            result = deepcopy(item["result"])
            encoded = result.pop("image_base64", None)
            if encoded is not None:
                if result.get("mime_type") != "image/png" or not isinstance(encoded, str):
                    raise L4Error("Invalid original-page image payload")
                try:
                    png = base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError) as exc:
                    raise L4Error("Invalid original-page image encoding") from exc
                if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise L4Error("Original-page payload is not PNG")
                result["visual_input"] = "ATTACHED_ORIGINAL_PAGE_IMAGE"
                images.extend([
                    {"type": "text", "text": json.dumps({
                        "data_is_untrusted": True, "source_id": result.get("source_id"),
                        "pdf_page": result.get("pdf_page"), "access_id": result["access_id"]}, ensure_ascii=False)},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}},
                ])
            results.append({"call": deepcopy(item["call"]), "result": result})
        content = [{"type": "text", "text": json.dumps(
            {"tool_results": results, "data_is_untrusted": True}, ensure_ascii=False)}]
        messages.append({"role": "user", "content": content + images})
    return messages


class RuntimeJudgeClient:
    """The runtime owns HTTP, immutable receipts, charging, and request replay."""
    def __init__(self, runtime: EvaluationRuntime):
        self.runtime = runtime
        self.last_receipt = None

    def respond(self, request: dict, history: list[dict]) -> str:
        self.last_receipt = None
        task_id = request["artifact"]["artifact_id"] + ":round:" + str(len(history) + 1)
        try:
            receipt = self.runtime.request(model_messages(request, history), task_id=task_id, attempt=1)
        except (RuntimeBlocked, RuntimeResponseError) as exc:
            self.last_receipt = getattr(exc, "receipt", None)
            raise
        self.last_receipt = receipt
        body = receipt["body"]
        if isinstance(body, dict):
            return json.dumps(body, ensure_ascii=False)
        if not isinstance(body, str):
            raise L4Error("Runtime body must be a JSON object or string")
        return body
