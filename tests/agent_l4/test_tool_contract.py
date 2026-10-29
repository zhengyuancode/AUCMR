import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from eval.l4_agent.tool_contract import validate_tool_request, ToolContractError
from eval.l4_agent.client import model_messages
from eval.l4_agent.contract import CONTRACT_VERSION
from eval.l4_agent.engine import _judge_one
from eval.l4_agent.fixtures import make_fixtures
from eval.l4_agent.inputs import collect
from eval.l4_agent.protocol import REPO, network_blocked
from eval.l4_agent.records import validate_record
from tests.agent_l4.test_staged import answer


class ToolContractTests(unittest.TestCase):
    def setUp(self):
        self.sources = [{'source_id': 'SRC-TEST', 'pages': 100}]
        self.policy = {'tools_per_round': 4}
        self.good = {'tool_calls': [{'name': 'source_page', 'arguments': {
            'source_id': 'SRC-TEST', 'pdf_page': 67, 'image': False}}]}

    def test_exact_extra_fields_and_unchanged_arguments(self):
        bad = {**deepcopy(self.good), 'origin': 'RUNTIME_SOURCE_BOOTSTRAP', 'instruction': 'History metadata'}
        before = deepcopy(bad)
        with self.assertRaises(ToolContractError) as ctx:
            validate_tool_request(bad, self.sources, self.policy)
        self.assertEqual(ctx.exception.details['extra_top_level_fields'], ['instruction', 'origin'])
        self.assertTrue(ctx.exception.details['tool_calls_valid'])
        self.assertIn('读页参数无需修改', ctx.exception.details['instruction'])
        self.assertEqual(bad, before)
        self.assertEqual(validate_tool_request(self.good, self.sources, self.policy), self.good['tool_calls'])

    def test_discovery_ids_are_not_presented_as_citable_page_ids(self):
        history=[{'tool_results':[{'call':{'name':name,'arguments':{'source_id':'s'}},
            'result':{'access_id':'L4-ACCESS-'+str(i),'status':'OK','text':'Actual content'}}]}
            for i,name in enumerate(['source_search','source_index','source_page'],1)]
        before=deepcopy(history)
        messages=model_messages({'system':'fixture','stage_id':'goal'},history)
        results=[json.loads(m['content'][0]['text'])['source_reads'][0]['returned_data'] for m in messages[2:]]
        for result in results[:2]:
            self.assertNotIn('access_id',result);self.assertFalse(result['citable'])
        self.assertEqual(results[2]['access_id'],'L4-ACCESS-3')
        self.assertEqual(history,before)  # Actual server-side audit unchanged.

    def test_invalid_tools_arguments_and_mixed_batches_are_rejected(self):
        variants = []
        for key, value in [('source_id', 'foreign'), ('pdf_page', 0), ('pdf_page', 101),
                           ('pdf_page', True), ('image', 'false'), ('dpi', 96), ('bbox', [0, 0, 1]),
                           ('crossref', True)]:
            bad = deepcopy(self.good); bad['tool_calls'][0]['arguments'][key] = value; variants.append(bad)
        bad = deepcopy(self.good); bad['tool_calls'][0]['name'] = 'shell'; variants.append(bad)
        variants += [{'tool_calls': []}, {'tool_calls': self.good['tool_calls'] * 5},
                     {'tool_calls': self.good['tool_calls'] + [{'name': 'shell', 'arguments': {}}]},
                     {'tool_calls': [{'name': 'source_search', 'arguments': {'source_id': 'SRC-TEST', 'query': ' '}}]}]
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ToolContractError) as ctx:
                validate_tool_request(value, self.sources, self.policy)
            self.assertFalse(ctx.exception.details['tool_calls_valid'])
            self.assertNotIn('读页参数无需修改', ctx.exception.details['instruction'])

    def test_history_is_data_and_targeted_feedback_reaches_real_dispatch(self):
        with tempfile.TemporaryDirectory(dir=REPO/'outputs/agent_l4') as tmp, network_blocked():
            root = Path(tmp); config = make_fixtures(root); config['request_organization'] = CONTRACT_VERSION
            manifest = collect(config); artifact = manifest['artifacts'][0]; task = config['targets'][0]
            test = self
            class Client:
                calls = 0
                good = None
                def respond(inner, request, history):
                    inner.calls += 1
                    messages = model_messages(request, history)
                    source_messages = [m for m in messages[2:] if isinstance(m['content'], list)]
                    for message in source_messages:
                        data = json.loads(message['content'][0]['text'])
                        test.assertEqual(data['record_type'], 'HISTORICAL_SOURCE_READS')
                        test.assertIn('不是返回模板', data['notice'])
                        test.assertFalse({'origin', 'instruction', 'tool_calls', 'tool_results'} & set(data))
                    if inner.calls == 1:
                        inner.good = deepcopy(request['tool_request_example'])
                        return json.dumps({**inner.good, 'origin': 'RUNTIME_SOURCE_BOOTSTRAP', 'instruction': 'old metadata'})
                    if inner.calls == 2:
                        feedback = history[-1]['validation_feedback']['tool_correction']
                        test.assertEqual(feedback['extra_top_level_fields'], ['instruction', 'origin'])
                        test.assertIn('读页参数无需修改', messages[-1]['content'])
                        return json.dumps(inner.good)
                    return json.dumps(answer(request, unresolved=True))
            client = Client()
            record = _judge_one(artifact, task, manifest['sources'], config, manifest, root, client)
            validate_record(record, artifact, manifest, config)
            self.assertEqual(record['processing_status'], 'UNRESOLVED')
            part = record['components'][0]
            self.assertEqual(len(part['transcript']), 3)
            self.assertEqual(len(part['source_access']), len(part['source_access_before']) + 1)
            self.assertEqual(part['source_access'][-1]['arguments'], client.good['tool_calls'][0]['arguments'])

    def test_prose_prefix_feedback_is_explicit_without_relaxing_parser_or_tools(self):
        for mode in ['valid', 'prose_again', 'illegal_tool']:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=REPO/'outputs/agent_l4') as tmp, network_blocked():
                root=Path(tmp);config=make_fixtures(root);config['request_organization']=CONTRACT_VERSION
                manifest=collect(config);artifact=manifest['artifacts'][0];task=config['targets'][0]
                class Client:
                    calls=0
                    good=None
                    def respond(inner,request,history):
                        inner.calls+=1
                        if inner.calls==1:
                            inner.good=deepcopy(request['tool_request_example'])
                            return 'I need to search for evidence.\n'+json.dumps(inner.good)
                        if inner.calls==2:
                            messages=model_messages(request,history)
                            self.assertIn('删除 JSON 前后的全部说明文字',messages[-1]['content'])
                            self.assertFalse(any(m['role']=='assistant' for m in messages))
                            if mode=='prose_again':return 'I need to search.\n'+json.dumps(inner.good)
                            if mode=='illegal_tool':return json.dumps({'tool_calls':[{'name':'shell','arguments':{}}]})
                            return json.dumps(inner.good)
                        return json.dumps(answer(request,unresolved=True))
                client=Client();record=_judge_one(artifact,task,manifest['sources'],config,manifest,root,client)
                validate_record(record,artifact,manifest,config)
                part=record['components'][0]
                self.assertTrue(part['transcript'][0]['raw_response'].startswith('I need'))
                if mode=='valid':
                    self.assertEqual(record['processing_status'],'UNRESOLVED')
                    self.assertEqual(len(part['transcript']),3)
                    self.assertEqual(len(part['source_access']),len(part['source_access_before'])+1)
                else:
                    self.assertEqual(record['processing_status'],'TECHNICAL_FAILURE')
                    self.assertEqual(client.calls,2)
                    self.assertEqual(part['source_access'],part['source_access_before'])
