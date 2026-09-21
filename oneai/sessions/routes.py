"""Transport only; no provider binaries or workstation paths on the relay."""
import os
from fastapi import Depends, HTTPException, Request
from .store import Store


def mount(app, cfg, authenticated, body):
    store = Store(cfg.state_path/'sessions.sqlite')

    def enabled():
        if os.environ.get('ONEAI_ENABLE_SESSIONS') != '1':
            raise HTTPException(403, 'sessions_not_enabled')

    def host(request: Request):
        enabled()
        header = request.headers.get('authorization', '')
        if not header.startswith('Bearer '):
            raise HTTPException(401, 'host_unauthorized')
        try:
            return store.authenticate(header[7:])
        except ValueError as error:
            raise HTTPException(401, str(error))

    @app.get('/api/agent/hosts')
    def hosts(user=Depends(authenticated)):
        enabled()
        return {'items': store.hosts()}

    @app.get('/api/agent/sessions')
    def sessions(user=Depends(authenticated)):
        enabled()
        return {'items': store.sessions()}

    @app.get('/api/agent/sessions/{sid}/events')
    def events(sid: str, after: int = 0, user=Depends(authenticated)):
        enabled()
        try:
            rows = store.replay(sid, after)
            return {'items': rows, 'cursor': rows[-1]['seq'] if rows else after}
        except ValueError as error:
            raise HTTPException(400, str(error))

    @app.post('/api/agent/commands')
    async def command(request: Request, user=Depends(authenticated)):
        enabled()
        try:
            return store.command(await body(request))
        except (ValueError, TypeError) as error:
            raise HTTPException(409, str(error))

    @app.post('/api/agent-host/poll')
    def poll(hid=Depends(host)):
        return {'command': store.claim(hid)}

    @app.post('/api/agent-host/events')
    async def report(request: Request, hid=Depends(host)):
        try:
            data = await body(request)
            events = data.get('events', [data])
            if not isinstance(events, list) or not 1 <= len(events) <= 100 or any(not isinstance(e, dict) for e in events):
                raise ValueError('invalid_event_batch')
            for event in events:
                store.report(hid, event)
            return {'accepted': True}
        except (ValueError, TypeError) as error:
            raise HTTPException(409, str(error))
