"""Start one detached frozen run; no polling and no credential output."""
import subprocess,sys,fcntl,datetime
from run import ROOT,verify,write,now
verify()
lock=(ROOT/'run.lock').open('a')
try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:raise SystemExit('Already running; use progress.py')
fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ');log=ROOT/f'console-{stamp}.log'
with log.open('ab') as out:
 p=subprocess.Popen([sys.executable,'-B',str(ROOT/'run.py')],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
write(ROOT/'process.json',{'pid':p.pid,'at':now(),'log':log.name,'detached':True})
print('Started',p.pid,'log',log.name)
