"""Assemble role inputs from whitelisted bundles/records, without method mappings."""
import json
from isolation import clean

def read(p):return json.loads(p.read_text())
def context(root,role,unit):
 if role.startswith('reference'):
  source=read(root/'bundles/source'/f'{unit}.json');data={'source':source}
  if role in ['reference_review','reference_adjudicator']:
   data.update({'builder_draft':read(root/'records/reference_builder'/f'{unit}.json'),'independent_draft':read(root/'records/reference_independent'/f'{unit}.json')})
  if role=='reference_adjudicator':data.update({'blind_draft':read(root/'records/reference_adjudicator_blind'/f'{unit}.json'),'review':read(root/'records/reference_review'/f'{unit}.json')})
 else:
  code=unit.removeprefix('stability-');product=read(root/'bundles/products'/f'{code}.json');source=read(root/'bundles/source'/f"{product['task_id']}.json");data={'source':source,'product':product}
  if role!='alignment':data['reference']=read(root/'references'/f"{product['task_id']}.json")
  if role in ['alignment','score_adjudicator']:
   left=read(root/'records/score_primary'/f'{code}.json');right=read(root/'records/score_secondary'/f'{code}.json')
   from runner import bare_units
   if role=='alignment':data.update({'left_units':bare_units(left),'right_units':bare_units(right)})
   else:data.update({'primary':left,'secondary':right,'alignment':read(root/'records/alignment'/f'{code}.json'),'review_plan':read(root/'review_plans'/f'{code}.json')})
 return clean(data)

def validate(role,record,data,root,unit):
 from runner import reference_validator,review_validator,final_reference_validator,adjudicated_validator
 from contracts import validate_score,validate_alignment
 source=data['source']
 if role=='reference_review':return review_validator(record,source,data['builder_draft'],data['independent_draft'])
 if role=='reference_adjudicator':return final_reference_validator(record,source,[data['builder_draft'],data['independent_draft'],data['blind_draft'],{'obligations':data['review']['omissions']}])
 if role.startswith('reference'):return reference_validator(record,source)
 if role in ['score_primary','score_secondary']:return validate_score(record,source,data['reference'],data['product']['text'],True)
 if role=='alignment':
  code=unit.removeprefix('stability-')
  return validate_alignment(record,read(root/'records/score_primary'/f'{code}.json'),read(root/'records/score_secondary'/f'{code}.json'))
 if role=='score_adjudicator':return adjudicated_validator(record,data,data['review_plan'],data['alignment'])
 raise ValueError('Unsupported role '+role)
