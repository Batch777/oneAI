import argparse
from .auth import pair
from ..config import Config
p=argparse.ArgumentParser(); p.add_argument('command',choices=['pair','serve']); p.add_argument('--port',type=int,default=8765); p.add_argument('--code',help='Set a temporary four-digit pairing code (pair only)'); a=p.parse_args()
if a.code is not None and a.command != 'pair': p.error('--code is only supported with pair')
if a.command=='pair': print(pair(Config.load(), code=a.code))
else:
 import uvicorn
 uvicorn.run('oneai.web.app:app_factory',factory=True,host='127.0.0.1',port=a.port,access_log=False,proxy_headers=True,forwarded_allow_ips='127.0.0.1')
