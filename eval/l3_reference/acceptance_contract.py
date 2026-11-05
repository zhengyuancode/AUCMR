"""Rule-bound acceptance: findings first, lossless patches, one targeted recheck."""
import copy
import re
from .validation import require, check_evidence

RULES={f'R{i}' for i in range(1,7)}
STATES={'PASS','DEFECT','UNRESOLVED','SUGGESTION'}
FINAL={'ACCEPTED','ACCEPTED_WITH_SOURCE_UNCERTAINTY','NEEDS_REVISION','NEEDS_SCOPE_DECISION','INCOMPLETE'}

def unique(rows,key):
    values=[r.get(key) for r in rows]
    require(all(isinstance(v,str) and v for v in values) and len(values)==len(set(values)), 'missing/duplicate '+key)
    return set(values)

def evidence(row,ctx,entries):
    check_evidence(row.get('source_locations'),ctx,entries)

def finding(f,old_ids,ctx,entries):
    require(f.get('status') in STATES and f.get('rule_id') in RULES,'invalid finding status/rule')
    require(isinstance(f.get('requirement_ids'),list) and set(f['requirement_ids'])<=old_ids,'unknown finding requirement')
    require(f.get('kind') in {'REFERENCE_ERROR','SOURCE_UNCERTAINTY','SCOPE_UNCERTAINTY','UNIT_UNCERTAINTY','SOURCE_NOT_SPECIFIED','PREFERENCE','INCOMPLETE'},'finding kind required')
    for k in ('source_evidence','observed_difference','effect_on_coverage_judgment_or_denominator','minimal_correction'):
        require(isinstance(f.get(k),str) and f[k].strip(),'finding field required: '+k)
    if f['status'] in {'DEFECT','UNRESOLVED'}:evidence(f,ctx,entries)
    if f['status']=='DEFECT' and f['rule_id']=='R4':require(bool(f.get('independence_example')),'R4 needs independent-satisfaction counterexample')

def validate_audit(a,ref,ctx,entries):
    require(a.get('target_id')==ref['target_id'],'audit target mismatch')
    ids={r['requirement_id'] for r in ref['requirements']}
    rows=a.get('items',[]);require(unique(rows,'requirement_id')==ids,'audit must cover every old requirement exactly once')
    findings=a.get('findings',[]);fids=unique(findings,'finding_id');byid={f['finding_id']:f for f in findings}
    for f in findings:finding(f,ids,ctx,entries)
    for row in rows:
        require(set(row.get('rules',{}))==RULES-{'R6'} and set(row['rules'].values())<=STATES,'each item needs R1-R5')
        require(bool(row.get('rationale')) and set(row.get('finding_ids',[]))<=fids,'audit rationale/finding link missing')
        for rule,status in row['rules'].items():
            if status in {'DEFECT','UNRESOLVED'}:
                # One documented uncertainty may affect multiple rules. Preserve
                # every affected rule; confirmed defects still need exact rule IDs.
                require(any((byid[f]['rule_id']==rule or status=='UNRESOLVED') and byid[f]['status']==status and row['requirement_id'] in byid[f]['requirement_ids'] for f in row['finding_ids']),'item finding not substantiated')
    require(isinstance(a.get('coverage'),list) and a['coverage'],'R6 source coverage inventory missing')
    for row in a['coverage']:
        require(row.get('disposition') in {'MAPPED','EXCLUDED','UNRESOLVED','NO_OBLIGATION'},'invalid R6 disposition')
        require(set(row.get('requirement_ids',[]))<=ids and set(row.get('finding_ids',[]))<=fids,'invalid R6 mapping')
        require(bool(row.get('section')) and bool(row.get('rationale')),'R6 section/rationale missing')
    for k in ('checked_text_pages','checked_image_pages','missing_checks','source_limitations'):
        require(isinstance(a.get(k),list),'audit coverage list missing: '+k)
    for k,image in [('checked_text_pages',False),('checked_image_pages',True)]:
        require(all(type(p) is int and any(e.get('pdf_page')==p and e.get('operation')=='page' and (not image or e.get('image_sha256')) for e in entries) for p in a[k]),'claimed unread page')
    require(a.get('status_recommendation') in FINAL,'invalid audit recommendation')

def flatten(audits):
    return {role+':'+f['finding_id']:{**f,'finding_id':role+':'+f['finding_id'],'reviewer':role,'item_rule_associations':[{'requirement_id':row['requirement_id'],'rule_id':rule,'status':status} for row in a['items'] if f['finding_id'] in row['finding_ids'] for rule,status in row['rules'].items() if status==f['status']]} for role,a in audits.items() for f in a['findings']}

