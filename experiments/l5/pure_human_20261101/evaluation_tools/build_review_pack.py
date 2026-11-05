"""Presentation only. Never calls models, changes A/B semantics, or fabricates scores."""
from pathlib import Path
import json,hashlib,random,html,os,shutil,datetime
R=Path(__file__).resolve().parents[1];P=R/'review';P.mkdir(exist_ok=True)
PRIVATE=R/'organizer_private';PRIVATE.mkdir(exist_ok=True);os.chmod(PRIVATE,0o700)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def esc(s):return html.escape(str(s))
config=json.loads((R/'config/run.json').read_text());manifest=json.loads((R/'inputs/manifest.json').read_text());cases={x['case']:json.loads((R/x['path']).read_text()) for x in manifest}
# Freeze identity assignment independently of generation success or content.
assign=PRIVATE/'mapping.json'
if assign.exists():mapping=json.loads(assign.read_text())
else:
 order=list(cases);rng=random.Random(config['presentation_seed']);rng.shuffle(order)
 tasks={case:f'T{i+1:03d}' for i,case in enumerate(order)};pool=[(case,kind) for case in order for kind in ['A','B']];rng.shuffle(pool)
 mapping=[{'artifact_code':f'P{i+1:03d}','task_id':tasks[c],'case_id':c,'kind':k,'document':cases[c]['document']} for i,(c,k) in enumerate(pool)]
 write(assign,mapping);os.chmod(assign,0o600)
tasks={m['case_id']:m['task_id'] for m in mapping}
CSS='''body{max-width:1100px;margin:32px auto;padding:0 24px;font:16px/1.65 system-ui,sans-serif;color:#192333;background:#fafafa}h1{font-size:26px}h2{font-size:21px}section,article{background:white;padding:20px;margin:20px 0;border:1px solid #d9dfeb;border-radius:8px}pre{white-space:pre-wrap;font:15px/1.6 Georgia,serif;overflow-wrap:anywhere}table{width:100%;border-collapse:collapse}td,th{border:1px solid #ccd3de;padding:8px;text-align:left}textarea,input,select{font:inherit;max-width:100%;box-sizing:border-box}textarea{width:100%;min-height:80px}button{padding:9px 16px;margin:8px 8px 8px 0}.hint{color:#526174}.warning{background:#fff2dc;padding:12px}a{color:#164e9e}@media print{button,.controls{display:none}body{max-width:none;margin:0}section{break-inside:avoid}}'''
def page(title,body,script=''):
 return '<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>'+esc(title)+'</title><style>'+CSS+'</style><body><h1>'+esc(title)+'</h1>'+body+('<script>'+script+'</script>' if script else '')+'</body></html>'
