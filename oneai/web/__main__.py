import argparse
from .auth import pair
from ..config import Config
p=argparse.ArgumentParser(); p.add_argument('command',choices=['pair','serve']); p.add_argument('--port',type=int,default=8765); a=p.parse_args()
if a.command=='pair': print(pair(Config.load()))
else:
 import uvicorn
 uvicorn.run('oneai.web.app:app_factory',factory=True,host='127.0.0.1',port=a.port,proxy_headers=True,forwarded_allow_ips='127.0.0.1')
