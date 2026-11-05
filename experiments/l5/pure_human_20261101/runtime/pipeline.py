"""Text-source adapter around the isolated original AUCMR author/verifier/DRA."""
import json
from pathlib import Path
from .core.schemas import (UseCase, CockburnSlot, SlotContent, Claim, QualityFlag,
                           RevisionRecord, COCKBURN_DEPENDENCY_ORDER)
from .core import agents

def forbidden_retrieval(*args, **kwargs):
    raise RuntimeError('External/page retrieval is unavailable in L5')

def execute(case, provider, config, outdir, root):
    outdir.mkdir(parents=True,exist_ok=True)
    def save(name,data):
        (outdir/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    save('input.json',{'case':case.case,'id':case.original_id,'title':case.title,
                       'actors':case.actors,'requirements_text':case.text,'sha256':case.source_hash,
                       'source_read_allowlist':[case.source_name]})
    save('source_spans.json',case.spans)
    save('evidence.json',[x.to_dict() for x in case.cards])
    uc=UseCase(use_case_name=case.title,actor=case.actors)
    # All exact excerpts were read once. No model-created evidence or generated
    # text becomes a source card. DRA can inspect the full frozen source pool.
    uc.add_evidence_cards(case.cards)
    provider.stage='input_actor_context'
    actor_seed=case.seed('primary_actor')
    actor_seed.claims=[Claim(content=case.actors,evidence_refs=actor_seed.evidence_card_ids)]
    prior=[]; stages=[]
    plan_template=(root/'prompts/text_plan.txt').read_text()
    for slot in COCKBURN_DEPENDENCY_ORDER:
        if slot==CockburnSlot.PRIMARY_ACTOR:
            uc.set_slot(slot,actor_seed)
            uc.add_revision(RevisionRecord(0,'user_input',[slot.value]))
            continue
        provider.stage=f'{slot.value}:text_evidence_plan'
        payload={'slot':slot.value,'use_case_name':case.title,'supplied_actor_set':case.actors,
                 'already_filled_slots':{k:v.to_dict() for k,v in uc.slots.items()},
                 'initial_slot':case.seed(slot.value).to_dict(),
                 'exact_source_cards':[c.to_prompt_dict() for c in case.cards]}
        raw=provider.llm(plan_template+'\n'+json.dumps(payload,ensure_ascii=False))
        plan=agents._parse_json(raw,provider=provider,retry_prompt=plan_template+'\n'+json.dumps(payload))
        if not isinstance(plan,dict) or not isinstance(plan.get('selected_card_ids'),list):
            raise ValueError('Invalid text planner schema')
        ids=plan['selected_card_ids']; card_map={c.card_id:c for c in case.cards}
        if any(not isinstance(i,str) or i not in card_map for i in ids):
            raise ValueError('Planner selected unknown source card')
        own=[card_map[i] for i in dict.fromkeys(ids)]
        seed=case.seed(slot.value)
        provider.stage=f'{slot.value}:author'
        sc=agents.answer_generator(actor=case.actors,use_case_name=case.title,slot=slot,
              evidence_cards=own,prior_evidence_cards=prior,filled_slots=uc.slots,
              current_slot_content=seed,provider=provider)
        save(f'{slot.value}.authored.json',sc.to_dict())
        provider.stage=f'{slot.value}:semantic_verify'
        sc=agents.semantic_grounding_verifier(slot,sc,own+prior,provider,
                                               fail_closed=True,use_source_images=False)
        uc.set_slot(slot,sc)
        for flag in sc.quality_flags:uc.add_flag(flag)
        sc.quality_flags=[]
        if sc.is_empty():uc.add_flag(QualityFlag('no_evidence',slot.value,'No supported claim emitted from the fully inspected requirements'))
        uc.add_revision(RevisionRecord(0,'requirements_refinement',[slot.value]))
        record={'slot':slot.value,'plan':plan,'source_spans_inspected':[c.card_id for c in case.cards],
                'author_rounds':1,'stop_reason':'allowed_text_fully_inspected_no_new_source',
                'max_slot_rounds':config['algorithm']['max_slot_rounds'],
                'before':seed.to_dict(),'after':sc.to_dict()}
        stages.append(record); save('slot_stages.json',stages)
        save(f'{slot.value}.verified.json',sc.to_dict())
        for c in own:
            if c.card_id not in {x.card_id for x in prior}:prior.append(c)
    save('before_dra.json',uc.to_dict())
    provider.stage='cross_slot_dra'
    uc=agents.cross_slot_revision(case.actors,case.title,uc,[case.source_name],0,
                                  forbidden_retrieval,provider,
                                  max_rounds=config['algorithm']['dra_max_rounds'])
    if any(f.flag_type=='consistency_check_failed' for f in uc.quality_flags):
        save('incomplete_result.json',uc.to_dict())
        raise ValueError('DRA detector parse failure: not a completed artifact')
    uc.runtime_stats.update(provider.stats_snapshot())
    uc.runtime_stats.update({'l5_input_mode':'independent_requirement_excerpts','dra_executed':True,
        'full_source_inspected':True,'per_slot_stop':'source_exhausted',
        'retrieval_calls':0,'image_calls':0,'model_created_evidence_cards':0})
    result=uc.to_dict()
    result.update({'case_id':case.case,'original_id':case.original_id,'actors':case.actors,
                   'source_sha256':case.source_hash,'protocol':config['protocol']})
    validate_result(result,case)
    save('result.json',result)
    return result

def validate_result(result,case):
    known={x.card_id for x in case.cards}
    for slot,data in result['slots'].items():
        claims=data.get('claims',[])
        for c in claims:
            if not c.get('evidence_refs') or not set(c['evidence_refs'])<=known:
                raise ValueError(f'Invalid evidence references: {slot}')
        if slot=='main_flow':
            if [c.get('step_number') for c in claims]!=list(range(1,len(claims)+1)):
                raise ValueError('Main-flow numbering not contiguous')
    if result['actors']!=case.actors or result['original_id']!=case.original_id:
        raise ValueError('Identity or supplied actor context changed')

def render_result(result):
    lines=[f"ID: {result['original_id']}",f"Title: {result['use_case_name']}",
           f"Actors: {result['actors']}",'']
    for slot,title in [('goal','Goal'),('trigger','Trigger'),('preconditions','Preconditions'),('main_flow','Main scenario'),
                       ('alternative_flow','Alternative flow'),('exception_flow','Error handling'),
                       ('postconditions','Postconditions')]:
        lines.append(title+':')
        claims=result['slots'].get(slot,{}).get('claims',[])
        for i,c in enumerate(claims,1):
            lines.append((f'{i}. ' if slot=='main_flow' else '- ')+c['content'])
        lines.append('')
    return '\n'.join(lines).rstrip()+'\n'
