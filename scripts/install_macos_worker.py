"""Install the current checkout's worker as a user LaunchAgent (macOS only)."""
from pathlib import Path
import os
import plistlib
import subprocess
import sys

if sys.platform != 'darwin': raise SystemExit('macOS only')
root = Path(__file__).resolve().parents[1]
python = root/'.venv/bin/python'
if not python.exists(): raise SystemExit('Create the project .venv first')
logs = root/'state/worker-logs'; logs.mkdir(parents=True,exist_ok=True)
path = Path.home()/'Library/LaunchAgents/com.oneai.worker.plist'
path.parent.mkdir(parents=True,exist_ok=True)
label = 'com.oneai.worker'
config = {'Label':label,'ProgramArguments':[str(python),'-m','oneai.worker','--interval','30'],
          'WorkingDirectory':str(root),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':30,
          'StandardOutPath':str(logs/'worker.log'),'StandardErrorPath':str(logs/'worker.err'),
          'EnvironmentVariables':{'PYTHONUNBUFFERED':'1'}}
if path.exists():
    old = plistlib.loads(path.read_bytes())
    if old.get('Label') != label or old.get('WorkingDirectory') != str(root):
        raise SystemExit('Existing worker points to another checkout; refusing replacement')
    subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{label}'],check=False,capture_output=True)
path.write_bytes(plistlib.dumps(config)); path.chmod(0o600)
subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(path)],check=True)
print(f'Installed {path}')
