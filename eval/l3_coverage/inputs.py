"""Read immutable reference collection and pre-registered 378 generation artifacts."""
import copy
from pathlib import Path
from eval.agent_evaluation.common import REPO, read_json, write_json, file_hash, digest
from eval.l3_reference.validation import require, source_context, validate_frozen
from eval.l3_reference.batch import APPROVED
from eval.serialized_slots import omitted_claims_are_empty
from .contract import ordered

REFERENCE_ROOT=REPO/'outputs/agent_evaluation/l3-pure54-agent-final-20261102-v1'
PREP=REPO/'docs/codex_evaluation/l3/20261031-pure54-v1'
SLOTS=('goal','primary_actor','preconditions','trigger','main_flow','alternative_flow','exception_flow','postconditions')

def locked(path,value):
    if path.exists():require(read_json(path)==value,'frozen coverage input changed: '+str(path))
    else:write_json(path,value)

def artifact(binding,target):
    path=REPO/binding['path'];require(file_hash(path)==binding['sha256'],'generation input hash changed')
    raw=read_json(path)
    require(raw.get('use_case_name')==target['name'] and raw.get('actor')==target['actor'],'generation target mismatch')
    require(isinstance(raw.get('slots'),dict),'invalid generation slots')
    refusal=raw.get('refusal') is True or raw.get('status')=='REFUSED' or any(isinstance(f,dict) and f.get('flag_type')=='unresolvable_use_case' for f in raw.get('quality_flags',[]))
    slots={}
    for slot in SLOTS:
        item=raw['slots'].get(slot)
        if item is None:
            require(slot=='primary_actor' or refusal,'missing output slot: '+slot);item={'claims':[]}
        require(isinstance(item,dict),'invalid slot structure')
        claims=item.get('claims')
        if claims is None and omitted_claims_are_empty(item,slot,refusal=refusal):claims=[]
        require(isinstance(claims,list),'invalid claim list: '+slot)
        converted=[]
        for index,c in enumerate(claims):
            require(isinstance(c,dict) and isinstance(c.get('content'),str) and c['content'].strip(),'invalid output claim')
            row={'claim_id':'CL-'+digest([binding['sha256'],slot,index])[:16],'content':c['content']}
            if 'step_number' in c:row['step_number']=c['step_number']
            converted.append(row)
        raw_text=item.get('raw_text')
        require(raw_text is None or isinstance(raw_text,str),'invalid raw_text')
        if raw_text and raw_text.strip() and not any(c['content']==raw_text for c in converted):
            converted.append({'claim_id':'CL-'+digest([binding['sha256'],slot,'raw_text'])[:16],'content':raw_text,'representation':'raw_text'})
        slots[slot]={'claims':converted}
    return {'artifact_id':'OUT-'+digest([target['target_id'],binding['sha256']])[:20],
            'artifact_sha256':binding['sha256'],'use_case_name':target['name'],'actor':target['actor'],
            'slots':slots,'explicit_refusal':refusal,'no_content':not any(v['claims'] for s,v in slots.items() if s!='primary_actor')}

