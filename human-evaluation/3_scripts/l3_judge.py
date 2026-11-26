import json, sys, io, re
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
BASE = r'C:\Users\JunxiHu\Documents\deepseek-harness\default-workspace\L3_delivery\results'
METHODS = ['AUCMR','CREST w/o cross-slot check','LVLM-Direct','ReAct-Elaborate','TextRAG-Elaborate','VDocRAG-Elaborate','ViDoRAG-Elaborate']

ref = json.load(open(BASE + r'\common-references93.json', encoding='utf-8'))
case = json.load(open(BASE + r'\case-results93.json', encoding='utf-8'))
req_meta = {}
for r in ref['references']:
    p = r['target']['project_id']
    for it in r['reference'].get('requirements', []):
        req_meta[it['requirement_id']] = (it.get('text',''), p, it.get('status','CONFIRMED'))
req_labels = defaultdict(dict)
for row in case['rows']:
    m = row['method']
    for rid, j in row.get('reconciled_judgments', {}).items():
        req_labels[rid][m] = j.get('label')
univ = [rid for rid, labels in req_labels.items()
        if labels and all(labels.get(m)=='NOT_COVERED' for m in METHODS)
        and req_meta.get(rid,(None,None,''))[2]=='CONFIRMED']

# 按项目的"业务无关"判定（逐条阅读后归纳）
PROJ_KW = {
 'jtvauctions': ['icon','button','checkbox','hyperlink','link','footer','header','footnote','grayed','gray','popup','pop-up',
   'textbox','text box','dropdown','drop-down','page','SEO','title tag','thumbnail','image','items per page','go to page',
   'previous and next','arrow icon','column','color','colour','resolution','scroll','search box','wildcard','control key',
   'endeca','global site header','global footer','omniture','google analytics','mailto','Dear [FirstName]','headline',
   'pre-checked','formatting','browser','cookied','EDIT button','delineated','533 x 400','SKU','per page'],
 'microcare': ['capture','numeric entry','default value','date-selection','valid from','valid up to','list of options',
   'footer','header','cumulative total','grid','report header','report footer'],
 'milstd6016b_sec4': ['display','screen','GUI','colour','color','icon','alert the operator'],  # 极少
 'peazip': ['ctrl+','middle-click','middle click','keyboard shortcut','f11','browser','refresh the file manager',
   'drag','drop','interface'],
 'qheadache': ['board','pixel','black','yellow','dialog','menu bar','mouse'],
 'rlcs': ['GUI','screen','display','icon','colour','color','audible','2 seconds','two seconds','logon screen',
   'checksum','hash','MD5','128 bits','timestamp','date/time','date and time','report screen','printer setup',
   'input field','input fields'],
}

def is_irrelevant(rid):
    text, p, st = req_meta[rid]
    t = text.lower()
    return any(k.lower() in t for k in PROJ_KW.get(p, []))

irr=[]; rel=[]
for rid in univ:
    (irr if is_irrelevant(rid) else rel).append(rid)

print('=== 逐条判断结果（437 条中） ===', flush=True)
print('业务无关(删):', len(irr), flush=True)
print('业务相关(留):', len(rel), flush=True)

byp=defaultdict(lambda:[0,0])
for rid in irr: byp[req_meta[rid][1]][0]+=1
for rid in rel: byp[req_meta[rid][1]][1]+=1
print('\n=== 分项目 删/留 ===', flush=True)
for p in sorted(byp):
    a,b=byp[p]; print(f'  {p:20s} 删={a:3d}  留={b:3d}', flush=True)

# 保存删除清单
with io.open(r'C:\Users\JunxiHu\Documents\deepseek-harness\default-workspace\l3_delete_list.txt','w',encoding='utf-8') as f:
    for rid in sorted(irr):
        f.write(rid+'\n')
print('\n删除清单已写入 l3_delete_list.txt，共', len(irr), '条', flush=True)

# 打印每项目删除样本，供审核
print('\n=== 各项目"删"的样本 ===', flush=True)
from collections import Counter
c=Counter()
for rid in irr:
    p=req_meta[rid][1]
    if c[p]<4:
        print(f'  [{p}] {req_meta[rid][0][:110]}', flush=True); c[p]+=1
print('\n=== 各项目"留"的样本（确认没误删业务） ===', flush=True)
c=Counter()
for rid in rel:
    p=req_meta[rid][1]
    if c[p]<4:
        print(f'  [{p}] {req_meta[rid][0][:110]}', flush=True); c[p]+=1
