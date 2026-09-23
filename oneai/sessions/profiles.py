"""Versioned role instructions, separate from model/provider transport."""
from pathlib import Path
import hashlib
ROOT=Path(__file__).with_name('prompts')
PROFILES={'general':('普通会话',None),'self-develop':('oneAI 自迭代','self-develop.md'),'supervisor':('Mix · 规划与审查','supervisor.md'),'implementer':('Mix · 实现与测试','implementer.md')}

def instructions(profile):
    if profile not in PROFILES:raise ValueError('unknown_profile')
    name=PROFILES[profile][1]
    if not name:return ''
    return (ROOT/'common.md').read_text()+'\n\n'+(ROOT/name).read_text()

def catalog(config):
    result=[]
    for key,workspaces in config.get('profiles',{'general':list(config['workspaces'])}).items():
        prompt=instructions(key)
        result.append({'id':key,'name':PROFILES[key][0],'workspaces':workspaces,'prompt':prompt,'sha256':hashlib.sha256(prompt.encode()).hexdigest()})
    return result
