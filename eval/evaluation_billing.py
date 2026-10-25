"""Decimal list-price accounting, separate from reservations and account invoices.

The legacy flat-rate path is retained for historical/offline fixtures only. Live
requests require an explicit tariff and usage contract via validate_billing().
Missing cache usage produces a labelled upper estimate, never a claimed invoice.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
from .agent_evaluation.common import digest


def amount(value):
    if isinstance(value, bool):
        raise ValueError("Invalid billing amount")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid billing amount") from None
    if not result.is_finite() or result < 0:
        raise ValueError("Invalid billing amount")
    return result


def count(value):
    # Usage counters are bounded integers, never arbitrary-precision monetary
    # multipliers. This also keeps subsequent SQLite accounting representable.
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def validate_billing(binding):
    billing = binding.get("billing", {})
    if not isinstance(billing, dict):
        raise ValueError("INVALID_BILLING")
    if any(billing.get(k) is not True for k in
           ("complete", "verified_upper_bound", "all_charges_in_token_usage")):
        raise ValueError("BILLING_NOT_VERIFIED")
    if type(billing.get("reasoning_in_output")) is not bool:
        raise ValueError("REASONING_BILLING_NOT_BOUND")
    if not billing["reasoning_in_output"]:
        amount(billing.get("reasoning_cny_per_million"))
    tariff = billing.get("tariff")
    if not isinstance(tariff, dict) or tariff.get("currency") != "CNY" or not tariff.get("version") or not tariff.get("source"):
        raise ValueError("TARIFF_NOT_BOUND")
    tiers = tariff.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        raise ValueError("TARIFF_TIERS_REQUIRED")
    previous = 0
    for tier in tiers:
        if not isinstance(tier, dict):
            raise ValueError("INVALID_TARIFF_TIER")
        lower, upper = tier.get("min_input_tokens"), tier.get("max_input_tokens_exclusive")
        if count(lower) is None or lower != previous or (upper is not None and (count(upper) is None or upper <= lower)):
            raise ValueError("TARIFF_TIERS_MUST_BE_CONTIGUOUS_HALF_OPEN_INTERVALS")
        for field in ("input_cny_per_million", "output_cny_per_million", "cached_input_cny_per_million"):
            amount(tier.get(field))
        previous = upper
    contract = billing.get("usage_contract", {})
    if not isinstance(contract, dict):
        raise ValueError("INVALID_USAGE_CONTRACT")
    if contract.get("verified") is not True or not contract.get("evidence"):
        raise ValueError("USAGE_CONTRACT_NOT_VERIFIED")
    path = contract.get("cached_input_path")
    if not isinstance(path, list) or not path or not all(isinstance(k, str) and k for k in path):
        raise ValueError("CACHE_USAGE_PATH_REQUIRED")
    if contract.get("missing_cache") not in {"unknown", "zero_documented"}:
        raise ValueError("CACHE_MISSING_POLICY_REQUIRED")
    if contract.get("image_tokens_in_prompt") is not True:
        raise ValueError("IMAGE_BILLING_NOT_BOUND")
    return tiers


def normalize_usage(raw, binding):
    raw = raw if isinstance(raw, dict) else {}
    usage = raw.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    inp, out = count(usage.get("prompt_tokens")), count(usage.get("completion_tokens"))
    details = usage.get("completion_tokens_details", {})
    reasoning = count(details.get("reasoning_tokens")) if isinstance(details, dict) else None
    result = {"raw_usage": usage, "usage": {"input_tokens": inp, "output_tokens": out,
              "reasoning_tokens": reasoning, "cached_input_tokens": None},
              "cost_cny": None, "list_price_cost_cny": None, "invoice_cost_cny": None,
              "cost_basis": "UNKNOWN", "billing_error": None,
              "billing_sha256": digest(binding.get("billing", {}))}
    try:
        rates = binding["billing"]
        if rates.get("all_charges_in_token_usage") is not True or inp is None or out is None:
            raise ValueError("MISSING_OR_UNVERIFIED_USAGE")
        if type(rates.get("reasoning_in_output")) is not bool:
            raise ValueError("REASONING_BILLING_NOT_BOUND")
        if reasoning is not None and rates["reasoning_in_output"] and reasoning > out:
            raise ValueError("REASONING_EXCEEDS_COMPLETION")
        cached, exact = None, False
        if "tariff" in rates:
            tiers = validate_billing(binding)
            tier = next((t for t in tiers if inp >= t["min_input_tokens"] and
                         (t["max_input_tokens_exclusive"] is None or inp < t["max_input_tokens_exclusive"])), None)
            if tier is None:
                raise ValueError("USAGE_OUTSIDE_TARIFF")
            contract = rates["usage_contract"]
            value = usage
            missing = False
            for key in contract["cached_input_path"]:
                if not isinstance(value, dict):
                    raise ValueError("MALFORMED_CACHE_USAGE")
                if key not in value:
                    missing = True
                    break
                value = value[key]
            if missing:
                cached = 0 if contract["missing_cache"] == "zero_documented" else None
            else:
                cached = count(value)
                if cached is None or cached > inp:
                    raise ValueError("INVALID_CACHE_USAGE")
            normal_rate = amount(tier["input_cny_per_million"])
            cache_rate = amount(tier["cached_input_cny_per_million"])
            total = inp * max(normal_rate, cache_rate) if cached is None else (inp - cached) * normal_rate + cached * cache_rate
            total += out * amount(tier["output_cny_per_million"])
            exact = cached is not None
            result.update(tariff_version=rates["tariff"]["version"], tariff_source=rates["tariff"]["source"],
                          applied_tier=tier, cost_basis="USAGE_LIST_PRICE" if exact else "USAGE_RATE_UPPER_BOUND")
        else:
            # Compatibility for replaying old offline tests; live dispatch rejects
            # this unversioned envelope. It must never be labelled an actual bill.
            total = inp * amount(rates["input_cny_per_million"]) + out * amount(rates["output_cny_per_million"])
            result["cost_basis"] = "LEGACY_RATE_ESTIMATE"
        if not rates["reasoning_in_output"]:
            if reasoning is None:
                raise ValueError("MISSING_REASONING_USAGE")
            total += reasoning * amount(rates["reasoning_cny_per_million"])
        cost = total / Decimal(1_000_000)
        if not math.isfinite(float(cost)):
            raise ValueError("UNREPRESENTABLE_COST")
        result.update(cost_cny=float(cost), cost_cny_decimal=str(cost),
                      list_price_cost_cny=float(cost) if exact else None)
        result["usage"]["cached_input_tokens"] = cached
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError) as exc:
        result.update(cost_cny=None, list_price_cost_cny=None, cost_basis="UNKNOWN", billing_error=str(exc))
    return result
