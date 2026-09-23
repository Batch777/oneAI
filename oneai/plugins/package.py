"""Strict v1 manifest and content-addressed packages; not an OS sandbox."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def manifest(value):
    keys = {'manifest_version','id','version','display_name','description','interface','entrypoint','input_schema','permissions','timeout_seconds'}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('invalid_manifest_fields')
    if value['manifest_version'] != 1 or value['interface'] != 'action.provider.v1':
        raise ValueError('unsupported_plugin_api')
    if not isinstance(value['id'],str) or not re.fullmatch(r'[a-z][a-z0-9.-]{2,79}',value['id']):
        raise ValueError('invalid_plugin_id')
    if not isinstance(value['version'],str) or not re.fullmatch(r'\d+\.\d+\.\d+',value['version']):
        raise ValueError('invalid_plugin_version')
    for key,limit in [('display_name',80),('description',500)]:
        if not isinstance(value[key],str) or not 1 <= len(value[key]) <= limit:raise ValueError('invalid_plugin_text')
    path=value['entrypoint']
    if not isinstance(path,str) or not path or len(path)>200 or '\\' in path or PurePosixPath(path).is_absolute() or any(x in ('..','.') for x in path.split('/')) or not path.endswith('.py'):
        raise ValueError('invalid_entrypoint')
    if value['permissions'] != ['owner-trusted-code']:
        raise ValueError('unsupported_plugin_permissions')
    if type(value['timeout_seconds']) is not int or not 1 <= value['timeout_seconds'] <= 60:raise ValueError('invalid_timeout')
    schema=value['input_schema']
    if not isinstance(schema,dict) or set(schema)!={'type','properties','required','additionalProperties'} or schema['type']!='object' or schema['additionalProperties'] is not False:
        raise ValueError('unsupported_input_schema')
    props=schema['properties'];required=schema['required']
    if not isinstance(props,dict) or len(props)>10 or not isinstance(required,list) or any(not isinstance(x,str) or x not in props for x in required):raise ValueError('invalid_schema_fields')
    for name,rule in props.items():
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',name) or not isinstance(rule,dict) or set(rule)-{'type','title','enum','maxLength','minimum','maximum'}:raise ValueError('unsupported_schema_keyword')
        if rule.get('type') not in ('string','integer','boolean'):raise ValueError('unsupported_field_type')
        if 'title' in rule and (not isinstance(rule['title'],str) or len(rule['title'])>80):raise ValueError('invalid_field_title')
        if 'enum' in rule and (not isinstance(rule['enum'],list) or not 1<=len(rule['enum'])<=20 or any(not isinstance(x,(str,int,bool)) for x in rule['enum'])):raise ValueError('invalid_enum')
        for k in ('maxLength','minimum','maximum'):
            if k in rule and (type(rule[k]) is not int or abs(rule[k])>100000):raise ValueError('invalid_schema_bound')
    return json.loads(encode(value))


def validate_input(schema,value):
    if not isinstance(value,dict) or set(value)-set(schema['properties']) or set(schema['required'])-set(value):raise ValueError('invalid_plugin_input')
    for k,v in value.items():
        rule=schema['properties'][k];kind=rule['type']
        if kind=='string' and (not isinstance(v,str) or len(v)>rule.get('maxLength',10000)):raise ValueError('invalid_plugin_input')
        if kind=='integer' and (type(v) is not int or v<rule.get('minimum',-100000) or v>rule.get('maximum',100000)):raise ValueError('invalid_plugin_input')
        if kind=='boolean' and type(v) is not bool:raise ValueError('invalid_plugin_input')
        if 'enum' in rule and v not in rule['enum']:raise ValueError('invalid_plugin_input')
    if len(encode(value).encode())>32000:raise ValueError('plugin_input_too_large')
    return value


def inspect(directory):
    directory=Path(directory)
    if directory.is_symlink() or not directory.is_dir():raise ValueError('invalid_package_directory')
    entries=[];total=0
    for path in sorted(directory.rglob('*')):
        mode=path.lstat().st_mode
        if stat.S_ISDIR(mode):continue
        if not stat.S_ISREG(mode):raise ValueError('plugin_links_or_special_files_rejected')
        size=path.stat().st_size;total+=size
        if total>20_000_000 or len(entries)>=500:raise ValueError('package_too_large')
        entries.append([path.relative_to(directory).as_posix(),hashlib.sha256(path.read_bytes()).hexdigest()])
    if (directory/'plugin.json').stat().st_size>20000:raise ValueError('manifest_too_large')
    m=manifest(json.loads((directory/'plugin.json').read_text()))
    if not (directory/m['entrypoint']).is_file():raise ValueError('entrypoint_missing')
    return m,hashlib.sha256(encode(entries).encode()).hexdigest()


def install(source,root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    m,digest=inspect(source)
    dest=root/'packages'/digest;dest.parent.mkdir(exist_ok=True,mode=0o700)
    if not dest.exists():
        temp=Path(tempfile.mkdtemp(prefix='.candidate-',dir=dest.parent))
        try:
            shutil.copytree(source,temp,dirs_exist_ok=True,symlinks=True)
            if inspect(temp)!=(m,digest):raise ValueError('package_changed_during_install')
            for p in temp.rglob('*'):p.chmod(0o500 if p.is_dir() else 0o400)
            temp.chmod(0o500);temp.rename(dest)
        finally:
            if temp.exists():shutil.rmtree(temp)
    elif inspect(dest)!=(m,digest):raise ValueError('installed_package_modified')
    index=root/'installed.json'
    values=json.loads(index.read_text()) if index.exists() else {}
    values[m['id']]=digest
    fd,tmp=tempfile.mkstemp(dir=root,prefix='.index-')
    with os.fdopen(fd,'w') as f:json.dump(values,f)
    os.replace(tmp,index)
    return m,digest
