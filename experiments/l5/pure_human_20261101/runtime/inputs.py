"""Load only frozen requirements; no control text, PDF, image, or historical output access."""
from dataclasses import dataclass
from pathlib import Path
import hashlib,json
from .core.schemas import EvidenceCard,Claim,SlotContent

def sha(b):return hashlib.sha256(b).hexdigest()
@dataclass
class InputCase:
 case:str
 original_id:str
 title:str
 actors:str
 text:str
 source_hash:str
 cards:list
 spans:list
 source_name:str
 def seed(self,slot):
  if slot=='primary_actor':return SlotContent(slot=slot,claims=[Claim(content=self.actors,evidence_refs=['TASK-META'])],evidence_card_ids=['TASK-META'])
  return SlotContent(slot=slot)

def parse_case(data,source_name='synthetic.json'):
 spans=[];cards=[]
 meta=f"Given use-case name: {data['title']}\nGiven primary actor / eligible initiating roles: {data['primary_actor']}"
 cards.append(EvidenceCard(card_id='TASK-META',content=meta,source_pages=[],source_locations=[{'source_file':source_name,'kind':'given_task_information'}],region_type='task_metadata',confidence=1.0,source_doc=source_name))
 full=[]
 for part in data['requirements']:
  text=part['text'];full.append(text);chunks=[];a=0
  # Line-preserving, bounded excerpts. Cuts never synthesize facts.
  acc=0
  for line in text.splitlines(keepends=True):
   acc+=len(line)
   if len(text[a:acc].split())>=160:chunks.append((a,acc));a=acc
  if a<len(text):chunks.append((a,len(text)))
  for start,end in chunks:
   cid=f"EC-REQ-{len(spans)+1:03d}"
   sp={'card_id':cid,'part_id':part['source_part_id'],'pdf_page':part['pdf_page'],'start_char':start,'end_char':end,'quote':text[start:end]}
   spans.append(sp)
   cards.append(EvidenceCard(card_id=cid,content=sp['quote'],source_pages=[],source_locations=[{'source_file':source_name,**sp}],region_type='requirement_text',confidence=1.0,source_doc=source_name))
 body='\n\n'.join(full)
 return InputCase(data['id'],data['original_id'],data['title'],data['primary_actor'],body,sha(json.dumps(data,sort_keys=True).encode()),cards,spans,source_name)

def load_case(root,case):
 m=json.loads((root/'inputs/manifest.json').read_text());entry=next((x for x in m if x['case']==case),None)
 if entry is None:raise ValueError('Case outside frozen input allowlist')
 path=root/entry['path'];expected=root/'inputs'/f'{case}.json'
 if path.resolve()!=expected.resolve() or path.is_symlink() or path.parent.resolve()!=(root/'inputs').resolve():raise ValueError('Input path violation')
 b=path.read_bytes()
 if sha(b)!=entry['sha256']:raise ValueError('Input hash mismatch')
 return parse_case(json.loads(b),str(path.relative_to(root)))
