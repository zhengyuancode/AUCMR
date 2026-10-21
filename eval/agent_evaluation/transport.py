"""Mock is explicit and never falls back to live. Live access requires verified bindings."""
from __future__ import annotations

import json
import os
import socket
from contextlib import contextmanager
from urllib.parse import urlparse

from .common import Blocked, EvaluationError, canonical, digest


@contextmanager
def deny_network():
    """Fail closed on accidental network use, including imports and HTTP clients."""
    from unittest.mock import patch
    def denied(*args, **kwargs):
        raise AssertionError("OFFLINE_NETWORK_DENIED")
    with patch.object(socket, "create_connection", denied), patch.object(socket.socket, "connect", denied), \
         patch.object(socket.socket, "connect_ex", denied), patch.object(socket, "getaddrinfo", denied):
        yield


class MockTransport:
    mode = "mock"
    def __init__(self, scripted):
        self.scripted = scripted
        self.calls = []

    def request(self, binding: dict, messages: list[dict], task: dict, attempt: int) -> dict:
        self.calls.append({"task_id": task["task_id"], "session_id": task["session_id"], "attempt": attempt,
                           "input_hash": digest(messages)})
        answer = self.scripted(task, messages, attempt)
        if isinstance(answer, Exception):
            raise answer
        return {"body": answer, "response_model": "MOCK_SCRIPTED_NOT_A_MODEL", "request_id": f"mock-{len(self.calls)}",
                "usage": {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}, "cost_cny": 0.0}


def binding_blockers(config: dict) -> list[str]:
    errors = []
    if not config["budgets"].get("live_authorized"):
        errors.append("LIVE_NOT_AUTHORIZED")
    for name in ("A", "B", "C"):
        b = config["models"][name]
        required = ("base_url", "api_key_env", "parameters", "capabilities", "billing", "per_request_upper_bound", "binding_evidence")
        if b.get("binding_status") != "BOUND" or any(k not in b for k in required):
            errors.append(f"{name}:NOT_BOUND")
            continue
        if b["capabilities"].get("images") is not True or b["capabilities"].get("structured_json") is not True:
            errors.append(f"{name}:CAPABILITY_UNVERIFIED")
        if not b["billing"].get("complete") or not b["billing"].get("verified_upper_bound"):
            errors.append(f"{name}:PRICE_NOT_VERIFIED")
        output_cap = b["parameters"].get("max_tokens", b["parameters"].get("max_completion_tokens"))
        if not isinstance(output_cap, int) or not 1 <= output_cap <= config["judging"]["max_output_tokens_per_request"]:
            errors.append(f"{name}:OUTPUT_BOUND_NOT_VERIFIED")
        if set(b["per_request_upper_bound"]) != {"input", "output", "reasoning", "images", "other"}:
            errors.append(f"{name}:INCOMPLETE_RESERVATION_BOUND")
        if not os.environ.get(b["api_key_env"]):
            errors.append(f"{name}:KEY_NOT_SET")
    if config["models"]["A"].get("api_model_id") == config["models"]["B"].get("api_model_id"):
        errors.append("A_B_REQUIRE_DIFFERENT_MODELS")
    return errors


class LiveTransport:
    mode = "live"
    def __init__(self, config: dict, *, offline=False):
        if offline:
            raise Blocked("Live transport is forbidden offline")
        errors = binding_blockers(config)
        if errors:
            raise Blocked("; ".join(errors))

    def request(self, binding: dict, messages: list[dict], task: dict, attempt: int) -> dict:
        # This path is never executed by offline tests. Network module imported only here.
        from urllib.request import Request, build_opener, ProxyHandler
        base = binding["base_url"].rstrip("/")
        parsed = urlparse(base)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query:
            raise Blocked("Verified HTTPS endpoint required")
        parameters = dict(binding["parameters"])
        if set(parameters) & {"model", "messages", "stream", "api_key", "base_url"}:
            raise Blocked("Binding parameters cannot override task or credentials")
        body = {"model": binding["api_model_id"], "messages": messages, "stream": False, **parameters}
        key = os.environ[binding["api_key_env"]]
        request = Request(base + "/chat/completions", data=canonical(body).encode(), headers={
            "Authorization": "Bearer " + key, "Content-Type": "application/json"})
        # No implicit environment proxies or cross-host credential redirects.
        from urllib.request import HTTPRedirectHandler
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise Blocked("Unexpected model endpoint redirect")
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=120) as response:
            data = json.load(response)
        choice = data["choices"][0]
        usage = data.get("usage", {})
        rates = binding["billing"]
        input_n, output_n = usage.get("prompt_tokens"), usage.get("completion_tokens")
        reasoning = usage.get("completion_tokens_details", {}).get("reasoning_tokens")
        cost = None
        if input_n is not None and output_n is not None and rates.get("all_charges_in_token_usage"):
            cost = (input_n * rates["input_cny_per_million"] + output_n * rates["output_cny_per_million"]) / 1e6
            if not rates.get("reasoning_in_output"):
                cost = None if reasoning is None else cost + reasoning * rates["reasoning_cny_per_million"] / 1e6
        return {"body": choice["message"]["content"], "finish_reason": choice.get("finish_reason"),
                "response_model": data.get("model"), "request_id": data.get("id"),
                "system_fingerprint": data.get("system_fingerprint"), "raw_usage": usage,
                "usage": {"input_tokens": input_n, "output_tokens": output_n, "reasoning_tokens": reasoning}, "cost_cny": cost}
