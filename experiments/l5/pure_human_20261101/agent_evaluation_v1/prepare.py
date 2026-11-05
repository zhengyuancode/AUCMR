"""Supervisor-only material staging, deterministic queues and pre-evaluation freeze."""
from pathlib import Path
import json,hashlib,random,os,math,shutil
from transport import write,sha,now
ROOT=Path(__file__).resolve().parent;UP=ROOT.parent

def prepare():
 assert not (ROOT/'freeze.json').exists(),'Already frozen; do not overwrite'
 # Verify previously frozen inputs, source controls, generation code and outputs.
 upstream={}
 for f in ['protocol/INPUT_AND_CONTROL_FREEZE.json','runs/formal-v1/freeze.json','runs/formal-v2-credential-recovery/freeze.json']:
  m=json.loads((UP/f).read_text());upstream[f]=sha(UP/f)
  for name,h in m['files'].items():assert sha(UP/name)==h,name;upstream[name]=h
 recovery=UP/'runs/formal-v2-credential-recovery'
 m=json.loads((recovery/'freeze.json').read_text());assert sha(recovery/'config.snapshot.json')==m['config_sha256']
 upstream['runs/formal-v2-credential-recovery/config.snapshot.json']=sha(recovery/'config.snapshot.json')
 summary=json.loads((recovery/'summary.json').read_text());assert len(summary['cases'])==29 and all(x['status']=='COMPLETED' for x in summary['cases'])
 for item in summary['cases']:
  for file,key in [('A.txt','output_sha256'),('result.json','result_sha256')]:
   f=recovery/item['case']/file;assert sha(f)==item[key];upstream[str(f.relative_to(UP))]=sha(f)
 mapping=json.loads((UP/'organizer_private/mapping.json').read_text());assert len(mapping)==58
 private=ROOT/'organizer_private';private.mkdir(exist_ok=True);os.chmod(private,0o700);write(private/'mapping.json',mapping);os.chmod(private/'mapping.json',0o600)
 tasks={m['task_id']:m['case_id'] for m in mapping}
 for task,case in tasks.items():
  p=UP/'inputs'/f'{case}.json';inp=json.loads(p.read_text())
  source={'task_id':task,'title':inp['title'],'primary_actor':inp['primary_actor'],'requirements':[{k:q[k] for k in ['source_part_id','pdf_page','text']} for q in inp['requirements']]}
  write(ROOT/'bundles/source'/f'{task}.json',source);upstream[str(p.relative_to(UP))]=sha(p)
 for m in mapping:
  p=recovery/m['case_id']/'A.txt' if m['kind']=='A' else UP/'private_controls'/f"{m['case_id']}.txt"
  write(ROOT/'bundles/products'/f"{m['artifact_code']}.json",{'artifact_code':m['artifact_code'],'task_id':m['task_id'],'text':p.read_text()});upstream[str(p.relative_to(UP))]=sha(p)
 seed=20261101;queues={}
 for i,name in enumerate(['reference','score_primary','score_secondary','adjudication']):
  seq=sorted(tasks if name=='reference' else [m['artifact_code'] for m in mapping]);random.Random(seed+i).shuffle(seq);queues[name]=seq
 sample=[];rng=random.Random(seed)
 for project in sorted({m['document'] for m in mapping}):
  members=sorted(m['artifact_code'] for m in mapping if m['document']==project);rng.shuffle(members);sample+=members[:math.ceil(len(members)*.1)]
 queues['agreement_audit']=sample;queues['stability']=list(sample);random.Random(seed+4).shuffle(queues['stability'])
 models={
  'qwen':{'provider':'Alibaba Model Studio','model':'qwen3.7-plus','base_url':'https://dashscope.aliyuncs.com/compatible-mode/v1','key_env':'AUCMR_JUNXI_DASHSCOPE_API_KEY','extra':{'enable_thinking':False}},
  'deepseek':{'provider':'DeepSeek','model':'deepseek-v4-flash','base_url':'https://api.deepseek.com','key_env':'AUCMR_JUNXI_DEEPSEEK_API_KEY','extra':{'thinking':{'type':'disabled'}}}}
 bindings={'reference_builder':'deepseek','reference_independent':'qwen','reference_review':'qwen','reference_adjudicator_blind':'qwen','reference_adjudicator':'qwen','score_primary':'qwen','score_secondary':'deepseek','alignment':'qwen','score_adjudicator':'qwen'}
 config={'version':'AUCMR_L5_PURE_AGENT_1.0.0','seed':seed,'bootstrap_seed':seed,'bootstrap_replicates':10000,'models':models,'bindings':bindings,'queues':queues,'max_tokens':16384,'temperature':0,'max_attempts':3,'retry_backoff_seconds':5,'timeout_seconds':300,'workers':2,'model_tools':[],'automatic_account_fallback':False,'all_references_before_any_scoring':True,'adjudicate_all_artifacts':True,'agreement_audit_rule':'ceil(0.1 * artifact count) per project, seed20261101; all-artifact adjudication is a superset','stability_sample':'same fixed artifacts, fresh secondary sessions with independent presentation order','preflight_actual_models':{'qwen':'qwen3.7-plus','deepseek':'deepseek-flash'},'generation_actual_model':'deepseek-flash','relation_disclosure':'Secondary uses generator family; primary/reference review/adjudicator share Qwen family but fresh sessions. Not statistically independent models.'}
 write(ROOT/'config.json',config)
 original=(UP/'protocol/EVALUATION_v1.md').read_text();(ROOT/'metric_rules.txt').write_text(original[original.index('## 原子化与判定规则'):original.index('## 聚合与统计')])
 (ROOT/'base_human_protocol.txt').write_text(original)
 write(ROOT/'permissions.json',{'supervisor':'local trusted staging and aggregate; never a scoring role','agent_transport':'stateless chat/completions; no tools, shell, filesystem, web, retrieval or session history','role_allowlists':'isolation.py:ROLES enforced before every request; role data and request logged','source_roles':'only sanitized bundles/source; review can see independently completed drafts; no products/private mapping/PDF/logs','score_roles':'one anonymous product + corresponding source + globally frozen reference + rules; no peer first judgment','alignment_role':'only two anonymized unit segmentations; judgments and duplicate links withheld','adjudication_role':'same anonymous materials + first judgments + blind alignment; no method identity','authorization':'User 2026-11-02 explicitly authorized Agent evaluation and Junxi credentials','identity_limits':'Template wording retained; method may be guessed from style'})
 write(ROOT/'upstream_manifest.json',upstream)
 print('Prepared 29 source bundles, 58 anonymous products and fixed queues; not yet frozen.')

