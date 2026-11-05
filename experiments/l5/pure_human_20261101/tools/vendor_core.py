"""Build isolated AUCMR core from existing source, not from ECHO's task prompts."""
from pathlib import Path
import ast,shutil,difflib,json,hashlib
R=Path(__file__).resolve().parents[1];U=R.parents[2];E=R.parent/'aucmr_echo'
C=R/'runtime/core';C.mkdir(parents=True,exist_ok=True)
# Use existing text-adapter technical guards; restore original task semantics.
s=(E/'runtime/core/agents.py').read_text();orig=(U/'crest/agents.py').read_text()
def node(t,name):return next(n for n in ast.parse(t).body if isinstance(n,ast.FunctionDef) and n.name==name)
for name in ['answer_generator','_slot_guidance']:
 n=node(orig,name);replacement='\n'.join(orig.splitlines()[n.lineno-1:n.end_lineno]);old=node(s,name);lines=s.splitlines();lines[old.lineno-1:old.end_lineno]=[replacement];s='\n'.join(lines)+'\n'
s=s.replace('Write one canonical successful use-case instance, normally 5-12 steps.','Write one canonical successful use-case instance, as many atomic steps as the source supports; no minimum length.')
s=s.replace('        data = {}\n\n    pre_constraint_draft', '        raise ValueError("Author response is not an object; technical failure")\n\n    if not (data.get("status") in ("no_evidence", "goal_evidence_missing") or isinstance(data.get("claims"), list) or isinstance(data.get("raw_text"), str)):\n        raise ValueError("Author response lacks content/status")\n\n    pre_constraint_draft')
(C/'agents.py').write_text(s)
for n in ['schemas.py']:shutil.copyfile(U/'crest'/n,C/n)
(C/'__init__.py').write_text('# Isolated core without image/index orchestrator imports.\n')
shutil.copytree(U/'crest/prompts',C/'prompts',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
shutil.copyfile(E/'runtime/core/settings.py',C/'settings.py')
(R/'runtime/__init__.py').write_text('')
for n in ['pipeline.py','transport.py']:shutil.copyfile(E/'runtime'/n,R/'runtime'/n)
diff='\n'.join(difflib.unified_diff(orig.splitlines(),s.splitlines(),fromfile='AUCMR/crest/agents.py',tofile='L5/runtime/core/agents.py'))
(R/'evidence/core_adaptation.diff').write_text(diff+'\n')
print('Isolated author/semantic verifier/DRA core created; original schemas and prompts restored.')
