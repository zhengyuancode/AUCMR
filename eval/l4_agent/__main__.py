"""Explicit panel-only commands; old L4 run directories are never reused."""
import argparse
import json
from .protocol import L4Error, local_path, read_json, digest
from .runner import load, verify_implementation, run, costs, validate_record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=['check','run','status'])
    parser.add_argument('--config',default='config/l4_panel/unified93-v4/config.json')
    parser.add_argument('--run-dir',default='outputs/agent_l4_panel/pilot-v4-20261029')
    parser.add_argument('--scope',choices=['pilot','full'],default='pilot')
    parser.add_argument('--authorize-live',action='store_true')
    args = parser.parse_args()
    if args.command == 'run':
        result = run(args.config,args.run_dir,scope=args.scope,authorize_live=args.authorize_live)
    elif args.command == 'check':
        from .review import check
        result = check(args.config)
    else:
        config,catalog,allocation = load(args.config,verify_files=False)
        directory = local_path(args.run_dir)
        snapshot = read_json(directory/'snapshot.json')
        if snapshot['config'] != config:
            raise L4Error('SNAPSHOT_CONFIG_CHANGED')
        mapped = {e['evaluation_id']:e for e in allocation['evaluations']}
        records = [read_json(p) for p in sorted(directory.glob('records/*.json'))]
        for r in records:
            validate_record(r,mapped[r['evaluation_id']],snapshot)
        result = {'scope':snapshot['scope'],'planned':len(snapshot['evaluation_ids']),
                  'completed':sum(r['status']=='COMPLETE' for r in records),
                  'technical_failures':sum(r['status']=='TECHNICAL_FAILURE' for r in records),
                  'remaining':len(snapshot['evaluation_ids'])-len(records),
                  **costs(directory,local_path(config['campaign_ledger']))}
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print('STOPPED '+type(exc).__name__+': '+str(exc))
        raise SystemExit(1)
