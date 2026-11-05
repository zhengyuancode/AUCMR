"""Read-only integrity verification. No experiment imports or network access."""
from pathlib import Path
import argparse,hashlib,json,sys,re,zipfile,xml.etree.ElementTree as ET
P=Path(__file__).resolve().parents[1];R=P.parents[3];D=P/'data'
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def load(p):return json.loads(Path(p).read_text())
def lines(p):return [json.loads(x) for x in Path(p).read_text().splitlines() if x]
def verify_original(rel,h):
 assert not Path(rel).is_absolute(),rel
 p=(R/rel).resolve();assert p.is_relative_to(R),rel
 assert p.is_file() and sha(p)==h,rel
parser=argparse.ArgumentParser();parser.add_argument('--with-repository',action='store_true');args=parser.parse_args()
manifest=load(P/'package_manifest.json');listed=set()
for item in manifest['files']:
 p=P/item['path'];assert p.is_file(),item['path'];assert p.stat().st_size==item['bytes'];assert sha(p)==item['sha256'],item['path'];listed.add(item['path'])
actual={str(p.relative_to(P)) for p in P.rglob('*') if p.is_file() and p.name!='package_manifest.json'}
assert listed==actual,{'extra':sorted(actual-listed),'missing':sorted(listed-actual)}
for p in P.glob('*.md'):
 for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)',p.read_text()):
  if not re.match(r'https?://',link):assert (p.parent/link.split('#')[0]).exists(),(p.name,link)
 # Server absolute paths are not portable and should not be present.
 assert '/home/' not in p.read_text(),p.name
from analyze import calculate
summary,tables=calculate()
assert summary==load(D/'analysis_summary.json')
prov=load(D/'provenance.json');records=lines(D/'final_records.jsonl');responses=lines(D/'accepted_responses.jsonl');reqs=lines(D/'request_accounting.jsonl');ids={x['request_id'] for x in reqs}
assert len(ids)==len(reqs)==492
assert {r['evaluation_id'] for r in records}==set(prov)=={r['evaluation_id'] for r in responses}
for r in records:
 assert r['status']=='COMPLETE' and len(r['ratings'])==7
 assert r['version']==prov[r['evaluation_id']]['version']
 assert all(a['request_id'] in ids for a in r['attempts'])
for a in responses:
 assert a['request_id'] in ids and a['finish_reason']=='stop'
 # JSON syntax remains an original result; unwrap only a whole Markdown fence.
 body=a['body'].strip()
 if body.startswith('```') and body.endswith('```'):body=body.split('\n',1)[1].rsplit('```',1)[0]
 obj=json.loads(body);assert set(obj)=={'ratings'}
 from_export=next(r for r in records if r['evaluation_id']==a['evaluation_id'])
 for r in from_export['ratings']:
  if r['status']=='SCORED':
   raw=obj['ratings'][r['label']]
   assert all(raw[k]==r[k] for k in ('pcs','pcs_reason','prc','prc_reason'))
reg=load(D/'regeneration_scope.json');affected={e for x in reg for e in x['affected_evaluation_ids']};assert len(affected)==9
# Verify existing Excel task mapping, without recalculating/rewriting the workbook.
z=zipfile.ZipFile(D/'L4-93cases-42agents.xlsx');ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
ss=[''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml'))] if 'xl/sharedStrings.xml' in z.namelist() else []
def sheet(n):
 rows=[]
 for row in ET.fromstring(z.read('xl/worksheets/sheet'+str(n)+'.xml')).findall('.//s:row',ns):
  cells=[]
  for c in row:
   v=c.find('s:v',ns);s=v.text if v is not None else ''.join(c.itertext());cells.append(ss[int(s)] if c.get('t')=='s' else s)
  rows.append(cells)
 return rows[7:]
a=load(D/'allocation.json');lookup={(e['task_id'],e['model_key']):e['agent_id'] for e in a['evaluations']};cases=sheet(1);agents=sheet(2);assert len(cases)==93 and len(agents)==42
for row in cases:
 for i,m in enumerate(('glm','doubao','qwen')):assert lookup[row[2],m]==row[4+i]
labels={row[0]:row[2] for row in cases};byagent={r['agent_id']:r for r in a['agents']}
for row in agents:assert sorted(labels[x.strip()] for x in row[5].split(','))==sorted(byagent[row[1]]['task_ids'])
original_count=0
if args.with_repository:
 checks={}
 for x in load(D/'source_artifacts.json'):checks[x['path']]=x['sha256']
 cat=load(D/'catalog.json')
 for x in cat['input_bindings']+cat['sources']:checks[x['path']]=x.get('sha256',x.get('expected_sha256'))
 for x in prov.values():checks[x['path']]=x['sha256']
 for x in reqs:checks[x['path']]=x['sha256']
 for x in responses:
  checks[x['runtime_path']]=x['runtime_sha256'];checks[x['attempt_path']]=x['attempt_sha256']
 for x in reg:checks[x['replacement_path']]=x['replacement_sha256']
 for k,v in load(D/'implementation_manifest.json')['files'].items():checks[k]=v
 for rel,h in checks.items():verify_original(rel,h)
 original_count=len(checks)
print(json.dumps({'verification':'PASS','package_files':len(listed),'evaluations':279,'method_records':1953,'unique_requests':492,'original_files_verified':original_count,'live_calls':0},ensure_ascii=False))
