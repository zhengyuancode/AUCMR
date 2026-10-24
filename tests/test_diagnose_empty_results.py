import copy
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from scripts.diagnose_empty_results import checked_result, prior_context, classify, capture, main


class EmptyDiagnosticTests(unittest.TestCase):
    def test_prior_context_excludes_current_and_future_slots(self):
        result = {'slots': {'goal': {'raw_text':'G'}, 'primary_actor': {'raw_text':'A'},
                            'main_flow': {'claims':[]}, 'postconditions': {'raw_text':'Future'}}}
        before = copy.deepcopy(result)
        context = prior_context(result, 'main_flow')
        self.assertEqual(list(context), ['goal','primary_actor'])
        context['goal']['raw_text'] = 'Changed'
        self.assertEqual(result, before)

    def test_source_hash_identity_and_path_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = {'method':'LVLM-Direct','actor':'A','use_case_name':'U'}
            raw = json.dumps(d).encode()
            (root/'result.json').write_bytes(raw)
            target = dict(d, relative_path='result.json', blob_sha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest())
            self.assertEqual(checked_result(root,target)[2],d)
            with self.assertRaises(ValueError): checked_result(root,dict(target,actor='B'))
            with self.assertRaises(ValueError): checked_result(root,dict(target,relative_path='../outside.json'))
            (root/'result.json').write_bytes(raw+b' ')
            with self.assertRaises(ValueError): checked_result(root,target)

    def test_classification_does_not_call_every_empty_a_model_abstention(self):
        event={'stage':'parse','raw_response':'{}','parsed':{}}
        self.assertEqual(classify([event],None,None),'explicit_empty_object')
        event={'stage':'parse','raw_response':'bad','parsed':None}
        self.assertEqual(classify([event],None,None),'parse_failed_or_json_null')
        event={'stage':'parse','raw_response':'{"main_flow":[]}','parsed':{'main_flow':[]}}
        self.assertEqual(classify([event],None,None),'empty_after_normalization_needs_raw_review')
        self.assertEqual(classify([],None,None),'no_final_parse_observed')
        self.assertEqual(classify([],None,'TimeoutError'),'technical_failure')
        self.assertEqual(classify([event,{'stage':'whitelist','before_claims':1,'after_claims':0}],None,None),'all_candidates_removed_by_citation_filter')

    def test_capture_delegates_without_changing_inputs_or_answer(self):
        original=Mock(return_value='{"claims":[{"content":"x"}]}')
        module=SimpleNamespace(_call_lvlm=original,_call_llm_text=Mock(),_parse_json_with_repair=json.loads,
                               _enforce_evidence_whitelist=lambda value,allowed: dict(value,claims=[]))
        def fill(slot,filled,context):
            parsed=module._parse_json_with_repair(module._call_lvlm('unchanged prompt',['image']))
            return module._enforce_evidence_whitelist(parsed,['P1'])
        runner=SimpleNamespace(slot_filler=fill)
        record=capture(module,runner,'main_flow',{}, {})
        self.assertEqual(record['status'],'all_candidates_removed_by_citation_filter')
        original.assert_called_once_with('unchanged prompt',['image'])
        self.assertIs(module._call_lvlm,original)
        self.assertEqual(record['events'][1]['parsed']['claims'][0]['content'],'x')

    def test_swallowed_api_exception_is_still_technical_failure(self):
        module=SimpleNamespace(_call_lvlm=Mock(side_effect=TimeoutError('SECRET')), _call_llm_text=Mock(),
                               _parse_json_with_repair=json.loads,_enforce_evidence_whitelist=Mock())
        def fill(*args):
            try: module._call_lvlm('prompt',[])
            except TimeoutError: return None
        record=capture(module,SimpleNamespace(slot_filler=fill),'main_flow',{}, {})
        self.assertEqual(record['status'],'technical_failure')
        self.assertNotIn('SECRET',json.dumps(record))

    def test_dry_run_never_imports_or_calls_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            config=root/'config.json'; manifest=root/'manifest.json'
            config.write_text(json.dumps({'paths':{'output':tmp},'experiment':{'use_cases':[]}}))
            manifest.write_text(json.dumps({'audit_commit':'fixture','targets':[]}))
            with patch('scripts.diagnose_empty_results.capture',side_effect=AssertionError('model forbidden')):
                self.assertEqual(main(['--config',str(config),'--manifest',str(manifest)]),0)


    def test_recorded_answers_replay_without_content_loss(self):
        from eval.baselines import _normalize_uncited_slot_response, _normalize_slot_response, _enforce_evidence_whitelist
        rows = json.loads((Path(__file__).parent/'fixtures/empty_diagnostic_20261024.json').read_text(encoding='utf-8'))
        for row in rows:
            with self.subTest(method=row['method']):
                parsed = json.loads(row['raw_response'])
                self.assertEqual(parsed, row['parsed'])
                if row['method'] == 'LVLM-Direct':
                    value = _normalize_uncited_slot_response('main_flow', parsed)
                    self.assertEqual(len(value['claims']), 9)
                else:
                    value = _enforce_evidence_whitelist(_normalize_slot_response('main_flow', parsed), row['allowed_refs'])
                    self.assertEqual(value['claims'], [])
                self.assertEqual(value, row['normalized'])

    def test_trace_keeps_failure_location_without_secret_values(self):
        import ssl
        from scripts.diagnose_empty_results import exception_trace
        def fail():
            secret = 'PRIVATE_KEY_VALUE'
            raise ssl.SSLError(secret)
        try:
            fail()
        except ssl.SSLError as exc:
            trace = exception_trace(exc)
        self.assertEqual(trace[0]['type'], 'SSLError')
        self.assertEqual(trace[0]['frames'][-1]['function'], 'fail')
        self.assertNotIn('PRIVATE_KEY_VALUE', json.dumps(trace))
        self.assertEqual(set(trace[0]['frames'][-1]), {'file', 'function', 'line'})

    def test_method_filter_limits_dry_run_to_requested_baseline(self):
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            targets = []
            for i, method in enumerate(['LVLM-Direct', 'VDocRAG-Elaborate']):
                result = {'method': method, 'actor': 'A', 'use_case_name': 'U'}
                raw = json.dumps(result).encode()
                name = str(i)+'.json'
                (root/name).write_bytes(raw)
                targets.append(dict(result, pilot=True, project='p', relative_path=name,
                    blob_sha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()))
            config = root/'config.json'; manifest = root/'manifest.json'
            config.write_text(json.dumps({'paths':{'output':tmp}, 'experiment':{'use_cases':[{'project':'p','use_case':'U','actor':'A'}]}}))
            manifest.write_text(json.dumps({'audit_commit':'fixture','targets':targets}))
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(['--config',str(config),'--manifest',str(manifest),'--methods','VDocRAG-Elaborate'])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())['targets'], 1)


if __name__ == '__main__': unittest.main()
