"""Small cloud client for macOS/Linux terminals; no pi or model process required."""
from __future__ import annotations
import argparse
import curses
import http.cookiejar
import json
import os
from pathlib import Path
import shlex
import subprocess
import uuid
from urllib.parse import urlsplit, urlencode
from urllib.request import Request, build_opener, HTTPCookieProcessor
from urllib.error import HTTPError, URLError
from .vault import atomic_write

class Client:
    def __init__(self, origin, folder):
        parsed=urlsplit(origin)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.query or parsed.fragment or parsed.path not in ('','/'):
            raise ValueError('Use an HTTPS server origin')
        self.origin=origin.rstrip('/'); self.folder=Path(folder)
        self.folder.mkdir(parents=True,exist_ok=True); self.folder.chmod(0o700)
        self.jar=http.cookiejar.LWPCookieJar(str(self.folder/'cookies'))
        if (self.folder/'cookies').exists(): self.jar.load(ignore_discard=True)
        self.opener=build_opener(HTTPCookieProcessor(self.jar)); self.csrf=''
        self.pending=self.folder/'pending.json'

    def request(self,path,data=None):
        headers={'Origin':self.origin,'Content-Type':'application/json','X-OneAI-CSRF':self.csrf}
        request=Request(self.origin+'/api'+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        try:
            with self.opener.open(request,timeout=20) as response: value=json.load(response)
        except HTTPError as error:
            try: detail=json.load(error).get('detail',str(error))
            except ValueError: detail=str(error)
            raise ValueError(f'{error.code}: {detail}') from error
        except (URLError,TimeoutError) as error: raise ConnectionError('Cloud unavailable. Unsaved drafts remain on this device.') from error
        self.jar.save(ignore_discard=True); Path(self.jar.filename).chmod(0o600)
        return value

    def connect(self,code=None):
        result=self.request('/login',{'code':code,'name':'Linux / Mac terminal'}) if code else self.request('/session')
        self.csrf=result['csrf']

    def command(self,value=None):
        if value is not None:
            if self.pending.exists(): raise ValueError('An earlier request needs retry first (x).')
            atomic_write(self.pending,json.dumps(value)); self.pending.chmod(0o600)
        elif not self.pending.exists(): raise ValueError('No pending request')
        value=json.loads(self.pending.read_text())
        try: result=self.request('/commands',value)
        except ValueError:
            # A definitive rejection cannot later become an accepted write.
            self.pending.unlink(); raise
        self.pending.unlink()
        return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',default=os.environ.get('ONEAI_APP_ORIGIN','https://47.82.117.21'))
    parser.add_argument('--state',default='~/.oneai/terminal')
    args=parser.parse_args(argv)
    client=Client(args.server,Path(args.state).expanduser())
    try: client.connect()
    except ValueError:
        import getpass
        client.connect(getpass.getpass('One-time pairing code: '))
    curses.wrapper(run,client)


def run(screen,client):
    curses.curs_set(0); screen.keypad(True)
    rows=[]; selected=0; offset=0; query=''; detail=None; scroll=0; message=''; total=0
    def write(y,value,attr=0):
        height,width=screen.getmaxyx()
        if 0<=y<height:
            try: screen.addnstr(y,1,value,max(0,width-3),attr)
            except curses.error: pass
    def prompt(label):
        height,width=screen.getmaxyx(); screen.move(height-2,0); screen.clrtoeol(); write(height-2,label)
        curses.echo(); curses.curs_set(1)
        try: return screen.getstr(height-1,1,max(1,width-3)).decode('utf-8')
        finally: curses.noecho(); curses.curs_set(0)
    def refresh():
        nonlocal rows,total,selected
        result=client.request('/tasks?'+urlencode({'offset':offset,'q':query}))
        rows=result['items']; total=result['total']; selected=min(selected,max(0,len(rows)-1))
    def edit(path,initial):
        if not path.exists(): atomic_write(path,initial)
        path.chmod(0o600)
        curses.def_prog_mode(); curses.endwin()
        try: subprocess.run(shlex.split(os.environ.get('EDITOR','vi'))+[str(path)],check=True)
        finally: curses.reset_prog_mode(); screen.refresh()
        return path.read_text()
    refresh()
    while True:
        screen.erase(); h,w=screen.getmaxyx()
        write(0,'oneAI  /  '+client.origin,curses.A_BOLD)
        write(1,'j/k move  Enter open  b back  n new  e edit  a approve  d archive  / search')
        write(2,'r refresh  x retry pending  [/] page  q quit | No email is sent by this client.')
        if detail:
            import textwrap
            content=[detail['title'],f"{detail['status']} | revision {detail['revision']}",'']
            for line in detail['result'].splitlines(): content.extend(textwrap.wrap(line,max(10,w-5)) or [''])
            for y,line in enumerate(content[scroll:scroll+max(0,h-6)],4): write(y,line)
        else:
            start=max(0,selected-max(0,h-8))
            for y,i in enumerate(range(start,min(len(rows),start+max(0,h-6))),4):
                row=rows[i]; write(y,f"{row['status']:12} {row['title']}",curses.A_REVERSE if i==selected else 0)
        write(h-2,message)
        write(h-1,f'{offset+1 if total else 0}-{min(offset+len(rows),total)} / {total}'+(' | Pending write: press x' if client.pending.exists() else ''))
        key=screen.getch()
        try:
            if key==ord('q'): break
            elif key in (ord('j'),curses.KEY_DOWN):
                if detail: scroll+=1
                else: selected=min(len(rows)-1,selected+1)
            elif key in (ord('k'),curses.KEY_UP):
                if detail: scroll=max(0,scroll-1)
                else: selected=max(0,selected-1)
            elif key in (10,13) and rows and not detail: detail=client.request('/tasks/'+rows[selected]['id']); scroll=0
            elif key==ord('b'): detail=None; scroll=0
            elif key==ord('r'):
                refresh()
                if detail: detail=client.request('/tasks/'+detail['id'])
                message='Refreshed'
            elif key==ord('/'):
                query=prompt('Search: '); offset=0; detail=None; refresh()
            elif key in (ord('['),ord(']')):
                offset=max(0,offset-50) if key==ord('[') else (offset+50 if offset+50<total else offset)
                detail=None; refresh()
            elif key==ord('n'):
                title=prompt('New task title (empty cancels): ')
                if title:
                    draft=client.folder/'new.md'; body=edit(draft,'')
                    if prompt('Submit task? Type yes: ')=='yes':
                        result=client.command({'id':str(uuid.uuid4()),'action':'create','title':title,'body':body})
                        draft.unlink(missing_ok=True); detail=client.request('/tasks/'+result['task_id']); refresh(); message='Saved to cloud'
            elif key==ord('e') and detail and detail['status'] in ('needs_review','reviewed'):
                draft=client.folder/(detail['id']+f"-r{detail['revision']}.md")
                body=edit(draft,detail['result'])
                if prompt('Save new revision? Type yes: ')=='yes':
                    client.command({'id':str(uuid.uuid4()),'action':'revise','task_id':detail['id'],'revision':detail['revision'],'body':body})
                    draft.unlink(missing_ok=True); detail=client.request('/tasks/'+detail['id']); message='Saved; review the new revision'
            elif key in (ord('a'),ord('d')) and detail:
                action='approve' if key==ord('a') else 'complete'
                if prompt(f'{action} revision {detail["revision"]}? Type yes: ')=='yes':
                    client.command({'id':str(uuid.uuid4()),'action':action,'task_id':detail['id'],'revision':detail['revision']})
                    detail=client.request('/tasks/'+detail['id']); refresh(); message='Saved'
            elif key==ord('x'):
                client.command(); refresh()
                if detail: detail=client.request('/tasks/'+detail['id'])
                message='Request confirmed'
        except (ValueError,ConnectionError,OSError,subprocess.SubprocessError) as error: message=str(error)

if __name__=='__main__': main()
