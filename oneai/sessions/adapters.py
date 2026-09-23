"""Provider-owned processes; only the host agent writes their stdio.

Never attach a second writer to a live desktop/TUI session. Runtime IDs refer
only to sessions created by this host. Unknown requests fail closed.
"""
from __future__ import annotations
from concurrent.futures import Future
import json
import os
import sys
from pathlib import Path
import subprocess
import threading
import uuid
from .telemetry import codex_usage, pi_usage, quota
from .profiles import instructions


class JsonLines:
    def __init__(self, argv, cwd, notify):
        self.notify = notify
        self.pending = {}
        self.lock = threading.RLock()
        self.closed = False
        self.process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('supervise.py')), str(os.getpid()), *argv], cwd=cwd, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def send(self, value):
        with self.lock:
            if self.process.poll() is not None:
                raise RuntimeError('runtime_disconnected')
            self.process.stdin.write((json.dumps(value, ensure_ascii=False)+'\n').encode())
            self.process.stdin.flush()

    def request(self, method, params=None, pi=False, timeout=45, on_sent=None):
        rid = uuid.uuid4().hex
        future = Future()
        with self.lock:
            self.pending[rid] = future
        try:
            self.send({'id': rid, 'type': method, **(params or {})} if pi else
                      {'id': rid, 'method': method, 'params': params or {}})
            if on_sent:
                on_sent()
            result = future.result(timeout)
            if 'error' in result or result.get('success') is False:
                raise RuntimeError(str(result.get('error', 'runtime_request_failed'))[:500])
            return result.get('data', {}) if pi else result.get('result', {})
        finally:
            with self.lock:
                self.pending.pop(rid, None)

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(4*1024*1024+1)
                if not line:
                    break
                if len(line) > 4*1024*1024 or not line.endswith(b'\n'):
                    raise RuntimeError('runtime_event_too_large')
                value = json.loads(line)
                with self.lock:
                    future = self.pending.get(value.get('id'))
                    is_response = ('result' in value or 'error' in value or value.get('type') == 'response')
                    if future is not None and is_response:
                        if not future.done():
                            future.set_result(value)
                        continue
                self.notify(value)
        except (ValueError, RuntimeError, OSError):
            self.process.terminate()
        finally:
            with self.lock:
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(RuntimeError('runtime_disconnected'))
            if not self.closed:
                self.notify({'oneai_disconnected': True})

    def close(self):
        self.closed = True
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.reader.join(timeout=2)
        for stream in (self.process.stdin, self.process.stdout):
            stream.close()


class Codex:
    def __init__(self, binary, cwd, directory, emit, remote_id=None, policy="workspace", settings=None):
        if policy not in ("workspace", "full"): raise ValueError("invalid_runtime_policy")
        self.settings=dict(settings or {}); self.usage_dirty=True
        self.emit, self.turn = emit, None
        self.settled = threading.Event()
        self.settled.set()
        self.started = threading.Event()
        self.started.set()
        self.rpc = JsonLines([binary, 'app-server', '--stdio'], cwd, self.event)
        try:
            self.rpc.request('initialize', {'clientInfo': {'name': 'oneai_host', 'title': 'oneAI', 'version': '0.1.0'}})
            self.rpc.send({'method': 'initialized'})
            params = {'cwd': str(cwd), 'approvalPolicy': 'on-request', 'approvalsReviewer': 'user', 'sandbox': 'workspace-write'}
            if policy == 'full':
                params.update(approvalPolicy='never', sandbox='danger-full-access')
            if self.settings.get('model'):params['model']=self.settings['model']
            prompt=instructions(self.settings.get('profile','general'))
            if prompt:params['developerInstructions']=prompt
            if remote_id:
                params['threadId'] = remote_id
            result = self.rpc.request('thread/resume' if remote_id else 'thread/start', params)
            self.remote_id = result['thread']['id']
            self.emit('model',{'model':result.get('model') or self.settings.get('model'),'effort':self.settings.get('effort'),'profile':self.settings.get('profile','general'),'effective_from':'current'})
        except Exception:
            self.rpc.close()
            raise

    def event(self, value):
        if value.get('oneai_disconnected'):
            self.emit('status', {'state': 'unknown', 'message': 'Codex 进程已断开，请检查执行主机。'})
            return
        method, params = value.get('method', ''), value.get('params', {})
        if 'id' in value and 'method' in value:
            # No blanket grants or approval emulation. A future approval adapter
            # will expose exact typed requests with expiration and turn binding.
            if method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval'):
                self.rpc.send({'id': value['id'], 'result': {'decision': 'decline'}})
            else:
                self.rpc.send({'id': value['id'], 'error': {'code': -32601, 'message': 'Unsupported interactive request'}})
            self.emit('error', {'message': '本版尚未开放此交互授权，操作已拒绝。', 'request_type': method})
        elif method == 'thread/tokenUsage/updated':
            self.emit('usage',codex_usage(params.get('tokenUsage') or {}))
        elif method == 'account/rateLimits/updated':
            self.emit('quota',quota(params))
        elif method == 'model/rerouted':
            self.emit('model',{'model':params.get('toModel'),'requested_model':params.get('fromModel'),'effective_from':'rerouted','reason':params.get('reason')})
        elif method == 'turn/started':
            self.turn = params['turn']['id']
            self.emit('status', {'state': 'running'})
        elif method == 'turn/completed':
            self.turn = None
            self.usage_dirty=True
            self.settled.set()
            error = params.get('turn', {}).get('error')
            if error:
                self.emit('error', {'message': str(error)[:1000]})
            self.emit('status', {'state': 'idle'})
        elif method == 'item/agentMessage/delta':
            self.emit('text', {'text': params.get('delta', ''), 'item_id': params.get('itemId', '')})
        elif method in ('item/started', 'item/completed'):
            item = params.get('item', {})
            if item.get('type') not in ('agentMessage', 'userMessage', 'reasoning'):
                self.emit('tool', {'type': item.get('type'), 'phase': method,
                                   'text': str(item.get('aggregatedOutput') or item.get('command') or item.get('name') or '')[:8000]})
        elif method == 'error':
            self.emit('error', {'message': str(params.get('error', 'Codex error'))[:1000]})

    def prompt(self, text, on_sent=None):
        self.started.clear()
        self.settled.clear()
        try:
            params={'threadId':self.remote_id,'input':[{'type':'text','text':text}]}
            if self.settings.get('model'):params['model']=self.settings['model']
            if self.settings.get('effort'):params['effort']=self.settings['effort']
            result=self.rpc.request('turn/start',params,on_sent=on_sent)
            self.emit('model',{'model':self.settings.get('model'),'effort':self.settings.get('effort'),'profile':self.settings.get('profile','general'),'effective_from':'current'})
        finally:
            self.started.set()

    def interrupt(self):
        if not self.started.wait(45):
            raise RuntimeError('turn_start_unconfirmed')
        turn = self.turn
        if turn:
            self.rpc.request('turn/interrupt', {'threadId': self.remote_id, 'turnId': turn})
            if not self.settled.wait(45):
                raise RuntimeError('turn_interrupt_unconfirmed')
        elif not self.settled.is_set():
            raise RuntimeError('turn_state_unknown')

    def configure(self,settings):
        self.settings=dict(settings)
        self.emit('model',{'model':settings.get('model'),'effort':settings.get('effort'),'profile':settings.get('profile','general'),'effective_from':'next_turn'})

    def collect(self):
        self.emit('quota',quota(self.rpc.request('account/rateLimits/read',timeout=10)))

    def close(self):
        self.rpc.close()


