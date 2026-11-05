from pathlib import Path
import json,hashlib,re,collections,datetime,difflib
import build_materials as m
R=m.R
for d in ['inputs','private_controls','config','prompts','tests','runtime']:(R/d).mkdir(exist_ok=True)
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def sha(x):return hashlib.sha256(x).hexdigest()
cat=m.cat; recipes=m.recipes
for doc,recipe in recipes.items():
 rows=m.docs[doc];parts=[]
 for c in recipe['chunks']:
  a=c['span']['start_line']-1;b=c['span']['end_line_exclusive']-1
  for page in c['span']['pages']:
   inds=[i for i in range(a,b) if rows[i]['page']==page]
   if not inds:continue
   t=m.clean(rows,inds[0],inds[-1]+1)
   if t:parts.append({'pdf_page':page,'normalized_lines':[inds[0]+1,inds[-1]+1],'text':t})
 # Suppress extracted figure fragments only when identifiable as a complete figure area.
 # Source trace remains exact to the normalized extraction with omission markers recorded.
 for p in parts:
  p['source_part_id']=f"SRC-{len([z for z in parts[:parts.index(p)]])+1:03d}"
 recipe['parts']=parts
 recipe['words']=sum(len(p['text'].split()) for p in parts)
 recipe['pdf_pages']=sorted({p['pdf_page'] for p in parts})

selected=[]
for doc in recipes:
 eligible=[x for x in cat if x['document']==doc and x['decision']=='INCLUDE']
 for x in eligible:x['selection_hash']=sha(('PURE-L5-20261101-project-balanced-v1|'+x['id']).encode())
 picked=sorted(eligible,key=lambda x:x['selection_hash'])[:3]
 selected.extend(picked)
 for x in cat:
  if x['document']==doc:x['selected']=x in picked
manifest=[]
for x in cat:
 doc=x['document'];rows=m.docs[doc];a=x['B_span']['start_line']-1;b=x['B_span']['end_line_exclusive']-1
 raw='\n'.join(r['text'] for r in rows[a:b]);display=x['B_text']
 # Agent diagrams are not text use-case behavior; preserve diagram verbatim in raw alongside original PDF.
 if doc=='2003 - agentmom':
  display=re.sub(r'(?s)(Use Case [^\n]+\n).*?Figure [1-4]\. [^\n]+\n',r'\1',display,count=1)
 if doc=='2008 - peering':
  display='\n'.join(t for t in display.splitlines() if not t.startswith('Figure '))
 x['B_raw_sha256']=sha((raw+'\n').encode());x['B_display_sha256']=sha((display+'\n').encode())
 (R/'private_controls'/f"{x['id']}.raw.txt").write_text(raw+'\n')
 (R/'private_controls'/f"{x['id']}.txt").write_text(display+'\n')
 x['B_display_changes']='空白/页眉页脚清理；Agentmom 图中文字及 Peering 图题不属于文本正文，展示时略去；完整原页与 raw 文本保留。无语言/内容修正。'
 x['B_original_pdf']=f"evidence/pure/{doc}.pdf"
 x['B_completeness_flags']={'explicit_preconditions':bool(re.search(r'Pre.?condition',display,re.I)), 'explicit_postconditions_or_result':bool(re.search(r'Post.?condition|Result\s*:',display,re.I)), 'behavior':bool(re.search(r'Flow|Sequence|Scenario|Course|Description|\n1\.',display,re.I)), 'alternatives_or_exceptions':bool(re.search(r'Alternate|Alternative|Exception',display,re.I)), 'word_count':len(display.split())}
 if not x['selected']:continue
 recipe=recipes[doc]
 inp={'id':x['id'],'document':doc,'original_id':x['original_id'],'title':x['title'],'primary_actor':x['primary_actor'],'requirements':recipe['parts'], 'source_pdf_sha256':sha((R/'evidence/pure'/f'{doc}.pdf').read_bytes()),'task':'refine_given_use_case_from_independent_requirements'}
 assert inp['primary_actor'], x['id']
 path=R/'inputs'/f"{x['id']}.json";write(path,inp)
 manifest.append({'case':x['id'],'path':str(path.relative_to(R)),'sha256':sha(path.read_bytes()),'document':doc,'original_id':x['original_id'],'title':x['title'],'input_words':recipe['words'],'source_pages':recipe['pdf_pages']})
