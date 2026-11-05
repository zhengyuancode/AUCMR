"""Enforced transport allowlist. Remote agents have no tools, paths or shell."""
ROLES={
'reference_builder':{'source'},'reference_independent':{'source'},'reference_adjudicator_blind':{'source'},
'reference_review':{'source','independent_draft','builder_draft'},
'reference_adjudicator':{'source','blind_draft','builder_draft','independent_draft','review'},
'score_primary':{'source','reference','product'},'score_secondary':{'source','reference','product'},
'alignment':{'source','product','left_units','right_units'},
'score_adjudicator':{'source','reference','product','primary','secondary','alignment','review_plan'},
}
def guard_payload(role,data):
 assert role in ROLES and isinstance(data,dict)
 assert set(data)==ROLES[role],('payload access violation',role,set(data))
 if 'source' in data:
  s=data['source'];assert set(s)=={'task_id','title','primary_actor','requirements'}
  for p in s['requirements']:assert set(p)=={'source_part_id','pdf_page','text'}
 if 'product' in data:assert set(data['product'])=={'artifact_code','task_id','text'}
 if role=='alignment':
  for side in [data['left_units'],data['right_units']]:
   for u in side:assert set(u)<= {'id','text','quote','start','end','occurrence','category'}
 # Model never receives execution credentials, paths, method labels or hidden documents.
 def check(x):
  if isinstance(x,dict):
   assert not ({'kind','case_id','document','api_key','key_env','generation_log','method_identity','tools','tool_choice','_agent'} & set(x)),'forbidden metadata key'
   for v in x.values():check(v)
  elif isinstance(x,list):
   for v in x:check(v)
 check(data)
def clean(x):
 if isinstance(x,dict):return {k:clean(v) for k,v in x.items() if k!='_agent'}
 if isinstance(x,list):return [clean(v) for v in x]
 return x
