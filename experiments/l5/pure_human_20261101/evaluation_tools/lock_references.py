"""Lock a HUMAN consensus reference before distributing candidate outputs."""
from pathlib import Path
import argparse,json,hashlib,datetime,re
R=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def norm(s):return re.sub(r'\s+',' ',s).strip()
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('consensus_json',type=Path);a=p.parse_args();src=json.loads(a.consensus_json.read_text())
 if src.get('provenance')!='human_consensus':raise SystemExit('Human consensus attestation is required; do not synthesize a reference')
 if len(set(src.get('reviewer_ids',[])))<2:raise SystemExit('At least two independent reference reviewers required')
 mapping=json.loads((R/'organizer_private/mapping.json').read_text());taskcase={m['task_id']:m['case_id'] for m in mapping};seen=set()
 for t in src['tasks']:
  tid=t['task_id'];assert tid not in seen and tid in taskcase;seen.add(tid)
  inp=R/'inputs'/f'{taskcase[tid]}.json';assert t['source_sha256']==sha(inp)
  parts={p['source_part_id']:p['text'] for p in json.loads(inp.read_text())['requirements']};oids=set()
  for o in t['obligations']:
   assert o['id'] not in oids;oids.add(o['id']);assert o['category'] in ['condition','main','branch','result'];assert isinstance(o['applicable'],bool)
   assert o['text'].strip() and norm(o['quote']) in norm(parts[o['evidence_part']]),(tid,o['id'],'quote must match independent source')
   if not o['applicable']:assert o.get('ambiguity_reason','').strip()
 assert seen==set(taskcase),'All planned targets must be recorded, including unevaluable ones'
 dest=R/'review/references_locked.json'
 if dest.exists():raise SystemExit('Reference already locked; no silent overwrite')
 src.update({'status':'LOCKED_HUMAN_REFERENCE','locked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'consensus_input_sha256':sha(a.consensus_json),'reference_scope':'independent requirements only; B is not gold'})
 dest.write_text(json.dumps(src,ensure_ascii=False,indent=2)+'\n');(R/'review/reference_lock.sha256').write_text(sha(dest)+'\n');print(dest)
