"""Pilot-first, resumable final L3 coverage experiment with an existing campaign ledger."""
import argparse
import copy
import fcntl
import json
import math
import os
import signal
import threading
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from eval.agent_evaluation.common import REPO,canonical,digest,file_hash,read_json,write_json,append_jsonl,now
from eval.agent_evaluation.budget import Budget,units
from eval.l3_reference.acceptance import AcceptanceTransport
from eval.l3_reference.validation import require
from .inputs import prepare,locked,PREP,SLOTS
from .contract import ordered,sample,validate,aggregate,reconcile,deterministic_rows
from .tasks import task,GlobalStop,task_key,PROMPT_REVISION,account_recovery_authorized

DEFAULT=REPO/'outputs/agent_evaluation/l3-coverage-pure54-20261104-v1'

class Progress:
    def __init__(self,root):
        self.root=root;self.mutex=threading.RLock();self.stop=threading.Event();self.done=threading.Event()
        self.state={'status':'PREPARED','active':{},'started':now()};self.call_cache={}
    def event(self,event,**values):
        with self.mutex:
            role=values.get('role')
            if role:self.state['active'][role]=values
            self.state.update({k:v for k,v in values.items() if k in {'status','phase','completed_cases','scope'}})
            self.state.update(updated=now(),event=event)
            for p in (self.root/'tasks').glob('*/call-*.json'):
                if p not in self.call_cache:self.call_cache[p]=read_json(p)
            calls=list(self.call_cache.values())
            self.state['initial_records']={r:len(list((self.root/'views/INITIAL'/r).glob('*.json'))) for r in ('A','B')}
            self.state.update(calls=len(calls),estimated_cny=round(sum(x['cost_cny'] for x in calls if x.get('cost_cny') is not None),6),unpriced_calls=sum(x.get('cost_cny') is None for x in calls),completed_model_tasks=len(list((self.root/'tasks').glob('*/result.json'))))
            write_json(self.root/'progress.json',self.state)
            append_jsonl(self.root/'events.jsonl',{'event':event,**values,'time':now()})
            active='; '.join(f"{r}:{v.get('phase','')} {v.get('target','')} #{v.get('attempt','-')}" for r,v in self.state['active'].items())
            line=f"[{self.state['updated']}] {self.state['status']} {event} | 完成评分 {self.state.get('completed_cases',0)}/378 | 初评记录 {self.state['initial_records']} | 调用 {len(calls)} 费用估算 ¥{self.state['estimated_cny']:.6f} 未计价 {self.state['unpriced_calls']} | {active}"
            with (self.root/'run.log').open('a') as f:f.write(line+'\n')
            print(line,flush=True)
    def __enter__(self):
        def heartbeat():
            while not self.done.wait(15):self.event('HEARTBEAT')
        self.thread=threading.Thread(target=heartbeat,daemon=True);self.thread.start();return self
    def __exit__(self,*args):self.done.set();self.thread.join()

