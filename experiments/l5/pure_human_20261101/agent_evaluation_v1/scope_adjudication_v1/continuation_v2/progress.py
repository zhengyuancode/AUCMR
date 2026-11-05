"""One bounded status snapshot; never calls a model or prints credentials."""
from pathlib import Path
import json,collections
R=Path(__file__).resolve().parent
def read(p):return json.loads(p.read_text())
print(json.dumps({'state':read(R/'state.json') if (R/'state.json').exists() else 'NOT_STARTED','record_counts':{role:len(list((R/'records'/role).glob('*.json'))) for role in ('scope_reader_one','scope_reader_two','coverage_primary','coverage_secondary','coverage_adjudicator')},'final_references':len(list((R/'references').glob('*.json'))),'jobs':dict(collections.Counter(read(p)['status'] for p in (R/'calls').glob('*/*/job.json')))},ensure_ascii=False,indent=2))
