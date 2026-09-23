"""A minimal standalone plugin: one request, one JSON-RPC result, then exit."""
import json
import subprocess
import sys

call=json.load(sys.stdin);p=call['params'];cwd=p['context']['workspace']
def git(*args):
    r=subprocess.run(['git','-c','core.fsmonitor=false','-C',cwd,*args],capture_output=True,text=True,timeout=5,check=True)
    return r.stdout.strip()
result={'branch':git('branch','--show-current'),'changed_files':len(git('status','--porcelain','--untracked-files=no').splitlines()),'recent_commits':git('log','-'+str(p['input']['commits']),'--format=%h %s').splitlines()}
print(json.dumps({'jsonrpc':'2.0','id':call['id'],'result':result},ensure_ascii=False))
