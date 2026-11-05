"""Offline verification of actual inherited/recovered responses, no new judging calls."""
from pathlib import Path
import json,copy
from contexts import context,validate
from inheritance import data_sha,verify_parent
from transport import sha,write,now
R=Path(__file__).resolve().parent
if __name__=='__main__':
 m=json.loads((R/'inheritance_manifest.json').read_text());verify_parent(R);rows=[]
 for key,entry in m['successful'].items():
  role,unit=key.split('/');data=context(R,role,unit)
  assert data_sha(data)==entry['data_sha256'],key+' input changed'
  if entry.get('recovered_response'):
   actual=json.loads(((R/m['parent'])/entry['parent_request']).read_text());actual_data=json.loads(actual['payload']['messages'][1]['content']);actual_data.pop('citation_catalog',None);actual_data.pop('product_catalog',None)
   assert data_sha(actual_data)==data_sha(data),'Recovered actual request context differs: '+key
  record=json.loads((R/entry['record_path']).read_text());validate(role,copy.deepcopy(record),data,R,unit)
  assert sha(R/entry['record_path'])==entry['record_sha256']
  rows.append({'role':role,'unit':unit,'input_identical':True,'validation_passed':True,'record_untouched':True,'recovered_response':entry.get('recovered_response',False)})
 parent=R.parent/'revision_v4';cfg=json.loads((R/'config.json').read_text());pcfg=json.loads((parent/'config.json').read_text());assert cfg['models']==pcfg['models'] and cfg['bindings']==pcfg['bindings']
 previous=json.loads((parent/'validation/model_binding_check.json').read_text());assert previous['same_provider_parameters'] and previous['parent_two_models_passed']
 write(R/'validation/model_binding_check.json',{'at':now(),'same_provider_parameters':True,'parent_two_models_passed':True,'parent_binding_evidence':previous,'parent_binding_sha256':sha(parent/'validation/model_binding_check.json'),'new_v5_live_model_calls':0,'scope':'Actual provider parameters unchanged; v5 administrative compatibility validated offline.'})
 write(R/'validation/inheritance_check.json',{'at':now(),'count':len(rows),'all_passed':True,'no_new_model_calls':True,'no_new_substantive_judgments':True,'records':rows})
 print(str(len(rows))+' actual prior role responses revalidated; zero new API calls.')
