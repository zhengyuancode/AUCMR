"""Explicit receipt-bound failed-stage recovery, never an automatic retry policy."""
import json
from eval.agent_evaluation.common import file_hash, read_json
from .validation import require


def provider_error(response):
    raw=(response or {}).get('raw_response') or {}
    if not isinstance(raw,dict):return None
    status=raw.get('http_status')
    if not isinstance(status,int) or status<400:return None
    try:error=json.loads(raw.get('error_response_text','')).get('error',{})
    except (TypeError,ValueError,AttributeError):error={}
    code=error.get('code') or error.get('type') if isinstance(error,dict) else None
    # Use a whitelist, never copy arbitrary provider text into diagnostics.
    code=code if code in {'Arrearage','InvalidApiKey','AuthenticationError','InsufficientQuota'} else 'HTTP_ERROR'
    return {'http_status':status,'code':code}


def authorized(root, input_path, receipt_path, attempt, kind):
    path=root/'failed-stage-recovery.json'
    if not path.exists():return False
    plan=read_json(path)
    require(plan.get('schema_version')=='AUCMR_L3_FAILED_STAGE_RECOVERY_1.0','invalid recovery authorization')
    require(bool(plan.get('authorization')),'recovery authorization missing')
    rule=plan.get('tasks',{}).get(str(input_path.parent.relative_to(root)))
    if rule is None:return False
    require(file_hash(input_path)==rule['input_sha256'],'recovery input hash mismatch')
    if attempt!=rule['failed_attempt'] or kind!=rule['kind']:return False
    require(str(receipt_path.relative_to(root))==rule['receipt_path'] and file_hash(receipt_path)==rule['receipt_sha256'],'recovery receipt hash mismatch')
    # Exactly one corrective response may be requested for this paid failure.
    return True


def review_context(context, materials, stage):
    """Bind actual B-review additions, not fabricated A/B/C candidate origins."""
    if stage!='C_FINAL' or 'B_review' not in materials:return context
    return {**context,'prior_review_additions':[
        r for r in materials['B_review'].get('requirements',[]) if r.get('origin_ids')==[]]}
