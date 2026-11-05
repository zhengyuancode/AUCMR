"""Validate source locators and real construction/review provenance before freezing."""
import json
import re
import unicodedata
from pathlib import Path

from eval.agent_evaluation.common import digest, file_hash, read_json
from . import VERSION

def require(ok, message):
    if not ok:
        raise ValueError(message)

def normalized(text):
    text = unicodedata.normalize('NFKC', text).replace('\xad', '').replace('\u200b', '')
    text = text.translate(str.maketrans({'‘':"'",'’':"'",'“':'"','”':'"'}))
    text = re.sub(r'(?<=\w)-\s*\n(?=\w)', '', text)
    return ''.join(text.split()).casefold()

def quote_in_page(quote, page):
    # PDF discretionary hyphens may be rendered visibly or omitted when copied.
    # Only source U+00AD gets this equivalence; meaningful ASCII hyphens remain.
    needle = normalized(quote)
    return needle in normalized(page) or needle in normalized(page.replace('\xad', '-'))

def confined(root, rel):
    root = Path(root).resolve()
    p = (root / rel).resolve()
    require(p.is_relative_to(root), 'path outside allowed root')
    return p

def source_context(bundle, target_id):
    bundle = Path(bundle)
    allow = read_json(bundle/'allowlist.json')['files']
    for name, expected in allow.items():
        require(file_hash(confined(bundle,name)) == expected, 'source-only bundle changed: '+name)
    data = read_json(bundle/'targets.json')
    target = next((x for x in data['targets'] if x['target_id']==target_id),None)
    require(target is not None, 'unknown target')
    source = next(x for x in data['sources'] if x['project_id']==target['project_id'])
    require(source['pdf'] in allow and allow[source['pdf']]==source['pdf_sha256'], 'source binding not in allowlist')
    import fitz
    with fitz.open(confined(bundle,source['pdf'])) as pdf:
        require(len(pdf)==source['pages'], 'PDF page count mismatch')
        pages = {i+1:p.get_text() for i,p in enumerate(pdf)}
    return {'bundle':bundle,'allowlist':allow,'target':target,'source':source,'pages':pages}

def check_evidence(items, context, entries):
    require(isinstance(items,list) and bool(items), 'evidence required')
    source = context['source']
    checks=[]
    for e in items:
        page=e.get('pdf_page_1based')
        require(type(page) is int and 1<=page<=source['pages'], 'PDF page out of bounds')
        matching=[x for x in entries if x.get('operation')=='page' and x.get('source_id')==source['source_id'] and x.get('source_sha256')==source['pdf_sha256'] and x.get('pdf_page')==page]
        require(bool(matching), 'source page not actually read')
        quote=e.get('quote','');visual=e.get('visual_region_description','')
        require(isinstance(quote,str) and isinstance(visual,str) and (quote.strip() or visual.strip()),'empty source locator')
        images=[x for x in matching if x.get('image_sha256')]
        mode='IMAGE_REGION_REQUIRES_SEMANTIC_REVIEW';warnings=[]
        advisory=context.get('quote_locator_policy')=='ADVISORY_AGENT_QUOTE'
        if quote.strip():
            text_match=quote_in_page(quote, context['pages'][page])
            image_transcription=bool(visual.strip() and images and normalized(quote) in normalized(visual))
            if not (text_match or image_transcription) and advisory:
                mode='UNVERIFIED_AGENT_QUOTE';warnings.append('Agent quote did not match the source text; preserved verbatim, not counted as verified evidence or semantic support.')
            else:
                require(text_match or image_transcription, f'quote not found on PDF page {page}: {quote[:90]}. This is a TEXT extraction check. If this is a label visible only in an already received page image, use quote="" and give its precise visible region in visual_region_description on that image page. Otherwise copy a contiguous excerpt from the supplied PDF text. Do not merely change page numbers, invent an excerpt, or remove a requirement to hide the error.')
                mode='PDF_TEXT_MATCH' if text_match else 'IMAGE_TRANSCRIPTION_REQUIRES_SEMANTIC_REVIEW'
        if visual.strip():
            if not images and advisory:
                warnings.append('Visual region was not delivered as an image; region remains unverified.')
                if not quote.strip():mode='UNVERIFIED_VISUAL_LOCATOR'
            else:require(bool(images),f'visual evidence page not read as image: {page}. Read that page as image before citing visual evidence; for a supported text-only excerpt keep visual_region_description="".')
        row={'pdf_page_1based':page,'mode':mode,'image_sha256':images[0]['image_sha256'] if images and mode not in {'PDF_TEXT_MATCH','UNVERIFIED_AGENT_QUOTE'} else None}
        if warnings:row['warning']=' '.join(warnings)
        checks.append(row)
    return checks