commonjs='''function saveJSON(obj,name){let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(obj,null,2)],{type:'application/json'}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}function cell(tag,value){let x=document.createElement(tag);x.textContent=value;return x}'''
rd=P/'stage_R_requirements';rd.mkdir(exist_ok=True)
links=[]
for case,inp in cases.items():
 task=tasks[case];source_digest=next(x['sha256'] for x in manifest if x['case']==case)
 body=f'<p>目标 {task}：{esc(inp["title"])}</p><p>给定主要参与者：{esc(inp["primary_actor"])}</p><p class="warning">阶段 R：只能阅读本页独立需求；不要查看任何候选产物或自行检索原文中的用例。先建立并锁定共同义务清单。</p><p><a href="../protocol.txt">完整评价协议</a></p>'
 for part in inp['requirements']:body+=f'<section id="{part["source_part_id"]}"><h2>{part["source_part_id"]} · 源 PDF 页 {part["pdf_page"]}</h2><pre>{esc(part["text"])}</pre></section>'
 body+='''<article class="controls"><h2>源证据标注表</h2><label>评审者匿名 ID <input id="reviewer" required></label><p>每行一项独立义务。填写证据 part ID 和原句；只凭标题、常识或推测的条目不能纳入。</p><div id="rows"></div><button onclick="add()">添加义务</button><button onclick="download()">导出阶段 R 草稿</button><p>导出只是个人草稿。组织者须协调两人分歧并用 lock_references.py 锁定共同清单，然后才发阶段 S。</p></article>'''
 script=commonjs+'''let count=0;function add(){count++;let box=cell('section','');box.className='obligation';box.dataset.id='O'+String(count).padStart(3,'0');box.innerHTML='<b>'+box.dataset.id+'</b><p>类别 <select class="cat"><option>condition</option><option>main</option><option>branch</option><option>result</option></select></p><p>义务<textarea class="text"></textarea></p><p>证据 part ID <input class="part"></p><p>证据原句<textarea class="quote"></textarea></p><label>适用且无未解决歧义 <input type="checkbox" class="app" checked></label><p>歧义/排除理由<textarea class="note"></textarea></p>';document.getElementById('rows').append(box)}function download(){let reviewer=document.getElementById('reviewer').value.trim();if(!reviewer)return alert('填写评审者 ID');let obligations=[...document.querySelectorAll('.obligation')].map(e=>({id:e.dataset.id,category:e.querySelector('.cat').value,text:e.querySelector('.text').value,evidence_part:e.querySelector('.part').value,quote:e.querySelector('.quote').value,applicable:e.querySelector('.app').checked,ambiguity_reason:e.querySelector('.note').value}));if(obligations.some(x=>!x.text||!x.evidence_part||!x.quote))return alert('每项需义务、来源及原句');saveJSON({task_id:TASK,reviewer_id:reviewer,source_sha256:SOURCE,obligations,created_at:new Date().toISOString(),status:'individual_reference_draft'},TASK+'-'+reviewer+'-reference.json')}add();'''
 script='const TASK='+json.dumps(task)+';const SOURCE='+json.dumps(source_digest)+';'+script
 (rd/f'{task}.html').write_text(page(f'{task} · 独立需求与证据标注',body,script));links.append(f'<li><a href="{task}.html">{task} — {esc(inp["title"])}</a>（{esc(inp["document"])}）</li>')
(rd/'index.html').write_text(page('阶段 R：29 个目标的共同需求','<p>本包不含 A、B 或身份映射。先完成需求证据标注并锁定。</p><ol>'+''.join(links)+'</ol>'))
shutil.copyfile(R/'protocol/EVALUATION_v1.md',P/'protocol.txt')
# Find authorized completed generation batch; no choosing by output score.
runname='formal-v2-credential-recovery' if (R/'runs/formal-v2-credential-recovery/freeze.json').exists() else 'formal-v1'
run=R/'runs'/runname
available=[];missing=[]
for case in cases:
 p=run/case/'status.json'
 st=json.loads(p.read_text()) if p.exists() else {}
 (available if st.get('status')=='COMPLETED' else missing).append(case)
# Stage S is intentionally not published while a full batch is absent.
write(P/'readiness.json',{'generation_run':runname,'planned_targets':len(cases),'completed_A':len(available),'missing_A':missing,'stage_R_ready':True,'stage_S_ready':len(missing)==0,'scores':'NOT_COLLECTED','B_role':'candidate, not reference answer'})
write(PRIVATE/'package_inventory.json',[{**m,'B_path':f'private_controls/{m["case_id"]}.txt' if m['kind']=='B' else None,'A_path':f'runs/{runname}/{m["case_id"]}/A.txt' if m['kind']=='A' else None} for m in mapping])
if missing:
 (P/'STATUS.txt').write_text(f'阶段 R 已就绪。A 完成 {len(available)}/{len(cases)}；未发放阶段 S，不能开始配对质量评分。B 与匿名映射保存在组织者私有目录。\n')
 print('Stage R ready; paired Stage S blocked:',len(missing),'missing A')
