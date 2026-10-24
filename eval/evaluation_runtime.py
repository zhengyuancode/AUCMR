"""Shared, auditable OpenAI-compatible requests for L2 and L4.

Injected transports must declare mode="mock" and implement
request(binding, messages, task, attempt), returning an OpenAI response object.
They use a separate offline campaign ledger and never count as real calls.
The caller owns semantic schema validation and any predeclared format retry.
"""
from __future__ import annotations

import base64
import copy
import json
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .agent_evaluation.budget import Budget, units
from .agent_evaluation.common import canonical, digest, now, read_json, write_json


class _RuntimeError(ValueError):
    """A safe failure with an optional persisted request receipt."""

    def __init__(self, message, *, receipt=None, evidence_path=None):
        super().__init__(message)
        self.receipt = copy.deepcopy(receipt)
        self.request_id = receipt.get("request_id") if receipt else None
        self.evidence_path = str(evidence_path) if evidence_path else None
        self.retryable = False


class RuntimeBlocked(_RuntimeError):
    """Configuration, budget, or uncertain billing prevents another request."""


class RuntimeDeferred(RuntimeBlocked):
    """This request was not dispatched; resume may try the same frozen task."""


class RuntimeResponseError(_RuntimeError):
    """A paid response was preserved but is not a complete JSON object."""


_COMPONENTS = {"input", "output", "reasoning", "images", "other"}
_SECRET_FIELDS = {"api_key", "authorization", "password", "secret", "access_token"}


def _amount(value):
    if isinstance(value, bool):
        raise RuntimeBlocked("Invalid numeric billing field")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise RuntimeBlocked("Invalid numeric billing field") from None
    if not amount.is_finite() or amount < 0:
        raise RuntimeBlocked("Invalid numeric billing field")
    return amount


def _no_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in _SECRET_FIELDS:
                raise RuntimeBlocked("Use an environment variable, never literal credentials")
            _no_secrets(child)
    elif isinstance(value, list):
        for child in value:
            _no_secrets(child)


def _redact(value, secret):
    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]")
    if isinstance(value, dict):
        return {_redact(k, secret): _redact(v, secret) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, secret) for v in value]
    return value


def _token_count(value):
    return value if type(value) is int and value >= 0 else None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeBlocked("Model endpoint redirect is forbidden")