def locator_warnings(checks,path=()):
    rows=[]
    if isinstance(checks,dict):
        if checks.get('warning'):rows.append({'path':list(path),**checks})
        else:
            for k,v in checks.items():rows+=locator_warnings(v,path+(k,))
    elif isinstance(checks,list):
        for i,v in enumerate(checks):rows+=locator_warnings(v,path+(i,))
    return rows

def context_for_result(context,result):
    return {**context,'quote_locator_policy':'ADVISORY_AGENT_QUOTE','review_stage':result.get('stage')} if result.get('quote_locator_policy')=='ADVISORY_AGENT_QUOTE' else context

def validate_draft(body, context, entries, candidates=None):
    require(isinstance(body,dict), 'draft must be object')
    scope=body.get('scope',{})
    for k in ('goal','actor','start_boundary','success_boundary'):
        require(isinstance(scope.get(k),str) and scope[k].strip(),'scope field required: '+k)
    require(scope['actor']==context['target']['actor'],'actor changed from target input')
    require(isinstance(scope.get('in_scope'),list) and isinstance(scope.get('out_of_scope'),list),'scope boundary lists required')
    checks={'scope':check_evidence(scope.get('evidence'),context,entries),'requirements':{},'decisions':{}}
    advisory_review=context.get('quote_locator_policy')=='ADVISORY_AGENT_QUOTE' and context.get('review_stage')=='B_COMPARE'
    def lineage(condition,message):
        if condition:return
        if not advisory_review:require(False,message)
        checks.setdefault('lineage',[]).append({'mode':'INTERMEDIATE_REVIEW_LINEAGE_UNRESOLVED','warning':message+'. Original mappings retained for final Agent adjudication; no edge was invented or silently corrected.'})
    rows=body.get('requirements')
    require(isinstance(rows,list),'requirements array required')
    ids=[r.get('local_id') for r in rows]
    require(all(isinstance(x,str) and x.strip() for x in ids) and len(set(ids))==len(ids),'unique nonempty local IDs required')
    final=candidates is not None
    for r in rows:
        require(isinstance(r.get('text'),str) and r['text'].strip(),'requirement text required')
        require(r.get('status') in (('CONFIRMED','REFERENCE_UNRESOLVED') if final else ('PROPOSED','REFERENCE_UNRESOLVED')),'invalid reference item status')
        require('conditions' in r and (r['conditions'] is None or isinstance(r['conditions'],str)),'conditions must be explicit string/null')
        uncited_proposal=r['status']=='PROPOSED' and context.get('review_stage') in {'A_BUILD','B_BLIND','C_BLIND'}
        if context.get('quote_locator_policy')=='ADVISORY_AGENT_QUOTE' and (r['status']=='REFERENCE_UNRESOLVED' or uncited_proposal) and r.get('evidence')==[]:
            source=context['source']
            require(any(e.get('operation')=='page' and e.get('source_id')==source['source_id'] and e.get('source_sha256')==source['pdf_sha256'] for e in entries),'source context not actually read for unresolved item')
            mode='UNCITED_PROPOSED_REQUIREMENT' if uncited_proposal else 'UNCITED_REFERENCE_UNRESOLVED'
            checks['requirements'][r['local_id']]=[{'mode':mode,'source_id':source['source_id'],'source_sha256':source['pdf_sha256'],'warning':'Agent supplied no citation for this '+r['status']+' item. Original claim/status retained, source support remains unverified; no evidence was invented. A proposal is not a confirmed final requirement.'}]
        else:checks['requirements'][r['local_id']]=check_evidence(r.get('evidence'),context,entries)
        if final:
            require(isinstance(r.get('origin_ids'),list) and set(r['origin_ids'])<=set(candidates),'unknown/missing candidate origins')
            if not r['origin_ids'] and context.get('prior_review_additions'):
                # B_COMPARE can discover a source requirement absent from both
                # blind inventories. C's exact adoption has real B-review
                # provenance, rather than a nonexistent A/B/C candidate edge.
                additions=[x for x in context['prior_review_additions']
                           if all(x.get(k)==r.get(k) for k in ('text','conditions','status'))]
                require(len(additions)==1,'unbound or ambiguous review addition: '+r['local_id'])
                require(bool(additions[0].get('evidence')),'prior review addition lacks source evidence')
                checks.setdefault('review_additions',{})[r['local_id']]={
                    'mode':'EXACT_PRIOR_B_REVIEW_ADDITION','review_local_id':additions[0]['local_id'],
                    'candidate_origin_ids':[], 'independently_adopted_by':'C_FINAL'}
            else:lineage(bool(r['origin_ids']),'missing candidate origins for '+r['local_id'])
            if r['origin_ids'] and all(x.startswith('C:') for x in r['origin_ids']):
                require(r['status']=='REFERENCE_UNRESOLVED','C-only addition has not been independently reviewed')
    if 'unresolved_issues' not in body and context.get('quote_locator_policy')=='ADVISORY_AGENT_QUOTE' and context.get('review_stage') in {'A_BUILD','B_BLIND','C_BLIND'}:
        checks.setdefault('metadata',[]).append({'mode':'AGENT_ISSUE_LIST_NOT_PROVIDED','warning':'Blind proposal omitted unresolved_issues. Missing metadata is preserved; this is not a declaration of no unresolved issues.'})
    else:require(isinstance(body.get('unresolved_issues'),list),'unresolved issue list required')
    if final:
        decisions=body.get('candidate_decisions',[])
        require(len(decisions)==len(candidates) and {d.get('candidate_id') for d in decisions}==set(candidates),'all candidates must have exactly one disposition')
        byid={r['local_id']:r for r in rows}
        for d in decisions:
            require(d.get('decision') in ('INCLUDED','EXCLUDED','UNRESOLVED') and isinstance(d.get('reason'),str) and d['reason'].strip(),'candidate disposition/rationale missing')
            checks['decisions'][d['candidate_id']]=check_evidence(d.get('evidence'),context,entries)
            dest=d.get('final_ids')
            require(isinstance(dest,list) and len(set(dest))==len(dest) and set(dest)<=set(ids),'invalid final ID mapping')
            if d['decision']=='EXCLUDED':
                require(not dest,'excluded candidate has output mapping')
            else:
                require(bool(dest),'retained/unresolved candidate disappeared')
                for rid in dest:
                    lineage(d['candidate_id'] in byid[rid]['origin_ids'],'candidate lineage mismatch: '+d['candidate_id']+' / '+rid)
                    if d['decision']=='UNRESOLVED':
                        require(byid[rid]['status']=='REFERENCE_UNRESOLVED','unresolved review promoted to CONFIRMED')
        for r in rows:
            for origin in r['origin_ids']:
                d=next(d for d in decisions if d['candidate_id']==origin)
                lineage(r['local_id'] in d['final_ids'],'origin is not mapped back to final item: '+origin+' / '+r['local_id'])
    return checks

