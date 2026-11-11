#!/usr/bin/env python3
"""Start only the original license server VM; computation stays on the host."""
from pathlib import Path
import datetime,fcntl,json,re,subprocess,time,sys,os
BASE=Path(__file__).resolve().parents[1]
CONTROL=BASE/'migration/source-vm/control.py'
LOG=BASE/'native/logs/license-service.log'
SOCKET_DIR=Path('/tmp/qingruan-migration-1000')
SOCKET_DIR.mkdir(mode=0o700,exist_ok=True)
if SOCKET_DIR.stat().st_uid!=os.getuid():raise SystemExit('许可控制目录所有者不正确。')
SOCKET_DIR.chmod(0o700)

def check():
 try:
  p=subprocess.run([str(BASE/'run-native.sh'),'--no-license','lmutil','lmstat','-a','-c','27000@127.0.0.1'],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=7)
  text=p.stdout
  return 'license server UP' in text and bool(re.search(r'snpslmd:\s+UP',text)),text
 except subprocess.TimeoutExpired:
  return False,'lmstat timed out'

with (SOCKET_DIR/'license.lock').open('w') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 ok,text=check()
 if ok:raise SystemExit(0)
 if os.environ.get('QINGRUAN_NATIVE')=='1':
  raise SystemExit('许可服务未启动，请在本机 Ubuntu 终端运行 /mnt/data/Ubuntu/qingruan/启动许可服务.sh，然后继续使用当前环境。')
 print('正在启动原许可服务（虚拟机仅提供许可，计算在本机运行）……',flush=True)
 subprocess.run(['python3',str(CONTROL),'start'],check=True)
 deadline=time.monotonic()+150
 while time.monotonic()<deadline:
  ok,text=check()
  if ok:
   LOG.parent.mkdir(exist_ok=True)
   with LOG.open('a') as f:f.write('\n'+datetime.datetime.now().isoformat()+' LICENSE_READY\n'+text+'\n')
   (BASE/'native/license-status.json').write_text(json.dumps({'status':'UP','server':'27000@127.0.0.1','vendor_port':27020,'transport':'loopback-only original VM server','checked_at':datetime.datetime.now().isoformat()},indent=2))
   print('原许可服务已就绪。',flush=True)
   raise SystemExit(0)
  time.sleep(2)
 LOG.parent.mkdir(exist_ok=True)
 with LOG.open('a') as f:f.write('\nLICENSE_STARTUP_FAILED\n'+text+'\n')
 print('许可服务尚未就绪，请查看 '+str(LOG),file=sys.stderr)
 raise SystemExit(1)
