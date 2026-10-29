"""One strict tool envelope for both model instructions and dispatch validation."""
from copy import deepcopy
import json

from .contract import obj, shape_errors


def tool_schema(sources, policy):
    variants = []
    for source in sources:
        sid = {"const": source["source_id"]}
        arguments = {
            "source_index": obj({"source_id": sid}),
            "source_search": obj({"source_id": sid, "query": {
                "type": "string", "minLength": 1, "maxLength": 500, "pattern": r"\S"}}),
            "source_page": obj({"source_id": sid,
                "pdf_page": {"type": "integer", "minimum": 1, "maximum": source["pages"]},
                "image": {"type": "boolean"}, "dpi": {"enum": [144, 216, 288]},
                "bbox": {"anyOf": [{"type": "null"}, {"type": "array", "minItems": 4,
                    "maxItems": 4, "items": {"type": "number"}}]}}),
        }
        arguments["source_page"]["required"] = ["source_id", "pdf_page"]
        for name, args in arguments.items():
            variants.append(obj({"name": {"const": name}, "arguments": args}))
    return obj({"tool_calls": {"type": "array", "minItems": 1,
        "maxItems": policy["tools_per_round"], "items": {"oneOf": variants} if variants else False}})


class ToolContractError(ValueError):
    def __init__(self, details):
        self.details = details
        super().__init__(json.dumps(details, ensure_ascii=False))


def validate_tool_request(value, sources, policy):
    contract = tool_schema(sources, policy)
    errors = shape_errors(value, contract)
    if not errors:
        # Do not normalize, strip fields, or replace model arguments.
        return deepcopy(value["tool_calls"])
    extra = sorted(set(value) - {"tool_calls"}) if isinstance(value, dict) else []
    calls_valid = isinstance(value, dict) and not shape_errors(
        {"tool_calls": value.get("tool_calls")}, contract)
    instruction = "Return one object matching tool_response_schema; top-level key must be tool_calls only. Do not return origin, instruction, tool_results, or an evaluation result in the same object."
    if extra and calls_valid:
        instruction = ("检测到多余顶层字段：" + "、".join(extra) +
            "。请删除这些字段，保留原 tool_calls。读页参数无需修改。 "
            "Remove only the listed extra top-level fields; keep the original tool_calls and arguments unchanged.")
    raise ToolContractError({"code": "INVALID_TOOL_ENVELOPE", "extra_top_level_fields": extra,
                             "tool_calls_valid": calls_valid, "schema_errors": errors,
                             "instruction": instruction})
