"""Focused offline checks for explicitly authorized second-pass recovery."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from eval.agent_evaluation.common import file_hash, write_json
from .recovery_policy import authorized, provider_error, review_context
from .response import decode
from .validation import validate_draft
from . import test_batch as synthetic
from .test_runtime import Fake


class PolicyTests(unittest.TestCase):
    def test_http_400_arrearage_not_truncation_and_no_error_text_leak(self):
        value={'raw_response':{'http_status':400,'error_response_text':json.dumps({'error':{'code':'Arrearage','message':'DO_NOT_LOG'}})}}
        self.assertEqual(provider_error(value),{'http_status':400,'code':'Arrearage'})
        value['raw_response']['error_response_text']='DO_NOT_LOG'
        self.assertEqual(provider_error(value),{'http_status':400,'code':'HTTP_ERROR'})
        self.assertIsNone(provider_error({'raw_response':{'choices':[]}}))

    def test_recovery_bound_to_one_attempt_kind_input_and_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);i=root/'tasks/T/C_FINAL/input.json';r=root/'task_progress/receipt/request-1.json'
            write_json(i,{'source':'original'});write_json(r,{'body':'old'})
            self.assertFalse(authorized(root,i,r,1,'FORMAT'))
            rule={'input_sha256':file_hash(i),'receipt_path':str(r.relative_to(root)),'receipt_sha256':file_hash(r),'failed_attempt':1,'kind':'FORMAT'}
            write_json(root/'failed-stage-recovery.json',{'schema_version':'AUCMR_L3_FAILED_STAGE_RECOVERY_1.0','authorization':'synthetic only','tasks':{str(i.parent.relative_to(root)):rule}})
            self.assertTrue(authorized(root,i,r,1,'FORMAT'))
            self.assertFalse(authorized(root,i,r,2,'FORMAT'))
            self.assertFalse(authorized(root,i,r,1,'OUTPUT_LENGTH'))
            write_json(r,{'body':'changed'})
            self.assertRaisesRegex(ValueError,'receipt hash',authorized,root,i,r,1,'FORMAT')

    def test_premature_root_close_preserves_all_draft_fields(self):
        draft={'scope':{'goal':'literal }},"requirements":[] text','unresolved_issues':['uncertain']},'requirements':[{'local_id':'R001','text':'Retain exactly'}],'candidate_decisions':[]}
        good=json.dumps(draft);end=good.index(', "requirements":',good.index('unresolved_issues'))
        malformed=good[:end]+'}'+good[end:]
        body,ops=decode(malformed)
        expected=copy.deepcopy(draft);expected['unresolved_issues']=expected['scope'].pop('unresolved_issues')
        self.assertEqual(body,{'draft':expected})
        self.assertIn('REMOVE_PREMATURE_ROOT_CLOSE_AFTER_SCOPE',ops)
        self.assertRaises(ValueError,decode,'{"scope":{"unresolved_issues":[]}},"requirements":[],"scope":{}}')


class RecoveryRuntimeTests(unittest.TestCase):
    setUp=synthetic.BatchTests.setUp
    execute=synthetic.BatchTests.execute

    def authorize_recovery(self,stage,kind):
        folder=self.root/'tasks'/self.tid/stage
        from eval.agent_evaluation.common import digest
        receipt=self.root/'task_progress'/digest(self.tid+'/'+stage)/'request-1.json'
        write_json(self.root/'failed-stage-recovery.json',{'schema_version':'AUCMR_L3_FAILED_STAGE_RECOVERY_1.0','authorization':'synthetic explicit recovery','tasks':{str(folder.relative_to(self.root)):{'input_sha256':file_hash(folder/'input.json'),'receipt_path':str(receipt.relative_to(self.root)),'receipt_sha256':file_hash(receipt),'failed_attempt':1,'kind':kind}}})

    def test_rejected_request_stops_whole_batch_then_explicit_recovery_once(self):
        class Rejected(Fake):
            def request(self,*args):
                v=super().request(*args)
                if len(self.calls)==1:return {'body':None,'raw_response':{'http_status':400,'error_response_text':'{"error":{"code":"Arrearage"}}'},'finish_reason':None,'cost_cny':None}
                return v
        t=Rejected(1)
        self.assertRaisesRegex(ValueError,'provider request rejected',self.execute,t,continue_on_error=True)
        self.assertEqual(len(t.calls),1)
        # A plain resume cannot retransmit the rejected request.
        self.assertRaisesRegex(ValueError,'provider request rejected',self.execute,t,continue_on_error=True)
        self.assertEqual(len(t.calls),1)
        self.authorize_recovery('A_BUILD','PROVIDER_REJECTED')
        self.assertEqual(self.execute(t,continue_on_error=True,retry_failed=True)['status'],'COMPLETE')
        self.assertEqual(len(t.calls),6)
        self.execute(t);self.assertEqual(len(t.calls),6)

    def test_failed_correction_is_not_retried_again(self):
        class Bad(Fake):
            def request(self,*args):
                v=super().request(*args);v['body']='invalid json';return v
        t=Bad(1)
        self.execute(t,continue_on_error=True);self.assertEqual(len(t.calls),1)
        self.authorize_recovery('A_BUILD','FORMAT')
        self.execute(t,continue_on_error=True,retry_failed=True);self.assertEqual(len(t.calls),2)
        self.execute(t,continue_on_error=True,retry_failed=True);self.assertEqual(len(t.calls),2)

    def test_length_recovery_requests_complete_json_and_retains_original(self):
        class Length(Fake):
            def request(self,binding,messages,task,attempt):
                v=super().request(binding,messages,task,attempt)
                if len(self.calls)==1:v['finish_reason']='length';v['body']='{"draft":'
                elif len(self.calls)==2:
                    self.correction=messages[-1]['content']
                return v
        t=Length(1)
        self.execute(t,continue_on_error=True);self.assertEqual(len(t.calls),1)
        first=file_hash(self.root/'tasks'/self.tid/'A_BUILD/call-1.json')
        self.authorize_recovery('A_BUILD','OUTPUT_LENGTH')
        self.assertEqual(self.execute(t,continue_on_error=True,retry_failed=True)['status'],'COMPLETE')
        self.assertIn('COMPLETE same draft',t.correction)
        self.assertEqual(first,file_hash(self.root/'tasks'/self.tid/'A_BUILD/call-1.json'))
        self.execute(t);self.assertEqual(len(t.calls),6)

    def test_exact_review_addition_retained_without_fabricated_candidate(self):
        t=Fake(1);self.execute(t)
        from eval.agent_evaluation.common import read_json
        folder=self.root/'tasks'/self.tid/'C_FINAL'
        final=read_json(folder/'result.json')['draft'];extra=copy.deepcopy(final['requirements'][0]);extra.update(local_id='R002',text='Source review addition',origin_ids=[])
        final['requirements'].append(extra)
        prior=copy.deepcopy(extra);prior['local_id']='R009'
        ctx=review_context(self.ctx,{'B_review':{'requirements':[prior]}},'C_FINAL')
        entries=[json.loads(x) for x in (folder/'access.jsonl').read_text().splitlines()]
        candidates={d['candidate_id']:{} for d in final['candidate_decisions']}
        checks=validate_draft(final,ctx,entries,candidates)
        self.assertEqual(checks['review_additions']['R002']['review_local_id'],'R009')
        self.assertEqual(final['requirements'][-1]['origin_ids'],[])
        altered=copy.deepcopy(final);altered['requirements'][-1]['text']='Altered claim'
        self.assertRaisesRegex(ValueError,'unbound',validate_draft,altered,ctx,entries,candidates)
        self.assertRaisesRegex(ValueError,'missing candidate origins',validate_draft,final,self.ctx,entries,candidates)


if __name__=='__main__':unittest.main()
