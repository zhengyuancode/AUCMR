"""Deterministic evidence validation, sampling and fixed-denominator aggregation."""
import json
import math
import re
from collections import Counter, defaultdict
from hashlib import sha256

from eval.agent_evaluation.common import canonical, digest
from eval.l3_reference.response import pairs, eof_closers
from eval.evaluation_response_format import normalize_json_envelope
from eval.l3_reference.validation import require

LABELS=('FULL','PARTIAL','NOT_COVERED','CONTRADICTED','UNRESOLVED')
SEED=297154423

def ordered(items,stream,key=lambda x:x):
    return sorted(items,key=lambda x:sha256(f'{SEED}|{stream}|{key(x)}'.encode()).hexdigest())

def sample(rows,stream,fraction=.1):
    """Each row is a requirement/artifact position; stratify by source project."""
    strata=defaultdict(list)
    for row in rows:strata[row['project_id']].append(row)
    return [x for project in sorted(strata) for x in ordered(strata[project],stream,key=lambda x:x['id'])[:math.ceil(len(strata[project])*fraction)]]

def quote_span(quote,text):
    """Recover source characters for whitespace/quote typography, never fuzzy words."""
    trans=str.maketrans({'“':'"','”':'"','‘':"'",'’':"'"})
    def mapped(value):
        chars=[];positions=[]
        for i,c in enumerate(value):
            c=c.translate(trans)
            if c.isspace():
                if chars and chars[-1]!=' ':chars.append(' ');positions.append(i)
            else:chars.append(c);positions.append(i)
        if chars and chars[-1]==' ':chars.pop();positions.pop()
        return ''.join(chars),positions
    q,_=mapped(quote);t,positions=mapped(text)
    if not q:return None
    start=t.find(q)
    if start>=0 and t.find(q,start+1)<0:return text[positions[start]:positions[start+len(q)-1]+1]
    # English quotation punctuation can move across the closing quote only at
    # the end of a full-claim citation. All other characters must be identical.
    def ending(v):
        return v[:-2]+v[-1]+'.' if v.endswith(('."',".'")) else v
    if ending(q)==ending(t):return text.strip()
    return None