def apply_revision(ref,revision,findings,ctx,entries):
    require(revision.get('target_id')==ref['target_id'] and revision.get('scope_preserved') is True,'scope cannot silently change')
    decisions=revision.get('decisions',[]);require(unique(decisions,'finding_id')==set(findings),'every finding requires a disposition')
    byfinding={d['finding_id']:d for d in decisions}
    for d in decisions:
        require(d.get('decision') in {'CONFIRM_DEFECT','NOT_A_DEFECT','UNRESOLVED','SOURCE_LIMITATION','SUGGESTION'} and bool(d.get('reason')),'invalid adjudication')
        if d['decision'] in {'CONFIRM_DEFECT','NOT_A_DEFECT','UNRESOLVED'}:evidence(d,ctx,entries)
        if d['decision']=='CONFIRM_DEFECT':require(findings[d['finding_id']]['status']=='DEFECT','cannot promote style/uncertainty to defect without full audit evidence')
    rows=copy.deepcopy(ref['requirements']);byid={r['requirement_id']:r for r in rows};old_ids=set(byid)
    next_number=max([int(x.rsplit('R',1)[1]) for x in old_ids],default=0)+1
    changes=revision.get('changes',[]);unique(changes,'change_id');touched=set();applied=[];lineage={i:[i] for i in old_ids};excluded=[]
    for c in changes:
        require(c.get('finding_ids') and set(c['finding_ids'])<=set(findings),'change must cite audited findings')
        require(all(byfinding[f]['decision']=='CONFIRM_DEFECT' for f in c['finding_ids']),'only confirmed defects may change content')
        ids=c.get('old_ids');new=c.get('new_items');op=c.get('operation')
        require(isinstance(ids,list) and len(set(ids))==len(ids) and set(ids)<=old_ids and not touched.intersection(ids),'overlapping/unknown patch IDs')
        require(isinstance(new,list) and bool(c.get('rationale')) and bool(c.get('effect_on_coverage_judgment_or_denominator')),'patch content/effect missing')
        valid=(op=='REPLACE' and len(ids)==len(new)==1 or op=='SPLIT' and len(ids)==1 and len(new)>=2 or op=='MERGE' and len(ids)>=2 and len(new)==1 or op=='ADD' and not ids and bool(new) or op=='EXCLUDE' and bool(ids) and not new)
        require(valid,'invalid patch cardinality')
        related=set().union(*(set(findings[f]['requirement_ids']) for f in c['finding_ids']))
        require(set(ids)<=related,'patch changes items outside cited defects')
        if op=='ADD':require(any(findings[f]['rule_id']=='R6' for f in c['finding_ids']),'addition requires confirmed omission')
        new_rows=[]
        for item in new:
            require(set(item)=={'text','conditions','status','evidence'},'new item must contain only text/conditions/status/evidence')
            require(isinstance(item['text'],str) and item['text'].strip() and (item['conditions'] is None or isinstance(item['conditions'],str)),'invalid replacement content')
            require(item['status'] in {'CONFIRMED','REFERENCE_UNRESOLVED'},'invalid reference status')
            check_evidence(item['evidence'],ctx,entries)
            if op=='REPLACE':rid=ids[0]
            else:rid=ref['target_id']+f'-R{next_number:04d}';next_number+=1
            new_rows.append({**copy.deepcopy(item),'requirement_id':rid,'local_id':rid,'source_sha256':ref['source_sha256'],'acceptance_change_id':c['change_id'],'origin_ids':ids})
        touched.update(ids)
        if op=='REPLACE':rows=[new_rows[0] if r['requirement_id']==ids[0] else r for r in rows]
        else:rows=[r for r in rows if r['requirement_id'] not in ids]+new_rows
        for rid in ids:lineage[rid]=[r['requirement_id'] for r in new_rows]
        if op=='EXCLUDE':excluded.extend({'item':byid[i],'change_id':c['change_id'],'finding_ids':c['finding_ids'],'reason':c['rationale']} for i in ids)
        applied.append({**c,'new_ids':[r['requirement_id'] for r in new_rows],'count_delta':len(new)-len(ids)})
    require(isinstance(revision.get('remaining_issues'),list),'remaining issue list required')
    for d in decisions:
        if d['decision']=='UNRESOLVED':require(any(d['finding_id'] in i.get('finding_ids',[]) for i in revision['remaining_issues']),'unresolved finding disappeared')
    require(revision.get('status_recommendation') in FINAL and bool(revision.get('unit_consistency')),'revision conclusion required')
    return {'requirements':rows,'changes':applied,'lineage':lineage,'excluded_items':excluded,'remaining_issues':revision['remaining_issues'],'before_count':len(ref['requirements']),'after_count':len(rows)}

