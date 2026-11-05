"""Explicitly authorized claim-ID citations and compact recovery of saved failures."""
import copy
import json
from collections import defaultdict

from eval.agent_evaluation.common import canonical,digest,file_hash,read_json
from eval.l3_reference.response import pairs
from eval.l3_reference.validation import require
from .contract import decode,validate
from .inputs import locked

POLICY='technical-recovery-policy-20261105.json'
DECODER='USER_AUTHORIZED_CLAIM_ID_CITATIONS_V1'

def permit(root,key,folder,recovery):
    path=root/POLICY
    if not path.exists():return None
    policy=read_json(path)
    require(policy.get('schema')==DECODER and policy.get('status')=='USER_AUTHORIZED','invalid technical recovery policy')
    item=policy.get('tasks',{}).get(key)
    if item is None:return None
    require(file_hash(folder/'input.json')==item['input_sha256'],'technical recovery input hash mismatch')
    receipt=(root/item['receipt_path']).resolve()
    require(receipt==recovery.root.resolve()/'request-4.json','technical recovery receipt outside task')
    require(file_hash(receipt)==item['receipt_sha256'],'technical recovery receipt hash mismatch')
    saved=read_json(receipt)
    require(saved['status']=='OK' and saved['result'].get('cost_cny') is not None and not saved['result'].get('request_budget_violation'),'unknown or transport failure cannot be recovered')
    require(item.get('maximum_attempt')==6,'technical recovery preserves six-request ceiling')
    locked(folder/'technical-recovery-authorization.json',{'policy_sha256':file_hash(path),'item':item})
    return item

def decode_citations(raw,artifact,requirements,compact=False):
    """Resolve the model-selected ID to its entire actual claim; retain labels."""
    if compact:
        data=json.loads(raw,object_pairs_hook=pairs)
        require(isinstance(data,dict) and set(data)<= {'judgments','reference_issues'} and 'judgments' in data,'compact judgments object required')
        body={'judgments':[],'reference_issues':data.get('reference_issues',[])};ops=[]
        for row in data['judgments']:
            require(isinstance(row,dict) and set(row)=={'requirement_id','label','claim_ids','rationale'},'compact row keys required')
            require(isinstance(row['claim_ids'],list) and all(isinstance(x,str) for x in row['claim_ids']),'compact claim IDs required')
            body['judgments'].append({'requirement_id':row['requirement_id'],'label':row['label'],'rationale':row['rationale'],
                'technical_status':'OK','reviewed_complete_output':True,
                'matches':[{'claim_id':cid} for cid in dict.fromkeys(row['claim_ids'])]})
        ops.append({'kind':'EXPAND_DECLARED_COMPACT_SCHEMA','complete_output_review_required_by_prompt':True})
    else:body,ops=decode(raw,artifact,requirements)
    body=copy.deepcopy(body);index=defaultdict(list)
    for slot,value in artifact['slots'].items():
        for claim in value['claims']:index[claim['claim_id']].append((slot,claim['content']))
    for row in body.get('judgments',[]):
        for match in row.get('matches',[]):
            entries=index.get(match.get('claim_id'),[])
            require(len(entries)==1,'claim citation must identify exactly one existing claim')
            slot,text=entries[0];original=copy.deepcopy(match)
            match.update(slot=slot,quote=text)
            if match!=original:ops.append({'kind':'RESOLVE_USER_AUTHORIZED_WHOLE_CLAIM_CITATION','requirement_id':row['requirement_id'],'original_locator':original,'resolved_locator':copy.deepcopy(match)})
    validate(body,requirements,artifact)
    return body,ops