def decode(raw,artifact=None,requirements=None):
    text,_=normalize_json_envelope(raw);operations=[]
    if isinstance(text,str):
        kwargs={'object_pairs_hook':pairs,'parse_constant':lambda x:(_ for _ in ()).throw(ValueError('nonfinite JSON'))}
        try:data=json.loads(text,**kwargs)
        except json.JSONDecodeError as e:
            redundant=re.match(r'}\s*,\s*(?={)',text[e.pos:])
            if e.msg=='Expecting value' and text[:e.pos].rstrip().endswith('},') and redundant:
                # The parser is between array elements, not inside a string;
                # remove only a redundant closing brace and its second comma.
                data=json.loads(text[:e.pos]+text[e.pos+redundant.end():],**kwargs)
                operations.append({'kind':'REMOVE_REDUNDANT_BRACE_BETWEEN_ROWS','offset':e.pos})
            else:
                close=eof_closers(text)
                if e.pos<len(text.rstrip()) or not close:raise
                data=json.loads(text+close,**kwargs);operations.append('CLOSE_COMPLETE_CONTAINERS')
    else:data=text
    if isinstance(data,dict) and set(data)=={'result'}:
        data=data['result'];operations.append('UNWRAP_RESULT')
    require(isinstance(data,dict),'JSON object required')
    require(set(data) in ({'tool_calls'},{'judgments'},{'judgments','reference_issues'},
                         {'tool_calls','judgments'},{'tool_calls','judgments','reference_issues'}),'expected judgments or source tool request')
    if requirements is not None and isinstance(data.get('judgments'),list):
        expected=[r['requirement_id'] for r in requirements]
        observed=[r.get('requirement_id') if isinstance(r,dict) else None for r in data['judgments']]
        pattern=r'(UC-[0-9a-f]+)-(R[0-9]+)'
        ep=[re.fullmatch(pattern,x) for x in expected]
        op=[re.fullmatch(pattern,x) if isinstance(x,str) else None for x in observed]
        if len(observed)==len(expected) and all(ep) and all(op):
            prefixes={m[1] for m in ep};other={m[1] for m in op}
            expected_suffix=[m[2] for m in ep];observed_suffix=[m[2] for m in op]
            if len(prefixes)==len(other)==1 and len(set(observed_suffix))==len(observed_suffix) and set(expected_suffix)==set(observed_suffix):
                target=next(iter(prefixes));wrong=next(iter(other))
                # Small edit distance applies ONLY to the opaque target prefix,
                # with the complete unique assigned suffix set unchanged.
                previous=list(range(len(target)+1))
                for i,ch in enumerate(wrong,1):
                    current=[i]
                    for j,t in enumerate(target,1):current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(ch!=t)))
                    previous=current
                if wrong!=target and previous[-1]<=2:
                    mapping={wrong+'-'+suffix:target+'-'+suffix for suffix in expected_suffix}
                    for row in data['judgments']:row['requirement_id']=mapping[row['requirement_id']]
                    for issue in data.get('reference_issues',[]):
                        if isinstance(issue,dict) and issue.get('requirement_id') in mapping:issue['requirement_id']=mapping[issue['requirement_id']]
                    operations.append({'kind':'RESTORE_MISTYPED_TARGET_PREFIX_WITH_EXACT_ASSIGNED_SUFFIX_SET','mapping':mapping})
    # Resolve citation metadata from unique identifiers and literal quotations.
    # No label, rationale, requirement, or output content is changed.
    if artifact is not None:
        index=defaultdict(list)
        for slot,v in artifact['slots'].items():
            for c in v['claims']:index[c['claim_id']].append((slot,c['content']))
        for row in data.get('judgments',[]) if isinstance(data.get('judgments'),list) else []:
            if not isinstance(row,dict):continue
            for m in row.get('matches',[]) if isinstance(row.get('matches'),list) else []:
                if not isinstance(m,dict):continue
                found=index.get(m.get('claim_id'),[]);quote=m.get('quote')
                if not isinstance(quote,str) or not quote.strip():continue
                if not found or (len(found)==1 and quote_span(quote,found[0][1]) is None):
                    # A wrong ID can be restored only when the literal quote
                    # resolves to one and only one claim in the complete output.
                    candidates=[(cid,slot,text) for cid,entries in index.items() for slot,text in entries if quote_span(quote,text) is not None]
                    if len(candidates)>1:
                        # Preserve the exact supplied slot and literal quote.
                        # Resolve a single omitted character in the opaque ID
                        # only if those constraints identify one existing claim.
                        supplied=m.get('claim_id')
                        scoped=[(cid,slot,text) for cid,slot,text in candidates
                                if slot==m.get('slot') and isinstance(supplied,str)
                                and len(cid)==len(supplied)+1
                                and any(cid[:j]+cid[j+1:]==supplied for j in range(len(cid)))]
                        if len(scoped)==1:candidates=scoped
                    if len(candidates)!=1:continue
                    cid,slot,text=candidates[0]
                    operations.append({'kind':'RESTORE_CLAIM_ID_BY_UNIQUE_QUOTE','requirement_id':row.get('requirement_id'),'original_claim_id':m.get('claim_id'),'claim_id':cid})
                    m['claim_id']=cid;found=[(slot,text)]
                if len(found)!=1:continue
                exact=quote_span(quote,found[0][1])
                if exact is None:continue
                if normalized(quote) not in normalized(found[0][1]):
                    m['quote']=exact
                    operations.append({'kind':'RESTORE_QUOTE_TYPOGRAPHY_FROM_CLAIM','requirement_id':row.get('requirement_id'),'claim_id':m['claim_id'],'original_quote':quote,'exact_quote':exact})
                if m.get('slot')!=found[0][0]:
                    operations.append({'kind':'RESTORE_SLOT_BY_UNIQUE_CLAIM_AND_QUOTE','requirement_id':row.get('requirement_id'),'claim_id':m['claim_id'],'original_slot':m.get('slot'),'slot':found[0][0]})
                    m['slot']=found[0][0]
    return data,operations

