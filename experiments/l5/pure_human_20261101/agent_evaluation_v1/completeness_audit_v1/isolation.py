"""Narrow remote annotation audit payload, no tools or repository access."""
def guard_payload(role,data):
 assert role in ('annotation_review_one','annotation_review_two')
 expected={'source','reference','product','annotation_cases','existing_coverage'}
 assert set(data) in (expected,expected|{'citation_catalog','product_catalog'})
 assert set(data['source'])=={'task_id','title','primary_actor','requirements'}
 assert set(data['product'])=={'task_id','artifact_code','text'}
 for p in data['source']['requirements']:
  assert set(p)=={'source_part_id','pdf_page','text'}
 def check(x):
  if isinstance(x,dict):
   assert not {'kind','case_id','document','api_key','key_env','generation_log','method_identity','tools','tool_choice','_agent'}&set(x)
   for v in x.values():check(v)
  elif isinstance(x,list):
   for v in x:check(v)
 check(data)
 if 'citation_catalog' in data:
  from citations import catalog
  assert data['citation_catalog']==catalog(data['source'])
  from product_citations import product_catalog
  assert data['product_catalog']==product_catalog(data['product'])

def clean(x):
 if isinstance(x,dict):return {k:clean(v) for k,v in x.items() if k!='_agent'}
 if isinstance(x,list):return [clean(v) for v in x]
 return x
