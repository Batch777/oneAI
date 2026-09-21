"""Portable text session client. Runtime state lives on the relay, never in the TUI."""
import argparse
import curses
import getpass
import json
import os
from pathlib import Path
import textwrap
import time
import uuid
from ..terminal import Client
from ..vault import atomic_write


class SessionClient(Client):
    def __init__(self, *args):
        super().__init__(*args)
        self.pending = self.folder/'session-pending.json'

    def command(self, value=None):
        if value is not None:
            if self.pending.exists():
                raise ValueError('Retry the pending session command first (x).')
            atomic_write(self.pending, json.dumps(value))
            self.pending.chmod(0o600)
        if not self.pending.exists():
            raise ValueError('No pending session command')
        value = json.loads(self.pending.read_text())
        try:
            result = self.request('/agent/commands', value)
        except ValueError as error:
            if str(error).startswith(('400:', '401:', '403:', '404:', '409:')):
                self.pending.unlink()
            raise
        self.pending.unlink()
        return result


def run(screen, client):
    curses.curs_set(0)
    screen.keypad(True)
    screen.timeout(500)
    rows, hosts, events = [], [], []
    selected, sid, cursor, message, refreshed = 0, None, 0, '', 0

    def write(y, value, attr=0):
        h, w = screen.getmaxyx()
        if 0 <= y < h:
            try:
                screen.addnstr(y, 1, value, max(1,w-3), attr)
            except curses.error:
                pass

    def prompt(label):
        h, w = screen.getmaxyx()
        write(h-2, label)
        screen.move(h-1,0);screen.clrtoeol()
        screen.timeout(-1);curses.echo();curses.curs_set(1)
        try:
            return screen.getstr(h-1,1,max(1,w-3)).decode('utf-8')
        finally:
            curses.noecho();curses.curs_set(0);screen.timeout(500)

    while True:
        try:
            if time.monotonic()-refreshed > 2:
                rows = client.request('/agent/sessions')['items']
                hosts = client.request('/agent/hosts')['items']
                if sid:
                    result = client.request(f'/agent/sessions/{sid}/events?after={cursor}')
                    events.extend(result['items']);cursor=result['cursor']
                refreshed=time.monotonic()
        except (ValueError, ConnectionError, OSError) as error:
            message=str(error);refreshed=time.monotonic()
        screen.erase();h,w=screen.getmaxyx()
        write(0,'oneAI / sessions',curses.A_BOLD)
        write(1,'j/k select  Enter open  b list  n new  m message  s stop  c close/resume  x retry  q quit')
        current=next((r for r in rows if r['id']==sid),None)
        if current:
            host=next((host for host in hosts if host['id']==current['host_id']),{})
            write(2,current['title']+' | '+current['provider']+' | '+(current['state'] if host.get('online') else 'host offline'))
            content=''
            for event in events:
                data=event['payload']
                if event['kind']=='text':content+=data.get('text','')
                elif event['kind']=='command' and data['action']=='prompt':content+='\n\nYou: '+data['text']+'\n\n'
                elif event['kind']=='message':content+='\n'+data.get('role','')+': '+data.get('text','')+'\n'
                elif event['kind']=='error':content+='\nError: '+data['message']+'\n'
                elif event['kind']=='tool':content+='\nTool: '+str(data.get('type',''))+'\n'
            lines=[]
            for line in content.splitlines():lines.extend(textwrap.wrap(line,max(10,w-4)) or [''])
            for y,line in enumerate(lines[-max(1,h-6):],3):write(y,line)
        else:
            selected=min(selected,max(0,len(rows)-1))
            start=max(0,selected-max(0,h-8))
            for y,i in enumerate(range(start,min(len(rows),start+max(0,h-6))),3):
                row=rows[i];write(y,row['provider']+' | '+row['title']+' | '+row['state'],curses.A_REVERSE if i==selected else 0)
        write(h-2,message)
        key=screen.getch()
        try:
            if key==ord('q'):break
            if key in (ord('j'),curses.KEY_DOWN):selected=min(len(rows)-1,selected+1)
            elif key in (ord('k'),curses.KEY_UP):selected=max(0,selected-1)
            elif key in (10,13) and rows and not sid:sid=rows[selected]['id'];events=[];cursor=0;refreshed=0
            elif key==ord('b'):sid=None
            elif key==ord('n'):
                names=' '.join(f'{i+1}:{host["name"]}' for i,host in enumerate(hosts))
                value=prompt('Host '+names+' (empty cancels): ')
                if not value:continue
                host=hosts[int(value)-1]
                providers=host['capabilities']['providers'];workspaces=host['capabilities']['workspaces']
                provider=prompt('Provider '+','.join(providers)+': ')
                workspace=prompt('Workspace '+','.join(workspaces)+': ')
                title=prompt('Session title: ')
                if provider not in providers or workspace not in workspaces:raise ValueError('Choose an advertised provider/workspace')
                result=client.command({'id':str(uuid.uuid4()),'action':'create','host_id':host['id'],'provider':provider,'workspace':workspace,'title':title})
                sid=result['session_id'];events=[];cursor=0;refreshed=0
            elif key==ord('m') and current:
                if current['state']!='idle':raise ValueError('Session is not idle; refresh or stop first')
                value=prompt('Message (empty cancels): ')
                if value:
                    client.command({'id':str(uuid.uuid4()),'action':'prompt','session_id':sid,'revision':current['revision'],'text':value});refreshed=0
            elif key in (ord('s'),ord('c')) and current:
                action='interrupt' if key==ord('s') else ('resume' if current['state']=='closed' else 'close')
                client.command({'id':str(uuid.uuid4()),'action':action,'session_id':sid,'revision':current['revision']});refreshed=0
            elif key==ord('x'):client.command();refreshed=0
        except (ValueError, ConnectionError, OSError, IndexError) as error:message=str(error)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',default=os.environ.get('ONEAI_APP_ORIGIN','https://47.82.117.21'))
    parser.add_argument('--state',default='~/.oneai/terminal')
    args=parser.parse_args(argv)
    client=SessionClient(args.server,Path(args.state).expanduser())
    try:
        try:client.connect()
        except ValueError:client.connect(getpass.getpass('One-time pairing code: '))
        curses.wrapper(run,client)
    except (ValueError,ConnectionError,OSError) as error:
        parser.exit(1,str(error)+'\n')


if __name__=='__main__':main()