def proof(root, item):
    result_path=confined(root,item['result_path']);log_path=confined(root,item['access_log_path'])
    require(file_hash(result_path)==item['result_sha256'],'task result hash mismatch')
    require(file_hash(log_path)==item['access_log_sha256'],'access log hash mismatch')
    result=read_json(result_path);entries=[json.loads(x) for x in log_path.read_text().splitlines() if x]
    require(result['status']=='OK' and result['session_id']==item['agent_session_id'],'successful task/session proof missing')
    require(all(e['session_id']==result['session_id'] for e in entries),'source log contains another session')
    input_path=confined(root,result['input_path'])
    require(file_hash(input_path)==result['input_sha256'],'task input changed')
    if result.get('quote_locator_policy'):
        config_path=Path(root)/'config.snapshot.json'
        require(result['quote_locator_policy']=='ADVISORY_AGENT_QUOTE' and config_path.exists() and file_hash(config_path)==result.get('quote_locator_policy_config_sha256'),'quote policy is not bound to original batch config')
        config=read_json(config_path)
        require(config.get('source_only') is True and 'previous acceptance rules advisory' in config.get('policy','').lower(),'advisory quote policy not authorized for this run')
    request=read_json(confined(root,result['receipt_path']))
    require(file_hash(confined(root,result['receipt_path']))==result['receipt_sha256'],'response receipt changed')
    raw_body=request['result'].get('body')
    from .response import decode, tools_already_delivered
    parsed,operations=decode(raw_body)
    require(result.get('response_normalization',[])==operations,'response normalization proof mismatch')
    if 'tool_calls' in parsed:
        messages_path=result_path.parent/('messages-'+request_path_attempt(result['receipt_path'])+'.json')
        require(tools_already_delivered(parsed['tool_calls'],read_json(messages_path),entries),'mixed-response tools were not delivered before final draft')
        require(result.get('tool_calls_already_delivered')==parsed['tool_calls'],'mixed-response tool provenance mismatch')
    if 'locator_repair_path' in result:
        from .locator_repairs import apply
        repair_path=confined(root,result['locator_repair_path'])
        require(file_hash(repair_path)==result.get('locator_repair_sha256'),'locator repair hash mismatch')
        parsed['draft']=apply(parsed['draft'],read_json(repair_path),input_sha256=result['input_sha256'],receipt_sha256=result['receipt_sha256'],source_sha256=read_json(input_path)['source_sha256'])
    require(parsed.get('draft')==result['draft'],'task result differs from actual response')
    return result,entries,read_json(input_path)