write(R/'inputs/manifest.json',manifest)
write(R/'screening/candidates.json',cat)
write(R/'screening/source_boundaries.json',recipes)
# Long lexical matches are a review aid, not a semantic leakage classifier.
leaks=[]
for x in selected:
 b=(R/'private_controls'/f"{x['id']}.txt").read_text();bw=re.findall(r"\w+",b.lower());sw=re.findall(r"\w+",' '.join(p['text'] for p in recipes[x['document']]['parts']).lower())
 matches=difflib.SequenceMatcher(None,bw,sw,autojunk=False).get_matching_blocks()
 long=[{'words':z.size,'B_quote':' '.join(bw[z.a:z.a+z.size])} for z in matches if z.size>=12]
 leaks.append({'case':x['id'],'matches_12_words_or_longer':long})
write(R/'validation/lexical_overlap_review.json',leaks)
# Exact source file identity audit against current L1-L4 project files.
old=Path('/home/dzy/code/TOSEM2026/AUCMR/data');oldpaths=[old/f'{n}.pdf' for n in ['jtvauctions','6016B-sec4-visual-excerpt','qheadache','microcare','rlcs','peazip']]
oldhash={p.name:sha(p.read_bytes()) for p in oldpaths if p.exists()}
newhash={d:sha((R/'evidence/pure'/f'{d}.pdf').read_bytes()) for d in recipes}
write(R/'evidence/overlap_audit.json',{'L1_L4_source_hashes':oldhash,'L5_source_hashes':newhash,'exact_matches':[(a,b) for a,h in newhash.items() for b,g in oldhash.items() if h==g],'collection_overlap':'Both include PURE; distinct named projects, not a new independent corpus. Hash inequality alone does not rule out excerpt duplication.','L0':'MMLongBench-Doc protocol identified; actual document inventory not recovered in this audit. Exact L0 overlap unresolved.','paper_vs_actual':'Current paper older cohort, latest L1-L4 actual 93 targets/6 projects and L3 PURE54 recorded separately.'})
# Human-readable evidence ledger, every occurrence, not only sample.
lines=['# PURE L5 逐例材料核查（生成前）','','页码均为 PDF 文件页码；原文保留于 evidence/pure。INCLUDE 是资格，selected 才是本轮抽中。','']
for d,recipe in recipes.items():
 xs=[x for x in cat if x['document']==d]
 lines += [f'## {d}',f"类型：{recipe['material_type']}。保留输入 {recipe['words']} 英文词，页 {recipe['pdf_pages']}。",'隐藏：'+recipe['hidden'],'平行副本核查：'+recipe['parallel_audit'],'','|目标 ID / 名称|B 页|判定 / 抽中|独立需求与理由|','|---|---|---|---|']
 for x in xs:lines.append(f"|{x['id']} — {x['title']}|{x['B_span']['pages']}|{x['decision']} / {'是' if x['selected'] else '否'}|{x['independent_requirement_locations']}；{x['reason']}|")
 lines+=['']
(R/'screening/EVIDENCE_TABLE.md').write_text('\n'.join(lines)+'\n')
print('COUNTS',dict(collections.Counter(x['decision'] for x in cat)),'selected',len(selected))
print('LONG MATCHES',json.dumps([x for x in leaks if x['matches_12_words_or_longer']],ensure_ascii=False,indent=2))
print('INPUT WORDS',{d:x['words'] for d,x in recipes.items()})