else:
 sd=P/'stage_S_products';sd.mkdir(exist_ok=True);index=[]
 for m in mapping:
  case=m['case_id'];code=m['artifact_code'];task=m['task_id'];inp=cases[case]
  path=(run/case/'A.txt') if m['kind']=='A' else (R/'private_controls'/f'{case}.txt')
  body=path.read_text()
  text=f'<p>目标：{task} — {esc(inp["title"])}</p><p>给定主要参与者：{esc(inp["primary_actor"])}</p><p><a href="../stage_R_requirements/{task}.html">共同需求</a> · <a href="../protocol.txt">规则</a></p><article><h2>文本用例</h2><pre>{esc(body)}</pre></article>'
  text+='''<article class="controls"><h2>人工评分</h2><p>载入已锁定的共同参考清单 JSON；不要修改它来迁就本产物。</p><input id="ref" type="file" accept=".json"><p>评审者 ID <input id="reviewer"></p><h3>完整性</h3><div id="coverage"></div><h3>产物原子语义单元</h3><p>逐一分解行为/条件/结果。给定名称/参与者不单独评分；重复单元仍填写并指定重复的 ID。</p><div id="claims"></div><button onclick="addClaim()">添加单元</button><button onclick="downloadScore()">导出人工评分 JSON</button></article>'''
  script=commonjs+'''let reference=null,refDigest=null,n=0;document.getElementById('ref').onchange=async e=>{let raw=await e.target.files[0].text();reference=JSON.parse(raw);let bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw));refDigest=[...new Uint8Array(bytes)].map(x=>x.toString(16).padStart(2,'0')).join('');let t=reference.tasks.find(x=>x.task_id===TASK);if(!t)return alert('清单没有此目标');document.getElementById('coverage').replaceChildren();for(let o of t.obligations.filter(x=>x.applicable)){let row=cell('section','');row.dataset.id=o.id;row.className='coverage';row.append(cell('p',o.id+' '+o.text));let s=document.createElement('select');for(let v of ['','0','0.5','1']){let op=cell('option',v||'请选择');op.value=v;s.append(op)}row.append(s);let reason=document.createElement('textarea');reason.placeholder='覆盖位置、遗漏/部分覆盖理由及源证据';row.append(reason);document.getElementById('coverage').append(row)}};function addClaim(){n++;let row=cell('section','');row.className='claim';row.dataset.id='C'+String(n).padStart(3,'0');row.innerHTML='<b>'+row.dataset.id+'</b><p>原子断言<textarea class="text"></textarea></p><p>类别 <select class="cat"><option>condition</option><option>main</option><option>branch</option><option>result</option></select> 判定 <select class="judgment"><option></option><option>S</option><option>X</option><option>U</option><option>Q</option></select></p><p>证据/理由<textarea class="reason"></textarea></p><p>如果完全冗余，重复哪个更早单元 ID <input class="duplicate"></p>';document.getElementById('claims').append(row)}function downloadScore(){if(!reference)return alert('先载入已锁定清单');let reviewer=document.getElementById('reviewer').value.trim();if(!reviewer)return alert('填写评审者 ID');let coverage=[...document.querySelectorAll('.coverage')].map(e=>({obligation_id:e.dataset.id,value:e.querySelector('select').value===''?null:Number(e.querySelector('select').value),reason:e.querySelector('textarea').value}));let units=[...document.querySelectorAll('.claim')].filter(e=>e.querySelector('.text').value.trim()).map(e=>({id:e.dataset.id,text:e.querySelector('.text').value,category:e.querySelector('.cat').value,judgment:e.querySelector('.judgment').value,reason:e.querySelector('.reason').value,duplicate_of:e.querySelector('.duplicate').value.trim()||null}));if(coverage.some(x=>x.value===null||!x.reason)||units.some(x=>!x.judgment||!x.reason))return alert('判定与理由不能为空');saveJSON({artifact_code:CODE,task_id:TASK,reviewer_id:reviewer,reference_sha256:refDigest,coverage,units,created_at:new Date().toISOString(),provenance:'human_entered'},CODE+'-'+reviewer+'-score.json')}addClaim();'''
  (sd/f'{code}.html').write_text(page(f'候选产物 {code}',text,'const TASK='+json.dumps(task)+';const CODE='+json.dumps(code)+';'+script));index.append(f'<li><a href="{code}.html">{code} · {task}</a></li>')
 (sd/'index.html').write_text(page('阶段 S：匿名产物评分','<p>仅在阶段 R 的共同清单锁定后发放。本包不含方法映射。请独立评分，不在完成前讨论身份。</p><ol>'+''.join(index)+'</ol>'))
 (P/'STATUS.txt').write_text('29 对产物就绪；先组织阶段 R，锁定清单后才发阶段 S。尚无人评分。\n');print('Stage S ready: 58 artifacts; no scores generated')
