"""Offline paper-rubric expert protocol and immutable recovery tests."""
import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from eval.l4_agent import expert
from eval.l4_agent.expert_contract import EXPERT_VERSION, EXPERT_INSTRUCTIONS, validate
from eval.l4_agent.expert_sources import prepare, load
from eval.l4_agent.engine import evaluate_run, validate_run, _judge_one
from eval.l4_agent.fixtures import make_fixtures
from eval.l4_agent.inputs import collect, freeze, validate_config
from eval.l4_agent.protocol import REPO, SLOTS, RUBRIC, new_run, network_blocked, digest, read_json
from eval.l4_agent.records import InvalidResponse, seal, validate_record
from eval.l4_agent.client import model_messages
from eval.evaluation_runtime import RuntimeDeferred


def value(stage, **changes):
    result = {'score': 4 if stage == 'prc' else 2, 'status': 'SCORED',
              'reason': '整体基本正确，局部具体行为仍需确认；按论文量表综合判分。', 'issues': [], 'source_refs': []}
    result.update(changes)
    return result


class Client:
    last_receipt = None
    def __init__(self): self.calls = 0
    def respond(self, request, history):
        self.calls += 1
        return json.dumps(value(request['stage_id']))


class ExpertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = network_blocked(); cls.network.__enter__()
        cls.addClassCleanup(cls.network.__exit__, None, None, None)
        cls.run_dir = new_run('test-expert')
        cls.config = make_fixtures(cls.run_dir)
        keep = {'F-01', 'F-04', 'F-05'}
        cls.config['targets'] = [t for t in cls.config['targets'] if t['task_id'] in keep]
        cls.config['input_bindings'] = [b for b in cls.config['input_bindings'] if b['task_id'] in keep]
        original = collect(cls.config)
        cls.config['source_contexts'] = {t['task_id']: prepare(cls.config, t, original['sources'], {'SRC-FIXTURE': [1]}, cls.run_dir/'fixtures'/(t['task_id']+'-context.json')) for t in cls.config['targets']}
        cls.config['request_organization'] = EXPERT_VERSION
        cls.manifest = freeze(cls.config, cls.run_dir)
        cls.client = Client()
        cls.records = evaluate_run(cls.run_dir, cls.client, mock=True)
        cls.artifact = cls.manifest['artifacts'][0]
        cls.task = cls.config['targets'][0]
        cls.pack = load(cls.config, cls.task, cls.manifest['sources'])

    def test_paper_rubric_verbatim(self):
        paper = (REPO.parent/'samples/main-zh.tex').read_text()
        for metric in RUBRIC.values():
            for explanation in metric.values(): self.assertIn(explanation.rstrip('。'), paper)
        self.assertNotIn('在已核验材料范围', str(RUBRIC))

    def test_no_evidence_no_exhaustive_checks_can_score(self):
        for score in range(4):
            self.assertEqual(validate(json.dumps(value('goal',score=score)), 'goal', self.artifact, self.pack['accesses'])['score'], score)

    def test_uncertainty_is_not_null(self):
        v=value('goal',issues=[{'location':'goal:0','kind':'UNCERTAINTY','explanation':'来源没有明确描述该细节，结合任务整体判断，不视为已有反证。','source_refs':[]}])
        self.assertEqual(validate(json.dumps(v),'goal',self.artifact,self.pack['accesses'])['score'],2)
        with self.assertRaises(InvalidResponse): validate(json.dumps({**v,'score':None}),'goal',self.artifact,self.pack['accesses'])

    def test_no_hidden_point_mapping(self):
        for score in range(1,6):
            self.assertEqual(validate(json.dumps(value('prc',score=score)), 'prc',self.artifact,self.pack['accesses'])['score'],score)

    def test_invalid_ids_types_fields_and_old_checks_rejected(self):
        for change in [{'score':True},{'score':4},{'status':'UNRESOLVED'},{'checks':[]},{'source_refs':['invented']}, {'reason':''}]:
            with self.subTest(change=change), self.assertRaises(InvalidResponse):
                validate(json.dumps(value('goal',**change)), 'goal',self.artifact,self.pack['accesses'])

    def test_issue_locations_still_strict(self):
        bad=value('goal',issues=[{'location':'goal:900','kind':'ERROR','explanation':'bad','source_refs':[]}])
        with self.assertRaises(InvalidResponse):validate(json.dumps(bad),'goal',self.artifact,self.pack['accesses'])

    def test_saved_scores_and_seven_slot_denominator(self):
        _,_,records=validate_run(self.run_dir)
        self.assertEqual(len(records),6)
        for r in records:
            self.assertEqual(r['processing_status'],'COMPLETED')
            self.assertEqual(r['pcs_mean'],sum(s['score'] for s in r['pcs'].values())/7)
            self.assertEqual(r['prc']['score'],4)
            self.assertEqual(r['rater_type'],'MODEL_SIMULATED_EXPERT')
        empty=next(r for r in records if r['task_id']=='F-04')
        self.assertEqual(empty['pcs']['alternative_flow']['score'],0)

    def test_all_methods_same_context_and_no_other_scores(self):
        a,b=self.records[:2]
        self.assertEqual(a['source_access'],b['source_access'])
        for r in (a,b):
            for turn in r['request_transcript']:
                req=turn['request'];self.assertEqual(req['rubric'],RUBRIC)
                self.assertNotIn('required_claim_ids',req)
                self.assertNotIn('method',req['artifact'])
                self.assertNotIn('pcs',req)
                self.assertEqual(turn['history'][0],expert.source_history(self.pack['accesses'])[0])

    def test_prc_sends_complete_original_artifact_once_without_new_fields(self):
        artifact=next(a for a in self.manifest['artifacts'] if a['task_id']=='F-04')
        task=next(t for t in self.config['targets'] if t['task_id']=='F-04')
        pack=load(self.config,task,self.manifest['sources'])
        before=deepcopy(artifact)
        req=expert.request_for('prc',artifact,task,self.manifest['sources'],pack)
        messages=model_messages(req,expert.source_history(pack['accesses']))
        self.assertEqual(len(messages),3)
        sent=json.loads(messages[1]['content'])
        self.assertEqual(sent['artifact']['slots'],artifact['slots'])
        self.assertEqual(sent['artifact']['task'],{'name':task['name'],'actor':task['actor']})
        self.assertEqual(sent['artifact']['slots']['alternative_flow']['claims'],[])
        self.assertNotIn('prc_review_guidance',sent)
        self.assertEqual(set(sent['output_schema']['properties']),{'score','status','reason','issues','source_refs'})
        self.assertEqual(artifact,before)
        self.assertIsInstance(messages[-1]['content'],list)  # Original source reads remain available.
        for score in range(1,6):
            judged=value('prc',score=score,issues=[{'location':'preconditions:0','kind':'ERROR',
                'explanation':'需要纠正前提；具体修订幅度由评审者判断。','source_refs':[]}])
            self.assertEqual(validate(json.dumps(judged),'prc',artifact,pack['accesses'])['score'],score)

    def test_prc_and_pcs_keep_identical_source_messages(self):
        sources=[]
        for stage in ['goal','prc']:
            req=expert.request_for(stage,self.artifact,self.task,self.manifest['sources'],self.pack)
            messages=model_messages(req,expert.source_history(self.pack['accesses']))
            self.assertEqual(len(messages),3)
            self.assertNotIn('prc_review_guidance',req)
            self.assertNotIn('READ_ONLY_COMPLETE_USE_CASE_REPEAT',json.dumps(messages))
            self.assertEqual(messages[0],{'role':'system','content':EXPERT_INSTRUCTIONS})
            self.assertEqual(req['rubric'],RUBRIC)
            sources.append(messages[-1])
        self.assertEqual(sources[0],sources[1])

    def test_prc_keeps_canonical_correction_target_last_without_repeated_prompt(self):
        req=expert.request_for('prc',self.artifact,self.task,self.manifest['sources'],self.pack)
        target=value('prc');raw=json.dumps(target)+'}'
        history=expert.source_history(self.pack['accesses'])+[{'validation_feedback':{
            'errors':['INVALID_JSON_RESPONSE'],'rejected_response':raw,
            'json_diagnostic':expert.json_diagnostic(raw)}}]
        messages=model_messages(req,history)
        self.assertEqual(json.loads(messages[-1]['content']),target)
        self.assertEqual(len(messages),5)  # System, input, sources, diagnosis, exact JSON target.
        self.assertNotIn('READ_ONLY_COMPLETE_USE_CASE_REPEAT',json.dumps(messages))
        self.assertFalse(any(m['role']=='assistant' for m in messages))

    def test_resume_does_not_call(self):
        class NoCalls:
            def respond(self,*a):raise AssertionError('repeated paid judgment')
        self.assertEqual(evaluate_run(self.run_dir,NoCalls(),mock=True,resume=True),self.records)

    def test_partial_component_resume_does_not_redraw(self):
        run=new_run('test-expert-resume');m=freeze(self.config,run)
        # _judge_one isolates checkpoint recovery without relabelling real inputs as mock.
        c=Client();calls=[]
        original=c.respond
        def interrupted(req,h):
            calls.append(req['stage_id'])
            if len(calls)==2:raise RuntimeDeferred('pause')
            return original(req,h)
        c.respond=interrupted
        with self.assertRaises(RuntimeDeferred):_judge_one(self.artifact,self.task,m['sources'],self.config,m,run,c)
        c2=Client();r=_judge_one(self.artifact,self.task,m['sources'],self.config,m,run,c2)
        self.assertEqual(c2.calls,7);self.assertEqual(r['processing_status'],'COMPLETED')

    def test_unauthed_no_call(self):
        run=new_run('test-expert-unauthorized');freeze(self.config,run);c=Client()
        with self.assertRaises(ValueError):evaluate_run(run,c,explicit_live=False)
        self.assertEqual(c.calls,0)

    def test_one_correction_then_stop(self):
        class Bad(Client):
            def respond(self,r,h):self.calls+=1;return '{truncated'
        run=new_run('test-expert-bad');m=freeze(self.config,run);c=Bad()
        r=_judge_one(self.artifact,self.task,m['sources'],self.config,m,run,c)
        self.assertEqual(c.calls,2);self.assertEqual(r['processing_status'],'TECHNICAL_FAILURE');self.assertIsNone(r['pcs_mean'])
        self.assertEqual(len(list((run/'attempts').rglob('*.json'))),2)
        validate_record(r,self.artifact,m,self.config)

    def test_unknown_cost_stops_and_keeps_receipt(self):
        class Unknown(Client):
            def respond(self,r,h):
                self.calls+=1;self.last_receipt={'cost_cny':None,'real_model_calls':1,'finish_reason':'stop'}
                return json.dumps(value(r['stage_id']))
        run=new_run('test-expert-cost');m=freeze(self.config,run);c=Unknown()
        r=_judge_one(self.artifact,self.task,m['sources'],self.config,m,run,c)
        self.assertEqual(c.calls,1);self.assertTrue(r['stop_run']);self.assertIsNone(r['runtime_cost_cny'])

    def test_truncated_valid_json_is_never_accepted(self):
        class Truncated(Client):
            def respond(self,r,h):
                self.calls+=1;self.last_receipt={'cost_cny':.01,'real_model_calls':1,'finish_reason':'length'}
                return json.dumps(value(r['stage_id']))
        run=new_run('test-expert-length');m=freeze(self.config,run);c=Truncated()
        r=_judge_one(self.artifact,self.task,m['sources'],self.config,m,run,c)
        self.assertEqual(c.calls,2);self.assertEqual(r['processing_status'],'TECHNICAL_FAILURE');self.assertEqual(r['runtime_cost_cny'],.02)

    def test_correction_message_never_reintroduces_null_rule(self):
        req=expert.request_for('goal',self.artifact,self.task,self.manifest['sources'],self.pack)
        msg=model_messages(req,[{'validation_feedback':{'errors':['bad field'],'rejected_response':'{}'}}])
        self.assertIn('不因缺少引用改成 null',msg[-1]['content'])
        self.assertNotIn('UNRESOLVED with null',json.dumps(msg))

    def test_extra_brace_diagnostic_never_accepts_or_echoes_bad_template(self):
        raw=json.dumps(value('goal'))+'}'
        diagnostic=expert.json_diagnostic(raw)
        self.assertEqual(diagnostic['problem'],'Extra data')
        self.assertEqual(diagnostic['unexpected_suffix'],'}')
        self.assertEqual(diagnostic['complete_object_for_reserialization'],value('goal'))
        with self.assertRaises(InvalidResponse):validate(raw,'goal',self.artifact,self.pack['accesses'])
        req=expert.request_for('goal',self.artifact,self.task,self.manifest['sources'],self.pack)
        msg=model_messages(req,[{'validation_feedback':{'errors':['INVALID_JSON_RESPONSE'],'rejected_response':raw,'json_diagnostic':diagnostic}}])
        self.assertFalse(any(m['role']=='assistant' for m in msg))
        self.assertNotIn('rejected_response_as_data_not_template',msg[-1]['content'])
        self.assertEqual(json.loads(msg[-2]['content'])['json_diagnostic']['unexpected_suffix'],'}')
        self.assertEqual(json.loads(msg[-1]['content']),value('goal'))
        self.assertNotIn('complete_object_for_reserialization',msg[-2]['content'])

    def test_context_and_record_tampering_rejected(self):
        c=deepcopy(self.config);c['source_contexts'][self.task['task_id']]['sha256']='bad'
        with self.assertRaises(ValueError):load(c,self.task,self.manifest['sources'])
        r=deepcopy(self.records[0]);r['pcs']['goal']['score']=0;r.pop('record_sha256');seal(r)
        with self.assertRaises(ValueError):validate_record(r,self.artifact,self.manifest,self.config)

    def test_format_diagnostic_does_not_guess_or_extract_ambiguous_values(self):
        for raw in ['{"score":2', '{"score":1,"score":2}}', '{"score":NaN}}', '{"score":2} prose']:
            self.assertNotIn('complete_object_for_reserialization',expert.json_diagnostic(raw) or {})

    def test_saved_malformed_responses_still_fail_after_two_attempts(self):
        data=read_json(Path(__file__).parent/'fixtures/expert_json_closers.json')
        for raw in data['raw_responses']:
            with self.assertRaises(InvalidResponse):validate(raw,'goal',self.artifact,self.pack['accesses'])
        class Replay(Client):
            def respond(self,req,history):
                raw=data['raw_responses'][self.calls];self.calls+=1;return raw
        run=new_run('test-expert-saved-json-failure');manifest=freeze(self.config,run);c=Replay()
        record=_judge_one(self.artifact,self.task,manifest['sources'],self.config,manifest,run,c)
        validate_record(record,self.artifact,manifest,self.config)
        self.assertEqual(c.calls,2);self.assertEqual(record['processing_status'],'TECHNICAL_FAILURE')
        self.assertIsNone(record['pcs_mean']);self.assertIsNone(record['components'][0]['value'])
        self.assertEqual([t['raw_response'] for t in record['components'][0]['transcript']],data['raw_responses'])

    def test_saved_failure_replay_corrects_serialization_without_changing_values(self):
        data=read_json(Path(__file__).parent/'fixtures/expert_json_closers.json')
        class Reserialize(Client):
            def respond(self,req,history):
                self.calls+=1
                if self.calls==1:return data['raw_responses'][0]
                if self.calls==2:
                    messages=model_messages(req,history)
                    target=messages[-1]['content']
                    if json.loads(target)!=data['unchanged_judgment']:raise AssertionError('Changed judgment')
                    return target
                return json.dumps(value(req['stage_id']))
        run=new_run('test-expert-saved-json-correction');manifest=freeze(self.config,run);c=Reserialize()
        record=_judge_one(self.artifact,self.task,manifest['sources'],self.config,manifest,run,c)
        validate_record(record,self.artifact,manifest,self.config)
        self.assertEqual(record['processing_status'],'COMPLETED')
        part=record['components'][0]
        self.assertEqual(len(part['transcript']),2);self.assertEqual(part['value'],data['unchanged_judgment'])
        self.assertEqual(part['transcript'][0]['raw_response'],data['raw_responses'][0])

    def test_delimiter_diagnostic_is_string_aware_and_never_reconstructs(self):
        data=read_json(Path(__file__).parent/'fixtures/expert_json_closers.json')
        first,second=map(expert.json_diagnostic,data['raw_responses'])
        self.assertEqual(first['delimiter_mismatch']['expected'],'END_OF_DOCUMENT')
        self.assertEqual(second['delimiter_mismatch']['actual'],']')
        self.assertEqual(second['delimiter_mismatch']['expected'],'}')
        self.assertNotIn('complete_object_for_reserialization',second)
        quoted=json.dumps({'text':'Quoted delimiters } ] { [ and escaped quote " plus backslash \\', 'items':[{}]})
        self.assertIsNone(expert.delimiter_diagnostic(quoted))
        self.assertEqual(expert.delimiter_diagnostic(quoted+']')['expected'],'END_OF_DOCUMENT')
        self.assertEqual(expert.delimiter_diagnostic('{"a":[')['problem'],'UNCLOSED_CONTAINER')

    def test_repair_target_does_not_bypass_reference_or_value_validation(self):
        data=read_json(Path(__file__).parent/'fixtures/expert_json_closers.json')
        bad=deepcopy(data['unchanged_judgment']);bad['source_refs']=['fabricated-page']
        with self.assertRaises(InvalidResponse):validate(json.dumps(bad),'goal',self.artifact,self.pack['accesses'])
        changed=deepcopy(data['unchanged_judgment']);changed['issues'][0]['explanation']='Changed judgment'
        class Changed(Client):
            def respond(self,req,history):
                self.calls+=1
                return data['raw_responses'][0] if self.calls==1 else json.dumps(changed)
        run=new_run('test-expert-saved-change-rejected');manifest=freeze(self.config,run);c=Changed()
        record=_judge_one(self.artifact,self.task,manifest['sources'],self.config,manifest,run,c)
        self.assertEqual(c.calls,2);self.assertEqual(record['processing_status'],'TECHNICAL_FAILURE')
        self.assertIn('FORMAT_CORRECTION_CHANGED_JUDGMENT',record['validation_errors'][0])

    def test_format_correction_cannot_change_judgment(self):
        class Changed(Client):
            def respond(self,r,h):
                self.calls+=1
                return json.dumps(value(r['stage_id'],score=1 if self.calls==1 else 3))+('}' if self.calls==1 else '')
        run=new_run('test-expert-format-change');m=freeze(self.config,run);c=Changed()
        r=_judge_one(self.artifact,self.task,m['sources'],self.config,m,run,c)
        self.assertEqual(c.calls,2);self.assertEqual(r['processing_status'],'TECHNICAL_FAILURE')
        self.assertIsNone(r['components'][0]['value'])
        self.assertIn('FORMAT_CORRECTION_CHANGED_JUDGMENT',r['validation_errors'][0])

    def test_context_bindings_cannot_vary_by_method(self):
        c=deepcopy(self.config);c['source_contexts']['Fixture-Method-A']=c['source_contexts']['F-01']
        with self.assertRaises(ValueError):validate_config(c)

    def test_empty_slot_does_not_force_prc_penalty_or_exemption(self):
        artifact=next(a for a in self.manifest['artifacts'] if a['task_id']=='F-04')
        task=next(t for t in self.config['targets'] if t['task_id']=='F-04')
        for score in range(1,6):
            class Independent(Client):
                def respond(self,req,history):
                    self.calls+=1
                    if req['stage_id']=='prc':
                        self_empty=req['program_handled_empty_slots']
                        if self_empty!=['alternative_flow']:raise AssertionError(self_empty)
                        if req['artifact']['slots']['alternative_flow']['claims']:raise AssertionError('Hidden empty slot')
                    return json.dumps(value(req['stage_id'],score=score if req['stage_id']=='prc' else 3))
            run=new_run('test-expert-independent-prc');manifest=freeze(self.config,run)
            record=_judge_one(artifact,task,manifest['sources'],self.config,manifest,run,Independent())
            validate_record(record,artifact,manifest,self.config)
            self.assertEqual(record['pcs']['alternative_flow']['score'],0)
            self.assertEqual(record['pcs_mean'],18/7)
            self.assertEqual(record['prc']['score'],score)

    def test_local_inaccuracy_keeps_model_score_and_seven_slot_mean(self):
        class Local(Client):
            def respond(self,req,history):
                self.calls+=1
                v=value(req['stage_id'],score=5 if req['stage_id']=='prc' else 3)
                if req['stage_id']=='goal':
                    v.update(score=2,issues=[{'location':'goal:0','kind':'INACCURACY',
                        'explanation':'校准用局部不准确，保留模型综合分数，不按问题数量重新计分。','source_refs':[]}])
                return json.dumps(v)
        run=new_run('test-expert-local-score');manifest=freeze(self.config,run)
        record=_judge_one(self.artifact,self.task,manifest['sources'],self.config,manifest,run,Local())
        validate_record(record,self.artifact,manifest,self.config)
        self.assertEqual(record['pcs_mean'],20/7)
        self.assertEqual(record['pcs']['goal']['score'],2)
        self.assertEqual(record['prc']['score'],5)

    def test_maximum_scores_remain_valid_for_multiple_methods(self):
        class Maximum(Client):
            def respond(self,req,history):
                self.calls+=1
                return json.dumps(value(req['stage_id'],score=5 if req['stage_id']=='prc' else 3))
        run=new_run('test-expert-no-score-quota');manifest=freeze(self.config,run)
        records=[]
        for artifact in [a for a in manifest['artifacts'] if a['task_id']=='F-01']:
            record=_judge_one(artifact,self.task,manifest['sources'],self.config,manifest,run,Maximum())
            validate_record(record,artifact,manifest,self.config);records.append(record)
        self.assertEqual([r['pcs_mean'] for r in records],[3,3])
        self.assertEqual([r['prc']['score'] for r in records],[5,5])
        from eval.l4_agent.export import summarize
        self.assertEqual(summarize(records,self.config)['rater_type'],'MODEL_SIMULATED_EXPERT')

    def test_missing_and_empty_are_not_scored_as_model_judgments(self):
        for state in ['MISSING_FILE','EMPTY_OUTPUT','REFUSED']:
            a=deepcopy(self.artifact);a['state']=state
            if state=='MISSING_FILE':a.pop('explicit_refusal',None)
            else:
                a['slots']={s:{'claims':[]} for s in SLOTS};a['empty_slots']=list(SLOTS)
                a['explicit_refusal']=state=='REFUSED'
            c=Client();r=_judge_one(a,self.task,self.manifest['sources'],self.config,self.manifest,self.run_dir,c)
            validate_record(r,a,self.manifest,self.config)
            self.assertEqual(c.calls,0);self.assertIsNone(r['prc']['score'])
            self.assertEqual(r['pcs_mean'],None if state=='MISSING_FILE' else 0)


if __name__=='__main__':unittest.main()
