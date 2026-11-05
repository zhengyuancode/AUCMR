"""Explicit, receipt-bound locator edits. Requirement content cannot be patched."""
import copy
from .validation import require

SCHEMA='AUCMR_L3_LOCATOR_REPAIR_1.0'


def apply(draft, record, *, input_sha256, receipt_sha256, source_sha256):
    require(record.get('schema_version')==SCHEMA,'unknown locator repair schema')
    for field,value in [('input_sha256',input_sha256),('receipt_sha256',receipt_sha256),('source_sha256',source_sha256)]:
        require(record.get(field)==value,'locator repair binding mismatch: '+field)
    require(bool(record.get('authorization')),'locator repair authorization missing')
    result=copy.deepcopy(draft);seen=set()
    require(isinstance(record.get('patches'),list) and record['patches'],'locator patches missing')
    for patch in record['patches']:
        path=patch.get('path')
        require(isinstance(path,list),'invalid locator path')
        valid=path==['scope','evidence'] or (len(path)==3 and path[0] in {'requirements','candidate_decisions'} and type(path[1]) is int and path[2]=='evidence')
        require(valid,'locator repair may only replace evidence arrays')
        require(tuple(path) not in seen,'duplicate locator patch');seen.add(tuple(path))
        node=result
        for key in path[:-1]:node=node[key]
        require((path[-1] in node)==patch.get('before_present',True),'locator field presence does not match receipt')
        require(node.get(path[-1])==patch.get('before'),'locator original does not match receipt')
        after=patch.get('after')
        require(isinstance(after,list) and after and bool(patch.get('reason')),'replacement locator/reason missing')
        for e in after:
            require(isinstance(e,dict) and set(e)<={'pdf_page_1based','quote','visual_region_description'} and 'pdf_page_1based' in e and ('quote' in e or 'visual_region_description' in e),'invalid replacement evidence')
        node[path[-1]]=copy.deepcopy(after)
    return result
