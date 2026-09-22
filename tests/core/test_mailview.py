import json
import sqlite3
from contextlib import contextmanager
import pytest
from oneai.config import Config
from oneai.tasks import Tasks
from oneai import mailview


def fixture(tmp_path):
    cfg=Config(tmp_path/'vault',tmp_path/'state',None);cfg.ensure_dirs()
    store=Tasks(cfg.state_path/'tasks.sqlite');tid=store.create('mail:event','Subject','plain snapshot');task=store.get(tid);store.db.close()
    with sqlite3.connect(cfg.state_path/'outlook.sqlite') as db:
        db.executescript('CREATE TABLE messages(id TEXT,payload TEXT,deleted INTEGER);CREATE TABLE work(id TEXT,message_id TEXT,payload TEXT);')
        db.execute('INSERT INTO messages VALUES(?,?,0)',('message/id','{}'));db.execute('INSERT INTO work VALUES(?,?,?)',('event','message/id','{}'))
    return cfg,task


def test_semantic_parser_drops_active_content_and_tracking():
    nodes=mailview.readable({'contentType':'html','content':'<head><style>bad</style></head><p onclick="steal()">Hi <b>Reader</b><a href="javascript:alert(1)">bad link</a><a href="https://example.com/x">good</a></p><script>steal()</script><img src="https://tracker.example/pixel"><img src="cid:logo"><table width="900"><tr><td colspan="999">cell</td></tr></table>'})
    encoded=json.dumps(nodes)
    assert 'steal' not in encoded and 'remote_src' in encoded and 'javascript' not in encoded
    assert 'https://example.com/x' in encoded and 'logo' in encoded and 'Reader' in encoded
    assert '999' not in encoded and 'width' not in encoded
    assert mailview.readable({'contentType':'text','content':'a\nb'})[0]['children'][0]['text']=='a\nb'


@pytest.mark.parametrize('url',['javascript:alert(1)','data:text/html,x','https://u:p@example.com','https://example.com:bad','mailto:a@example.com?subject=x%0aInjected','https://example.com\n/x'])
def test_unsafe_links_are_never_clickable(url):assert mailview.safe_link(url) is None


def mock_graph(monkeypatch):
    @contextmanager
    def session(cfg):yield object()
    monkeypatch.setattr(mailview,'graph_session',session)
    calls=[]
    def get(auth,url,limit=0,binary=False):
        calls.append(url)
        if binary:return b'\x89PNG\r\n\x1a\nfixture'
        if '/attachments?' in url:return {'value':[{'id':'file/id','name':'pic.png','@odata.type':'#microsoft.graph.fileAttachment','size':20,'contentType':'image/png','isInline':True}]}
        return {'subject':'Subject','body':{'contentType':'html','content':'<p>Hello <a href="https://example.com">link</a></p>'},'from':{'emailAddress':{'name':'Sender','address':'sender@example.com'}},'hasAttachments':False}
    monkeypatch.setattr(mailview,'graph_get',get)
    return calls


def test_cached_display_metadata_only_and_click_download(tmp_path,monkeypatch):
    cfg,task=fixture(tmp_path);calls=mock_graph(monkeypatch)
    view=mailview.display(cfg,task)
    assert len(calls)==2 and view['attachments'][0]['inline']
    assert all('$value' not in url for url in calls)
    assert 'message%2Fid' in calls[0]
    assert mailview.display(cfg,task)==view and len(calls)==2
    data,name,mime=mailview.attachment(cfg,task,'file/id')
    assert data.startswith(b'\x89PNG') and name=='pic.png'
    assert calls[-1].endswith('/file%2Fid/$value')
    with pytest.raises(ValueError):mailview.attachment(cfg,task,'not-this-mail')
    assert len(calls)==3


def test_deleted_message_blocks_cached_content_and_download(tmp_path,monkeypatch):
    cfg,task=fixture(tmp_path);mock_graph(monkeypatch);mailview.display(cfg,task)
    with sqlite3.connect(cfg.state_path/'outlook.sqlite') as db:db.execute('UPDATE messages SET deleted=1')
    with pytest.raises(ValueError,match='mail_unavailable'):mailview.display(cfg,task)


