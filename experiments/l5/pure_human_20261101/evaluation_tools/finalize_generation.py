"""Structural/provenance checks and packaging only; never judges content quality."""
from pathlib import Path
import sys,json,hashlib,collections,subprocess,zipfile,html,re,datetime,shutil
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R))
from runtime.inputs import load_case
from runtime.pipeline import validate_result,render_result
RUN=R/'runs/formal-v2-credential-recovery'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def verify_manifest(path):
 m=json.loads(path.read_text())
 for p,h in m['files'].items():assert digest(R/p)==h,p
 return m
if __name__=='__main__':
 if not (RUN/'summary.json').exists():raise SystemExit('Generation still running; finalization not performed')
 inp=verify_manifest(R/'protocol/INPUT_AND_CONTROL_FREEZE.json');verify_manifest(R/'runs/formal-v1/freeze.json');rec=verify_manifest(RUN/'freeze.json');assert digest(RUN/'config.snapshot.json')==rec['config_sha256']
 config=json.loads((RUN/'config.snapshot.json').read_text());summary=json.loads((RUN/'summary.json').read_text());assert {x['case'] for x in summary['cases']}==set(config['cases'])
 good=[];failed=[]
 for s in summary['cases']:
  case=s['case'];d=RUN/case
  if s['status']!='COMPLETED':failed.append(s);continue
  assert digest(d/'A.txt')==s['output_sha256'];assert digest(d/'result.json')==s['result_sha256']
  result=json.loads((d/'result.json').read_text());validate_result(result,load_case(R,case));assert render_result(result)==(d/'A.txt').read_text()
  good.append(case)
 requests=[json.loads(p.read_text()) for p in RUN.glob('*/requests/*/status.json')]
 usage={k:sum((x.get('usage') or {}).get(k,0) or 0 for x in requests) for k in ['prompt_tokens','completion_tokens','total_tokens']}
 subprocess.run([sys.executable,'-B',str(R/'evaluation_tools/build_review_pack.py')],check=True)
 mapping=json.loads((R/'organizer_private/mapping.json').read_text())
 if len(good)==len(config['cases']):
  for m in mapping:
   f=R/'review/stage_S_products'/f"{m['artifact_code']}.html";s=f.read_text();body=re.search(r'<article><h2>文本用例</h2><pre>(.*?)</pre></article>',s,re.S)
   src=RUN/m['case_id']/'A.txt' if m['kind']=='A' else R/'private_controls'/f"{m['case_id']}.txt"
   assert body and html.unescape(body[1])==src.read_text(),m['artifact_code']
   assert 'organizer_private' not in s and 'formal-v2-credential-recovery' not in s
  with zipfile.ZipFile(R/'review/paired_review_pack.zip','w',zipfile.ZIP_DEFLATED) as z:
   for folder in ['stage_R_requirements','stage_S_products']:
    for p in sorted((R/'review'/folder).glob('*.html')):z.write(p,p.relative_to(R/'review'))
   z.write(R/'review/protocol.txt','protocol.txt')
  with zipfile.ZipFile(R/'review/paired_review_pack.zip') as z:assert not any('private' in n or 'mapping' in n for n in z.namelist())
 status={'generation_run':RUN.name,'A_completed':len(good),'planned':len(config['cases']),'failed':failed,'request_count':len(requests),'http_status_counts':dict(collections.Counter(str(x.get('http_status')) for x in requests)),'response_model_counts':dict(collections.Counter(str(x.get('response_model')) for x in requests)),'usage':usage,'all_frozen_files_verified':True,'A_text_matches_untouched_result':True,'source_control_input_hashes_verified':True,'presentation_semantics_verified':len(good)==len(config['cases']),'human_scores':'NOT_COLLECTED','independent_human_reference':'NOT_CREATED','credential_source':'AUCMR_JUNXI_DEEPSEEK_API_KEY','credential_authorized':True,'recovery_executed':True,'finalized_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 old=R/'validation/final_status.json';history=R/'validation/final_status.before_recovery.json'
 if old.exists() and not history.exists():shutil.copyfile(old,history)
 write(old,status);write(RUN/'delivery_audit.json',status)
 label=f"A 已完成 {len(good)}/{len(config['cases'])}；人工评分尚未开展。"
 if failed:label+=' 技术失败保留，未按质量重新生成或替换目标。'
 else:label+=' 58 份匿名产物包已就绪，须先完成阶段 R 证据标注并锁定，再发阶段 S。'
 p=R/'README.md';s=p.read_text();s=re.sub(r'当前状态：\*\*.*?\*\*','当前状态：**'+label+'**',s,count=1);p.write_text(s)
 p=R/'REPORT.md';s=p.read_text();title='## 凭据恢复后的最终交付状态\n\n'+label+f"\n\n用户明确授权后，仅以 Junxi DeepSeek 凭据更换原余额不足凭据，恢复批次完成 {len(requests)} 次请求。原模型、输入、B、提示与评价协议哈希验证通过；A 展示文本与未编辑结构化结果相符。原 formal-v1 的 29 次 HTTP402 全部保留。详细状态和 token 用量见 runs/{RUN.name}/delivery_audit.json；没有把内部模型核验当人工分数。\n\n"
 s=s.split('\n',1)[0]+'\n\n'+title+s.split('\n',1)[1];p.write_text(s)
 p=R/'review/ORGANIZER.md';s=p.read_text();s=re.sub(r'当前 A=0/29，阶段 S 未就绪；.*?现可先进行阶段 R；阶段 S 发放仍以 readiness.json 为准。',label+' 请先执行阶段 R，不能跳过独立参考锁定。',s,count=1,flags=re.S);p.write_text(s)
 print(json.dumps({'A_completed':len(good),'failed':len(failed),'requests':len(requests),'package_ready':not failed,'human_scores':'NOT_COLLECTED'},ensure_ascii=False))
