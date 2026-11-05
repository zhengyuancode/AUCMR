"""Detach one frozen runner; does not poll long-running work."""
from pathlib import Path
import os,sys,subprocess,fcntl,datetime
from transport import write,now
from runner import check_freeze
R=Path(__file__).resolve().parent
if __name__=='__main__':
 check_freeze()
 lock=(R/'run.lock').open('a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise SystemExit('Runner already active. Use progress.py; do not duplicate.')
 fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
 stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ');log=R/f'console-{stamp}.log'
 with log.open('ab') as out:
  proc=subprocess.Popen([sys.executable,'-B',str(R/'runner.py')],cwd=R,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
 write(R/'process.json',{'pid':proc.pid,'started_at':now(),'console_log':log.name,'detached':True})
 print('Started frozen Agent evaluation; PID',proc.pid,'log',log.name)
