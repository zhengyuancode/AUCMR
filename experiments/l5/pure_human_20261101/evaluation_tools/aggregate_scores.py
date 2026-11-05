"""Aggregate genuine human JSON entries. No model judging or inferred missing ratings."""
from pathlib import Path
import argparse,json,hashlib,random,collections,itertools,statistics
R=Path(__file__).resolve().parents[1]
def mean(xs):
 xs=[x for x in xs if x is not None];return sum(xs)/len(xs) if xs else None
def metrics(score,ref):
 obs={o['id']:o for o in ref['obligations'] if o['applicable']};cov=score['coverage'];assert len(cov)==len(obs) and {c['obligation_id'] for c in cov}==set(obs)
 assert all(c['value'] in [0,0.5,1] and c['reason'].strip() for c in cov)
 units=score['units'];seen={};unique=[];duplicates=0
 for u in units:
  assert u['id'] not in seen and u['text'].strip() and u['reason'].strip() and u['judgment'] in ['S','X','U','Q']
  if u.get('duplicate_of'):
   assert u['duplicate_of'] in seen and not seen[u['duplicate_of']].get('duplicate_of');duplicates+=1
  else:unique.append(u)
  seen[u['id']]=u
 counts=collections.Counter(u['judgment'] for u in unique);den=sum(counts[x] for x in ['S','X','U'])
 k=counts['S']/den if den else (0 if not units else None)
 return {'C':mean([c['value'] for c in cov]),'K':k,'R':duplicates/len(units) if units else None,'N_obligations':len(obs),'M_units':len(units),'D_redundant_units':duplicates,'claim_judgments':dict(counts),'empty_output':not units,'C_by_category':{cat:mean([c['value'] for c in cov if obs[c['obligation_id']]['category']==cat]) for cat in ['condition','main','branch','result']}}
def quantile(xs,p):
 xs=sorted(xs);k=(len(xs)-1)*p;i=int(k);return xs[i]+(xs[min(i+1,len(xs)-1)]-xs[i])*(k-i)
def kappa(pairs,weighted=False):
 if not pairs:return None
 labels=[0,0.5,1] if weighted else sorted({z for p in pairs for z in p})
 n=len(pairs);a=collections.Counter(x for x,y in pairs);b=collections.Counter(y for x,y in pairs)
 loss=lambda x,y:abs(x-y) if weighted else float(x!=y)
 observed=sum(loss(x,y) for x,y in pairs)/n;expected=sum(a[x]*b[y]*loss(x,y) for x in labels for y in labels)/(n*n)
 return 1-observed/expected if expected else None
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('score_directory',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 refpath=R/'review/references_locked.json'
 if not refpath.exists():raise SystemExit('No locked HUMAN reference; no quality scores can be calculated')
 refobj=json.loads(refpath.read_text());assert refobj['status']=='LOCKED_HUMAN_REFERENCE';refs={t['task_id']:t for t in refobj['tasks']};rh=hashlib.sha256(refpath.read_bytes()).hexdigest()
 mapping={m['artifact_code']:m for m in json.loads((R/'organizer_private/mapping.json').read_text())};records=[];keys=set();raw={}
 for f in sorted(a.score_directory.glob('*.json')):
  s=json.loads(f.read_text());assert s['provenance']=='human_entered';assert s['reference_sha256']==rh;code=s['artifact_code'];m=mapping[code];assert s['task_id']==m['task_id'];key=(code,s['reviewer_id']);assert key not in keys;keys.add(key);raw[key]=s
  records.append({**m,'reviewer_id':s['reviewer_id'],**metrics(s,refs[m['task_id']])})
 if not records:raise SystemExit('No human scores; nothing to aggregate')
 byartifact={code:[x for x in records if x['artifact_code']==code] for code in mapping}
 missing=[code for code,x in byartifact.items() if len(x)<2]
 pairrows=[]
 for case in dict.fromkeys(m['case_id'] for m in mapping.values()):
  versions={m['kind']:m for m in mapping.values() if m['case_id']==case};ar=byartifact[versions['A']['artifact_code']];br=byartifact[versions['B']['artifact_code']]
  reviewers={r['reviewer_id'] for r in ar}&{r['reviewer_id'] for r in br}
  if len(reviewers)<2:continue
  row={'case_id':case,'document':versions['A']['document'],'paired_reviewers':sorted(reviewers)}
  for metric in ['C','K','R']:
   pairs=[(next(x[metric] for x in ar if x['reviewer_id']==v),next(x[metric] for x in br if x['reviewer_id']==v)) for v in reviewers];pairs=[(x,y) for x,y in pairs if x is not None and y is not None]
   row[metric]={'A':mean([x for x,y in pairs]),'B':mean([y for x,y in pairs]),'difference_A_minus_B':mean([x-y for x,y in pairs]),'n_raters':len(pairs)}
  pairrows.append(row)
 projects={}
 for doc in dict.fromkeys(x['document'] for x in pairrows):
  rows=[x for x in pairrows if x['document']==doc];projects[doc]={m:{v:mean([x[m][v] for x in rows]) for v in ['A','B','difference_A_minus_B']} for m in ['C','K','R']}
 rng=random.Random(20261101);names=list(projects);boot={m:[] for m in ['C','K','R']}
 if names:
  for _ in range(10000):
   sample=rng.choices(names,k=len(names))
   for m in boot:
    val=mean([projects[d][m]['difference_A_minus_B'] for d in sample])
    if val is not None:boot[m].append(val)
 summary={m:{**{v:mean([x[m][v] for x in projects.values()]) for v in ['A','B','difference_A_minus_B']},'cluster_bootstrap_95_CI':[quantile(boot[m],.025),quantile(boot[m],.975)] if boot[m] else None,'projects_with_paired_data':sum(x[m]['difference_A_minus_B'] is not None for x in projects.values())} for m in boot}
 reviewers=sorted({x['reviewer_id'] for x in records});agreement=[]
 for r1,r2 in itertools.combinations(reviewers,2):
  pairs=[]
  for code in mapping:
   if (code,r1) in raw and (code,r2) in raw:
    c1={c['obligation_id']:c['value'] for c in raw[code,r1]['coverage']};c2={c['obligation_id']:c['value'] for c in raw[code,r2]['coverage']};pairs.extend((c1[k],c2[k]) for k in c1)
  agreement.append({'reviewers':[r1,r2],'coverage_weighted_kappa':kappa(pairs,True),'n_items':len(pairs)})
 out={'status':'COMPLETE_HUMAN_PANEL' if not missing else 'INCOMPLETE_HUMAN_PANEL','missing_two_raters':missing,'individual_scores':records,'paired_cases':pairrows,'projects':projects,'project_equal_weight_summary':summary,'agreement':agreement,'claim_nominal_kappa':'Requires blinded cross-reviewer unit alignment; different self-segmented C001 IDs are not automatically matched. Preserve raw units for alignment.','no_noninferiority_claim':True,'reference_sha256':rh,'scores_provenance':'human JSON entries only; no imputed missing values'}
 if a.output.exists():raise SystemExit('Output exists; choose a new analysis version')
 a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(a.output)
