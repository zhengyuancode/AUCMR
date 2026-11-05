"""Targeted batch tests with synthetic receipts and network access denied."""
import contextlib
import copy
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from eval.agent_evaluation.common import read_json, write_json, file_hash, digest
from eval.agent_evaluation.transport import deny_network
from eval.agent_evaluation.budget import Budget
from . import batch, runner
from .validation import source_context
from .test_runtime import Fake


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name); self.root = self.base / 'run'; self.root.mkdir()
        self.bundle = self.base / 'sources'; self.bundle.mkdir()
        import fitz
        pdf = fitz.open()
        for _ in range(3):
            pdf.new_page().insert_text((50, 50), 'Synthetic source evidence.')
        pdf.save(self.bundle / 'source.pdf'); pdf.close()
        self.tid = 'UC-SYNTHETIC-BATCH'
        target = {'target_id': self.tid, 'project_id': 'qheadache', 'actor': 'Player', 'name': 'Synthetic target'}
        source = {'source_id': 'SYNTHETIC', 'project_id': 'qheadache', 'pdf': 'source.pdf',
                  'pdf_sha256': file_hash(self.bundle / 'source.pdf'), 'pages': 3, 'xml': None, 'source_version': 'SYNTHETIC_ONLY'}
        write_json(self.bundle / 'targets.json', {'targets': [target], 'sources': [source]})
        write_json(self.bundle / 'allowlist.json', {'files': {p.name: file_hash(p) for p in self.bundle.iterdir()}})
        self.ctx = source_context(self.bundle, self.tid)
        self.seeds = {tid: {'target_id': tid, 'requirements': [], 'test_only': True} for tid in runner.PILOTS}
        self.plan = {'targets': [{'target_id': tid} for tid in runner.PILOTS]+[target],
                     'remaining_target_ids': [self.tid], 'source_allowlist_sha256': file_hash(self.bundle / 'allowlist.json')}
        write_json(self.root / 'plan.snapshot.json', self.plan)
        for tid, ref in self.seeds.items():
            write_json(self.root / 'references' / (tid + '.json'), ref)
        self.config = {'models': {r: {'provider': 'MOCK', 'api_model_id': 'MOCK-'+r, 'parameters': {'max_tokens': 8192}} for r in 'ABC'},
                       'source_bundle_sha256': 'a'*64, 'bootstrap_image_pages': {self.tid: [1, 2]},
                       'reference_limits': {'max_requests_per_task': 8, 'max_format_repairs': 2,
                                            'max_total_format_repairs': 2, 'max_total_requests': 40}}
        self.budget = Budget(self.root / 'ledger.sqlite', provider_managed=True)

    def execute(self, transport, **options):
        reporter = batch.Progress(self.root, self.plan)
        with deny_network(), contextlib.redirect_stdout(io.StringIO()):
            result = batch.run(self.root, self.config, self.plan, {self.tid: self.ctx}, self.seeds,
                               transport, self.budget, reporter, **options)
        return result

    def add_second_case(self):
        tid='UC-SYNTHETIC-SECOND'
        data=read_json(self.bundle/'targets.json');target={**data['targets'][0],'target_id':tid,'name':'Second synthetic case'}
        data['targets'].append(target);write_json(self.bundle/'targets.json',data)
        allow=read_json(self.bundle/'allowlist.json');allow['files']['targets.json']=file_hash(self.bundle/'targets.json');write_json(self.bundle/'allowlist.json',allow)
        self.ctx=source_context(self.bundle,self.tid)
        self.plan['targets'].append(target);self.plan['remaining_target_ids'].append(tid);write_json(self.root/'plan.snapshot.json',self.plan)
        self.config['bootstrap_image_pages'][tid]=[1,2]
        return tid,source_context(self.bundle,tid)

    def execute_two(self,transport,second,ctx,**options):
        reporter=batch.Progress(self.root,self.plan)
        with deny_network(),contextlib.redirect_stdout(io.StringIO()):
            return batch.run(self.root,self.config,self.plan,{self.tid:self.ctx,second:ctx},self.seeds,transport,self.budget,reporter,**options)

    def test_complete_collection_preserves_pilots_and_resume_no_calls(self):
        before = {tid: file_hash(self.root / 'references' / (tid+'.json')) for tid in self.seeds}
        transport = Fake(2, pending=True); result = self.execute(transport)
        self.assertEqual(len(transport.calls), 5)
        self.assertEqual(result['status'], 'COMPLETE'); self.assertEqual(result['completed_targets'], 3)
        self.assertEqual(result['references'][-1]['reference']['requirements'][-1]['status'], 'REFERENCE_UNRESOLVED')
        self.assertEqual(result['content_sha256'], digest({k:v for k,v in result.items() if k!='content_sha256'}))
        for tid, signature in before.items():
            self.assertEqual(file_hash(self.root / 'references' / (tid+'.json')), signature)
        first = file_hash(self.root / 'common-references.json')
        self.execute(transport)
        self.assertEqual(len(transport.calls), 5)
        self.assertEqual(file_hash(self.root / 'common-references.json'), first)
        progress = read_json(self.root / 'progress.json')
        self.assertEqual(progress['status'], 'COMPLETE'); self.assertEqual(progress['completed_stages'], 5)
        self.assertEqual(progress['calls'], 5); self.assertEqual(progress['coverage_calls'], 0)
        for stage in ('A_BUILD', 'B_BLIND', 'C_BLIND'):
            inputs = read_json(self.root / 'tasks' / self.tid / stage / 'input.json')
            self.assertEqual(inputs['candidate_materials'], {})
            self.assertEqual(inputs['bootstrap_image_pages'], [1, 2])

    def test_interruption_resumes_saved_stages_without_resending(self):
        transport = Fake(1); original = batch.task; count = [0]
        def interrupted(*args, **kwargs):
            count[0] += 1
            if count[0] == 3:
                raise KeyboardInterrupt()
            return original(*args, **kwargs)
        with patch.object(batch, 'task', interrupted):
            self.assertRaises(KeyboardInterrupt, self.execute, transport)
        self.assertEqual(len(transport.calls), 2)
        self.assertFalse((self.root / 'common-references.json').exists())
        self.execute(transport); self.assertEqual(len(transport.calls), 5)

    def test_transport_failure_stops_and_resume_does_not_retry(self):
        class Broken(Fake):
            def request(self, *args):
                self.calls.append('SENT'); raise OSError('synthetic failure')
        transport = Broken()
        for _ in range(2):
            self.assertRaisesRegex(ValueError, 'request failed', self.execute, transport)
            self.assertEqual(len(transport.calls), 1)
        self.assertFalse((self.root / 'common-references.json').exists())
        self.assertEqual(read_json(self.root / 'common-references.partial.json')['completed_targets'], 2)

    def test_total_format_corrections_bounded_even_with_distinct_errors(self):
        class Invalid(Fake):
            def request(self, *args):
                response = super().request(*args); body = json.loads(response['body'])
                body['draft']['scope']['evidence'][0]['quote'] = 'Not on page '+str(len(self.calls))
                response['body'] = json.dumps(body); return response
        transport = Invalid(1)
        for _ in range(2):
            self.assertRaisesRegex(ValueError, 'total structural/locator', self.execute, transport)
            self.assertEqual(len(transport.calls), 3)

    def test_partial_never_published_as_final(self):
        result = batch.collect(self.root, self.plan, {self.tid:self.ctx}, self.seeds)
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertFalse((self.root / 'common-references.json').exists())

    def test_changed_seed_refused(self):
        write_json(self.root / 'references' / (runner.PILOTS[0]+'.json'), {'changed': True})
        self.assertRaisesRegex(ValueError, 'pilot changed', self.execute, Fake())

    def test_tampered_stage_receipt_refuses_resume(self):
        transport = Fake(1); self.execute(transport)
        path = self.root / 'tasks' / self.tid / 'A_BUILD' / 'result.json'
        data = read_json(path); data['draft']['requirements'][0]['text']='tampered'; write_json(path,data)
        self.assertRaisesRegex(ValueError, 'hash mismatch', self.execute, transport)
        self.assertEqual(len(transport.calls),5)

    def test_progress_file_and_log_include_stage_and_request(self):
        reporter = batch.Progress(self.root, self.plan)
        with contextlib.redirect_stdout(io.StringIO()):
            reporter.update('STAGE_START',status='RUNNING',current_stage='A_BUILD',current_target_name='synthetic')
            reporter.update('REQUEST_START',attempt=1,cached=False)
            reporter.update('HEARTBEAT')
        data = read_json(self.root/'progress.json')
        self.assertEqual(data['status'],'RUNNING'); self.assertEqual(data['attempt'],1)
        self.assertIn('synthetic A_BUILD',batch.one_line(data))
        self.assertIn('请求#1',(self.root/'run.log').read_text())
        self.assertEqual(len((self.root/'events.jsonl').read_text().splitlines()),3)

    def test_resolved_error_not_left_as_current_stage_error(self):
        reporter=batch.Progress(self.root,self.plan)
        with contextlib.redirect_stdout(io.StringIO()):
            reporter.update('FORMAT_CORRECTION',status='RUNNING',reason='old resolved error')
            reporter.update('STAGE_DONE')
            reporter.update('STAGE_START',current_stage='B_BLIND')
        self.assertNotIn('reason',read_json(self.root/'progress.json'))
        self.assertIn('old resolved error',(self.root/'events.jsonl').read_text())

    def test_without_explicit_mode_no_live_transport(self):
        with patch('sys.argv',['batch']),patch.object(batch,'AcceptanceTransport') as live:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertRaises(SystemExit,batch.main)
            live.assert_not_called()

    def test_user_advisory_quotes_preserved_as_warnings_without_retry(self):
        from .validation import validate_frozen
        self.config.update(source_only=True,policy='User adopted final references; previous acceptance rules advisory.')
        write_json(self.root/'config.snapshot.json',self.config)
        class InexactQuotes(Fake):
            def request(self,*args):
                result=super().request(*args);body=json.loads(result['body'])
                body['draft']['scope']['evidence'][0]['quote']='Unverified Agent scope transcription.'
                body['draft']['requirements'][0]['evidence'][0]['quote']='Unverified Agent requirement transcription.'
                result['body']=json.dumps(body);return result
        transport=InexactQuotes(1);self.execute(transport)
        self.assertEqual(len(transport.calls),5)
        ref=read_json(self.root/'references'/(self.tid+'.json'))
        self.assertEqual(len(validate_frozen(ref,self.bundle,self.root)),1)
        self.assertEqual(ref['requirements'][0]['text'],'Synthetic requirement 1')
        self.assertEqual(ref['requirements'][0]['evidence'][0]['quote'],'Unverified Agent requirement transcription.')
        result=read_json(self.root/'tasks'/self.tid/'A_BUILD/result.json')
        self.assertEqual(result['evidence_checks']['requirements']['D001'][0]['mode'],'UNVERIFIED_AGENT_QUOTE')
        self.assertTrue(result['quote_locator_warnings'])
        self.assertIn('source_locator_review',ref)
        self.config['policy']='unauthorized change';write_json(self.root/'config.snapshot.json',self.config)
        self.assertRaisesRegex(ValueError,'quote policy',validate_frozen,ref,self.bundle,self.root)

    def test_advisory_quotes_do_not_allow_unknown_source_pages(self):
        self.config.update(source_only=True,policy='Previous acceptance rules advisory.')
        write_json(self.root/'config.snapshot.json',self.config)
        class BadPage(Fake):
            def request(self,*args):
                result=super().request(*args);body=json.loads(result['body']);body['draft']['scope']['evidence'][0]['pdf_page_1based']=999
                result['body']=json.dumps(body);return result
        transport=BadPage(1);self.assertRaises(ValueError,self.execute,transport)
        self.assertFalse((self.root/'common-references.json').exists())

    def test_explicit_unresolved_uncited_item_keeps_status_and_denominator(self):
        from .validation import validate_frozen
        self.config.update(source_only=True,policy='User adopted references; previous acceptance rules advisory.')
        write_json(self.root/'config.snapshot.json',self.config)
        class UncitedPending(Fake):
            def request(self,*args):
                result=super().request(*args);body=json.loads(result['body'])
                body['draft']['requirements'][-1]['evidence']=[]
                result['body']=json.dumps(body);return result
        transport=UncitedPending(2,pending=True);self.execute(transport)
        self.assertEqual(len(transport.calls),5)
        ref=read_json(self.root/'references'/(self.tid+'.json'))
        self.assertEqual(len(validate_frozen(ref,self.bundle,self.root)),2)
        self.assertEqual(ref['requirements'][-1]['status'],'REFERENCE_UNRESOLVED')
        self.assertEqual(ref['requirements'][-1]['evidence'],[])
        result=read_json(self.root/'tasks'/self.tid/'C_FINAL/result.json')
        self.assertEqual(result['evidence_checks']['requirements']['D002'][0]['mode'],'UNCITED_REFERENCE_UNRESOLVED')

    def test_uncited_proposed_requirement_not_silently_confirmed_or_accepted(self):
        self.config.update(source_only=True,policy='Previous acceptance rules advisory.')
        write_json(self.root/'config.snapshot.json',self.config)
        class Uncited(Fake):
            def request(self,*args):
                result=super().request(*args);body=json.loads(result['body']);body['draft']['requirements'][0]['evidence']=[]
                result['body']=json.dumps(body);return result
        self.assertRaises(ValueError,self.execute,Uncited(1))
        self.assertFalse((self.root/'references'/(self.tid+'.json')).exists())

    def test_prepare_snapshots_all54_and_approved_two_without_live_calls(self):
        with tempfile.TemporaryDirectory() as tmp,deny_network(),patch.object(batch,'AcceptanceTransport') as live:
            root=Path(tmp); config,plan,contexts,seeds=batch.prepare(root)
            self.assertEqual(len(contexts),54); self.assertEqual(len(plan['remaining_target_ids']),52)
            self.assertEqual(len(seeds),2); self.assertFalse(config['budgets']['live_authorized'])
            self.assertNotIn('input_bindings',config)
            for tid in contexts:
                self.assertTrue(all(1<=p<=contexts[tid]['source']['pages'] for p in config['bootstrap_image_pages'][tid]))
            self.assertEqual(len(batch.collect(root,plan,contexts,seeds)['references']),2)
            batch.prepare(root); live.assert_not_called()
            manifest=read_json(root/'plan.snapshot.json');manifest['approval']='tampered';write_json(root/'plan.snapshot.json',manifest)
            self.assertRaisesRegex(ValueError,'Snapshot changed',batch.prepare,root)

    def test_continue_skips_bad_case_finishes_others_and_does_not_retry_failed_on_resume(self):
        first=self.tid;second,ctx=self.add_second_case()
        class InvalidFirst(Fake):
            def request(self,*args):
                result=super().request(*args)
                if args[2]['task_id'].startswith(first+'/'):result['body']='INVALID_JSON_PRESERVED'
                return result
        transport=InvalidFirst(1);result=self.execute_two(transport,second,ctx,continue_on_error=True)
        self.assertEqual(len(transport.calls),6)  # No paid format correction; other case has five stages.
        self.assertEqual(result['status'],'PARTIAL');self.assertFalse((self.root/'common-references.json').exists())
        manifest=read_json(self.root/'batch-results.json')
        self.assertEqual(manifest['status'],'FINISHED_WITH_FAILURES');self.assertEqual(manifest['processed_targets'],4)
        self.assertEqual(manifest['completed_targets'],3);self.assertEqual(manifest['failed_targets'],1)
        failed=read_json(self.root/'failed-targets.json')['targets'][0]
        self.assertEqual(failed['target_id'],first);self.assertEqual(failed['failure']['stage'],'A_BUILD')
        self.assertEqual(read_json(self.root/'progress.json')['failed_targets'],1)
        self.execute_two(transport,second,ctx,continue_on_error=True)
        self.assertEqual(len(transport.calls),6)
        self.assertEqual(len((self.root/'failures.jsonl').read_text().splitlines()),1)

    def test_retry_failed_reuses_completed_stages_then_resolves_failure_history(self):
        first=self.tid;second,ctx=self.add_second_case();transport=Fake(1);original=batch.task
        def fail_compare(*args,**kwargs):
            if args[1]['target']['target_id']==first and args[2]=='B_COMPARE':raise ValueError('Synthetic local adapter failure')
            return original(*args,**kwargs)
        with patch.object(batch,'task',fail_compare):self.execute_two(transport,second,ctx,continue_on_error=True)
        self.assertEqual(len(transport.calls),7)
        result=self.execute_two(transport,second,ctx,continue_on_error=True,retry_failed=True)
        self.assertEqual(len(transport.calls),10);self.assertEqual(result['status'],'COMPLETE')
        self.assertEqual(read_json(self.root/'failed-targets.json')['count'],0)
        history=[json.loads(x) for x in (self.root/'failures.jsonl').read_text().splitlines()]
        self.assertEqual([x['state'] for x in history],['FAILED','RESOLVED'])
        self.assertEqual(read_json(self.root/'batch-results.json')['completed_targets'],4)

    def test_continue_transport_failure_has_no_same_request_retry(self):
        first=self.tid;second,ctx=self.add_second_case()
        class BrokenFirst(Fake):
            def request(self,*args):
                if args[2]['task_id'].startswith(first+'/'):
                    self.calls.append('SENT_ONCE');raise OSError('Synthetic timeout')
                return super().request(*args)
        transport=BrokenFirst(1);self.execute_two(transport,second,ctx,continue_on_error=True)
        self.assertEqual(len(transport.calls),6)
        self.assertEqual(read_json(self.root/'batch-results.json')['status'],'FINISHED_WITH_FAILURES')

    def test_continue_does_not_hide_authentication_or_integrity_failure(self):
        first=self.tid;second,ctx=self.add_second_case()
        class AuthFailure(Fake):
            def request(self,*args):
                result=super().request(*args);result['finish_reason']=None;result['raw_response']={'http_status':401}
                return result
        transport=AuthFailure(1)
        self.assertRaises(ValueError,self.execute_two,transport,second,ctx,continue_on_error=True)
        self.assertEqual(len(transport.calls),1)
        from .batch_failures import fatal_error
        self.assertTrue(fatal_error(ValueError('Source-only bundle changed'),self.root,first,'A_BUILD'))
        self.assertFalse((self.root/'failures.jsonl').exists())

    def test_user_interrupt_is_not_converted_to_skipped_case(self):
        with patch.object(batch,'task',side_effect=KeyboardInterrupt):
            self.assertRaises(KeyboardInterrupt,self.execute,Fake(1),continue_on_error=True)
        self.assertFalse((self.root/'failures.jsonl').exists())

    def test_failure_record_redacts_only_supplied_credentials_and_keeps_reason(self):
        from .batch_failures import record,current_failures
        with patch.dict('os.environ',{'AUCMR_EVAL_ARK_API_KEY':'synthetic-private-key'}):
            record(self.root,self.tid,'A_BUILD',error=ValueError('synthetic-private-key: invalid shape'))
        data=(self.root/'failures.jsonl').read_text()
        self.assertNotIn('synthetic-private-key',data);self.assertIn('[REDACTED]',data)
        self.assertIn('invalid shape',current_failures(self.root)[self.tid]['reason'])


if __name__ == '__main__':
    unittest.main()
