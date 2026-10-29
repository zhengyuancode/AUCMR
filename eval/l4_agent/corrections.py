"""Bounded replacement of explicitly identified checks, never append or dedupe."""
from copy import deepcopy
import json
import re

from .contract import (schema, obj, shape_errors, check_coverage, evidence_catalog,
                       assessment_rules, pcs_rules)
from .records import parse_response


def plan_correction(raw, stage, artifact, accesses):
    if stage == "prc":
        return None
    try:
        value = parse_response(raw)
        expected = [c["claim_id"] for c in artifact["slots"][stage]["claims"]]
        checks = value["checks"]
        if not isinstance(checks, list) or any(not isinstance(c, dict) for c in checks):
            return None
        if any(check_coverage(value, expected).values()):
            # Ambiguous duplicate judgments must not be silently selected/deduped.
            return None
        errors = shape_errors(value, schema(stage, artifact, evidence_catalog(accesses)))
        bad_ids = set()
        for error in errors:
            path = error["path"].split("/")
            if len(path) >= 3 and path[1] == "checks" and path[2].isdigit():
                bad_ids.add(checks[int(path[2])]["claim_id"])
        if not errors:
            # A deeper evidence validation failed: require explicit replacement
            # of all checks rather than guessing which judgment to edit.
            bad_ids = set(expected)
        return {"base_response": raw, "base_accesses": deepcopy(accesses),
                "target_claim_ids": [cid for cid in expected if cid in bad_ids]}
    except (ValueError, TypeError, KeyError, IndexError):
        return None


def retained_checks(plan):
    return [deepcopy(c) for c in parse_response(plan["base_response"])["checks"]
            if c["claim_id"] not in plan["target_claim_ids"]]


def correction_schema(plan, stage, artifact, accesses):
    full = schema(stage, artifact, evidence_catalog(accesses))
    check = deepcopy(full["properties"]["checks"]["items"])
    check["properties"].pop("claim_id")
    check["required"].remove("claim_id")
    assessment = obj({k: deepcopy(v) for k, v in full["properties"].items() if k != "checks"})
    assessment_only = not plan["target_claim_ids"]
    result = assessment if assessment_only else obj({
        "assessment": assessment,
        "replacements": obj({cid: deepcopy(check) for cid in plan["target_claim_ids"]})})
    retained = retained_checks(plan)
    def wrap(value):
        return value if assessment_only else {"properties": {"assessment": value}}
    any_unresolved = True if any(c["finding"] == "UNRESOLVED" for c in retained) else (
        {"anyOf": [{"properties": {"replacements": {"properties": {
            cid: {"properties": {"finding": {"const": "UNRESOLVED"}}}}}}}
            for cid in plan["target_claim_ids"]]} if plan["target_claim_ids"] else False)
    all_supported = False if any(c["finding"] != "SUPPORTED" for c in retained) else {
        "properties": {"replacements": {"additionalProperties": {
            "properties": {"finding": {"const": "SUPPORTED"}}}}}}
    result["allOf"] = assessment_rules(wrap) + pcs_rules(any_unresolved, all_supported, wrap)
    return result


def merge_correction(raw_patch, plan, stage, artifact, accesses):
    value = parse_response(raw_patch)
    errors = shape_errors(value, correction_schema(plan, stage, artifact, accesses))
    if errors:
        raise ValueError(json.dumps(errors, ensure_ascii=False))
    base = parse_response(plan["base_response"])
    targets = set(plan["target_claim_ids"])
    if not targets:
        return {**deepcopy(value), "checks": deepcopy(base["checks"])}
    # Existing order and number are fixed; only named invalid checks are replaced.
    checks = [{"claim_id": c["claim_id"], **deepcopy(value["replacements"][c["claim_id"]])}
              if c["claim_id"] in targets else deepcopy(c) for c in base["checks"]]
    return {**deepcopy(value["assessment"]), "checks": checks}


def discovery_guidance(accesses):
    read = {(e["arguments"].get("source_id"), e["arguments"].get("pdf_page"))
            for e in accesses if e["operation"] == "source_page" and e["result"]["status"] == "OK"}
    found = {}
    for entry in accesses:
        if entry["operation"] != "source_search":
            continue
        sid = entry["arguments"].get("source_id")
        for hit in entry["result"].get("hits", []):
            page = hit.get("pdf_page")
            if type(page) is int and (sid, page) not in read:
                found[(sid, page)] = {"source_id": sid, "pdf_page": page,
                    "citable": False, "required_read": {"name": "source_page",
                    "arguments": {"source_id": sid, "pdf_page": page, "image": False}}}
    return list(found.values())


def correction_reads(plan):
    """Read only explicitly named pages in the cited, recorded search hits.

    This locates evidence; it neither validates the claim nor chooses a quote.
    Ambiguous source identities and unrecorded pages are never guessed.
    """
    accesses = plan["base_accesses"]
    searches = {e["access_id"]: e for e in accesses
                if e["operation"] == "source_search" and e["result"]["status"] == "OK"}
    unread = {(g["source_id"], g["pdf_page"]): g["required_read"]
              for g in discovery_guidance(accesses)}
    reads = {}
    for check in parse_response(plan["base_response"])["checks"]:
        if check["claim_id"] not in plan["target_claim_ids"]:
            continue
        pages = {int(a or b) for a, b in re.findall(
            r"\bpage\s+(\d+)\b|第\s*(\d+)\s*页", check.get("rationale", ""), re.I)}
        cited = []
        for evidence in check.get("evidence", []):
            identifier = evidence.get("evidence_id")
            if not isinstance(identifier, str):
                continue
            # Locate the search that was mistakenly used as a chunk source.
            # This is discovery only: the original citation stays INVALID.
            # Require the exact access grammar, recorded search and explicit
            # unique hit page below; never guess another access or page.
            match = re.fullmatch(r"(L4-ACCESS-[1-9][0-9]*):T(?:0|[1-9][0-9]*)", identifier)
            page_match = re.fullmatch(r"(L4-ACCESS-[1-9][0-9]*):([1-9][0-9]*)", identifier)
            # A mistaken access:page locator is only usable when the model
            # also names that exact page in its rationale. This still does
            # not make it a citation; require the recorded hit below.
            if page_match and int(page_match.group(2)) not in pages:
                continue
            search_id = (match or page_match).group(1) if (match or page_match) else identifier
            if search_id in searches:
                cited.append(searches[search_id])
        for page in sorted(pages):
            sources = {e["arguments"]["source_id"] for e in cited
                       if any(h.get("pdf_page") == page for h in e["result"].get("hits", []))}
            if len(sources) == 1:
                key = (next(iter(sources)), page)
                if key in unread:
                    reads[key] = unread[key]
    return list(reads.values())


def acquire_correction_evidence(plan, tools):
    """Use the unchanged audited tool limits; no model calls or retries."""
    results = []
    for call in correction_reads(plan):
        # Bound deterministic reads by the same per-round opportunity as tools.
        if len(results) >= tools.policy["tools_per_round"]:
            break
        result = tools.invoke(call["name"], call["arguments"])
        results.append({"call": deepcopy(call), "result": deepcopy(result)})
        if result["status"] != "OK":
            break
    return results
