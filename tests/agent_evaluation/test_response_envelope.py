import json
import unittest

from jsonschema import Draft202012Validator, ValidationError
from eval.agent_evaluation.agents.roles import prompt, response_schema
from eval.agent_evaluation.schemas import payload_schema


class ResponseEnvelopeTests(unittest.TestCase):
    def test_schema_describes_wire_envelope_and_keeps_payload_definitions(self):
        schema=response_schema(['scope','scene_proposal'])
        Draft202012Validator.check_schema(schema)
        for name, definition in schema['$defs'].items():
            self.assertEqual(definition,payload_schema('scope')['$defs'][name])
        self.assertIn('locator',schema['$defs'])
        self.assertNotIn('content',schema['$defs'])
        validator=Draft202012Validator(schema)
        validator.validate({'tool_calls':[{'name':'source_index','arguments':{'source_id':'FIX-C01'}}]})
        for body in [{'scene_proposal':{}}, {'scope':{},'scene_proposal':{}},
                     {'records':[]}, {'tool_calls':[]},
                     {'records':[],'tool_calls':[{'name':'source_index','arguments':{}}]}]:
            with self.subTest(body=body),self.assertRaises(ValidationError):validator.validate(body)
        records=schema['oneOf'][1]['properties']['records']
        self.assertEqual([r['properties']['kind']['const'] for r in records['prefixItems']],['scope','scene_proposal'])

    def test_rendered_prompt_has_one_explicit_output_contract(self):
        text=prompt('SCENE_BUILD_A',['scope','scene_proposal'])
        self.assertNotIn('返回 scene_proposal，不自称已审定',text)
        self.assertIn('records_envelope_v2',text)
        self.assertIn('先给 scope，再给 scene_proposal',text)
        actual=json.loads(text.rsplit('\n',1)[1])
        self.assertEqual(actual,response_schema(['scope','scene_proposal']))
        self.assertIn('原文首次具有完整动作和结果依据的变体',text)

    def test_actual_failed_shape_still_rejected_not_relabelled(self):
        failed={'scene_proposal':{'scope_id':'C01.scope','core_status':'CORE_UNESTABLISHED',
                'branch_status':'NOT_ESTABLISHED_WITHIN_SEARCH','scenarios':[],
                'source_search_log_ids':[],'unresolved':['目标范围未建立']}}
        with self.assertRaises(ValidationError):
            Draft202012Validator(response_schema(['scope','scene_proposal'])).validate(failed)


if __name__=='__main__':unittest.main()
