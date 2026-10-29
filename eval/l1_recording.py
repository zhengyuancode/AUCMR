"""Durable L1 judgments and material checkpoints; no billing or metric changes."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class L1Recording:
    def __init__(self, root, context, secrets=()):
        self.root = Path(root)
        self.secrets = tuple(s for s in secrets if s)
        self.root.mkdir(parents=True, exist_ok=True)
        manifest = self.root / "manifest.json"
        if manifest.exists():
            if self._read(manifest) != context:
                raise RuntimeError("L1 checkpoint inputs, model, or implementation changed; no dispatch")
        else:
            self._save(manifest, context, exclusive=True)

    def _safe(self, value):
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, dict):
            return {k: self._safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._safe(v) for v in value]
        return value

    def _save(self, path, value, exclusive=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        value = self._safe(value)
        sealed = {"data": value, "sha256": digest(value)}
        target = path if exclusive else path.with_name(path.name + "." + uuid4().hex + ".tmp")
        with target.open("x", encoding="utf-8") as f:
            json.dump(sealed, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush(); os.fsync(f.fileno())
        if not exclusive:
            os.replace(target, path)

    def _read(self, path):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value["sha256"] != digest(value["data"]):
            raise RuntimeError("L1 checkpoint hash mismatch")
        return value["data"]

    def judge(self, prompt, images, dispatch, parse):
        request = {"prompt": prompt, "images": [{"path": str(Path(p).resolve()),
                   "sha256": file_hash(p)} for p in images]}
        path = self.root / "requests" / (digest(request) + ".json")
        replayed = path.exists()
        if replayed:
            record = self._read(path)
            if record["request"] != self._safe(request):
                raise RuntimeError("L1 request identity mismatch")
            if record["state"] not in {"RECEIVED", "VALID"}:
                raise RuntimeError("L1 recorded " + record["state"] + "; preserved, not resent: " + path.name)
        else:
            record = {"state": "PENDING", "request": request, "raw_response": None, "parsed": None}
            self._save(path, record, exclusive=True)  # Durable BEFORE dispatch.
            try:
                record["raw_response"] = self._safe(dispatch())
            except Exception as exc:
                record.update(state="TRANSPORT_FAILED", error=self._safe(type(exc).__name__ + ": " + str(exc)))
                self._save(path, record)
                raise RuntimeError("L1 transport failed; retained, not retried: " + path.name) from None
            record["state"] = "RECEIVED"
            self._save(path, record)  # Raw response survives a parser/process failure.
        parsed = parse(record["raw_response"])
        if parsed is None:
            record.update(state="REJECTED", error="UNPARSEABLE_JUDGMENT")
            self._save(path, record)
            raise RuntimeError("L1 malformed raw response retained: " + path.name)
        if record["state"] == "VALID" and record["parsed"] != parsed:
            raise RuntimeError("L1 parsed checkpoint mismatch; no redraw")
        if record["state"] != "VALID":
            record.update(state="VALID", parsed=parsed)
            self._save(path, record)
        return parsed, replayed

    def load_metrics(self, identity):
        from eval.compute_metrics import UCMetrics, SlotMetrics
        path = self.root / "materials" / (digest(identity) + ".json")
        if not path.exists():
            return None
        record = self._read(path)
        if record["identity"] != identity:
            raise RuntimeError("L1 material identity mismatch")
        values = record["metrics"]
        values["slot_metrics"] = {k: SlotMetrics(**v) for k, v in values["slot_metrics"].items()}
        return UCMetrics(**values)

    def save_metrics(self, identity, metrics):
        path = self.root / "materials" / (digest(identity) + ".json")
        self._save(path, {"identity": identity, "metrics": asdict(metrics)}, exclusive=True)


def make_recording(root, judge, bindings):
    from eval.implementation import implementation_manifest
    implementation = implementation_manifest("legacy")
    implementation["eval/l1_recording.py"] = file_hash(__file__)
    endpoints = [judge._endpoint, judge._vision_endpoint]
    fields = ("base_url", "model_name", "temperature", "max_tokens")
    context = {"version": "L1_RECORDING_1", "bindings": list(bindings),
               "implementation": implementation, "system_prompt": judge.SYSTEM_PROMPT,
               "endpoints": [{k: getattr(e, k, None) for k in fields} for e in endpoints],
               "injected_test_model": judge._model_fn is not None}
    return L1Recording(root, context, [getattr(e, "api_key", "") for e in endpoints])