def test_mail_endpoints_auth_and_download_headers(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from oneai.web.app import create_app
    from oneai.web.auth import pair
    cfg,task=fixture(tmp_path);calls=mock_graph(monkeypatch)
    c=TestClient(create_app(cfg,origin='https://testserver'),base_url='https://testserver')
    base='/api/tasks/'+task['id']
    assert c.get(base+'/mail').status_code==401
    assert c.get(base+'/attachment?attachment_id=file/id').status_code==401
    c.post('/api/login',json={'code':pair(cfg)},headers={'origin':'https://testserver'})
    assert c.get(base+'/mail').json()['from']['emailAddress']['address']=='sender@example.com'
    file=c.get(base+'/attachment',params={'attachment_id':'file/id'})
    assert file.status_code==200 and file.headers['content-disposition'].startswith('attachment;')
    assert file.headers['cache-control']=='no-store'
    assert c.get(base+'/attachment',params={'attachment_id':'file/id','preview':'true'}).headers['content-type']=='image/png'
    assert c.get(base+'/attachment?attachment_id=other').status_code==400


def test_remote_image_url_is_inert_and_https_only():
    nodes=mailview.readable({'contentType':'html','content':'<img src="https://images.example/a.png"><img src="http://images.example/a.png"><img src="javascript:evil()">'})
    assert nodes[0]['remote_src']=='https://images.example/a.png'
    assert nodes[1]['remote_src'] is None and nodes[2]['remote_src'] is None


def test_thumbnail_compresses_caches_and_checks_deletion(tmp_path,monkeypatch):
    import io
    from PIL import Image
    cfg,task=fixture(tmp_path);mock_graph(monkeypatch)
    original=io.BytesIO();Image.new('RGB',(2000,1000),'blue').save(original,'PNG')
    calls=[]
    def download(*args):calls.append(1);return original.getvalue(),'photo.png','image/png'
    monkeypatch.setattr(mailview,'attachment',download)
    data=mailview.thumbnail(cfg,task,'file/id')
    with Image.open(io.BytesIO(data)) as image:
        assert image.size==(480,240) and image.format=='JPEG' and not image.getexif()
    assert len(data)<len(original.getvalue())
    assert mailview.thumbnail(cfg,task,'file/id')==data and len(calls)==1
    with pytest.raises(ValueError):mailview.thumbnail(cfg,task,'foreign')
    with sqlite3.connect(cfg.state_path/'outlook.sqlite') as db:db.execute('UPDATE messages SET deleted=1')
    with pytest.raises(ValueError):mailview.thumbnail(cfg,task,'file/id')


def test_thumbnail_rejects_non_image(tmp_path,monkeypatch):
    cfg,task=fixture(tmp_path);mock_graph(monkeypatch)
    monkeypatch.setattr(mailview,'attachment',lambda *a:(b'<html>bad</html>','x.png','image/png'))
    with pytest.raises(ValueError,match='thumbnail_unavailable'):mailview.thumbnail(cfg,task,'file/id')


def test_received_date_backfill_and_sort_survive_edit(tmp_path):
    from fastapi.testclient import TestClient
    from oneai.web.app import create_app
    from oneai.web.auth import pair
    cfg,task=fixture(tmp_path)
    with sqlite3.connect(cfg.state_path/'outlook.sqlite') as db:
        db.execute('UPDATE work SET payload=?',(json.dumps({'receivedDateTime':'2012-10-06T15:03:02Z'}),))
    store=Tasks(cfg.state_path/'tasks.sqlite')
    recent=store.create('mail:new','new','body','2026-09-22T01:00:00Z')
    store.finish(task['id'],'prepared',[],[])
    store.command({'id':'reply-date','action':'generate_reply','task_id':task['id'],'revision':1})
    store.db.close()
    c=TestClient(create_app(cfg,origin='https://testserver'),base_url='https://testserver')
    c.post('/api/login',headers={'origin':'https://testserver'},json={'code':pair(cfg)})
    rows=c.get('/api/tasks').json()['items']
    assert rows[0]['id']==recent
    old=next(r for r in rows if r['id']==task['id'])
    assert old['mail_received']=='2012-10-06T15:03:02+00:00'
    assert old['updated']!=old['mail_received'] and old['revision']==2
    store=Tasks(cfg.state_path/'tasks.sqlite');store.backfill_mail_dates(cfg.state_path/'outlook.sqlite');store.db.close()
    assert Tasks.received_date('bad') is None
    assert Tasks.received_date('2026-01-01') is None
