"""Check the complete request before reservation; never truncate source evidence.

UTF-8 bytes are a configurable conservative bound, NOT measured provider tokens.
The binding must document/verify tokenizer expansion, chat overhead and image
limits. Unknown bounds block live dispatch rather than silently becoming zero.
"""
from __future__ import annotations

import base64
import math
import struct
from .agent_evaluation.common import canonical
from .evaluation_billing import amount, count, validate_billing


def positive(value, name):
    if count(value) is None or value <= 0:
        raise ValueError("REQUEST_BOUND_NOT_BOUND: " + name)
    return value


def validate_limits(binding):
    limits = binding.get("request_limits", {})
    if not isinstance(limits, dict) or limits.get("verified") is not True or not limits.get("evidence"):
        raise ValueError("REQUEST_LIMITS_NOT_VERIFIED")
    for key in ("max_input_tokens", "context_tokens", "max_request_bytes",
                "message_overhead_tokens", "request_overhead_tokens"):
        positive(limits.get(key), key)
    if amount(limits.get("text_tokens_per_utf8_byte")) < 1:
        raise ValueError("TEXT_TOKEN_BOUND_MUST_BE_AT_LEAST_ONE_PER_BYTE")
    return limits


def check_request(binding, messages, *, input_limit=None):
    tiers = validate_billing(binding)
    limits = validate_limits(binding)
    parameters = binding["parameters"]
    if not isinstance(parameters, dict) or set(parameters) & {"model", "messages", "stream", "api_key", "base_url", "headers"}:
        raise ValueError("INVALID_MODEL_PARAMETERS")
    if "max_tokens" in parameters and "max_completion_tokens" in parameters:
        raise ValueError("AMBIGUOUS_OUTPUT_LIMIT")
    output = positive(parameters.get("max_completion_tokens", parameters.get("max_tokens")), "output")
    if not isinstance(messages, list) or not messages:
        raise ValueError("NONEMPTY_MESSAGES_REQUIRED")
    text_bytes, images, image_tokens = 0, 0, 0
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "content"} or message["role"] not in {"system", "user", "assistant", "tool"}:
            raise ValueError("UNSUPPORTED_MESSAGE_FIELDS")
        content = message["content"]
        parts = [{"type": "text", "text": content}] if isinstance(content, str) else content
        if not isinstance(parts, list) or not parts:
            raise ValueError("INVALID_CONTENT")
        for part in parts:
            if not isinstance(part, dict):
                raise ValueError("INVALID_CONTENT_PART")
            if part.get("type") == "text" and set(part) == {"type", "text"} and isinstance(part["text"], str):
                text_bytes += len(part["text"].encode("utf-8"))
                continue
            if set(part) != {"type", "image_url"} or part["type"] != "image_url" or not isinstance(part["image_url"], dict):
                raise ValueError("UNSUPPORTED_CONTENT_PART")
            image = part["image_url"]
            if set(image) - {"url", "detail"}:
                raise ValueError("UNBOUND_IMAGE_OPTIONS")
            if binding.get("capabilities", {}).get("images") is not True:
                raise ValueError("IMAGE_CAPABILITY_NOT_BOUND")
            url = image.get("url", "")
            # Project source tools produce PNG. Other codecs need their own
            # dimension validation; accepting them here would bypass pixel caps.
            if not isinstance(url, str) or not url.startswith("data:image/png;base64,"):
                raise ValueError("REQUEST_IMAGE_MUST_BE_INLINE_PNG")
            encoded = url.split(",", 1)[1]
            if len(encoded) > positive(limits.get("max_image_base64_bytes"), "max_image_base64_bytes"):
                raise ValueError("IMAGE_BASE64_LIMIT_EXCEEDED")
            data = base64.b64decode(encoded, validate=True)
            if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
                raise ValueError("INVALID_PNG_HEADER")
            width, height = struct.unpack(">II", data[16:24])
            if not width or not height or width * height > positive(limits.get("max_image_pixels"), "max_image_pixels"):
                raise ValueError("IMAGE_PIXEL_LIMIT_EXCEEDED")
            if len(data) > positive(limits.get("max_image_bytes"), "max_image_bytes"):
                raise ValueError("IMAGE_BYTE_LIMIT_EXCEEDED")
            detail = image.get("detail", "auto")
            bound = limits.get("image_tokens_by_detail", {}).get(detail)
            image_tokens += positive(bound, "image_tokens_by_detail." + str(detail))
            images += 1
    if images and images > positive(limits.get("max_images"), "max_images"):
        raise ValueError("IMAGE_COUNT_LIMIT_EXCEEDED")
    text_tokens = math.ceil(text_bytes * amount(limits["text_tokens_per_utf8_byte"]))
    overhead = len(messages) * limits["message_overhead_tokens"] + limits["request_overhead_tokens"]
    total = text_tokens + overhead + image_tokens
    cap = limits["max_input_tokens"]
    if input_limit is not None:
        cap = min(cap, positive(input_limit, "protocol_input_limit"))
    if total > cap or total + output > limits["context_tokens"]:
        raise ValueError("COMPLETE_CONTEXT_LIMIT_EXCEEDED_NO_TRUNCATION")
    payload = {"model": binding.get("api_model_id") or binding.get("model_id"),
               "messages": messages, "stream": False, **parameters}
    size = len(canonical(payload).encode("utf-8"))
    if size > limits["max_request_bytes"]:
        raise ValueError("REQUEST_BYTE_LIMIT_EXCEEDED")
    # Reserve against the largest applicable tariff, never cache discounts.
    applicable = [t for t in tiers if t["min_input_tokens"] <= total]
    if tiers[-1]["max_input_tokens_exclusive"] is not None and total >= tiers[-1]["max_input_tokens_exclusive"]:
        raise ValueError("REQUEST_EXCEEDS_TARIFF_RANGE")
    input_rate = max(max(amount(t["input_cny_per_million"]), amount(t["cached_input_cny_per_million"])) for t in applicable)
    output_rate = max(amount(t["output_cny_per_million"]) for t in applicable)
    required = {"input": total * input_rate / 1_000_000, "output": output * output_rate / 1_000_000,
                "reasoning": amount(0), "images": amount(0), "other": amount(0)}
    if not binding["billing"]["reasoning_in_output"]:
        reasoning = positive(limits.get("max_reasoning_tokens"), "max_reasoning_tokens")
        required["reasoning"] = reasoning * amount(binding["billing"]["reasoning_cny_per_million"]) / 1_000_000
    supplied = binding.get("per_request_upper_bound")
    if not isinstance(supplied, dict) or set(supplied) != set(required):
        raise ValueError("INCOMPLETE_RESERVATION_BOUND")
    if any(amount(supplied[k]) < v for k, v in required.items()):
        raise ValueError("RESERVATION_BELOW_REQUEST_BOUND")
    return {"text_utf8_bytes": text_bytes, "input_token_upper_bound": total,
            "image_count": images, "image_token_upper_bound": image_tokens,
            "chat_overhead_token_upper_bound": overhead, "output_token_limit": output,
            "request_bytes": size, "required_reservation_cny": {k: str(v) for k, v in required.items()},
            "basis": "VERIFIED_CONSERVATIVE_BOUND_NOT_TOKEN_USAGE"}


def usage_bound_error(result, request_budget):
    """A provider counter contradicting a verified bound invalidates further dispatch."""
    if request_budget is None:
        return None
    usage = result.get("usage", {})
    for actual_key, cap_key in (("input_tokens", "input_token_upper_bound"),
                                ("output_tokens", "output_token_limit")):
        actual = usage.get(actual_key)
        if count(actual) is not None and actual > request_budget[cap_key]:
            return "PROVIDER_USAGE_EXCEEDS_VERIFIED_REQUEST_BOUND: " + actual_key
    return None
