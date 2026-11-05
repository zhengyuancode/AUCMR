"""Offline source-only verification of inherited records. No model calls, no grading."""
from pathlib import Path
import json,copy
from isolation import clean
from inheritance import data_sha,verify_parent
from runner import reference_validator,review_validator,final_reference_validator
from transport import sha,write,now
R=Path(__file__).resolve().parent
if __name__=='__main__':
 m=json.loads((R/'inheritance_manifest.json').read_text());verify_parent(R);rows=[]
 for key,entry in m['successful'].items():
  role,task=key.split('/');source=json.loads((R/'bundles/source'/f'{task}.json').read_text());data={'source':source}
  def draft(name):return json.loads((R/'records'/name/f'{task}.json').read_text())
  if role in ['reference_review','reference_adjudicator']:
   builder=draft('reference_builder');independent=draft('reference_independent')
   data.update({'builder_draft':builder,'independent_draft':independent})
  if role=='reference_adjudicator':data.update({'blind_draft':draft('reference_adjudicator_blind'),'review':draft('reference_review')})
  assert data_sha(clean(data))==entry['data_sha256'],key+' input changed'
  record=json.loads((R/entry['record_path']).read_text())
  if role=='reference_review':review_validator(copy.deepcopy(record),source,builder,independent)
  elif role=='reference_adjudicator':final_reference_validator(copy.deepcopy(record),source,[builder,independent,data['blind_draft'],{'obligations':data['review']['omissions']}])
  else:reference_validator(copy.deepcopy(record),source)
  assert sha(R/entry['record_path'])==entry['record_sha256']
  rows.append({'role':role,'task_id':task,'input_identical':True,'validation_passed':True,'record_untouched':True})
 parent=R.parent/'revision_v2';cfg=json.loads((R/'config.json').read_text());pcfg=json.loads((parent/'config.json').read_text())
 assert cfg['models']==pcfg['models'] and cfg['bindings']==pcfg['bindings']
 live=sorted((parent/'validation/live_synthetic').glob('*/summary.json'))[-1];oldlive=json.loads(live.read_text());assert all(x['passed'] for x in oldlive['results'])
 write(R/'validation/model_binding_check.json',{'at':now(),'same_provider_parameters':True,'parent_two_models_passed':True,'parent_summary':str(live.relative_to(parent)),'parent_summary_sha256':sha(live),'new_v3_live_model_calls':0,'scope':'Actual provider validation inherited from v2 because HTTP model/binding/parameters unchanged; v3 source catalog validated offline only'})
 write(R/'validation/inheritance_check.json',{'at':now(),'count':len(rows),'all_passed':True,'no_new_model_calls':True,'no_product_judgments':True,'records':rows})
 print('129 inherited source-role responses revalidated; context hashes match; zero new API calls.')