def validate_recheck(recheck,ref,candidate,findings,ctx,entries):
    require(recheck.get('target_id')==ref['target_id'],'recheck target mismatch')
    actual_changes={c['change_id'] for c in candidate['changes']}
    reported_changes=unique(recheck.get('change_checks',[]),'change_id')
    no_change_attestation=not actual_changes and len(reported_changes)==1 and re.fullmatch(r'NO-CHANGES(?:-[0-9]+)?',next(iter(reported_changes))) and candidate['requirements']==ref['requirements']
    require(reported_changes==actual_changes or no_change_attestation,'all changes need targeted recheck')
    require(unique(recheck.get('finding_checks',[]),'finding_id')==set(findings),'all adjudications need targeted recheck')
    for row in recheck['change_checks']+recheck['finding_checks']:
        require(row.get('status') in STATES and bool(row.get('rationale')),'invalid recheck result')
        if row['status']!='SUGGESTION':evidence(row,ctx,entries)
    ids={r['requirement_id'] for r in candidate['requirements']}
    unique(recheck.get('new_findings',[]),'finding_id')
    for f in recheck['new_findings']:finding(f,ids,ctx,entries)
    for k in ('related_item_checks','remaining_issues','missing_checks'):require(isinstance(recheck.get(k),list),'recheck list missing: '+k)
    checked=unique(recheck['related_item_checks'],'requirement_id')
    require(checked<=ids,'unknown related item')
    required={i for c in candidate['changes'] for i in c['new_ids']}
    require(required<=checked,'changed requirements not rechecked')
    for item in recheck['related_item_checks']:require(item.get('status') in STATES and bool(item.get('rationale')),'invalid related item check')
    require(recheck.get('status_recommendation') in FINAL and recheck.get('scope_and_denominator_preserved_or_versioned') is True,'recheck conclusion missing')

def final_status(audits,revision,candidate,recheck,image_pages,pages):
    missing=[]
    for role,a in audits.items():
        missing+=a['missing_checks']
        if set(a['checked_text_pages'])!=set(range(1,pages+1)) or not set(image_pages)<=set(a['checked_image_pages']):missing.append(role+': unreviewed required source pages')
    missing+=recheck['missing_checks']
    issues=candidate['remaining_issues']+recheck['remaining_issues']
    kinds={i.get('kind') for i in issues}
    confirmed={d['finding_id'] for d in revision['decisions'] if d['decision']=='CONFIRM_DEFECT'}
    fixed={f for c in candidate['changes'] for f in c['finding_ids']}
    checks=recheck['change_checks']+recheck['finding_checks']+recheck['related_item_checks']+recheck['new_findings']
    if missing or 'INCOMPLETE' in kinds:return 'INCOMPLETE'
    if confirmed-fixed or any(c['status']=='DEFECT' for c in checks) or 'CONFIRMED_DEFECT' in kinds:return 'NEEDS_REVISION'
    if kinds.intersection({'SCOPE_UNCERTAINTY','UNIT_UNCERTAINTY','PROTOCOL_CONFLICT'}):return 'NEEDS_SCOPE_DECISION'
    if recheck['status_recommendation'] in {'NEEDS_REVISION','NEEDS_SCOPE_DECISION','INCOMPLETE'}:return recheck['status_recommendation']
    if any(c['status']=='UNRESOLVED' for c in checks) and not issues:return 'NEEDS_SCOPE_DECISION'
    uncertain=[r for r in candidate['requirements'] if r['status']=='REFERENCE_UNRESOLVED']
    byid={r['requirement_id']:r for r in candidate['requirements']}
    if any(i.get('scoring_treatment')=='FIXED_DENOMINATOR_UNRESOLVED_FOR_ALL_METHODS' and any(rid not in byid or byid[rid]['status']!='REFERENCE_UNRESOLVED' for rid in i.get('requirement_ids',[])) for i in issues):return 'NEEDS_REVISION'
    # Source omissions outside the fixed requirement set do not create scored
    # uncertainties. Preserve them as documentation without inventing new rows.
    if uncertain or any(i.get('kind')=='SOURCE_UNCERTAINTY' and i.get('requirement_ids') for i in issues):
        source_issues=[i for i in issues if i.get('kind')=='SOURCE_UNCERTAINTY']
        if not source_issues:return 'NEEDS_SCOPE_DECISION'
        if any(i.get('scoring_treatment') not in {'FIXED_DENOMINATOR_UNRESOLVED_FOR_ALL_METHODS','DOCUMENT_ONLY_NO_ADDED_REQUIREMENT'} for i in source_issues):return 'NEEDS_SCOPE_DECISION'
        if any(not any(r['requirement_id'] in i.get('requirement_ids',[]) and i.get('scoring_treatment')=='FIXED_DENOMINATOR_UNRESOLVED_FOR_ALL_METHODS' for i in source_issues) for r in uncertain):return 'NEEDS_SCOPE_DECISION'
        return 'ACCEPTED_WITH_SOURCE_UNCERTAINTY'
    return 'ACCEPTED'