def freeze():
 assert not (ROOT/'freeze.json').exists()
 tests=json.loads((ROOT/'validation/offline.json').read_text());assert tests['passed']
 live=sorted((ROOT/'validation/live_synthetic').glob('*/summary.json'));assert live,'Live synthetic schema check required'
 latest=json.loads(live[-1].read_text());assert all(x['passed'] for x in latest['results']),'Both live synthetic model schema checks must pass before freeze'
 required=['PROTOCOL.md','prompts.json','config.json','metric_rules.txt','base_human_protocol.txt','permissions.json','upstream_manifest.json','prepare.py','runner.py','transport.py','isolation.py','contracts.py','metrics.py','progress.py','start.py','tests_contracts.py','tests_metrics.py','tests_offline.py','calibrate.py','preflight.py','README.md']
 paths=[ROOT/x for x in required]+list((ROOT/'validation').rglob('*json'))+list((ROOT/'bundles').rglob('*.json'))+[ROOT/'organizer_private/mapping.json',ROOT/'validation/offline.json']
 for x in paths:assert x.is_file(),str(x)
 write(ROOT/'freeze.json',{'version':'AUCMR_L5_PURE_AGENT_1.0.0','frozen_at':now(),'timing':'Agent execution protocol fixed AFTER A generation, BEFORE formal reference construction or scoring. Main C/K/R metrics fixed before generation in original human protocol.','files':{str(p.relative_to(ROOT)):sha(p) for p in paths},'formal_calls_before_freeze':0,'synthetic_connectivity_checks_exist':True,'metrics_unchanged':True,'no_human_evaluation_claim':True})
 print('Frozen Agent execution protocol and input hashes.')
if __name__=='__main__':
 import sys
 if '--freeze' in sys.argv:freeze()
 else:prepare()