def recover(root,folder,key,item,config,artifact,requirements,recovery,transport,progress,stop_error):
    record=read_json(folder/'input.json');fingerprint=digest(record);task_id='coverage/'+key
    role=record['role'];phase=record['phase'];binding=config['models'][role]
    def save(body,ops,attempt,compact):
        receipt=recovery.root/f'request-{attempt}.json'
        locked(folder/'result.json',{'input_sha256':file_hash(folder/'input.json'),'receipt_path':str(receipt.relative_to(root)),
            'receipt_sha256':file_hash(receipt),'response':body,'normalization':ops,'decoder':DECODER,'compact':compact,
            'technical_recovery_policy_sha256':file_hash(root/POLICY)})
        progress.event('TASK_COMPLETE',role=role,phase=phase,task=key)
        return body
    old=read_json(root/item['receipt_path'])['result']
    if old.get('finish_reason')=='stop':
        try:body,ops=decode_citations(old['body'],artifact,requirements)
        except (ValueError,KeyError,TypeError):pass
        else:return save(body,ops,4,False)
    # A fresh compact request avoids repeating a malformed/truncated assistant
    # transcript. The full output, source pages, requirements and label rules stay.
    original=read_json(folder/'messages-1.json')
    system=original[0]['content']+'\nUSER-AUTHORIZED TECHNICAL RECOVERY: The following compact response schema supersedes only the earlier response formatting and literal-quotation requirements. Review the COMPLETE generated output against EVERY assigned requirement. Keep all substantive coverage rules, including FULL only when all required aspects are expressed. Return one JSON object with judgments rows {"requirement_id":"assigned ID","label":"FULL|PARTIAL|NOT_COVERED|CONTRADICTED|UNRESOLVED","claim_ids":["exact existing CL-ID"],"rationale":"at most 35 words"} and optional reference_issues. Each selected claim ID cites its ENTIRE actual output claim, which the executor resolves verbatim. Never invent IDs. FULL/PARTIAL/CONTRADICTED require evidence IDs. Include every assigned requirement exactly once. No quote fields, no repeated lists, no tool requests, no markdown. Returning rows declares that you reviewed the complete output. Distinguish genuine semantic/source uncertainty from a technical inability to finish.\n'
    messages=[{'role':'system','content':system},copy.deepcopy(original[1])]
    for attempt in (5,6):
        if progress.stop.is_set():raise stop_error('shared stop requested')
        require(attempt<=config['max_requests_per_task'],'six-request ceiling')
        locked(folder/f'messages-{attempt}.json',messages)
        request_id=digest(['L3_COVERAGE',str(root),task_id,fingerprint,attempt])
        progress.event('REQUEST_START',role=role,phase=phase,task=key,attempt=attempt)
        result,error,status,_=recovery.request(request_id,binding,messages,{'task_id':task_id,'session_id':key,'role':role},attempt,None)
        call={'request_id':request_id,'role':role,'phase':phase,'target_id':record['target_id'],'artifact_id':artifact['artifact_id'],
            'status':status,'error':error,**{k:(result or {}).get(k) for k in ('response_model','finish_reason','raw_usage','cost_cny','cost_basis')}}
        locked(folder/f'call-{attempt}.json',call);progress.event('REQUEST_SAVED',role=role,phase=phase,task=key,attempt=attempt)
        if status!='OK' or result is None or result.get('cost_cny') is None or result.get('request_budget_violation'):
            raise stop_error('new technical recovery transport/accounting failure preserved; no retry')
        try:
            require(result.get('finish_reason')=='stop' and result.get('response_model'),'incomplete/truncated compact response')
            body,ops=decode_citations(result['body'],artifact,requirements,True)
        except (ValueError,KeyError,TypeError) as exc:
            locked(folder/f'validation-{attempt}.json',{'error':str(exc)})
            if attempt==6:raise
            messages=[{'role':'system','content':system},copy.deepcopy(original[1]),
                      {'role':'user','content':'The previous compact response failed validation: '+str(exc)+'. Re-evaluate all assigned IDs in the required compact schema. Do not copy a previous response. Keep rationales short and cite exact existing IDs.'}]
            continue
        return save(body,ops,attempt,True)
