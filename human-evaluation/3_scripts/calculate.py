"""Frozen metric contract applied directly to generation and L1/L2 source files.

Usage: python -B calculate.py --manifest inputs_43.json --out results_43
Future cohorts use the same schema and code with a NEW manifest/output directory.
No model, runtime, scheduler, or earlier metric-calculator imports.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

HERE=Path(__file__).resolve().parent
SLOTS=('goal','preconditions','trigger','main_flow','alternative_flow','exception_flow','postconditions')
RULE_INPUTS={'R1':('preconditions','trigger'),'R2':('trigger','main_flow'),
    'R3':('main_flow','postconditions'),'R4':('main_flow',),'R5':('goal','main_flow'),
    'R6':('main_flow','alternative_flow'),'R7':('exception_flow',)}
PRIMARY=('postgoal_supported_claim_rate','main_chain_pass_yield','branch_rule_pass_rate')
DEFINITIONS={
 'postgoal_supported_claim_rate':{'title':'后六槽证据卡支持断言率','role':'primary_support',
   'definition':'Per-target entailed final nonempty assertions in the six post-goal slots / all final nonempty assertions in those slots. Evidence-card support only; no slot_fit conjunction or PDF truth claim.'},
 'main_chain_pass_yield':{'title':'主链可评且衔接通过率','role':'primary_flow_product',
   'definition':'For each body in Sv: trigger/main_flow/postconditions must each have nonblank claims and R2/R3 must both have zero reported violations. Missing required slot gives 0 as absent evaluable product, not as a semantic violation. Missing required judgment stays unknown. Does not test source support of the whole chain.'},
 'branch_rule_pass_rate':{'title':'分支规则衔接通过率','role':'primary_branch',
   'definition':'Per-target mean zero-violation indicator for applicable R6/R7. Pairwise comparison uses only the same rules applicable to BOTH bodies. No applicable rules = NA, never automatic success. This is a rule-level measure, not branch coverage.'},
 'mainflow_conditional_pass_rate':{'title':'共同可评主流程的衔接通过率','role':'mandatory_decomposition',
   'definition':'Both R2/R3 must be body-applicable. Given that, both must report zero violations. Always report its applicability denominator beside main_chain_pass_yield.'},
 'main_chain_available':{'title':'主链结构齐备率','role':'structural_diagnostic',
   'definition':'Trigger, main_flow and postconditions all contain nonblank claims. Presence only; no quality claim.'},
 'postconditions_present':{'title':'后置条件产出率','role':'structural_diagnostic',
   'definition':'Postconditions contain at least one nonblank claim. Confounded in the existing no_cross_slot_reuse implementation.'},
 'postcondition_supported_claim_rate':{'title':'后置条件断言支持精度','role':'mandatory_yield_companion',
   'definition':'Entailed postcondition assertions / all final postcondition assertions. Empty postconditions are NA, not 0 or 1. Always show presence and denominator; paired version uses common nonempty bodies.'},
 'postcondition_joint_claim_rate':{'title':'后置条件断言支持且槽位正确率','role':'auxiliary',
   'definition':'Postcondition assertions simultaneously entailed and slot_fit=true / postcondition assertions. Empty postconditions are NA.'},
 'qualified_postcondition_yield':{'title':'至少一条合格后置条件的产出率','role':'auxiliary',
   'definition':'At least one final postcondition assertion is entailed and slot_fit=true. Empty slot gives no successful product. This weak existential threshold does not mean all postconditions are correct.'},
 'postgoal_claim_count':{'title':'后六槽断言数','role':'quantity_diagnostic','definition':'Count of final nonblank assertions in post-goal slots; not requirement coverage.'},
 'l2_findings':{'title':'L2观察到的违规条数','role':'dra_diagnostic','definition':'Sum of reported rule findings. Not matched true repair/regression counts; show rule applicability and repeated-input judge disagreements.'},
 'generation_model_events':{'title':'生成阶段模型输入事件数','role':'process_only','definition':'Logical model_input events, including replay. Not paid requests, monetary cost, tokens or latency.'},
 'generation_page_presentations':{'title':'生成阶段页面呈现次数','role':'process_only','definition':'Sum of page occurrences in evidence_read events; repeated pages counted.'},
 'generation_image_reads':{'title':'生成阶段图像阅读事件数','role':'process_only','definition':'Count of evidence_read events. A multi-page read and a single-page read are not equivalent workloads.'},
}
for rule in RULE_INPUTS:
    DEFINITIONS[rule+'_pass']={'title':rule+'规则通过率','role':'rule_decomposition',
       'definition':'Body-defined applicability; indicator of zero findings for that rule. Missing evaluation stays unknown.'}


def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(v):return hashlib.sha256(canonical(v).encode()).hexdigest()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())


def write(p,v):
    p=Path(p);text=json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    if p.exists():assert p.read_text()==text,'IMMUTABLE_OUTPUT_CHANGED:'+str(p)
    else:p.write_text(text)


def cell(value,applicable=True,lower=None,upper=None,n=None,unknown=0):
    return {'value':value,'applicable':bool(applicable),
        'lower':value if lower is None else lower,'upper':value if upper is None else upper,
        'denominator':n,'unknown_labels':unknown}


def ratio(labels):
    n=len(labels);u=sum(x is None for x in labels);yes=sum(x==1 for x in labels)
    if not n:return cell(None,False,n=0)
    return cell(yes/n if not u else None,lower=yes/n,upper=(yes+u)/n,n=n,unknown=u)


def any_success(labels):
    if 1 in labels:return cell(1,n=1)
    if None in labels:return cell(None,lower=0,upper=1,n=1,unknown=1)
    return cell(0,n=1)


def claim_key(slot,c,refs='evidence_refs'):
    return slot,c['content'],tuple(sorted(set(c.get(refs,[]))))


def labels(candidates):
    if not candidates:return None,None,'NO_EXACT_CLAIM_REF_MATCH'
    ent=set();qualified=set()
    for c in candidates:
        if c.get('status')=='NO_VALID_CITATION':ent.add(0);qualified.add(0);continue
        j=c.get('judgment')
        if c.get('status')!='COMPLETE' or not j:
            ent.add(None);qualified.add(None);continue
        if j.get('entailment') not in ('entailment','neutral','contradiction') or not isinstance(j.get('slot_fit'),bool):
            raise ValueError('INVALID_LABEL_SCHEMA')
        e=int(j['entailment']=='entailment');ent.add(e);qualified.add(e*int(j['slot_fit']))
    one=lambda x:next(iter(x)) if len(x)==1 else None
    return one(ent),one(qualified),'EXACT_MATCH' if len(ent)==len(qualified)==1 else 'CONFLICT_RETAINED'


def branch_value(rules,common=None):
    keys=common if common is not None else [k for k in ('R6','R7') if rules[k]['applicable']]
    return ratio([rules[k]['pass'] for k in keys])


def load_score(p,input_hash,protected):
    if not p or not Path(p).exists():return None
    protected[str(Path(p).resolve())]=sha(p);d=read(p)
    assert d['input_hash']==input_hash,'EVALUATION_INPUT_MISMATCH:'+str(p)
    return d


def position_metrics(p,protected):
    path=p.get('generation_path')
    identity={k:p[k] for k in ('target_id','config','project','position_id','condition_id')}
    identity['defect_tags']=p.get('defect_tags',[])
    if not path or not Path(path).exists():
        return dict(identity,body_available=False,missing_reason='GENERATION_MISSING',metrics={},rules={})
    g=read(path);protected[str(Path(path).resolve())]=sha(path)
    context=g.get('context',{})
    assert context.get('target_id')==p['target_id'],'GENERATION_TARGET_MISMATCH'
    assert context.get('config_id')==p['config'],'GENERATION_CONFIG_MISMATCH'
    assert context.get('repeat')==p['repeat']==1,'GENERATION_REPEAT_MISMATCH'
    if p.get('generation_sha256'):assert sha(path)==p['generation_sha256'],'GENERATION_HASH_MISMATCH'
    artifact=g['artifact'];ih=digest(artifact)
    if p.get('input_hash'):assert ih==p['input_hash'],'ARTIFACT_HASH_MISMATCH'
    if not p.get('body_evaluable',True):
        return dict(identity,body_available=False,missing_reason='BODY_NOT_EVALUABLE',metrics={},rules={})
    l1=load_score(p.get('l1_path'),ih,protected);l2=load_score(p.get('l2_path'),ih,protected)
    index=defaultdict(list)
    for c in (l1 or {}).get('claims',[]):index[claim_key(c['slot'],c,'original_refs')].append(c)
    slots={s:[c for c in artifact['use_case']['slots'].get(s,{}).get('claims',[]) if str(c.get('content','')).strip()] for s in SLOTS}
    detail=[]
    for slot,claims in slots.items():
        for i,c in enumerate(claims):
            e,q,status=labels(index[claim_key(slot,c)])
            detail.append({'slot':slot,'index':i,'entailed':e,'qualified':q,'status':status})
    rules={}
    for rule,needed in RULE_INPUTS.items():
        app=all(slots[s] for s in needed);count=None;passed=None
        if app and l2 and l2.get('status')=='COMPLETE':
            resolution=l2.get('rule_resolution',{}).get(rule,{}).get('state')
            assert resolution in ('MODEL_CHECK','SATISFIED_LITERAL_ACTOR_PRESENCE'),'RULE_APPLICABILITY_MISMATCH:'+rule
            count=l2['counts'][rule];assert isinstance(count,int) and count>=0
            passed=int(count==0)
        rules[rule]={'applicable':bool(app),'count':count,'pass':passed}
    chain=all(slots[s] for s in ('trigger','main_flow','postconditions'))
    rp=[rules[k]['pass'] for k in ('R2','R3')]
    flow=cell(int(all(rp)),n=1) if None not in rp else cell(None,lower=0,upper=1,n=1,unknown=1)
    post=[x for x in detail if x['slot']=='postconditions'];other=[x for x in detail if x['slot']!='goal']
    events=[e for e in g.get('events',[]) if e['phase']=='generation']
    metrics={
        'postgoal_supported_claim_rate':ratio([x['entailed'] for x in other]),
        'main_chain_pass_yield':flow if chain else cell(0,n=1),
        'branch_rule_pass_rate':branch_value(rules),
        'mainflow_conditional_pass_rate':flow if chain else cell(None,False),
        'main_chain_available':cell(int(chain),n=1),
        'postconditions_present':cell(int(bool(post)),n=1),
        'postcondition_supported_claim_rate':ratio([x['entailed'] for x in post]),
        'postcondition_joint_claim_rate':ratio([x['qualified'] for x in post]),
        'qualified_postcondition_yield':any_success([x['qualified'] for x in post]),
        'postgoal_claim_count':cell(len(other)),
        'l2_findings':cell(sum((l2 or {}).get('counts',{}).values()) if l2 and l2.get('status')=='COMPLETE' else None),
        'generation_model_events':cell(sum(e['kind']=='model_input' for e in events)),
        'generation_image_reads':cell(sum(e['kind']=='evidence_read' for e in events)),
        'generation_page_presentations':cell(sum(len(e['actual_pages']) for e in events if e['kind']=='evidence_read')),
    }
    for rule,r in rules.items():
        metrics[rule+'_pass']=cell(r['pass'],r['applicable'],lower=0 if r['pass'] is None and r['applicable'] else None,
            upper=1 if r['pass'] is None and r['applicable'] else None,n=1 if r['applicable'] else 0,unknown=int(r['pass'] is None and r['applicable']))
    return dict(identity,body_available=True,artifact_hash=ih,metrics=metrics,rules=rules,
        claim_labels=detail,postgoal_claims=len(other),postgoal_supported=sum(x['entailed']==1 for x in other),
        postgoal_unknown=sum(x['entailed'] is None for x in other))


def aggregate(records,metric):
    body=[r for r in records if r['body_available']]
    members=[r for r in body if r['metrics'][metric]['applicable']]
    cells=[r['metrics'][metric] for r in members];complete=all(c['value'] is not None for c in cells)
    bounds=[mean(c[k] for c in cells) if cells and all(c[k] is not None for c in cells) else None for k in ('lower','upper')]
    return {'planned':len(records),'bodies':len(body),'applicable':len(members),
        'unknown':sum(c['value'] is None for c in cells),'mean':mean(c['value'] for c in cells) if complete and cells else None,
        'bounds':bounds,'applicable_ids':[r['target_id'] for r in members]}


def pair(records,variant,metric):
    mapping={(r['target_id'],r['config']):r for r in records}
    ids=sorted({r['target_id'] for r in records})
    sv=[t for t in ids if mapping[t,'full']['body_available'] and mapping[t,variant]['body_available']]
    comparisons=[];na=[]
    for t in sv:
        x,y=mapping[t,'full'],mapping[t,variant];a,b=x['metrics'][metric],y['metrics'][metric]
        if metric=='branch_rule_pass_rate':
            common=[k for k in ('R6','R7') if x['rules'][k]['applicable'] and y['rules'][k]['applicable']]
            a,b=branch_value(x['rules'],common),branch_value(y['rules'],common)
        if not a['applicable'] or not b['applicable']:na.append(t);continue
        val=a['value']-b['value'] if a['value'] is not None and b['value'] is not None else None
        comparisons.append({'target_id':t,'project':x['project'],'full':a['value'],'variant':b['value'],
            'delta':val,'lower':a['lower']-b['upper'] if a['lower'] is not None and b['upper'] is not None else None,
            'upper':a['upper']-b['lower'] if a['upper'] is not None and b['lower'] is not None else None,
            'variant_defect_tags':y['defect_tags']})
    complete=bool(comparisons) and all(p['delta'] is not None for p in comparisons)
    bydoc={}
    for doc in sorted({p['project'] for p in comparisons}):
        group=[p for p in comparisons if p['project']==doc]
        bydoc[doc]={'n':len(group),'delta':mean(p['delta'] for p in group) if all(p['delta'] is not None for p in group) else None}
    return {'Sv_n':len(sv),'Sv_ids':sv,'generation_missing':[t for t in ids if t not in sv],
        'applicable_pairs':len(comparisons),'inapplicable':na,'unknown_pairs':sum(p['delta'] is None for p in comparisons),
        'full_mean':mean(p['full'] for p in comparisons) if complete else None,
        'variant_mean':mean(p['variant'] for p in comparisons) if complete else None,
        'delta_full_minus_variant':mean(p['delta'] for p in comparisons) if complete else None,
        'bounds':[mean(p[k] for p in comparisons) if comparisons and all(p[k] is not None for p in comparisons) else None for k in ('lower','upper')],
        'by_document':bydoc,'pairs':comparisons,
        'interpretation':'CONFOUNDED_ABLATION_CONTROL_FLOW' if variant=='no_cross_slot_reuse' and any(
            r['defect_tags'] or r.get('condition_id')=='half_r1_v1_A_original_algorithm'
            for r in records if r['config']==variant) else 'DESCRIPTIVE_PAIRED_COMPARISON'}


def calculate(manifest,out):
    manifest=Path(manifest).resolve();out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    data=read(manifest);protected={str(manifest):sha(manifest)};positions=data['positions'];configs=data['configs']
    assert data['metric_contract_sha256']==sha(HERE/'metric_contract.json'),'METRIC_CONTRACT_CHANGED'
    assert len({(p['target_id'],p['config']) for p in positions})==len(positions),'DUPLICATE_TARGET_CONFIG'
    ids=sorted({p['target_id'] for p in positions})
    assert len(positions)==len(ids)*len(configs),'EXPLICIT_MISSING_POSITIONS_REQUIRED'
    assert all({p['config'] for p in positions if p['target_id']==target}==set(configs) for target in ids),'INCOMPLETE_CONFIG_GRID'
    assert all(p['repeat']==1 for p in positions),'R1_ONLY'
    assert len({p['condition_id'] for p in positions})==1,'SEPARATE_ALGORITHM_CONDITIONS_BEFORE_COMBINING'
    records=[position_metrics(p,protected) for p in positions]
    grouped={cfg:{m:aggregate([r for r in records if r['config']==cfg],m) for m in DEFINITIONS} for cfg in configs}
    pairs={cfg:{m:pair(records,cfg,m) for m in DEFINITIONS} for cfg in configs if cfg!='full'}
    bydoc={doc:{cfg:{m:aggregate([r for r in records if r['project']==doc and r['config']==cfg],m) for m in DEFINITIONS} for cfg in configs}
           for doc in sorted({r['project'] for r in records})}
    for p,h in protected.items():assert sha(p)==h,'INPUT_CHANGED_DURING_CALCULATION'
    result={'cohort_id':data['cohort_id'],'metric_contract_sha256':data['metric_contract_sha256'],
        'scope':{'targets':len(ids),'configs':len(configs),'positions':len(records),'bodies':sum(r['body_available'] for r in records)},
        'primary_metrics':PRIMARY,'definitions':DEFINITIONS,'groups':grouped,'pairs':pairs,
        'by_document':bydoc,'positions':records,
        'claim':'All effects and missingness are retained; no ranking-dependent metric selection or relabeling.'}
    write(out/'results.json',result);write(out/'input_protection.json',protected)
    write(out/'verification.json',{'source_files_verified':len(protected),'source_changes':0,'new_model_calls':0,
        'unknown_claims':sum(x['entailed'] is None for r in records if r['body_available'] for x in r['claim_labels']),
        'new_reference_requirements':0,'contract_verified':True})
    print(json.dumps({'output':str(out),'scope':result['scope'],'primary':{c:{m:grouped[c][m]['mean'] for m in PRIMARY} for c in configs}},ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--manifest',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args();calculate(args.manifest,args.out)
