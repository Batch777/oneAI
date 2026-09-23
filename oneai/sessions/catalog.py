"""Discover from installed harnesses; never infer account entitlement from a name."""
import tempfile
from pathlib import Path
from .adapters import JsonLines
from .profiles import catalog as profiles

def discover(config):
    result=[]
    for provider in config['providers']:
        binary=config['binaries'][provider]
        argv=[binary,'app-server','--stdio'] if provider=='codex' else [binary,'--mode','rpc','--no-session','--no-extensions','--no-skills','--no-prompt-templates','--no-themes']
        with tempfile.TemporaryDirectory(prefix='oneai-models-') as cwd:
            rpc=JsonLines(argv,Path(cwd),lambda event:None)
            try:
                if provider=='codex':
                    rpc.request('initialize',{'clientInfo':{'name':'oneai_catalog','version':'0.2'}});rpc.send({'method':'initialized'})
                    cursor=None
                    for _ in range(10):
                        data=rpc.request('model/list',{'limit':100,**({'cursor':cursor} if cursor else {})})
                        for m in data['data']:
                            result.append({'provider':provider,'id':m['model'],'name':m.get('displayName',m['model']),'efforts':[x['reasoningEffort'] for x in m.get('supportedReasoningEfforts',[])],'default_effort':m.get('defaultReasoningEffort'),'default':m.get('isDefault',False)})
                        cursor=data.get('nextCursor')
                        if not cursor:break
                else:
                    for m in rpc.request('get_available_models',pi=True)['models']:
                        if m['provider'] not in config.get('model_providers',{}).get('pi',['kimi-coding']):continue
                        levels=m.get('thinkingLevelMap')
                        efforts=[k for k,v in levels.items() if v is not None] if levels else (['off','low','medium','high'] if m.get('reasoning') else ['off'])
                        result.append({'provider':provider,'id':m['provider']+'/'+m['id'],'name':m['name'],'efforts':efforts,'default_effort':'high' if 'high' in efforts else efforts[0],'context_window':m.get('contextWindow'),'default':m['id']=='kimi-for-coding'})
            finally:rpc.close()
    return {'models':result,'profiles':profiles(config)}