def prepare(root):
    collection=read_json(REFERENCE_ROOT/'common-references.json');manifest=read_json(REFERENCE_ROOT/'common-references.manifest.json')
    require(collection['status']=='COMPLETE' and len(collection['references'])==54,'54 complete references required')
    require(file_hash(REFERENCE_ROOT/'common-references.json')==manifest['sha256'],'reference collection hash changed')
    require(collection['content_sha256']==digest({k:v for k,v in collection.items() if k!='content_sha256'}),'collection content mismatch')
    ref_plan=read_json(REFERENCE_ROOT/'plan.snapshot.json');seed_rows={x['target_id']:x for x in ref_plan['approved_pilots']}
    contexts={};references={};protections={str(REFERENCE_ROOT/'common-references.json'):manifest['sha256']}
    for entry in collection['references']:
        ref=entry['reference'];tid=ref['target_id'];path=REFERENCE_ROOT/entry['reference_path']
        require(read_json(path)==ref and file_hash(path)==entry['reference_file_sha256'],'individual reference changed')
        ctx=source_context(Path(ref_plan['source_bundle']),tid);contexts[tid]=ctx
        if tid in seed_rows:
            require(file_hash(path)==seed_rows[tid]['sha256'] and path.read_bytes()==(APPROVED/(tid+'.json')).read_bytes(),'approved pilot reference changed')
            require(ref['source_sha256']==ctx['source']['pdf_sha256'] and ref['denominator']==len(ref['requirements']),'approved pilot source/denominator mismatch')
        else:validate_frozen(ref,ctx['bundle'],REFERENCE_ROOT)
        # A view, not a fabricated reconstruction of the approved pilot's schema.
        references[tid]={'target_id':tid,'version':ref['version'],'reference_sha256':file_hash(path),'source_sha256':ref['source_sha256'],
                         'scope':copy.deepcopy(ref['scope']),'requirements':copy.deepcopy(ref['requirements'])}
        protections[str(path)]=file_hash(path)
    binding_path=PREP/'coordinator/stage2-input-lock.json';bindings=read_json(binding_path)['input_bindings']
    methods=sorted({b['method'] for b in bindings});require(len(methods)==7 and len(bindings)==378,'expected 378 artifacts')
    require({(b['target_id'],b['method']) for b in bindings}=={(t,m) for t in references for m in methods},'artifact matrix incomplete')
    cases={};failures=[]
    for b in bindings:
        require((REPO/b['path']).is_file() and file_hash(REPO/b['path'])==b['sha256'],'frozen generation file missing or changed')
        tid=b['target_id'];key='CASE-'+digest([tid,b['method']])[:20]
        try:view=artifact(b,contexts[tid]['target']);error=None
        except (ValueError,KeyError,TypeError) as exc:view=None;error=str(exc);failures.append({'case_id':key,'error':error})
        cases[key]={'case_id':key,'target_id':tid,'project_id':contexts[tid]['target']['project_id'],
                    'method':b['method'],'binding':b,'artifact':view,'input_error':error}
        protections[str(REPO/b['path'])]=b['sha256']
    cfg=copy.deepcopy(read_json(REFERENCE_ROOT/'config.snapshot.json'))
    cfg['budgets']['live_authorized']=False
    config={'models':cfg['models'],'cost_control':'PROVIDER_MANAGED','budgets':cfg['budgets'],'campaign_ledger':cfg['campaign_ledger'],
            'chunk_size':20,'max_requests_per_task':6,'format_repairs':1,'seed':297154423,'agreement_sample_fraction':.1,'stability_fraction':.1,
            'reference_unresolved_policy':'UNRESOLVED_IN_FIXED_DENOMINATOR','transport_retry':False,
            'authorization':'User requested completing final L3 scoring experiment on 2026-11-04.',
            'reference_collection_sha256':manifest['sha256'],'input_lock_sha256':file_hash(binding_path)}
    plan={'targets':[contexts[t]['target'] for t in contexts],'methods':methods,'cases':cases,
          'pilot_targets':read_json(PREP/'pilot-selection.json')['target_order'],
          'order_A':ordered(list(cases),'COVERAGE_A'),'order_B':ordered(list(cases),'COVERAGE_B'),
          'references':references,'source_bundle':ref_plan['source_bundle'],'input_failures':failures,
          'sampling_unit':'CONFIRMED_REQUIREMENT_X_ARTIFACT','strata':'project_id x COVERAGE_REQUIREMENT',
          'pilot_review':'All confirmed pilot positions; preserved in the union with all disagreements and 10% stratified agreement audit.',
          'main_aggregate':'case mean within document, then equal mean of four documents',
          'reference_quality':'SOURCE_CONSTRAINED_AGENT_REFERENCE_NOT_OFFICIAL_GOLD','human_audit':'NOT_RUN'}
    root.mkdir(parents=True,exist_ok=True)
    locked(root/'config.snapshot.json',config);locked(root/'plan.snapshot.json',plan);locked(root/'protected-inputs.json',protections)
    return config,plan,contexts
