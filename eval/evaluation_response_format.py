"""Versioned removal of a whole JSON code fence; never repair JSON content."""
import hashlib
import json

FORMAT_VERSION = "whole_json_fence_v1"


def normalize_json_envelope(raw):
    """Return text and audit metadata, leaving JSON/schema validation to callers."""
    body = raw
    removed = False
    if isinstance(raw, str):
        lines = raw.strip().split("\n")
        if (len(lines) >= 3 and lines[0].rstrip("\r") in ("```json", "```")
                and lines[-1] == "```"
                and not any(line.lstrip().startswith("```") for line in lines[1:-1])):
            # Preserve inner bytes, including duplicate keys and nonfinite values,
            # so downstream strict validators can still reject them.
            stripped = raw.strip()
            body = stripped[stripped.index("\n") + 1:stripped.rfind("\n")]
            removed = True
        try:
            json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            raw_valid = True
        except (ValueError, TypeError):
            raw_valid = False
    else:
        raw_valid = None
    return body, {"version": FORMAT_VERSION, "fence_removed": removed,
                  "raw_json_valid": raw_valid,
                  "normalized_text_sha256": hashlib.sha256(body.encode()).hexdigest()
                  if isinstance(body, str) else None}