class Experiment:
    def __init__(self,root,config,plan,contexts,transport,budget,progress):
        self.root,self.config,self.plan,self.contexts=root,config,plan,contexts
        self.transport,self.budget,self.progress=transport,budget,progress
        self.chunk=config['chunk_size']
    def parallel(self,roles,fn):
        def worker(role):
            try:return fn(role)
            except BaseException:
                self.progress.stop.set();raise
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(worker,r) for r in roles]
            for f in futures:f.result()
    def call(self,case,reqs,phase,role,opinions=None,artifact=None):
        if self.progress.stop.is_set():raise GlobalStop('another worker stopped; no new request')
        tid=case['target_id']
        return task(self.root,self.config,self.contexts[tid],self.plan['references'][tid],artifact or case['artifact'],reqs,phase,role,self.budget,self.transport,self.progress,opinions)
    def calibration(self):
        # Controlled contrasts of the actual first pilot requirement, no new research target.
        tid=self.plan['pilot_targets'][0];ref=self.plan['references'][tid];requirement=ref['requirements'][0]
        expected={
            'EXACT':[requirement['text'],'FULL'],
            'MISSING_ORDER':['The Game menu contains an "Open game ..." entry.','PARTIAL'],
            'UNRELATED':['The system displays the current board.','NOT_COVERED'],
            'REVERSED_ORDER':['The Game menu lists "Exit", "Save Game As...", "Save Game ...", and "Open game ...", in that order.','CONTRADICTED']}
        require('listed before' in requirement['text'],'calibration source requirement changed')
        locked(self.root/'calibration-fixtures.json',{'reference_id':requirement['requirement_id'],'reference_sha256':ref['reference_sha256'],'fixtures':expected,'purpose':'Controlled coverage-label calibration; excluded from research scores.'})
        def role_run(role):
            outcomes=[]
            for name,(text,label) in expected.items():
                slots={s:{'claims':[]} for s in SLOTS};slots['main_flow']['claims']=[{'claim_id':'CAL-CLAIM','content':text}]
                artifact={'artifact_id':'CAL-'+name,'artifact_sha256':digest(slots),'use_case_name':self.contexts[tid]['target']['name'],'actor':self.contexts[tid]['target']['actor'],'slots':slots,'no_content':False,'explicit_refusal':False}
                case={'target_id':tid,'artifact':artifact}
                response=self.call(case,[requirement],'CALIBRATION',role)
                actual=response['judgments'][0]['label'];outcomes.append({'fixture':name,'expected':label,'observed':actual,'passed':actual==label})
            write_json(self.root/'calibration'/PROMPT_REVISION/f'{role}.json',outcomes)
            require(all(x['passed'] for x in outcomes),'coverage calibration mismatch for '+role)
        self.parallel(('A','B','C'),role_run)
        write_json(self.root/'calibration.json',{'status':'PASS','prompt_revision':PROMPT_REVISION,'controlled_contrasts':12,'all_three_roles':True,'does_not_certify_research_accuracy':True})
    def view_path(self,phase,role,key):return self.root/'views'/phase/role/(key+'.json')
    def read_view(self,phase,role,key):
        p=self.view_path(phase,role,key)
        return read_json(p)['rows'] if p.exists() else {}
    def evaluate(self,case,phase,role,selected=None,opinions=None):
        ref=self.plan['references'][case['target_id']];artifact=case['artifact'];rows={};errors=[];issues=[];tasks=[]
        if case.get('input_error'):
            errors.append({'kind':'INPUT_ERROR','error':case['input_error']})
        else:
            deterministic=deterministic_rows(ref,artifact)
            rows={k:v for k,v in deterministic.items() if selected is None or k in selected}
            confirmed=[r for r in ref['requirements'] if r['status']=='CONFIRMED' and r['requirement_id'] not in deterministic]
            for offset in range(0,len(confirmed),self.chunk):
                reqs=[r for r in confirmed[offset:offset+self.chunk] if selected is None or r['requirement_id'] in selected]
                if not reqs:continue
                ids=[r['requirement_id'] for r in reqs]
                op={rid:opinions[rid] for rid in ids} if opinions else None
                key=task_key(phase,role,artifact['artifact_id'],ids);tasks.append(key)
                try:
                    body=self.call(case,reqs,phase,role,op)
                    rows.update(validate(body,reqs,artifact));issues+=body.get('reference_issues',[])
                except GlobalStop:raise
                except (ValueError,KeyError,TypeError) as exc:
                    if any(s in str(exc).lower() for s in ('hash changed','hash mismatch','frozen coverage input changed','differ from response','differs from response','source-only bundle changed','cached request differs','cached repair messages differ')):
                        raise GlobalStop('input or cached provenance integrity failure: '+str(exc)) from exc
                    error={'task':key,'requirement_ids':ids,'error':str(exc)};errors.append(error)
                    append_jsonl(self.root/'failures.jsonl',{'time':now(),'case_id':case['case_id'],'phase':phase,'role':role,**error})
                    self.progress.event('TASK_FAILED_CONTINUING',role=role,phase=phase,target=self.contexts[case['target_id']]['target']['name'],task=key)
        record={'case_id':case['case_id'],'phase':phase,'role':role,'rows':rows,'reference_issues':issues,'errors':errors,'tasks':tasks}
        write_json(self.view_path(phase,role,case['case_id']),record)
        return rows
    def initial(self,keys,phase='INITIAL',selection=None):
        def role_run(role):
            for key in self.plan['order_'+role]:
                if key not in keys:continue
                self.evaluate(self.plan['cases'][key],phase,role,None if selection is None else selection.get(key,set()))
        self.parallel(('A','B'),role_run)
    def register_repairs(self,keys,phases):
        """Append one auditable technical repair permit per already-paid failed task."""
        path=self.root/'repair-overrides.json'
        plan=read_json(path) if path.exists() else {'schema':'L3_COVERAGE_ONE_TARGETED_FORMAT_REPAIR_V1','tasks':{}}
        require(plan.get('schema')=='L3_COVERAGE_ONE_TARGETED_FORMAT_REPAIR_V1','invalid targeted repair plan')
        pending={}
        for phase,role in phases:
            for key in sorted(keys):
                view=self.view_path(phase,role,key)
                if not view.exists():continue
                record=read_json(view);selected=set(record['rows']);eligible=False
                for error in record['errors']:
                    selected.update(error.get('requirement_ids',[]));task_id=error.get('task')
                    if not task_id:continue
                    folder=self.root/'tasks'/task_id;recovery=self.root/'task_progress'/digest('coverage/'+task_id)
                    receipts=sorted(recovery.glob('request-*.json'),key=lambda p:int(p.stem.split('-')[-1]))
                    if not receipts:continue
                    receipt=receipts[-1];attempt=int(receipt.stem.split('-')[-1]);saved=read_json(receipt)
                    result=saved.get('result') or {}
                    if attempt>=self.config['max_requests_per_task'] or saved.get('status')!='OK' or result.get('cost_cny') is None or result.get('request_budget_violation'):continue
                    if not (folder/f'validation-{attempt}.json').exists():continue
                    if task_id not in plan['tasks']:
                        plan['tasks'][task_id]={'input_sha256':file_hash(folder/'input.json'),'receipt_path':str(receipt.relative_to(self.root)),
                            'receipt_sha256':file_hash(receipt),'extra_format_repairs':1,'reason':'One targeted technical repair after preserved bounded failure: '+error['error']}
                    eligible=True
                if eligible:pending[(phase,role,key)]=selected
        if pending:
            locked(self.root/'repairs'/('plan-'+digest(plan)+'.json'),plan)
            write_json(path,plan)
        return pending
    def targeted_repair(self,keys,phases):
        pending=self.register_repairs(keys,phases)
        if not pending:return
        require(all(role in ('A','B') for _,role,_ in pending),'C repair requires its blind/adjudication workflow')
        self.progress.event('TARGETED_FORMAT_REPAIRS',failed_artifacts=len(pending))
        def worker(role):
            for (phase,r,key),selected in sorted(pending.items()):
                if r==role:self.evaluate(self.plan['cases'][key],phase,r,selected)
        self.parallel(sorted({r for _,r,_ in pending}),worker)

    def positions(self,keys):
        positions=[]
        for key in sorted(keys):
            case=self.plan['cases'][key]
            if case['input_error'] or case['artifact']['no_content']:continue
            for req in self.plan['references'][case['target_id']]['requirements']:
                if req['status']=='CONFIRMED':positions.append({'id':key+'/'+req['requirement_id'],'case_id':key,'requirement_id':req['requirement_id'],'project_id':case['project_id']})
        return positions
    def review(self,keys,pilot=False):
        positions=self.positions(keys);agreed=[];disputed=[];missing=[]
        first={key:(self.read_view('INITIAL','A',key),self.read_view('INITIAL','B',key)) for key in keys}
        for row in positions:
            a,b=first[row['case_id']];rid=row['requirement_id']
            if rid not in a or rid not in b:missing.append(row);continue
            (agreed if a[rid]['label']==b[rid]['label'] else disputed).append(row)
        audit=sample(agreed,'COVERAGE_AGREEMENT_REVIEW')
        pilots=[r for r in positions if self.plan['cases'][r['case_id']]['target_id'] in self.plan['pilot_targets']]
        # Full pilot review is fixed before seeing scores and retained in the final union.
        selected={r['id']:r for r in [*disputed,*audit,*pilots] if r not in missing}
        for key in sorted(keys):
            for role in ('A','B'):
                p=self.view_path('INITIAL',role,key)
                for issue in read_json(p).get('reference_issues',[]) if p.exists() else []:
                    match=next((r for r in positions if r['case_id']==key and r['requirement_id']==issue['requirement_id']),None)
                    if match and match not in missing:selected[match['id']]=match
        # Every recovered primary judgment gets C blind review/adjudication,
        # including A/B agreements, rather than relying on relaxed citation text.
        technical_review=[]
        policy_path=self.root/'technical-recovery-policy-20261105.json'
        if not pilot and policy_path.exists():
            for item in read_json(policy_path)['tasks'].values():
                if item['phase']!='INITIAL':continue
                for row in positions:
                    if row['case_id']==item['case_id'] and row['requirement_id'] in item['requirement_ids'] and row not in missing:
                        selected[row['id']]=row;technical_review.append(row)
        groups=defaultdict(set)
        for row in selected.values():groups[row['case_id']].add(row['requirement_id'])
        selection={'disagreements':disputed,'agreement_sample':audit,'full_pilot_review':pilots,'missing_initial':missing,'selected':list(selected.values()),'seed':297154423}
        if not pilot and policy_path.exists():selection['technical_recovery_full_review']=technical_review
        selection_name='pilot-review-selection' if pilot else 'review-selection'
        if missing:selection_name+='-partial-'+digest(selection)[:12]
        locked(self.root/(selection_name+'.json'),selection)
        self.review_selection=selection
        for key in ordered(list(groups),'COVERAGE_REVIEW_CASE_ORDER'):
            case=self.plan['cases'][key];chosen=groups[key]
            blind=self.evaluate(case,'C_BLIND','C',chosen)
            a,b=first[key];usable=chosen & blind.keys()
            opinions={rid:{'opinion_1':a[rid] if int(digest(key)[:2],16)%2 else b[rid],
                           'opinion_2':b[rid] if int(digest(key)[:2],16)%2 else a[rid],
                           'own_locked_blind_judgment':blind[rid]} for rid in usable}
            self.evaluate(case,'C_FINAL','C',usable,opinions)
            self.summarize(keys,groups,partial=True)
        return groups
    def stability(self,keys):
        selected=sample(self.positions(keys),'COVERAGE_STABILITY')
        locked(self.root/'stability-selection.json',selected)
        groups=defaultdict(set)
        for row in selected:groups[row['case_id']].add(row['requirement_id'])
        self.initial(set(groups),'STABILITY',groups)
        self.targeted_repair(set(groups),[('STABILITY','A'),('STABILITY','B')])
        result={}
        for role in ('A','B'):
            matched=0;complete=0
            for row in selected:
                key=row['case_id'];rid=row['requirement_id'];a=self.read_view('INITIAL',role,key);b=self.read_view('STABILITY',role,key)
                if rid in a and rid in b:complete+=1;matched+=a[rid]['label']==b[rid]['label']
            result[role]={'sampled':len(selected),'completed':complete,'identical_labels':matched,'agreement':matched/complete if complete else None}
        write_json(self.root/'stability.json',{'results':result,'selection_seed':297154423,'replaces_primary_judgments':False})
        return all(x['completed']==x['sampled'] for x in result.values())
    def summarize(self,keys,groups,partial=False):
        rows=[];complete=0
        for key in sorted(keys):
            case=self.plan['cases'][key];ref=self.plan['references'][case['target_id']]
            a=self.read_view('INITIAL','A',key);b=self.read_view('INITIAL','B',key);c=self.read_view('C_FINAL','C',key)
            final=reconcile(ref['requirements'],a,b,c,groups.get(key,set()))
            scores={name:aggregate(ref,v) for name,v in [('A_FIRST',a),('B_FIRST',b),('RECONCILED',final)]}
            if all(v['status']!='INCOMPLETE' for v in scores.values()):complete+=1
            rows.append({'case_id':key,'target_id':case['target_id'],'project_id':case['project_id'],'method':case['method'],
                         'artifact_status':'TECHNICAL_INPUT_ERROR' if case['input_error'] else 'REFUSED' if case['artifact']['explicit_refusal'] else 'VALID_EMPTY' if case['artifact']['no_content'] else 'PRESENT',
                         'artifact_sha256':case['binding']['sha256'],'reference_sha256':ref['reference_sha256'],'scores':scores,'reconciled_judgments':final})
        totals={}
        for method in self.plan['methods']:
            selected=[r for r in rows if r['method']==method];views={}
            for view in ('A_FIRST','B_FIRST','RECONCILED'):
                projects=defaultdict(list)
                for row in selected:projects[row['project_id']].append(row['scores'][view])
                project_means={p:sum(s['RC_full'] for s in ss if s['RC_full'] is not None)/len(ss) for p,ss in projects.items()}
                n=sum(r['scores'][view]['N'] for r in selected);f=sum(r['scores'][view]['counts']['FULL'] for r in selected)
                views[view]={'per_document_macro':project_means,'equal_document_macro':sum(project_means.values())/len(project_means) if project_means else None,
                             'micro':f/n if n else None,'N':n,'FULL':f,'counts':dict(sum((Counter(r['scores'][view]['counts']) for r in selected),Counter()))}
            totals[method]=views
        result={'status':'PARTIAL' if partial or len(keys)!=378 or complete!=378 else 'COMPLETE','completed_cases':complete,'expected_cases':len(keys),
                'rows':rows,'method_aggregates':totals,'reference_unresolved_kept_in_denominator':True,'human_validation':'NOT_RUN','official_gold':False}
        write_json(self.root/('pilot-results.json' if len(keys)==14 else 'results.json'),result)
        self.progress.event('SCORES_UPDATED',completed_cases=complete,scope='PILOT' if len(keys)==14 else 'FULL')
        return result
    def run(self,pilot_only=False):
        self.calibration()
        pilot={k for k,v in self.plan['cases'].items() if v['target_id'] in self.plan['pilot_targets']}
        require(len(pilot)==14,'14 pilot artifacts required')
        self.initial(pilot)
        self.targeted_repair(pilot,[('INITIAL','A'),('INITIAL','B')])
        pilot_groups=self.review(pilot,pilot=True)
        if self.register_repairs(pilot,[('C_BLIND','C'),('C_FINAL','C')]):pilot_groups=self.review(pilot,pilot=True)
        result=self.summarize(pilot,pilot_groups)
        require(result['completed_cases']==14,'pilot scoring incomplete; inspect preserved failures')
        write_json(self.root/'pilot-check.json',{'status':'PASS','cases':14,'calibration':'PASS','full_C_review':True,'does_not_certify_unbiased_accuracy':True})
        if pilot_only:return result
        keys=set(self.plan['cases']);self.progress.event('FULL_INITIAL_STARTED',phase='INITIAL',scope='FULL')
        self.initial(keys-pilot)
        self.targeted_repair(keys,[('INITIAL','A'),('INITIAL','B')])
        groups=self.review(keys)
        if self.register_repairs(keys,[('C_BLIND','C'),('C_FINAL','C')]):groups=self.review(keys)
        stable=self.stability(keys)
        result=self.summarize(keys,groups)
        result['stability_complete']=stable
        if not stable:result['status']='INCOMPLETE_STABILITY'
        write_json(self.root/'results.json',result)
        selection=self.review_selection;positions=self.positions(keys)
        agreements=0;both=0
        for row in positions:
            a=self.read_view('INITIAL','A',row['case_id']);b=self.read_view('INITIAL','B',row['case_id']);rid=row['requirement_id']
            if rid in a and rid in b:both+=1;agreements+=a[rid]['label']==b[rid]['label']
        write_json(self.root/'reliability.json',{'eligible_agent_positions':len(positions),'both_initial_completed':both,'initial_agreements':agreements,
                   'initial_agreement_rate':agreements/both if both else None,'disagreements':len(selection['disagreements']),
                   'stratified_agreement_audit':len(selection['agreement_sample']),'reviewed_union_including_full_pilot':len(selection['selected']),
                   'agreement_is_not_accuracy':True})
        issues=[]
        for p in (self.root/'views').glob('*/*/*.json'):
            v=read_json(p)
            if v.get('reference_issues'):issues.append({'case_id':v['case_id'],'phase':v['phase'],'role':v['role'],'issues':v['reference_issues']})
        write_json(self.root/'reference-issues.json',issues)
        # Shared raw inputs and references are read-only throughout the run.
        protected=read_json(self.root/'protected-inputs.json')
        require(all(file_hash(Path(p))==h for p,h in protected.items()),'protected input changed during scoring')
        call_paths=list((self.root/'tasks').glob('*/call-*.json'))
        calls=[read_json(p) for p in call_paths];unpriced=[]
        with self.budget.connect() as db:
            for path,call in zip(call_paths,calls):
                row=db.execute('SELECT state,actual FROM reservations WHERE id=?',(call['request_id'],)).fetchone()
                if call.get('cost_cny') is None:
                    attempt=int(path.stem.split('-')[-1]);receipt=self.root/'task_progress'/digest('coverage/'+path.parent.name)/f'request-{attempt}.json'
                    require(row and row[0]=='UNPRICED' and row[1] is None and account_recovery_authorized(self.root,path.parent,receipt,attempt,read_json(receipt)['result']),
                            'unacknowledged unknown run charge')
                    unpriced.append(call['request_id']);continue
                require(call.get('cost_cny') is not None and row and row[0]=='SETTLED' and row[1]==units(call['cost_cny']),'run charge missing or inconsistent with common ledger')
        write_json(self.root/'verification.json',{'protected_files':len(protected),'changed':0,'research_cases':378,'requirements_per_method':1039,
                   'calibration':'PASS','stability_complete':stable,'status':result['status'],'recorded_calls':len(calls),
                   'estimated_cny':round(sum(c['cost_cny'] for c in calls if c.get('cost_cny') is not None),6),'unpriced_run_calls':len(unpriced),
                   'unpriced_request_ids':unpriced,'unpriced_amount_cny':None if unpriced else 0,'common_ledger_amounts_match':True,
                   'artifact_status_counts':dict(Counter(r['artifact_status'] for r in result['rows']))})
        return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run-dir',default=str(DEFAULT))
    mode=p.add_mutually_exclusive_group(required=True);mode.add_argument('--prepare-only',action='store_true');mode.add_argument('--authorize-live',action='store_true');mode.add_argument('--status',action='store_true')
    p.add_argument('--pilot-only',action='store_true');args=p.parse_args();root=Path(args.run_dir).resolve()
    require(root.is_relative_to(REPO/'outputs/agent_evaluation') and root!=REPO/'outputs/agent_evaluation','L3 independent run directory required')
    if args.status:print(canonical(read_json(root/'progress.json')));return
    if args.authorize_live:
        names=['AUCMR_EVAL_ZHIPU_API_KEY','AUCMR_EVAL_ARK_API_KEY','AUCMR_JUNXI_DASHSCOPE_API_KEY'];present={n:bool(os.environ.get(n)) for n in names};print(canonical(present),flush=True)
        require(all(present.values()),'missing required key; no request sent')
    root.mkdir(parents=True,exist_ok=True)
    with (root/'run.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);config,plan,contexts=prepare(root)
        if args.prepare_only:print(canonical({'status':'PREPARED','cases':len(plan['cases']),'input_errors':plan['input_failures']}));return
        ledger=REPO/config['campaign_ledger'];require(ledger.exists(),'existing campaign ledger required')
        budget=Budget(ledger,provider_managed=True);live=copy.deepcopy(config);live['budgets']['live_authorized']=True
        transport=AcceptanceTransport(live,root,budget)
        locked(root/'targeted-repair-policy.json',{'scope':'Only recorded technical failures with saved priced responses','maximum_extra_format_repairs_per_task':1,'maximum_requests_per_task':config['max_requests_per_task'],'transport_retries':0,'semantic_rule_changes':False,'preserve_all_receipts':True})
        implementation={p.name:file_hash(p) for p in sorted(Path(__file__).parent.glob('*.py'))}
        locked(root/'execution-revisions'/(digest(implementation)+'.json'),{'files':implementation,'prompt_revision':PROMPT_REVISION})
        with Progress(root) as progress:
            previous_handler=signal.signal(signal.SIGINT,lambda *_:progress.stop.set())
            progress.event('START',status='RUNNING')
            try:
                result=Experiment(root,config,plan,contexts,transport,budget,progress).run(args.pilot_only)
                progress.event('FINISHED',status='PILOT_COMPLETE' if args.pilot_only else result['status'])
            except BaseException as exc:
                progress.stop.set();progress.event('STOPPED',status='STOPPED',error_type=type(exc).__name__)
                raise
            finally:signal.signal(signal.SIGINT,previous_handler)

if __name__=='__main__':main()