class Pi:
    def __init__(self, binary, cwd, directory, emit, remote_id=None, policy="workspace", settings=None):
        if policy not in ("workspace", "full"): raise ValueError("invalid_runtime_policy")
        self.emit = emit; self.settings=dict(settings or {}); self.usage_dirty=True
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.remote_id = remote_id or str(directory/'session.jsonl')
        extra=[]
        prompt=instructions(self.settings.get('profile','general'))
        if prompt:
            prompt_file=directory/'profile.md';prompt_file.write_text(prompt);prompt_file.chmod(0o600)
            extra=['--append-system-prompt',str(prompt_file)]
        self.rpc = JsonLines([binary, '--mode', 'rpc', '--session', self.remote_id,*extra,
                              '--no-extensions', '--no-prompt-templates', '--no-skills', '--no-themes',
                              '--no-approve', '--tools', 'read,bash,edit,write,grep,find,ls' if policy == 'full' else 'read,grep,find,ls'], cwd, self.event)
        try:
            if self.settings:self.configure(self.settings)
            else:self.report_model(self.rpc.request('get_state',pi=True))
        except Exception:
            self.rpc.close()
            raise

    def event(self, value):
        kind = value.get('type')
        if value.get('oneai_disconnected'):
            self.emit('status', {'state': 'unknown', 'message': 'pi 进程已断开，请检查执行主机。'})
        elif kind == 'agent_start':
            self.emit('status', {'state': 'running'})
        elif kind == 'agent_settled':
            self.usage_dirty=True
            self.emit('status', {'state': 'idle'})
        elif kind == 'message_update':
            event = value.get('assistantMessageEvent', {})
            if event.get('type') == 'text_delta':
                self.emit('text', {'text': event.get('delta', '')})
        elif kind == 'message_end' and value.get('message', {}).get('errorMessage'):
            self.emit('error', {'message': value['message']['errorMessage'][:1000]})
        elif kind in ('tool_execution_start', 'tool_execution_end'):
            self.emit('tool', {'type': value.get('toolName'), 'phase': kind,
                               'text': str(value.get('result', ''))[:8000]})
        elif kind == 'extension_ui_request':
            self.rpc.send({'type': 'extension_ui_response', 'id': value['id'], 'cancelled': True})

    def report_model(self,state):
        model=state.get('model') or {}
        self.emit('model',{'model':model.get('provider','')+'/'+model.get('id',''),'effort':state.get('thinkingLevel'),'profile':self.settings.get('profile','general'),'effective_from':'current'})

    def configure(self,settings):
        self.settings=dict(settings)
        value=settings.get('model')
        if value:
            provider,sep,model=value.partition('/')
            if not sep:raise ValueError('pi_model_requires_provider')
            actual=self.rpc.request('set_model',{'provider':provider,'modelId':model},pi=True)
            if actual.get('provider')!=provider or actual.get('id')!=model:raise ValueError('model_selection_mismatch')
        if settings.get('effort'):self.rpc.request('set_thinking_level',{'level':settings['effort']},pi=True)
        self.report_model(self.rpc.request('get_state',pi=True))
        self.usage_dirty=True

    def collect(self):
        self.emit('usage',pi_usage(self.rpc.request('get_session_stats',pi=True,timeout=10)))

    def prompt(self, text, on_sent=None):
        self.rpc.request('prompt', {'message': text}, pi=True, timeout=3600, on_sent=on_sent)

    def interrupt(self):
        self.rpc.request('clear_queue', pi=True)
        self.rpc.request('abort', pi=True)
        self.emit('status', {'state': 'idle'})

    def close(self):
        self.rpc.close()


ADAPTERS = {'codex': Codex, 'pi': Pi}
