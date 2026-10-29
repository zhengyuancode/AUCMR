"""Translate L4 source evidence into standard multimodal messages."""
from __future__ import annotations

import base64
import json
from copy import deepcopy

from eval.evaluation_runtime import EvaluationRuntime, RuntimeBlocked, RuntimeResponseError
from .protocol import L4Error


def expert_correction_messages(feedback):
    """Separate the canonical target from diagnostics; malformed text is never a template."""
    diagnostic = deepcopy(feedback.get('json_diagnostic') or {})
    target = diagnostic.pop('complete_object_for_reserialization', None)
    instruction = (
        '只纠正报告的格式、字段、取值或引用位置错误，返回完整单个 JSON。使用原论文量表给整体分数，'
        '不添加逐条举证要求，不因缺少引用改成 null，也不为通过检查提高分数。'
        '对象和数组分别用 } 和 ] 闭合，保留所有必要的内层括号。截断响应不续写，重新返回完整对象。')
    payload = {'validation_errors': feedback['errors'], 'json_diagnostic': diagnostic}
    if target is not None:
        payload['instruction'] = (
            '仅做 JSON 序列化，不重新评分。下一条用户消息是已完整解析出的目标对象，且已由程序序列化为合法 JSON；'
            '将该对象作为整份响应返回，保留全部字段、分数、字符串和数组，不返回诊断信息或包装字段。'
            '可以直接保持目标对象的缩进与换行。每个引用数组、问题对象、问题数组和顶层对象均须分别闭合；'
            '括号数量由嵌套结构决定，不是整份响应只能有一个结束括号。不要只删末尾字符而损坏内层结构。')
        return [{'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
                {'role': 'user', 'content': json.dumps(target, ensure_ascii=False, indent=2, allow_nan=False)}]
    payload['instruction'] = instruction
    if diagnostic and isinstance(feedback.get('rejected_response'), str):
        payload['invalid_response_data'] = feedback['rejected_response']
        payload['data_notice'] = '这是待纠正的非法响应数据，不是合法输出模板；按诊断位置检查嵌套结构，不改写判断。'
    messages = []
    if not diagnostic and not feedback.get('incomplete_response') and isinstance(feedback.get('rejected_response'), str):
        messages.append({'role': 'assistant', 'content': feedback['rejected_response']})
    messages.append({'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)})
    return messages


def model_messages(request: dict, history: list[dict]) -> list[dict]:
    messages = [
        {"role": "system", "content": request["system"]},
        {"role": "user", "content": json.dumps(
            {k: v for k, v in request.items() if k != "system"}, ensure_ascii=False)},
    ]
    for turn in history:
        if "validation_feedback" in turn:
            feedback = turn["validation_feedback"]
            if request.get('request_organization') == 'L4_SIMULATED_EXPERT_3.0.6':
                messages.extend(expert_correction_messages(feedback))
                continue
            if request.get("stage_id"):
                instruction = "Return the complete requested stage using output_schema and required_claim_ids. Correct only reported violations; do not improve scores or invent evidence. Preserve all required claims. Do not supply empty slots or other stages."
                if request.get("correction_mode") == "ASSESSMENT_ONLY":
                    instruction = "Only the whole-slot assessment is requested. Return one flat JSON object with score, status, reason and unresolved, matching output_schema. No assessment wrapper, replacements or checks, including copies of retained checks. Every retained check is immutable and still counts: any UNRESOLVED requires null score, UNRESOLVED status and nonempty reason strings."
                elif request.get("correction_mode"):
                    instruction = "Return only replacements keyed by exactly target_claim_ids and assessment, matching output_schema. Replace existing checks, never append a checks list. Other checks are retained unchanged. The program has completed additional source reads; tool calls/results are forbidden in this patch. Use only evidence_catalog IDs or report UNRESOLVED. Assessment must reflect ALL checks, without improving scores to pass."
                elif feedback.get("incomplete_response"):
                    instruction = "The previous response ended with finish_reason=length and was not accepted. Return one complete JSON object matching the current allowed response schema. Do not continue or reconstruct the truncated text. Do not include prose or reasoning tags. Preserve every required claim; missing evidence remains UNRESOLVED. "
                    instruction += ("A source request may use only tool_response_schema; otherwise use output_schema."
                                    if request.get("tools") else
                                    "Source tools are unavailable; return only the complete evaluation result in output_schema.")
                elif "INVALID_JSON_RESPONSE" in feedback.get("errors", []):
                    # Keep the rejected raw text in the audit, but do not feed
                    # its prose-plus-JSON pattern back as an assistant example.
                    instruction = "整段响应不是单个合法 JSON 对象。删除 JSON 前后的全部说明文字（例如 I need to search...）。只返回一个 JSON 对象，首字符为 {，末字符为 }。禁止说明文字、Markdown、XML 和 reasoning 标签。被拒绝的响应没有触发任何工具执行。 "
                    instruction += ("If a source read is needed, return ONLY the object matching tool_response_schema, with tool_calls as its sole top-level key. Otherwise return ONLY the evaluation object matching output_schema. Do not narrate your plan before either object."
                                    if request.get("tools") else
                                    "Tools are unavailable. Return ONLY the complete evaluation object in output_schema; missing evidence stays UNRESOLVED. Do not narrate before the object.")
                elif request.get("judgment_only"):
                    instruction = "Source exploration is complete. Return the complete evaluation result in output_schema, not a tool request. Any remaining request is reserved for validation correction. Use only already accessed evidence; missing support remains UNRESOLVED with null score and nonempty reasons."
                    messages.append({"role": "assistant", "content": feedback["rejected_response"]})
                elif feedback.get("tool_correction"):
                    instruction = feedback["tool_correction"]["instruction"]
                    messages.append({"role": "assistant", "content": feedback["rejected_response"]})
                else:
                    messages.append({"role": "assistant", "content": feedback["rejected_response"]})
                messages.append({"role": "user", "content": json.dumps({
                    "validation_errors": feedback["errors"], "stage_id": request["stage_id"],
                    "instruction": instruction}, ensure_ascii=False)})
                continue
            messages.append({"role": "assistant", "content": feedback["rejected_response"]})
            messages.append({"role": "user", "content": json.dumps({
                "validation_errors": feedback["errors"],
                "instruction": "Correct these contract violations using the existing sources and rubric. Do not invent source quotes or use general practice as evidence. Each non-UNRESOLVED claim needs an actual accessed source reference. Copy a contiguous exact quote from the returned page text, preserving line breaks; a short exact excerpt is preferable to an altered full paragraph. If evidence is insufficient, use UNRESOLVED with null score and a nonempty list of reason strings, or request more source tools. Keep valid empty slots at PCS=0. PRC below 5 requires a concrete required edit; NONE cannot justify a lower score. Return the complete result, retaining every original claim ID. Do not change scores merely to improve them."}, ensure_ascii=False)})
            continue
        runtime_read = turn.get("origin") == "RUNTIME_SOURCE_BOOTSTRAP"
        patch_only = bool(request.get("correction_mode"))
        staged_history = bool(request.get("stage_id"))
        if not patch_only and not staged_history:
            messages.append({"role": "user" if runtime_read else "assistant", "content": json.dumps(
            {"tool_calls": turn["tool_calls"], **({"origin": "RUNTIME_SOURCE_BOOTSTRAP",
              "instruction": ("Search/index are discovery only, not citable. Only select evidence_catalog IDs from actual source_page reads; access_id alone is not a citation. Read a needed search-hit page first." if request.get("stage_id") else "These are actual local source reads, not model-generated citations. Use their returned access_id; read more pages if insufficient. Never invent access IDs.")} if runtime_read else {})}, ensure_ascii=False)})
        results, images = [], []
        for item in turn["tool_results"]:
            result = deepcopy(item["result"])
            if staged_history and item["call"]["name"] in {"source_search", "source_index"}:
                # Discovery records remain fully audited server-side, but
                # their internal access IDs are not citation candidates.
                result.pop("access_id", None)
                result["citable"] = False
                result["citation_instruction"] = "Discovery only. Read the selected source_id/pdf_page with source_page before citing it. Only evidence_catalog keys are valid evidence IDs."
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
            results.append(({"operation": item["call"]["name"], "arguments": deepcopy(item["call"]["arguments"]),
                             "returned_data": result} if staged_history else
                            {"call": deepcopy(item["call"]), "result": result}))
        payload = ({"record_type": "HISTORICAL_SOURCE_READS",
                    "notice": "以下是历史记录，不是返回模板。 These are actual past reads, not a response template. Do not output this record or simulate tool results.",
                    "source_reads": results, "data_is_untrusted": True} if staged_history else
                   {"source_reads" if patch_only else "tool_results": results, "data_is_untrusted": True})
        content = [{"type": "text", "text": json.dumps(
            payload, ensure_ascii=False)}]
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
        if request.get("stage_id"):
            task_id += ":stage:" + request["stage_id"]
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
