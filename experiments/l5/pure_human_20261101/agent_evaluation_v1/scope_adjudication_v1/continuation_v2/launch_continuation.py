"""Detached continuation, with final pointer published at the original evaluation root."""
from pathlib import Path
import sys,subprocess,fcntl
from resume import HERE,BASE,execute,run,read,write,sha,now
if '--start' in sys.argv:
 lock=(HERE/'run.lock').open('a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise SystemExit('Already running')
 fcntl.flock(lock,fcntl.LOCK_UN);lock.close()
 write(HERE/'launcher_freeze.json',{'at':now(),'file':'launch_continuation.py','sha256':sha(Path(__file__))})
 log=HERE/'console-continuation.log'
 with log.open('ab') as out:
  p=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve())],cwd=HERE,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True)
 write(HERE/'process.json',{'pid':p.pid,'at':now(),'log':str(log)})
 write(BASE/'CURRENT_RUN.json',{'directory':str(HERE),'progress_command':f'python -B {HERE}/progress.py','state':str(HERE/'state.json')})
 print('Started repaired continuation',p.pid)
else:
 assert sha(Path(__file__))==read(HERE/'launcher_freeze.json')['sha256']
 execute()
 if read(HERE/'state.json')['status']=='COMPLETED':
  # Existing summarizer writes beside ROOT; publish its pointer one level above too.
  pointer=read(BASE/'CURRENT_RESULT.json')
  write(BASE.parent/'CURRENT_RESULT.json',pointer)
