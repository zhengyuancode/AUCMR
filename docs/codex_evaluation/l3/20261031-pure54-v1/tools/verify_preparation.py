"""Read-only preparation checks. Does not perform Agent or human evaluation."""
import hashlib
import json
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path

OUT=Path(__file__).resolve().parents[1]
REPO=Path(__file__).resolve().parents[5]
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    cohort=read(OUT/'cohort.json');sources=read(OUT/'sources/source-lock.json')['sources'];plan=read(OUT/'execution-plan.json');pilot=read(OUT/'pilot-selection.json');private=read(OUT/'coordinator/stage2-input-lock.json')
    assert Counter(t['project_id'] for t in cohort['targets'])=={'qheadache':8,'microcare':16,'rlcs':19,'peazip':11}
    assert len(cohort['excluded_targets'])==39 and len(cohort['methods'])==7 and cohort['seed']==297154423
    assert len(private['input_bindings'])==378 and len(private['pilot_bindings'])==14
    assert all(sha(REPO/b['path'])==b['sha256'] for b in private['input_bindings'])
    for s in sources:
        assert s['version_comparison']=='BYTE_IDENTICAL'
        assert sha(OUT/s['pdf'])==sha(REPO/s['local_frozen_pdf'])==s['pdf_sha256']
        if s['xml']:assert sha(OUT/s['xml'])==s['xml_sha256']
    pub=OUT/'stage1-source-only';allow=read(pub/'allowlist.json')
    assert all(sha(pub/rel)==h for rel,h in allow['files'].items())
    actual={str(p.relative_to(pub)) for p in pub.rglob('*') if p.is_file()}
    assert actual==set(allow['files'])|{'allowlist.json'}
    assert not any(p.endswith(('.py','.cjs','.zip')) for p in actual)
    def keys(value):
        if isinstance(value,dict):
            for k,v in value.items():yield k;yield from keys(v)
        elif isinstance(value,list):
            for v in value:yield from keys(v)
    for p in pub.rglob('*.json'):
        assert not set(keys(read(p))) & {'input_bindings','model_proposals_from_existing_record','method','method_map','ranking','scores','candidate_decisions','anonymous_outputs'}
    with zipfile.ZipFile(OUT/'stage1-source-only.zip') as z:
        assert set(z.namelist())=={'stage1-source-only/'+x for x in actual}
        assert all(z.read('stage1-source-only/'+x)==(pub/x).read_bytes() for x in actual)
    assert sha(OUT/'stage1-source-only.zip')==plan['stage1_input_sha256']
    assert plan['model_calls']==0 and plan['live_authorized'] is False and plan['runtime_adapter_status']=='NOT_IMPLEMENTED'
    assert plan['reference_status']==plan['human_audit_status']==plan['coverage_status']=='NOT_RUN'
    assert set(pilot['target_order'])==set(plan['pilot_targets'])
    assert len(pilot['target_order'])==2 and all(c['scope_limit'] is False for c in pilot['cases'])
    audit=read(OUT/'human-audit-plan.json');assert audit['status']=='NOT_RUN' and sum(len(s['selected_targets']) for s in audit['strata'])==7
    for p in (pub/'pilot-drafts').glob('*.json'):
        r=read(p);assert r['freeze_status']=='DRAFT' and r['requirements']==[] and r['human_audit_status']=='NOT_RUN'
    protected=read(OUT/'coordinator/protection-before.json')
    changed=[rel for rel,h in protected['files'].items() if not (REPO/rel).is_file() or sha(REPO/rel)!=h]
    assert not changed,changed
    cleanup=read(OUT/'coordinator/cleanup.json');assert cleanup['status']=='DELETED'
    assert all(not (REPO/x['path']).exists() for x in cleanup['deleted_files'])
    tests=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',str(OUT/'tools'),'-p','test_contract.py','-v'],capture_output=True,text=True)
    assert tests.returncode==0,tests.stderr
    print(json.dumps({'status':'PASS','protocol_version':plan['protocol_version'],'source_pdf_versions_byte_identical':4,'official_xml_files':2,'targets':54,'comparison_slots':378,'pilot_cases':2,'pilot_comparison_slots':14,'source_only_files':len(allow['files']),'source_only_zip_sha256':plan['stage1_input_sha256'],'protected_files_unchanged':len(protected['files']),'deleted_old_preparation_files':len(cleanup['deleted_files']),'offline_test_command':'python -B -m unittest discover -s docs/codex_evaluation/l3/20261031-pure54-v1/tools -p test_contract.py -v','offline_test_exit_code':tests.returncode,'offline_test_output':tests.stderr,'human_audit_targets_planned':7,'model_calls':0,'runtime_adapter_status':plan['runtime_adapter_status'],'reference_status':'NOT_RUN','meaning':'Preparation and counting contracts verified; no Agent/human reference quality or model evaluation claim.'},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
