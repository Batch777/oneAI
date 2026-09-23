"""Linux owner-trusted runner. Immutable packages + bounded independent processes.

This is process fault isolation, not a hostile-code OS sandbox. Installation
requires an explicit local --trust-owner-code choice; browser cannot upload code.
"""
import argparse
import fcntl
import json
import os
import re
from pathlib import Path
import selectors
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit
from .package import install,inspect,validate_input,encode


def execute(package,call,workspace):
    m,digest=inspect(package)
    if digest!=call['digest'] or m!=call['manifest']:raise ValueError('plugin_digest_mismatch')
    validate_input(m['input_schema'],call['input'])
    request={'jsonrpc':'2.0','id':call['id'],'method':'plugin.invoke','params':{'input':call['input'],'context':{'workspace':str(workspace),'invocation_id':call['id']}}}
    # Minimal environment prevents accidental model/relay credential inheritance.
    with tempfile.TemporaryDirectory(prefix='oneai-plugin-') as temp:
        env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':temp,'TMPDIR':temp,'LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0'}
        supervisor=Path(__file__).parents[1]/'sessions/supervise.py'
        proc=subprocess.Popen([sys.executable,str(supervisor),str(os.getpid()),sys.executable,'-I','-B',str(package/m['entrypoint'])],cwd=temp,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        output=bytearray();deadline=time.monotonic()+m['timeout_seconds']
        try:
            proc.stdin.write((encode(request)+'\n').encode());proc.stdin.close()
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout,selectors.EVENT_READ)
                while selector.get_map():
                    left=deadline-time.monotonic()
                    if left<=0:raise TimeoutError('plugin_timeout')
                    for key,_ in selector.select(min(left,.2)):
                        chunk=os.read(key.fileobj.fileno(),65536)
                        if not chunk:selector.unregister(key.fileobj);continue
                        output.extend(chunk)
                        if len(output)>64000:raise ValueError('plugin_output_too_large')
            proc.wait(timeout=max(.1,deadline-time.monotonic()))
            if proc.returncode:raise ValueError('plugin_process_failed')
            response=json.loads(output)
            if not isinstance(response,dict) or response.get('jsonrpc')!='2.0' or response.get('id')!=call['id'] or not isinstance(response.get('result'),dict) or 'error' in response:raise ValueError('invalid_plugin_response')
            encode(response['result']);return response['result']
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
            proc.stdout.close()


class Runner:
    def __init__(self,config,root):
        self.config=config;self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.lock=(self.root/'runner.lock').open('a');fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.db=sqlite3.connect(self.root/'runner.sqlite')
        self.db.executescript('CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,payload TEXT);')
        # Null payload means crash after local receipt; never repeat uncertain work.
        for (rid,) in self.db.execute('SELECT id FROM runs WHERE payload IS NULL').fetchall():
            self.db.execute('UPDATE runs SET payload=? WHERE id=?',(encode({'id':rid,'status':'unknown','result':{'error':'runner_restarted_after_receipt'}}),rid))
        self.db.commit();self.last_inventory=0
    def request(self,route,data):
        req=urllib.request.Request(self.config['server'].rstrip('/')+'/api/agent-host/plugins/'+route,data=encode(data).encode(),headers={'Authorization':'Bearer '+self.config['token'],'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=20) as r:return json.load(r)
    def inventory(self):
        index=self.root/'installed.json';values=json.loads(index.read_text()) if index.exists() else {};items=[]
        for pid,digest in values.items():
            try:
                if not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest):raise ValueError('invalid_digest')
                m,actual=inspect(self.root/'packages'/digest)
                if m['id']!=pid or digest!=actual:raise ValueError('installed_package_modified')
                items.append({'manifest':m,'digest':digest})
            except (ValueError,OSError):print('plugin_package_unavailable',flush=True)
        return items
    def flush(self):
        for rid,payload in self.db.execute("SELECT id,payload FROM runs WHERE payload IS NOT NULL AND payload!='ack'").fetchall():
            self.request('result',json.loads(payload));self.db.execute("UPDATE runs SET payload='ack' WHERE id=?",(rid,));self.db.commit()
    def step(self):
        self.flush()
        if time.monotonic()-self.last_inventory>20:
            self.request('inventory',{'items':self.inventory()});self.last_inventory=time.monotonic()
        call=self.request('poll',{})['invocation']
        if not call:return
        if self.db.execute('SELECT 1 FROM runs WHERE id=?',(call['id'],)).fetchone():return
        self.db.execute('INSERT INTO runs VALUES(?,NULL)',(call['id'],));self.db.commit()
        try:
            if call['workspace'] not in self.config['workspaces']:raise ValueError('workspace_not_allowed')
            workspace=Path(self.config['workspaces'][call['workspace']]).resolve(strict=True)
            if not workspace.is_dir():raise ValueError('workspace_not_directory')
            # Claim pins a digest, including when the registry updates mid-flight.
            result=execute(self.root/'packages'/call['digest'],call,workspace);status='completed'
        except (TimeoutError,subprocess.TimeoutExpired):status='unknown';result={'error':'plugin_timeout_result_unknown'}
        except Exception as e:
            # Error types only: no paths, environment values or traceback leakage.
            status='failed';result={'error':'plugin_execution_failed','type':type(e).__name__}
        self.db.execute('UPDATE runs SET payload=? WHERE id=?',(encode({'id':call['id'],'status':status,'result':result}),call['id']));self.db.commit();self.flush()
    def close(self):self.db.close();self.lock.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    i=sub.add_parser('install');i.add_argument('source',type=Path);i.add_argument('--root',type=Path,required=True);i.add_argument('--trust-owner-code',action='store_true')
    r=sub.add_parser('serve');r.add_argument('--root',type=Path,required=True);r.add_argument('--config',type=Path,required=True)
    a=p.parse_args()
    if a.command=='install':
        if not a.trust_owner_code:p.error('Only reviewed owner-trusted code is supported; pass --trust-owner-code')
        lockroot=a.root;lockroot.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (lockroot/'install.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX);m,d=install(a.source,a.root);print(m['id'],m['version'],d)
        return
    if a.config.stat().st_mode & 0o077:p.error('chmod 600 host configuration first')
    config=json.loads(a.config.read_text());u=urlsplit(config['server'])
    if u.scheme!='https' and not (u.scheme=='http' and u.hostname in ('127.0.0.1','localhost')):p.error('HTTPS required')
    runner=Runner(config,a.root)
    try:
        while True:
            try:runner.step()
            except Exception as e:print('plugin_runner_unavailable',type(e).__name__,flush=True)
            time.sleep(2)
    except KeyboardInterrupt:pass
    finally:runner.close()

if __name__=='__main__':main()
