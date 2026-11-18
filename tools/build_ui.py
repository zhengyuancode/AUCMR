"""Build presentation-only UI v2; preserve original task/bundle bytes and answer schema."""
from pathlib import Path
import ast,html,json,re,shutil,hashlib
ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n')
labels={'text':'义务内容','conditions':'成立条件','scope':'适用范围','status':'来源状态','evidence':'来源证据','part_id':'来源片段','pdf_page':'PDF 页码','quote':'原文引用','source_span':'字符定位','start':'起点','end':'终点'}
def value(v):
 if isinstance(v,dict):return '<dl>'+''.join('<dt>'+html.escape(labels.get(k,k))+'</dt><dd>'+value(x)+'</dd>' for k,x in v.items())+'</dl>'
 if isinstance(v,list):return ''.join('<div class="reference-detail">'+value(x)+'</div>' for x in v) or '无'
 return html.escape('未提供' if v is None else str(v))
def pretty(raw):
 m=re.fullmatch(r'<pre>(.*?)</pre>',raw,re.S)
 if not m:return None
 try:v=ast.literal_eval(html.unescape(m[1]))
 except (ValueError,SyntaxError):return None
 if not isinstance(v,dict) or 'text' not in v:return None
 body='<p class="obligation-text">'+html.escape(str(v['text']))+'</p>'
 rest={k:x for k,x in v.items() if k!='text'}
 body+='<details class="reference-detail"><summary>查看来源证据、范围与状态</summary>'+value(rest)+'</details>'
 body+='<details class="reference-detail"><summary>原始记录（完整保留）</summary>'+raw+'</details>'
 return body
checks={}
for package in sorted((ROOT/'reviewer_packages').iterdir()):
 if not package.is_dir():continue
 views={}
 for p in sorted((package/'tasks').glob('*.js')):
  t=json.loads(p.read_text()[len('window.CURRENT_TASK='):].rstrip().rstrip(';'))
  rows={g['key']:[pretty(r.get('html','')) for r in g['rows']] for g in t['groups']}
  if any(any(x for x in r) for r in rows.values()):views[t['task_id']]={'material_sha256':t['material_sha256'],'rows':rows}
 for name in ['app.js','style.css']:shutil.copyfile(ROOT/'tools'/name,package/name)
 (package/'presentation.js').write_text('window.REVIEW_VIEWS='+json.dumps(views,ensure_ascii=False)+';\n')
 for p in package.glob('*.html'):
  text=p.read_text();text=text.replace('<script src="presentation.js"></script>','');text=text.replace('<script src="app.js">','<script src="presentation.js"></script><script src="app.js">');p.write_text(text)
 dump(package/'FILES_SHA256.json',{str(p.relative_to(package)):sha(p) for p in sorted(package.rglob('*')) if p.is_file() and p.name!='FILES_SHA256.json'})
 original=ROOT.parent/'human_review_20261118/reviewer_packages'/package.name
 frozen=[p for p in original.rglob('*') if p.is_file() and (p.parts[-2] in ['tasks','assets'] or p.name in ['bundle.json','bundle.js'])]
 checks[package.name]={'tasks':len(list((package/'tasks').glob('*.js'))),'presentation_records':len(views),'unchanged_data_files':len(frozen),'all_original_data_match':all(sha(p)==sha(package/p.relative_to(original)) for p in frozen)}
dump(ROOT/'DATA_INTEGRITY.json',checks)
print(json.dumps(checks,indent=2))
