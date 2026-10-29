"""Panel contracts, recovery and accounting against isolated mock transport."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sqlite3
import unittest
from unittest.mock import patch
import fitz

from eval.l4_agent import protocol as p
from eval.l4_agent import runner
from eval.l4_agent.assignment import build, validate
from eval.l4_agent.contract import parse, validate_ratings
from eval.l4_agent.inputs import alternatives, validate_catalog
from eval.l4_agent.sources import Workspace, message
from eval.l4_agent.export import export
from eval.evaluation_runtime import EvaluationRuntime, RuntimeResponseError


def score(views):
    return {'ratings':{label:{'pcs':{s:0 if s in v['empty_slots'] else 3 for s in p.SLOTS},
        'pcs_reason':'内容正确，使用原量表。','prc':4,'prc_reason':'只需少量人工修改。'}
        for label,v in views.items() if not v['no_content']}}


class MockTransport:
    mode = 'mock'
    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = 0

    def request(self,binding,messages,task,attempt):
        self.calls += 1
        action = self.actions.pop(0)
        if isinstance(action,Exception):
            raise action
        if action == 'http_error':
            return {'http_status':401,'error_response_text':'Authentication failed'}
        return {'id':'mock-'+str(self.calls),'model':binding['api_model_id'],
                'choices':[{'finish_reason':'stop','message':{'content':action if isinstance(action,str) else json.dumps(action)}}],
                'usage':{'prompt_tokens':100,'completion_tokens':100,'total_tokens':200}}


class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory(prefix='l4-panel-test-',dir=p.REPO/'outputs/evaluation_preparation')
        cls.root = Path(cls.temp.name)
        pdf = cls.root/'source.pdf'
        with fitz.open() as document:
            page = document.new_page()
            page.insert_text((60,60),'Original source title. Customer places a bid. Case material.')
            document.save(pdf)
        targets,bindings = [],[]
        methods = ['M'+str(i) for i in range(7)]
        for i in range(93):
            target = {'task_id':'T'+str(i),'project_id':'doc','name':'Case '+str(i),'actor':'Customer'}
            targets.append(target)
            path = cls.root/(target['task_id']+'.json')
            raw = {'use_case_name':target['name'],'actor':'Customer',
                   'slots':{s:{'claims':[{'content':'Customer places a bid.'}]} for s in p.SLOTS}}
            p.save_new(path,raw)
            for method in methods:
                bindings.append({'task_id':target['task_id'],'method':method,'path':str(path.relative_to(p.REPO)),'sha256':p.file_hash(path)})
        cls.catalog = {'targets':targets,'methods':methods,'input_bindings':bindings,
                       'sources':[{'source_id':'SRC1','project_id':'doc','path':str(pdf.relative_to(p.REPO)),'sha256':p.file_hash(pdf)}]}
        cls.allocation = build(cls.catalog,42)
        cls.allocation_path = cls.root/'allocation.json'
        p.save_new(cls.allocation_path,cls.allocation)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.work = TemporaryDirectory(dir=self.root)
        self.directory = Path(self.work.name)
        self.ledger = self.directory/'ledger.sqlite'
        model = {'provider':'mock','api_model_id':'mock-judge','base_url':'https://example.invalid/v1','api_key_env':'TEST_PANEL_KEY',
                 'parameters':{'max_tokens':8192},'binding_status':'NOT_BOUND','cost_control':'PROVIDER_MANAGED',
                 'interface_observation':{'accepted_by_controller':True,'text_and_image_success_observed':True},
                 'capabilities':{'images':True},'cost_estimate':{'input_cny_per_million':2,'output_cny_per_million':6,'source':'offline-test'}}
        self.config = {'version':p.VERSION,'live_authorized':False,'full_experiment_authorized':False,
            'models':{k:deepcopy(model) for k in ('glm','doubao','qwen')},'campaign_ledger':str(self.ledger.relative_to(p.REPO)),
            'material_limits':{'initial_source_text_chars':50000,'tools_per_round':4},
            'tool_rounds':2,'format_corrections':1,'max_requests_per_evaluation':4,'implementation':p.implementation(),
            'pilot_task_ids':['T0','T1','T2'],'allocation_path':str(self.allocation_path.relative_to(p.REPO))}
        self.evaluation = self.allocation['evaluations'][0]
        self.agent = next(a for a in self.allocation['agents'] if a['agent_id']==self.evaluation['agent_id'])
        self.views = alternatives(self.catalog,self.evaluation)
        self.snapshot = {'config':self.config,'scope':'pilot','evaluation_ids':[self.evaluation['evaluation_id']]}

    def tearDown(self):
        self.work.cleanup()

    def factory(self,transport):
        def make(binding,directory,layer,ledger,**kwargs):
            return EvaluationRuntime(binding,directory,layer,ledger,transport=transport,**kwargs)
        return make

    def evaluate(self,actions):
        transport = MockTransport(actions)
        result = runner.evaluate_one(self.config,self.catalog,self.evaluation,self.agent,self.directory,self.snapshot,self.factory(transport))
        runner.validate_record(result,self.evaluation,self.snapshot)
        return result,transport

    def test_allocation_exact_load_and_model_coverage(self):
        self.assertEqual(validate(self.allocation,self.catalog)['evaluations'],279)
        self.assertEqual(Counter(len(a['task_ids']) for a in self.allocation['agents']),{7:27,6:15})

    def test_roles_balanced_and_reproducible(self):
        self.assertEqual(build(self.catalog,42),self.allocation)
        groups = [Counter(a['role'] for a in self.allocation['agents'] if a['model_key']==m) for m in ('glm','doubao','qwen')]
        self.assertEqual(groups[0],groups[1]); self.assertEqual(groups[1],groups[2])
        self.assertGreater(len({tuple(e['method_order']) for e in self.allocation['evaluations']}),7)

    def test_duplicate_assignment_rejected(self):
        broken = deepcopy(self.allocation); broken['evaluations'][1] = broken['evaluations'][0]
        with self.assertRaisesRegex(ValueError,'DUPLICATE'): validate(broken,self.catalog)

    def test_frozen_input_change_rejected(self):
        changed = deepcopy(self.catalog); changed['input_bindings'][0]['sha256']='bad'
        with self.assertRaisesRegex(ValueError,'FROZEN_INPUT_CHANGED'): validate_catalog(changed)

    def test_anonymous_seven_alternatives(self):
        self.assertEqual(set(self.views),set('ABCDEFG'))
        self.assertNotIn('method',p.canonical(self.views))

    def test_fenced_json_accepted(self):
        obj = score(self.views)
        self.assertEqual(parse('```json\n'+json.dumps(obj)+'\n```'),obj)

    def test_duplicate_and_truncated_json_rejected(self):
        for text in ('{"ratings":{},"ratings":{}}','{"ratings":','{"x":NaN}'):
            with self.subTest(text=text),self.assertRaises(ValueError): parse(text)

    def test_wrong_labels_and_inner_fields_rejected(self):
        for change in ('missing','extra','inner'):
            obj = score(self.views)
            if change=='missing': obj['ratings'].pop('A')
            elif change=='extra': obj['ratings']['H']=obj['ratings']['A']
            else: obj['ratings']['A']['extra']='bad'
            with self.subTest(change=change),self.assertRaises(ValueError): validate_ratings(obj,self.views)

    def test_pcs_range_boolean_and_null_rejected(self):
        for value in (True,None,-1,4,2.5):
            obj = score(self.views); obj['ratings']['A']['pcs']['goal']=value
            with self.subTest(value=value),self.assertRaises(ValueError): validate_ratings(obj,self.views)

    def test_prc_independent_and_empty_rule(self):
        views = deepcopy(self.views); views['A']['empty_slots']=['goal']
        obj = score(views); obj['ratings']['A']['prc']=5
        validate_ratings(obj,views)
        obj['ratings']['A']['pcs']['goal']=1
        with self.assertRaisesRegex(ValueError,'EMPTY_SLOT'): validate_ratings(obj,views)

    def test_no_content_omitted_from_model_ratings(self):
        views=deepcopy(self.views);views['A']['no_content']=True
        obj=score(views);self.assertNotIn('A',obj['ratings']);validate_ratings(obj,views)

    def test_full_workspace_page_search_and_cases(self):
        w=Workspace(self.catalog,self.evaluation,self.config['material_limits'])
        self.assertEqual(len(w.initial()['all_document_cases']),93)
        r=w.tools([{'name':'source_search','arguments':{'source_id':'SRC1','query':'Customer'}},
                   {'name':'case_outputs','arguments':{'task_id':'T92'}}])
        self.assertEqual(len(r[1]['data']['alternatives']),7)
        self.assertEqual(r[0]['data']['matches'][0]['pdf_page'],1)
        self.assertEqual(w.audit[-2]['operation'],'source_search')

    def test_image_144dpi_and_exact_source(self):
        w=Workspace(self.catalog,self.evaluation,self.config['material_limits'])
        data=w.page('SRC1',1,True)
        parts=message(data)['content']
        self.assertEqual(parts[-1]['type'],'image_url')
        import base64
        pix=fitz.Pixmap(base64.b64decode(data['image_base64']))
        self.assertEqual((pix.width,pix.height),(1190,1684))
        self.assertNotIn('image_base64',w.audit[-1])

    def test_tools_reject_invalid_fields_page_and_count(self):
        w=Workspace(self.catalog,self.evaluation,self.config['material_limits'])
        cases=[[],[{'name':'bad','arguments':{}}],[{'name':'source_page','arguments':{'source_id':'SRC1','pdf_page':2}}],
               [{'name':'source_page','arguments':{'source_id':'SRC1','pdf_page':1},'origin':'history'}],
               [{'name':'case_outputs','arguments':{'task_id':'missing'}}]]
        for calls in cases:
            with self.subTest(calls=calls),self.assertRaises(ValueError): w.tools(calls)

    def test_authorization_before_any_runtime(self):
        with self.assertRaisesRegex(ValueError,'LIVE_NOT_AUTHORIZED'):
            runner.run('missing','missing',authorize_live=False,factory=lambda *a,**k:self.fail('called'))

    def test_score_once_and_recover_without_request(self):
        result,transport=self.evaluate([score(self.views)])
        self.assertEqual(result['status'],'COMPLETE');self.assertEqual(transport.calls,1)
        replay=MockTransport([])
        again=runner.evaluate_one(self.config,self.catalog,self.evaluation,self.agent,self.directory,self.snapshot,self.factory(replay))
        self.assertEqual(result,again);self.assertEqual(replay.calls,0)
        self.assertEqual(runner.costs(self.directory,self.ledger)['known_estimated_cny'],'0.0008')

    def test_one_format_correction_no_semantic_rescore(self):
        result,transport=self.evaluate(['{"bad":',score(self.views)])
        self.assertEqual(result['status'],'COMPLETE');self.assertEqual(transport.calls,2)
        self.assertEqual(len(result['attempts']),2)

    def test_invalid_after_correction_is_failure_not_zero(self):
        result,transport=self.evaluate(['bad','bad',score(self.views)])
        self.assertEqual(result['status'],'TECHNICAL_FAILURE');self.assertIsNone(result['ratings']);self.assertEqual(transport.calls,2)

    def test_maximum_four_requests(self):
        tool={'tool_calls':[{'name':'source_page','arguments':{'source_id':'SRC1','pdf_page':1}}]}
        result,transport=self.evaluate([tool,tool,'bad',score(self.views)])
        self.assertEqual(result['status'],'COMPLETE');self.assertEqual(transport.calls,4)

    def test_extra_tool_round_fails_no_increased_retry(self):
        tool={'tool_calls':[{'name':'source_page','arguments':{'source_id':'SRC1','pdf_page':1}}]}
        result,transport=self.evaluate([tool,tool,tool,tool])
        self.assertEqual(result['status'],'TECHNICAL_FAILURE');self.assertEqual(transport.calls,4)

    def test_transport_unknown_preserved_and_not_resent(self):
        result,transport=self.evaluate([TimeoutError('secret must not be logged')])
        self.assertEqual(result['status'],'TECHNICAL_FAILURE');self.assertEqual(transport.calls,1)
        self.assertEqual(runner.costs(self.directory,self.ledger)['unknown_cost_requests'],1)
        self.assertNotIn('secret must not be logged',''.join(p.read_text() for p in self.directory.rglob('*.json')))
        replay=MockTransport([])
        again=runner.evaluate_one(self.config,self.catalog,self.evaluation,self.agent,self.directory,self.snapshot,self.factory(replay))
        self.assertEqual(again,result);self.assertEqual(replay.calls,0)
        with sqlite3.connect(self.ledger) as db:
            self.assertEqual(db.execute('SELECT actual,state FROM reservations').fetchone(),(None,'UNPRICED'))

    def test_http_failure_does_not_retry(self):
        result,transport=self.evaluate(['http_error'])
        self.assertEqual(transport.calls,1);self.assertEqual(result['status'],'TECHNICAL_FAILURE')

    def test_crash_after_runtime_save_reuses_cache(self):
        transport=MockTransport([score(self.views)])
        original=runner.save_new
        def crash(path,value):
            if path.parent.name=='attempts': raise OSError('crash')
            return original(path,value)
        with patch.object(runner,'save_new',side_effect=crash),self.assertRaises(OSError):
            runner.evaluate_one(self.config,self.catalog,self.evaluation,self.agent,self.directory,self.snapshot,self.factory(transport))
        result=runner.evaluate_one(self.config,self.catalog,self.evaluation,self.agent,self.directory,self.snapshot,self.factory(transport))
        self.assertEqual(transport.calls,1);self.assertEqual(result['status'],'COMPLETE')

    def test_implementation_change_stops_before_call(self):
        self.config['implementation']={}
        transport=MockTransport([])
        with self.assertRaisesRegex(ValueError,'IMPLEMENTATION_CHANGED'):
            runner.evaluate_one(self.config,self.catalog,self.evaluation,self.agent,self.directory,self.snapshot,self.factory(transport))
        self.assertEqual(transport.calls,0)

    def test_record_tampering_rejected(self):
        result,_=self.evaluate([score(self.views)])
        result['ratings'][0]['pcs_mean']=0
        with self.assertRaisesRegex(ValueError,'RECORD_HASH'): runner.validate_record(result,self.evaluation,self.snapshot)
        result=p.sealed({k:v for k,v in result.items() if k!='sha256'})
        with self.assertRaisesRegex(ValueError,'SAVED_MEAN'): runner.validate_record(result,self.evaluation,self.snapshot)

    def test_export_failure_null_and_incomplete_coverage(self):
        result,_=self.evaluate(['http_error'])
        summary=export(self.directory,[result],self.snapshot,runner.costs(self.directory,self.ledger))
        saved=p.read_json(self.directory/'export.json')
        self.assertEqual(summary['technical_failures'],1)
        self.assertEqual(len(saved['ratings']),7)
        self.assertTrue(all(r['pcs_mean'] is None for r in saved['ratings']))
        self.assertEqual(export(self.directory,[result],self.snapshot,runner.costs(self.directory,self.ledger)),summary)

    def test_partial_success_not_three_model_average(self):
        result,_=self.evaluate([score(self.views)])
        export(self.directory,[result],self.snapshot,runner.costs(self.directory,self.ledger))
        saved=p.read_json(self.directory/'export.json')
        self.assertTrue(all(r['pcs_mean'] is None and not r['three_model_coverage'] for r in saved['case_method_means']))

    def test_csv_reason_cannot_be_executable_formula(self):
        obj=score(self.views);obj['ratings']['A']['pcs_reason']='=1+1'
        result,_=self.evaluate([obj])
        export(self.directory,[result],self.snapshot,runner.costs(self.directory,self.ledger))
        import csv
        with (self.directory/'ratings.csv').open(encoding='utf-8-sig') as f:
            self.assertEqual(next(csv.DictReader(f))['pcs_reason'],"'=1+1")
        self.assertEqual(p.read_json(self.directory/'export.json')['ratings'][0]['pcs_reason'],'=1+1')

    def test_batch_continues_failure_and_resume_skips_all(self):
        run_dir=p.REPO/'outputs/agent_l4_panel'/self.directory.name
        self.addCleanup(__import__('shutil').rmtree,run_dir,True)
        actions=['http_error']+[score(self.views)]*8
        transport=MockTransport(actions)
        with patch.object(runner,'load',return_value=(self.config,self.catalog,self.allocation)):
            result=runner.run('fake',str(run_dir),authorize_live=True,factory=self.factory(transport))
            self.assertEqual((result['completed'],result['technical_failures']),(8,1))
            self.assertEqual(transport.calls,9)
            repeated=runner.run('fake',str(run_dir),authorize_live=True,factory=self.factory(transport))
            self.assertEqual(repeated,result);self.assertEqual(transport.calls,9)

    def test_full_scope_requires_review(self):
        with patch.object(runner,'load',return_value=(self.config,self.catalog,self.allocation)),self.assertRaisesRegex(ValueError,'FULL_EXPERIMENT_NOT_AUTHORIZED'):
            runner.run('fake','outputs/agent_l4_panel/never-created',scope='full',authorize_live=True)

    def test_empty_panel_has_no_model_call_and_no_prc(self):
        views=deepcopy(self.views)
        for view in views.values():
            view.update(no_content=True,empty_slots=list(p.SLOTS),slots={s:{'claims':[]} for s in p.SLOTS})
        transport=MockTransport([])
        with patch.object(runner,'alternatives',return_value=views):
            result=runner.evaluate_one(self.config,self.catalog,self.evaluation,self.agent,self.directory,self.snapshot,self.factory(transport))
        runner.validate_record(result,self.evaluation,self.snapshot)
        self.assertEqual(transport.calls,0)
        self.assertTrue(all(r['pcs_mean']==0 and r['prc'] is None for r in result['ratings']))
        self.assertFalse(self.ledger.exists())

    def test_missing_receipt_file_stops_before_next_request(self):
        result,_=self.evaluate([score(self.views)])
        cache=next(self.directory.glob('sessions/*/runtime_requests/*.json'))
        cache.unlink()
        transport=MockTransport([])
        with self.assertRaisesRegex(ValueError,'MISSING_PAID_REQUEST_EVIDENCE'):
            runner.evaluate_one(self.config,self.catalog,self.evaluation,self.agent,self.directory,self.snapshot,self.factory(transport))
        self.assertEqual(transport.calls,0)

    def test_pending_dispatch_is_unknown_and_never_resent(self):
        transport=MockTransport([score(self.views)])
        # Simulate a process dying after dispatch metadata is saved, before a response.
        def killed(*args,**kwargs): raise KeyboardInterrupt()
        transport.request=killed
        with self.assertRaises(KeyboardInterrupt):
            runner.evaluate_one(self.config,self.catalog,self.evaluation,self.agent,self.directory,self.snapshot,self.factory(transport))
        replay=MockTransport([])
        result=runner.evaluate_one(self.config,self.catalog,self.evaluation,self.agent,self.directory,self.snapshot,self.factory(replay))
        self.assertEqual(result['status'],'TECHNICAL_FAILURE');self.assertEqual(replay.calls,0)
        self.assertEqual(runner.costs(self.directory,self.ledger)['unknown_cost_requests'],1)

    def test_ledger_disagreement_stops(self):
        self.evaluate([score(self.views)])
        with sqlite3.connect(self.ledger) as db: db.execute('UPDATE reservations SET actual=0')
        with self.assertRaisesRegex(ValueError,'LEDGER_AMOUNT_MISMATCH'): runner.costs(self.directory,self.ledger)

    def test_overwrite_refused(self):
        path=self.directory/'record.json';p.save_new(path,{'a':1})
        with self.assertRaises(FileExistsError): p.save_new(path,{'a':2})
        self.assertEqual(p.read_json(path),{'a':1})


if __name__=='__main__': unittest.main()