def normalized(s):return ' '.join(s.split())

def validate(data,requirements,artifact):
    rows=data.get('judgments');require(isinstance(rows,list),'judgments array required')
    expected={r['requirement_id']:r for r in requirements}
    require(len(rows)==len(expected),'every assigned requirement needs one judgment')
    require(all(isinstance(x,dict) for x in rows),'invalid judgment')
    ids=[x.get('requirement_id') for x in rows]
    require(len(set(ids))==len(ids) and set(ids)==set(expected),'unknown, missing or duplicate requirement ID')
    claims={(slot,c['claim_id']):c['content'] for slot,v in artifact['slots'].items() for c in v['claims']}
    for row in rows:
        require(row.get('label') in LABELS,'invalid semantic label')
        require(row.get('technical_status')=='OK','Agent must not fabricate technical success/failure states')
        require(row.get('reviewed_complete_output') is True,'complete output must be reviewed')
        require(isinstance(row.get('rationale'),str) and row['rationale'].strip(),'rationale required')
        matches=row.get('matches');require(isinstance(matches,list),'matches array required')
        if row['label'] in {'FULL','PARTIAL','CONTRADICTED'}:require(bool(matches),'output evidence required')
        if expected[row['requirement_id']]['status']=='REFERENCE_UNRESOLVED':require(row['label']=='UNRESOLVED','unresolved reference cannot be promoted')
        for m in matches:
            require(isinstance(m,dict),'invalid output locator')
            text=claims.get((m.get('slot'),m.get('claim_id')))
            quote=m.get('quote')
            require(text is not None and isinstance(quote,str) and quote.strip(),'unknown output locator or empty quotation')
            require(normalized(quote) in normalized(text),'quotation must occur verbatim in the specified output claim')
    issues=data.get('reference_issues',[])
    require(isinstance(issues,list),'reference_issues array required')
    for issue in issues:
        require(isinstance(issue,dict) and issue.get('requirement_id') in expected and isinstance(issue.get('reason'),str) and issue['reason'].strip(),'reference issue needs an assigned ID and explanation')
    return {r['requirement_id']:r for r in rows}

def deterministic_rows(ref,artifact):
    rows={}
    for r in ref['requirements']:
        label='UNRESOLVED' if r['status']=='REFERENCE_UNRESOLVED' else 'NOT_COVERED' if artifact['no_content'] else None
        if label:
            rows[r['requirement_id']]={'requirement_id':r['requirement_id'],'label':label,'technical_status':'OK','matches':[],
                'rationale':'Frozen reference is unresolved for every method.' if label=='UNRESOLVED' else 'Verified valid empty output/refusal contains no requirement expression.',
                'reviewed_complete_output':True,'origin':'DETERMINISTIC_PROTOCOL'}
    return rows

def aggregate(ref,rows):
    counts=Counter({k:0 for k in (*LABELS,'TECHNICAL_FAILURE')})
    for req in ref['requirements']:
        row=rows.get(req['requirement_id'])
        if req['status']=='REFERENCE_UNRESOLVED':
            require(row is None or row.get('label')=='UNRESOLVED','unresolved reference changed')
            counts['UNRESOLVED']+=1
        elif row is None or row.get('technical_status')!='OK':counts['TECHNICAL_FAILURE']+=1
        else:
            require(row['label'] in LABELS,'invalid aggregation label');counts[row['label']]+=1
    n=len(ref['requirements']);require(sum(counts.values())==n,'fixed denominator mismatch')
    return {'N':n,'counts':dict(counts),'RC_full':counts['FULL']/n if n else None,
            'status':'REFERENCE_EMPTY' if not n else 'INCOMPLETE' if counts['TECHNICAL_FAILURE'] else 'COMPLETE_WITH_UNRESOLVED' if counts['UNRESOLVED'] else 'COMPLETE'}

def reconcile(requirements,a,b,c,required_review):
    result={}
    for r in requirements:
        rid=r['requirement_id']
        if rid in required_review:
            if rid in c:result[rid]=c[rid]
        elif rid in a and rid in b and a[rid]['label']==b[rid]['label']:result[rid]=a[rid]
    return result