class EvaluationRuntime:
    def __init__(self, binding: dict, run_dir: Path, layer: str,
                 campaign_ledger: Path, budget_cap_cny: float = 500,
                 live_authorized: bool = False, transport=None):
        if not isinstance(binding, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", layer):
            raise RuntimeBlocked("Invalid binding or layer")
        _no_secrets(binding)
        if type(live_authorized) is not bool:
            raise RuntimeBlocked("Explicit boolean authorization required")
        if transport is not None and getattr(transport, "mode", None) != "mock":
            raise RuntimeBlocked("An injected transport must explicitly declare mode=mock")
        if _amount(budget_cap_cny) <= 0:
            raise RuntimeBlocked("Campaign cap must be positive")
        self.binding = copy.deepcopy(binding)
        self.run_dir = Path(run_dir).resolve()
        self.layer = layer
        self.live_authorized = live_authorized
        self.transport = transport
        self.is_live = transport is None
        self.real_model_calls = 0
        self.cache_dir = self.run_dir / "runtime_requests"
        try:
            self.budget = Budget(Path(campaign_ledger), cap=budget_cap_cny)
            with self.budget.connect() as db:
                mode = "live" if self.is_live else "mock"
                previous = db.execute("SELECT value FROM meta WHERE key='runtime_transport_mode'").fetchone()
                if previous and previous[0] != mode:
                    raise RuntimeBlocked("Mock and real requests cannot share a campaign ledger")
                if not previous and not self.is_live and db.execute("SELECT 1 FROM reservations LIMIT 1").fetchone():
                    raise RuntimeBlocked("Mock requests cannot use an existing campaign ledger")
                db.execute("INSERT OR IGNORE INTO meta VALUES ('runtime_transport_mode', ?)", (mode,))
        except ValueError as exc:
            if isinstance(exc, RuntimeBlocked):
                raise
            raise RuntimeBlocked("Campaign ledger initialization failed") from None

    def _payload(self, messages):
        binding = self.binding
        if binding.get("binding_status") != "BOUND":
            raise RuntimeBlocked("MODEL_NOT_BOUND")
        model = binding.get("api_model_id") or binding.get("model_id")
        if any(not isinstance(v, str) or not v.strip() for v in
               (model, binding.get("provider"), binding.get("base_url"), binding.get("api_key_env"))):
            raise RuntimeBlocked("Incomplete model binding")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", binding["api_key_env"]):
            raise RuntimeBlocked("Invalid credential environment variable name")
        endpoint = urlparse(binding["base_url"])
        if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username
                or endpoint.password or endpoint.query or endpoint.fragment):
            raise RuntimeBlocked("A credential-free HTTPS model endpoint is required")
        parameters = binding.get("parameters")
        if not isinstance(parameters, dict) or set(parameters) & {
                "model", "messages", "stream", "api_key", "base_url", "headers"}:
            raise RuntimeBlocked("Invalid model parameters")
        output_limit = parameters.get("max_completion_tokens", parameters.get("max_tokens"))
        if type(output_limit) is not int or output_limit <= 0:
            raise RuntimeBlocked("Bind a positive output token limit")
        billing = binding.get("billing", {})
        if not isinstance(billing, dict) or any(billing.get(k) is not True for k in
                ("complete", "verified_upper_bound", "all_charges_in_token_usage")):
            raise RuntimeBlocked("Complete verified token billing is required")
        for key in ("input_cny_per_million", "output_cny_per_million"):
            _amount(billing.get(key))
        if type(billing.get("reasoning_in_output")) is not bool:
            raise RuntimeBlocked("Declare reasoning token billing")
        if not billing["reasoning_in_output"]:
            _amount(billing.get("reasoning_cny_per_million"))
        bound = binding.get("per_request_upper_bound")
        if not isinstance(bound, dict) or set(bound) != _COMPONENTS:
            raise RuntimeBlocked("Complete input/output/reasoning/images/other upper bound required")
        for value in bound.values():
            _amount(value)
        if not isinstance(messages, list) or not messages:
            raise RuntimeBlocked("A nonempty messages array is required")
        has_images = False
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant", "tool"}:
                raise RuntimeBlocked("Invalid message")
            content = message.get("content")
            if isinstance(content, str):
                continue
            if not isinstance(content, list) or not content:
                raise RuntimeBlocked("Invalid message content")
            for part in content:
                if not isinstance(part, dict):
                    raise RuntimeBlocked("Invalid multimodal content")
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    continue
                if part.get("type") != "image_url" or not isinstance(part.get("image_url"), dict):
                    raise RuntimeBlocked("Use standard text and image_url content parts")
                url = part["image_url"].get("url")
                match = re.fullmatch(r"data:image/(?:png|jpeg|webp|gif);base64,([A-Za-z0-9+/]+={0,2})", url or "")
                if not match:
                    raise RuntimeBlocked("An image must be an inline image data URL")
                try:
                    base64.b64decode(match.group(1), validate=True)
                except ValueError:
                    raise RuntimeBlocked("Invalid image base64") from None
                has_images = True
        if has_images and binding.get("capabilities", {}).get("images") is not True:
            raise RuntimeBlocked("Image capability is not bound")
        payload = {"model": model, "messages": copy.deepcopy(messages), "stream": False, **parameters}
        try:
            canonical(payload)
        except (TypeError, ValueError):
            raise RuntimeBlocked("Request must be finite JSON") from None
        return payload

    def _save(self, path, record):
        record = {k: v for k, v in record.items() if k != "record_sha256"}
        record["record_sha256"] = digest(record)
        write_json(path, record)

    def _reservation(self, request_id):
        with self.budget.connect() as db:
            return db.execute("SELECT reserved,actual,state FROM reservations WHERE id=?", (request_id,)).fetchone()

    def _normalize(self, raw, request_id):
        raw = raw if isinstance(raw, dict) else {}
        usage = raw.get("usage", {})
        usage = usage if isinstance(usage, dict) else {}
        input_n = _token_count(usage.get("prompt_tokens"))
        output_n = _token_count(usage.get("completion_tokens"))
        details = usage.get("completion_tokens_details", {})
        reasoning = _token_count(details.get("reasoning_tokens")) if isinstance(details, dict) else None
        rates = self.binding["billing"]
        cost = None
        if input_n is not None and output_n is not None:
            amount = input_n * _amount(rates["input_cny_per_million"]) + output_n * _amount(rates["output_cny_per_million"])
            if rates["reasoning_in_output"]:
                cost = float(amount / 1_000_000)
            elif reasoning is not None:
                cost = float((amount + reasoning * _amount(rates["reasoning_cny_per_million"])) / 1_000_000)
        choices = raw.get("choices")
        choice = choices[0] if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict) else {}
        message = choice.get("message", {})
        message = message if isinstance(message, dict) else {}
        return {"body": message.get("content"), "finish_reason": choice.get("finish_reason"),
                "response_model": raw.get("model"), "request_id": request_id,
                "provider_request_id": raw.get("id"), "system_fingerprint": raw.get("system_fingerprint"),
                "usage": {"input_tokens": input_n, "output_tokens": output_n, "reasoning_tokens": reasoning},
                "raw_usage": usage, "cost_cny": cost, "offline": not self.is_live,
                "real_model_calls": 1 if self.is_live else 0}

    def _complete(self, path, record, *, cached):
        result = self._normalize(record.get("raw_response"), record["request_id"])
        result.update(evidence_path=str(path), cached=cached)
        row = self._reservation(record["request_id"])
        if row is None:
            raise RuntimeBlocked("Cached response is missing its campaign reservation", receipt=result, evidence_path=path)
        if row[2] == "RESERVED":
            try:
                self.budget.settle(record["request_id"], result["cost_cny"])
            except ValueError:
                record["state"] = "BILLING_UNKNOWN"
                self._save(path, record)
                raise RuntimeBlocked("UNKNOWN_BILLING: preserve the response and reconcile before continuing", receipt=result, evidence_path=path) from None
        elif row[2] != "SETTLED":
            raise RuntimeBlocked("UNKNOWN_BILLING: campaign reservation is unresolved", receipt=result, evidence_path=path)
        elif result["cost_cny"] is None or row[1] != units(result["cost_cny"]):
            raise RuntimeBlocked("Cached response billing disagrees with the campaign ledger", receipt=result, evidence_path=path)
        error = None
        body = result["body"]
        if result["finish_reason"] != "stop":
            error = "INCOMPLETE_FINISH_REASON"
        elif not isinstance(result["response_model"], str) or not result["response_model"]:
            error = "MISSING_RESPONSE_MODEL"
        elif not isinstance(body, (str, dict)):
            error = "INVALID_JSON_RESPONSE"
        elif isinstance(body, str):
            try:
                parsed = json.loads(body, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                if not isinstance(parsed, dict):
                    error = "INVALID_JSON_RESPONSE"
            except (ValueError, TypeError):
                error = "INVALID_JSON_RESPONSE"
        if record.get("secret_redacted"):
            error = "SECRET_ECHOED"
        record.update(state="REJECTED_RESPONSE" if error else "COMPLETE", result=result, error=error)
        self._save(path, record)
        if error:
            raise RuntimeResponseError(error + ": response and usage retained; identical requests are not resent", receipt=result, evidence_path=path)
        return {**result, "cached": cached}

    def _http(self, payload, secret):
        endpoint = self.binding["base_url"].rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        request = Request(endpoint, data=canonical(payload).encode("utf-8"), headers={
            "Authorization": "Bearer " + secret, "Content-Type": "application/json"})
        try:
            with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=120) as response:
                text = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            return {"http_status": exc.code, "error_response_text": exc.read().decode("utf-8", errors="replace")}
        try:
            return json.loads(text)
        except ValueError:
            return {"invalid_response_text": text}

    def request(self, messages: list, *, task_id: str, attempt: int = 1) -> dict:
        if not isinstance(task_id, str) or not task_id.strip() or type(attempt) is not int or attempt < 1:
            raise RuntimeBlocked("A task ID and positive attempt number are required")
        payload = self._payload(messages)
        fingerprint = digest({"binding": self.binding, "payload": payload, "live": self.is_live})
        request_id = "-".join((self.layer, digest(str(self.run_dir))[:16], digest(task_id)[:16], fingerprint[:32]))
        path = self.cache_dir / (request_id + ".json")
        if path.exists():
            record = read_json(path)
            signature = record.get("record_sha256")
            if (signature != digest({k: v for k, v in record.items() if k != "record_sha256"})
                    or record.get("request_id") != request_id or record.get("fingerprint") != fingerprint):
                raise RuntimeBlocked("Request cache integrity mismatch")
            if "raw_response" in record:
                return self._complete(path, record, cached=True)
            row = self._reservation(request_id)
            if row and row[2] == "RESERVED":
                try:
                    self.budget.settle(request_id, None)
                except ValueError:
                    pass
            raise RuntimeBlocked("UNKNOWN_BILLING: prior request has no saved response; do not resend",
                                 receipt=self._normalize(None, request_id), evidence_path=path)
        secret = None
        if self.is_live:
            if not self.live_authorized:
                raise RuntimeDeferred("LIVE_NOT_AUTHORIZED")
            secret = os.environ.get(self.binding["api_key_env"])
            if not secret:
                raise RuntimeDeferred("MODEL_KEY_NOT_SET")
            if secret in canonical(payload):
                raise RuntimeBlocked("Request content contains the model credential")
        try:
            self.budget.reserve(request_id, self.binding["provider"], self.binding["per_request_upper_bound"])
        except ValueError:
            raise RuntimeDeferred("Campaign request blocked: budget, concurrency, existing request, or unknown charge") from None
        record = {"request_id": request_id, "fingerprint": fingerprint, "layer": self.layer,
                  "run_id": self.run_dir.name, "task_id": task_id, "attempt": attempt,
                  "mode": "live" if self.is_live else "mock", "created_at": now(),
                  "state": "PENDING", "payload": payload, "binding_sha256": digest(self.binding)}
        self._save(path, record)
        try:
            if self.is_live:
                self.real_model_calls += 1
                raw = self._http(payload, secret)
            else:
                raw = self.transport.request(copy.deepcopy(self.binding), copy.deepcopy(payload["messages"]),
                                             {"task_id": task_id, "session_id": self.run_dir.name,
                                              "request_id": request_id, "layer": self.layer}, attempt)
            # Reject non-JSON data before writing; raw exceptions may contain credentials.
            canonical(raw)
        except Exception as exc:
            record.update(state="BILLING_UNKNOWN", transport_error=type(exc).__name__)
            self._save(path, record)
            try:
                self.budget.settle(request_id, None)
            except ValueError:
                pass
            raise RuntimeBlocked("UNKNOWN_BILLING: transport failed; preserve request and do not resend",
                                 receipt=self._normalize(None, request_id), evidence_path=path) from None
        safe_raw = _redact(raw, secret)
        record.update(state="RECEIVED", raw_response=safe_raw, received_at=now(),
                      secret_redacted=safe_raw != raw)
        self._save(path, record)
        return self._complete(path, record, cached=False)
