"""Remote roles have only these JSON inputs; no tools or filesystem access."""
ROLES={
 'scope_reader_one':{'source','prior_reference'},
 'scope_reader_two':{'source','prior_reference'},
 'scope_adjudicator':{'source','prior_reference','interpretations'},
 'coverage_primary':{'source','reference','product'},
 'coverage_secondary':{'source','reference','product'},
 'coverage_adjudicator':{'source','reference','product','independent_ratings'},
}
def guard_payload(role,data):
 assert role in ROLES,'Unknown remote role'
 generated={'citation_catalog'}|({'product_catalog'} if 'product' in data else set())
 assert set(data) in (ROLES[role],ROLES[role]|generated),'Role payload exceeds allowed scope'
 assert set(data['source'])=={'task_id','title','primary_actor','requirements'}
 for p in data['source']['requirements']:assert set(p)=={'source_part_id','pdf_page','text'}
 if 'product' in data:assert set(data['product'])=={'artifact_code','task_id','text'}
 def visit(x):
  if isinstance(x,dict):
   assert not {'kind','case_id','document','api_key','key_env','generation_log','method_identity','tools','tool_choice','_agent'}&set(x),'Forbidden metadata'
   for v in x.values():visit(v)
  elif isinstance(x,list):
   for v in x:visit(v)
 visit(data)
 if 'citation_catalog' in data:
  from citations import catalog
  assert data['citation_catalog']==catalog(data['source'])
 if 'product_catalog' in data:
  from product_citations import product_catalog
  assert data['product_catalog']==product_catalog(data['product'])

def clean(x):
 if isinstance(x,dict):return {k:clean(v) for k,v in x.items() if k!='_agent'}
 if isinstance(x,list):return [clean(v) for v in x]
 return x
