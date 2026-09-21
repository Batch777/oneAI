"""Recent mailbox verification hints; no outbound calls or automatic verification."""
import datetime as dt
import hashlib
import html
import ipaddress
import json
import re
import sqlite3
from urllib.parse import urlsplit


def verification_hints(subject,body):
    text=html.unescape(subject+'\n'+body)
    code=None
    for pattern in [r'(?:验证码|动态口令|一次性密码)\s*(?:为|是|[:：])?\s*([0-9]{4,8})(?!\d)',r'(?:verification|security|one[- ]time|sign[- ]in)\s*(?:code|password)\s*(?:is|[:：])?\s*([0-9]{4,8})(?!\d)',r'(?<!\d)([0-9]{4,8})\s*(?:is your|为您的).{0,20}(?:code|验证码)']:
        match=re.search(pattern,text,re.I)
        if match:code=match[1];break
    account=bool(re.search(r'验证.{0,12}(?:账户|帐户|账号|邮箱|邮件地址)|激活.{0,12}(?:账户|帐户|账号)|(?:verify|confirm|activate).{0,25}(?:account|email|address)',text,re.I))
    links=[]
    if account:
        for raw in re.findall(r'https://[^\s<>"\']+',text):
            url=raw.rstrip('.,，。);]')
            try:
                parts=urlsplit(url);host=parts.hostname or '';port=parts.port
            except ValueError:continue
            if parts.username or parts.password or not host or len(url)>2000 or port not in (None,443):continue
            if host=='localhost' or '.' not in host or host.endswith(('.local','.internal')):continue
            try:
                ipaddress.ip_address(host)
                continue # Never expose IP-literal verification destinations.
            except ValueError:pass
            if url not in [x['url'] for x in links]:links.append({'url':url,'domain':host})
            if len(links)==3:break
    return {'code':code,'links':links,'account_verification':account}


def recent_alerts(cfg,now=None):
    path=cfg.state_path/'outlook.sqlite'
    if not path.exists():return []
    now=now or dt.datetime.now(dt.timezone.utc)
    results=[]
    with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as db:
        # Bounded recent delta events; old imported mail is never announced.
        rows=db.execute('SELECT payload FROM messages WHERE deleted=0 ORDER BY rowid DESC LIMIT 200').fetchall()
    for (payload,) in rows:
        try:
            m=json.loads(payload);received=dt.datetime.fromisoformat(m['receivedDateTime'].replace('Z','+00:00'))
            age=(now-received).total_seconds()
            if not 0<=age<=900:continue
            body=(m.get('body') or {}).get('content') or m.get('bodyPreview','')
            hints=verification_hints(m.get('subject',''),body)
            if not hints['code'] and not hints['account_verification']:continue
            sender=((m.get('from') or {}).get('emailAddress') or {}).get('address','')
            results.append({'id':hashlib.sha256((m['id']+m['receivedDateTime']).encode()).hexdigest()[:24],
                'subject':m.get('subject') or '账户验证','sender':sender,'received':m['receivedDateTime'],**hints})
        except (KeyError,ValueError,TypeError):continue
    return sorted(results,key=lambda x:x['received'],reverse=True)[:5]
