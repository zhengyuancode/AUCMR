from pathlib import Path
import tempfile,json,hashlib,subprocess,re
R=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='L5_REVIEW_SYNTHETIC_',dir='/tmp') as tmp:
 t=Path(tmp)
 for d in ['evaluation_tools','config','inputs','private_controls','protocol','runs/formal-v1/SYN']:(t/d).mkdir(parents=True,exist_ok=True)
 inp={'id':'SYN','original_id':'SYN','title':'SYNTHETIC display','primary_actor':'Reader','document':'SYNTHETIC ONLY','requirements':[{'source_part_id':'SRC-001','pdf_page':1,'text':'A reader opens an item < X & Y.'}]}
 p=t/'inputs/SYN.json';p.write_text(json.dumps(inp));(t/'inputs/manifest.json').write_text(json.dumps([{'case':'SYN','path':'inputs/SYN.json','sha256':hashlib.sha256(p.read_bytes()).hexdigest()}]));(t/'config/run.json').write_text(json.dumps({'presentation_seed':20261101}));(t/'protocol/EVALUATION_v1.md').write_text('SYNTHETIC TEST ONLY')
 (t/'private_controls/SYN.txt').write_text('Precondition: item exists.\n1. Reader opens item < X & Y.\n2. System displays item.\n')
 (t/'runs/formal-v1/SYN/status.json').write_text('{"status":"COMPLETED"}');(t/'runs/formal-v1/SYN/A.txt').write_text('Preconditions:\nAn item exists.\nMain scenario:\n1. The reader opens the item.\n2. The system displays it.\n')
 (t/'evaluation_tools/build_review_pack.py').write_text((R/'evaluation_tools/build_review_pack.py').read_text())
 subprocess.run(['python','-B',str(t/'evaluation_tools/build_review_pack.py')],check=True,capture_output=True)
 scripts=0
 for h in (t/'review').rglob('*.html'):
  raw=h.read_text()
  for js in re.findall(r'<script>(.*?)</script>',raw,re.S):
   f=t/'syntax.js';f.write_text(js);subprocess.run(['node','--check',str(f)],check=True,capture_output=True);scripts+=1
  if '/stage_S_products/' in str(h):
   assert 'kind' not in raw and 'case_id' not in raw and 'formal-v1' not in raw
  assert '< X & Y' not in raw
 assert len(list((t/'review/stage_S_products').glob('P*.html')))==2
 report={'passed':True,'synthetic_only':True,'real_model_calls':0,'inline_scripts_checked':scripts,'checks':['source-only R','two anonymized S pages','HTML escapes','private mapping excluded','JavaScript syntax'],'formal_outputs_created':0}
 (R/'validation/review_pack_tests.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
