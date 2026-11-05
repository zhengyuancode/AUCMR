"""Narrow L1 continuation: retain old judgments, replace rejected requests once."""
import json
import re
from pathlib import Path

from eval.compute_metrics import NLIJudge
from eval.l1_recording import digest, file_hash


def repair_prompt(prompt, images):
    slot = re.match(r"Use-case slot: ([a-z_]+)\n", prompt)
    if slot is None:
        raise RuntimeError("L1 repair requires an explicit slot")
    if images:
        prompt = prompt.replace(
            "[This evidence card has no textual content.]",
            "[No text transcription is supplied for this card. Read its attached "
            "source page image; absence of a transcription does not mean the page is blank.]",
        )
    return prompt + (
        "\n\nReturn ONE JSON object only, with exactly these four fields: "
        '{"entailment":"entailment|contradiction|neutral","slot_fit":true/false,'
        '"rationale":"one sentence","slot_fit_rationale":"one sentence"}. '
        "Choose one entailment label, never a boolean. Both reasons must be nonempty. "
        "Judge source entailment and slot fit independently, using the existing rules. "
        f"The actual slot for this request is {slot.group(1)}. "
        "Do not return preliminary judgments, multiple objects, or commentary."
    )


class RepairJudge(NLIJudge):
    def _parse_response(self, response):
        # A repair must contain one complete object; json_repair can otherwise
        # merge multiple objects and silently choose the last conflicting value.
        text = response.strip()
        fence = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
        if fence:
            text = fence.group(1)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate JSON key')
                result[key] = value
            return result
        try:
            value = json.loads(text, object_pairs_hook=unique)
            if not isinstance(value, dict) or set(value) != {
                    'entailment', 'slot_fit', 'rationale', 'slot_fit_rationale'}:
                return None
        except (ValueError, TypeError):
            return None
        return super()._parse_response(json.dumps(value))

    def _default_nli_call(self, prompt, image_paths=None):
        return super()._default_nli_call(repair_prompt(prompt, image_paths), image_paths)


class RepairRecording:
    """Read old VALID receipts; write only into the separately bound repair store."""
    def __init__(self, original, repairs, allowed_rejections):
        self.original = original
        self.repairs = repairs
        self.allowed_rejections = dict(allowed_rejections)
        self.root = repairs.root

    def judge(self, prompt, images, dispatch, parse):
        request = {"prompt": prompt, "images": [
            {"path": str(Path(p).resolve()), "sha256": file_hash(p)} for p in images]}
        old = self.original.root / "requests" / (digest(request) + ".json")
        if old.exists():
            record = self.original._read(old)
            if record["request"] != request:
                raise RuntimeError("L1 original request changed")
            if record["state"] == "VALID":
                if NLIJudge.__new__(NLIJudge)._parse_response(record["raw_response"]) != record["parsed"]:
                    raise RuntimeError("L1 original parser changed")
                return dict(record["parsed"]), True
            if (record["state"] != "REJECTED" or
                    self.allowed_rejections.get(old.name) != file_hash(old)):
                raise RuntimeError("L1 request is not authorized for repair")
        return self.repairs.judge(repair_prompt(prompt, images), images, dispatch, parse)