def request_path_attempt(path):
    name=Path(path).name
    require(re.fullmatch(r'request-[1-9][0-9]*\.json',name),'invalid receipt filename')
    return name[len('request-'):-len('.json')]

def validate_frozen(ref, bundle, run_root):
    require(ref.get('protocol_version')==VERSION and ref.get('freeze_status')=='FROZEN','wrong protocol/unfrozen reference')
    require(type(ref.get('version')) is int and ref['version']>=1,'invalid reference version')
    require(ref.get('content_sha256')==digest({k:v for k,v in ref.items() if k!='content_sha256'}),'reference content hash mismatch')
    ctx=source_context(bundle,ref['target_id'])
    require(ref['source_sha256']==ctx['source']['pdf_sha256'],'reference target/source mismatch')
    results={};logs={};inputs={}
    for stage,record in ref['provenance'].items():
        results[stage],logs[stage],inputs[stage]=proof(run_root,record)
    require(set(results)=={'A_BUILD','B_BLIND','B_COMPARE','C_BLIND','C_FINAL'},'complete independent construction/review records required')
    require(len({r['session_id'] for r in results.values()})==5,'sessions are not independent')
    require(results['B_BLIND']['completed']<results['B_COMPARE']['started'],'B blind work was not completed before compare')
    require(results['C_BLIND']['completed']<results['C_FINAL']['started'],'C blind work was not completed before adjudication')
    for stage in ('A_BUILD','B_BLIND','C_BLIND'):
        require(inputs[stage]['candidate_materials']=={},'blind source task exposed proposals')
        validate_draft(results[stage]['draft'],context_for_result(ctx,results[stage]),logs[stage])
    expected_ab={'A':results['A_BUILD']['draft'],'B':results['B_BLIND']['draft']}
    require(inputs['B_COMPARE']['candidate_materials']==expected_ab,'B compare does not bind to actual blind/build drafts')
    require(inputs['C_FINAL']['candidate_materials']=={**expected_ab,'B_review':results['B_COMPARE']['draft'],'C':results['C_BLIND']['draft']},'adjudication inputs not bound to prior tasks')
    ab={prefix+':'+r['local_id']:r for prefix,body in expected_ab.items() for r in body['requirements']}
    validate_draft(results['B_COMPARE']['draft'],context_for_result(ctx,results['B_COMPARE']),logs['B_COMPARE'],ab)
    candidates={**ab,**{'C:'+r['local_id']:r for r in results['C_BLIND']['draft']['requirements']}}
    from .recovery_policy import review_context
    final_ctx=review_context(context_for_result(ctx,results['C_FINAL']),inputs['C_FINAL']['candidate_materials'],'C_FINAL')
    final=results['C_FINAL']['draft'];validate_draft(final,final_ctx,logs['C_FINAL'],candidates)
    require(ref['scope']==final['scope'] and ref['candidate_decisions']==final['candidate_decisions'],'frozen scope/dispositions altered')
    require(len(ref['requirements'])==len(final['requirements']),'frozen requirements omitted or added')
    seen=set()
    for i,(r,original) in enumerate(zip(ref['requirements'],final['requirements']),1):
        require(r['requirement_id']==ref['target_id']+f'-R{i:04d}' and r['requirement_id'] not in seen,'unstable or duplicate reference ID')
        seen.add(r['requirement_id'])
        require({k:v for k,v in r.items() if k not in ('requirement_id','source_sha256')}==original,'frozen requirement differs from adjudicated response')
        require(r['source_sha256']==ref['source_sha256'],'item source version changed')
    return ref['requirements']
