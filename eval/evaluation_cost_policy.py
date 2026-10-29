"""Provider-controlled spending with auditable, non-blocking cost estimates."""
import base64
import hashlib
import json
from decimal import Decimal, InvalidOperation


def provider_managed(binding):
    return binding.get("cost_control") == "PROVIDER_MANAGED"


def interface_ready(binding):
    observation = binding.get("interface_observation", {})
    return binding.get("binding_status") == "BOUND" or (
        provider_managed(binding) and observation.get("accepted_by_controller") is True
        and observation.get("text_and_image_success_observed") is True)


def check_provider_request(binding, messages):
    """Validate request structure/output limits, not a speculative token bound."""
    parameters = binding.get("parameters", {})
    forbidden = {"model", "messages", "stream", "api_key", "base_url", "headers", "tools"}
    if not isinstance(parameters, dict) or forbidden.intersection(parameters):
        raise ValueError("INVALID_MODEL_PARAMETERS")
    if "max_tokens" in parameters and "max_completion_tokens" in parameters:
        raise ValueError("AMBIGUOUS_OUTPUT_LIMIT")
    output = parameters.get("max_completion_tokens", parameters.get("max_tokens"))
    if type(output) is not int or output <= 0:
        raise ValueError("OUTPUT_LIMIT_REQUIRED")
    if not isinstance(messages, list) or not messages:
        raise ValueError("NONEMPTY_MESSAGES_REQUIRED")
    images = 0
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "content"} or message['role'] not in {'system','user','assistant','tool'}:
            raise ValueError("INVALID_MESSAGE")
        content = message['content']
        if isinstance(content, str):
            continue
        if not isinstance(content, list) or not content:
            raise ValueError("INVALID_CONTENT")
        for part in content:
            if not isinstance(part, dict):
                raise ValueError("INVALID_CONTENT_PART")
            if part.get('type') == 'text' and isinstance(part.get('text'), str):
                continue
            if part.get('type') != 'image_url' or not isinstance(part.get('image_url'), dict):
                raise ValueError("INVALID_CONTENT_PART")
            url = part['image_url'].get('url', '')
            if not isinstance(url, str) or not url.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/webp;base64,')):
                raise ValueError("INLINE_IMAGE_REQUIRED")
            try:
                data = base64.b64decode(url.split(',', 1)[1], validate=True)
            except ValueError:
                raise ValueError("INVALID_IMAGE_BASE64") from None
            if not data:
                raise ValueError("EMPTY_IMAGE")
            images += 1
    size = len(json.dumps({'model':binding.get('api_model_id', binding.get('model_id')), 'messages':messages,
                           'stream':False, **parameters}, ensure_ascii=False).encode())
    limits = binding.get('request_limits', {})
    # Only explicit byte/count limits apply. No input-token estimates are certified.
    for field, actual in [('max_request_bytes',size), ('max_images',images)]:
        cap = limits.get(field)
        if type(cap) is int and cap > 0 and actual > cap:
            raise ValueError('REQUEST_LIMIT_EXCEEDED: '+field)
    return {'basis':'PROVIDER_MANAGED_NO_TOKEN_BOUND','output_token_limit':output,
            'request_bytes':size,'image_count':images,'required_reservation_cny':None}


def recorded_usage(raw, binding):
    from .evaluation_billing import count, amount
    usage = raw.get('usage') if isinstance(raw, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get('completion_tokens_details') or {}
    prompt_details = usage.get('prompt_tokens_details') or {}
    inp, out = count(usage.get('prompt_tokens')), count(usage.get('completion_tokens'))
    result = {'raw_usage':usage,'usage':{'input_tokens':inp,'output_tokens':out,
              'reasoning_tokens':count(details.get('reasoning_tokens')) if isinstance(details,dict) else None,
              'cached_input_tokens':count(prompt_details.get('cached_tokens')) if isinstance(prompt_details,dict) else None},
              'cost_cny':None,'list_price_cost_cny':None,'invoice_cost_cny':None,
              'cost_basis':'UNKNOWN','billing_error':None,
              'billing_sha256':hashlib.sha256(json.dumps(binding.get('cost_estimate',{}),sort_keys=True).encode()).hexdigest()}
    try:
        rates = binding['cost_estimate']
        if inp is None or out is None or not rates.get('source'):
            raise ValueError('MISSING_USAGE_OR_PRICE_SOURCE')
        cost = (inp*amount(rates['input_cny_per_million']) + out*amount(rates['output_cny_per_million'])) / Decimal(1_000_000)
        value = float(cost)
        import math
        if not math.isfinite(value):
            raise ValueError('UNREPRESENTABLE_COST')
        result.update(cost_cny=value,cost_cny_decimal=str(cost),cost_basis='UNCACHED_TOKEN_ESTIMATE_NOT_INVOICE',
                      estimate_source=rates['source'],estimate_scope='Reported prompt and completion tokens only; not proof of all charges')
    except (KeyError, ValueError, TypeError, InvalidOperation, OverflowError) as exc:
        result['billing_error'] = type(exc).__name__
    return result
